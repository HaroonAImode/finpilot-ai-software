import json
import secrets
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import TokenCipher
from app.core.tenancy import get_company_id, get_optional_installation_for_company
from app.db.session import get_db
from app.models.installation import Installation, InstallationStatus
from app.models.workspace import Workspace
from app.services.slack.oauth import authorization_url, exchange_code

router = APIRouter(tags=["auth"])


def _build_state(cipher: TokenCipher, company_id: UUID) -> tuple[str, str]:
    nonce = secrets.token_urlsafe(32)
    payload = json.dumps({"nonce": nonce, "company_id": str(company_id)})
    return cipher.encrypt_state(payload), nonce


def _parse_state(cipher: TokenCipher, encrypted_state: str) -> tuple[UUID, str]:
    payload = cipher.decrypt_state(encrypted_state)
    data = json.loads(payload)
    return UUID(data["company_id"]), data["nonce"]


@router.get("/connect")
async def start_slack_oauth(
    response: Response,
    company_id: UUID = Depends(get_company_id),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Return the Slack consent URL for the caller to navigate to.

    JSON rather than a 302, because this route is company-scoped and therefore
    needs a bearer token — and a browser navigation cannot carry one. The
    frontend fetches this with the token attached, then sets window.location to
    the returned URL. Redirecting here instead would either force the route to
    be public (losing the company scoping) or break the moment it sat behind the
    Gateway.
    """
    cipher = TokenCipher(settings.token_encryption_key)
    state, nonce = _build_state(cipher, company_id)
    response.set_cookie(
        "slack_oauth_nonce", nonce, max_age=600, httponly=True, samesite="lax",
        secure=settings.app_env != "development",
    )
    return {"authorize_url": authorization_url(settings, state)}


@router.get("/callback")
async def slack_oauth_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if error:
        raise HTTPException(status_code=400, detail="Slack authorization was not completed")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing OAuth callback parameters")

    cipher = TokenCipher(settings.token_encryption_key)
    try:
        company_id, expected_nonce = _parse_state(cipher, state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state") from exc
    if not secrets.compare_digest(expected_nonce, request.cookies.get("slack_oauth_nonce", "")):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    try:
        payload = await exchange_code(settings, code)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Slack OAuth token exchange failed") from exc

    team = payload.get("team") or {}
    team_id = team.get("id")
    team_name = team.get("name")
    token = payload.get("access_token")
    if not team_id or not team_name or not token:
        raise HTTPException(status_code=502, detail="Slack OAuth response was incomplete")
    scopes = [scope for scope in (payload.get("scope") or "").split(",") if scope]

    workspace = (await db.execute(select(Workspace).where(Workspace.slack_team_id == team_id))).scalar_one_or_none()
    if workspace is None:
        workspace = Workspace(slack_team_id=team_id, name=team_name, domain=team.get("domain"), is_enterprise=bool(payload.get("is_enterprise_install", False)))
        db.add(workspace)
        await db.flush()
    else:
        workspace.name, workspace.domain = team_name, team.get("domain")

    other_owner = (await db.execute(select(Installation).where(Installation.workspace_id == workspace.id))).scalar_one_or_none()
    if other_owner and other_owner.company_id != company_id:
        raise HTTPException(status_code=409, detail="This Slack workspace is already connected to a different FinPilot company")

    installation = (await db.execute(select(Installation).where(Installation.company_id == company_id))).scalar_one_or_none()
    token_cipher = TokenCipher(settings.token_encryption_key)
    if installation is None:
        installation = Installation(
            company_id=company_id, workspace_id=workspace.id,
            bot_token_encrypted=token_cipher.encrypt(token), bot_user_id=payload.get("bot_user_id"),
            scopes=scopes, status=InstallationStatus.active,
        )
        db.add(installation)
    else:
        installation.workspace_id = workspace.id
        installation.bot_token_encrypted = token_cipher.encrypt(token)
        installation.bot_user_id = payload.get("bot_user_id")
        installation.scopes = scopes
        installation.status = InstallationStatus.active
    await db.commit()

    target = f"{settings.frontend_base_url.rstrip('/')}/app/settings?{urlencode({'tab': 'connected-apps', 'workspace': workspace.name})}"
    response = RedirectResponse(target, status_code=302)
    response.delete_cookie("slack_oauth_nonce")
    return response


@router.get("/status")
async def get_connection_status(
    installation: Installation | None = Depends(get_optional_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if installation is None:
        return {"connected": False}
    workspace = await db.get(Workspace, installation.workspace_id)
    return {
        "connected": installation.status == InstallationStatus.active,
        "workspace_name": workspace.name if workspace else None,
        "scopes": installation.scopes,
        "installed_at": installation.installed_at.isoformat(),
    }


@router.delete("/installation")
async def disconnect_slack(
    installation: Installation = Depends(get_optional_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if installation is None:
        raise HTTPException(status_code=404, detail="Slack is not connected for this company")
    installation.status = InstallationStatus.revoked
    await db.commit()
    return {"connected": False}
