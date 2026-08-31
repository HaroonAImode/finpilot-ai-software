import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.tenancy import get_account_for_company
from app.db.session import get_db
from app.models import (
    EmailAccount, EmailAttachment, EmailMessage, EmailSyncJob, EmailSyncStatus,
    ReviewStatus, SenderRule, SenderRuleAction,
)
from app.schemas.sync import (
    CategoryUpdateRequest,
    EmailAttachmentListResponse,
    EmailAttachmentResponse,
    EmailSyncJobResponse,
    EmailSyncSettingsRequest,
    EmailSyncStartResponse,
    ReviewDecisionRequest,
    SenderRuleCreateRequest,
    SenderRuleResponse,
)
from app.services.attachment_fetch import AttachmentFetchError, refetch_and_store
from app.services.download_manager import DownloadManager
from app.services.scanner_bridge import ScannerBridgeError, send_attachment_to_scanner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"])
files_router = APIRouter(prefix="/files", tags=["files"])
sender_rules_router = APIRouter(prefix="/sender-rules", tags=["sender-rules"])


async def _get_attachment_scoped(db: AsyncSession, attachment_id: UUID, account_id: UUID) -> EmailAttachment:
    # A soft-deleted attachment 404s here too, not just from the list — every
    # direct link (preview, content, review, retry) has to agree it's gone.
    attachment = await db.scalar(
        select(EmailAttachment).filter(
            EmailAttachment.id == attachment_id, EmailAttachment.account_id == account_id,
            EmailAttachment.deleted_at.is_(None),
        )
    )
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return attachment


@router.post("/", response_model=EmailSyncStartResponse)
async def start_sync(
    payload: EmailSyncSettingsRequest = EmailSyncSettingsRequest(),
    account: EmailAccount = Depends(get_account_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        # model_fields_set, not "is not None": None is itself a meaningful
        # choice (the connector's default window) once Phase 3 gains a picker
        # for this — telling that apart from "field simply omitted" now
        # avoids a schema change later.
        if "sync_window_days" in payload.model_fields_set:
            account.sync_window_days = payload.sync_window_days
            # Widening the window must not resume from a cursor set under a
            # narrower one — same reasoning as Slack's settings endpoint.
            account.sync_cursor = None

        sync_job = EmailSyncJob(account_id=account.id, status=EmailSyncStatus.queued)
        db.add(sync_job)
        await db.commit()
        await db.refresh(sync_job)

        from app.worker import run_sync
        run_sync.delay(str(sync_job.id))

        return {"sync_job_id": sync_job.id, "status": "queued", "message": "Sync started successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to start email sync: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{sync_id}", response_model=EmailSyncJobResponse)
async def get_sync_status(
    sync_id: UUID,
    account: EmailAccount = Depends(get_account_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    sync_job = await db.scalar(
        select(EmailSyncJob).filter(EmailSyncJob.id == sync_id, EmailSyncJob.account_id == account.id)
    )
    if not sync_job:
        raise HTTPException(status_code=404, detail="Sync job not found")
    return EmailSyncJobResponse.from_orm(sync_job)


@files_router.get("/", response_model=EmailAttachmentListResponse)
async def list_files(
    category: str = Query(None, description="Filter by category"),
    review_status: str = Query(
        "imported",
        description=(
            "Which review state to list. Defaults to 'imported' so the "
            "Documents page never mixes in un-approved attachments; pass "
            "'needs_review' for the review tray, or 'all' for everything."
        ),
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    account: EmailAccount = Depends(get_account_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    query = (
        select(EmailAttachment, EmailMessage)
        .join(EmailMessage, EmailAttachment.message_id == EmailMessage.id)
        .filter(EmailAttachment.account_id == account.id, EmailAttachment.deleted_at.is_(None))
    )
    if category and category != "all":
        query = query.filter(EmailAttachment.category == category)
    if review_status and review_status != "all":
        try:
            query = query.filter(EmailAttachment.review_status == ReviewStatus(review_status))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown review_status '{review_status}'")

    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await db.execute(query.order_by(EmailAttachment.created_at.desc()).offset(skip).limit(limit))
    ).all()

    attachments = []
    for attachment, message in rows:
        response = EmailAttachmentResponse.model_validate(attachment)
        response.from_name = message.from_name
        response.from_address = message.from_address
        response.subject = message.subject
        response.received_at = message.received_at
        attachments.append(response)

    return {"attachments": attachments, "total": total or 0, "page": skip // limit, "page_size": limit}


@files_router.get("/{attachment_id}/content")
async def stream_attachment_content(
    attachment_id: UUID,
    account: EmailAccount = Depends(get_account_for_company),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
) -> StreamingResponse:
    attachment = await _get_attachment_scoped(db, attachment_id, account.id)
    if not attachment.downloaded or not attachment.s3_key:
        raise HTTPException(status_code=409, detail="This attachment is not available for preview yet")

    try:
        s3_object = DownloadManager(settings).s3_client.get_object(
            Bucket=settings.s3_bucket_name, Key=attachment.s3_key
        )
    except Exception:
        logger.exception("Failed to open attachment %s from object storage", attachment_id)
        raise HTTPException(status_code=502, detail="Preview storage is temporarily unavailable")

    safe_filename = attachment.filename.replace('"', "'").replace("\r", "").replace("\n", "")
    return StreamingResponse(
        iter(lambda: s3_object["Body"].read(1024 * 1024), b""),
        media_type=attachment.mimetype or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{safe_filename}"'},
    )


async def _maybe_create_rule(
    db: AsyncSession, account: EmailAccount, from_address: str | None,
    scope: str | None, action: SenderRuleAction,
) -> None:
    """Create the sender rule a review decision implies, if one was asked for.

    Upserts rather than inserts: deciding the other way on a later attachment
    from the same sender should flip the existing rule, not collide with the
    unique constraint or leave two contradictory rows.
    """
    if not scope or not from_address:
        return
    address = from_address.strip().lower()
    pattern = address if scope == "address" else address.rsplit("@", 1)[-1]
    if not pattern:
        return

    existing = await db.scalar(
        select(SenderRule).where(SenderRule.account_id == account.id, SenderRule.pattern == pattern)
    )
    if existing is not None:
        existing.action = action
        return
    db.add(SenderRule(account_id=account.id, pattern=pattern, action=action))


@files_router.post("/{attachment_id}/approve", response_model=EmailAttachmentResponse)
async def approve_attachment(
    attachment_id: UUID,
    payload: ReviewDecisionRequest = ReviewDecisionRequest(),
    account: EmailAccount = Depends(get_account_for_company),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
) -> EmailAttachmentResponse:
    """Approve a needs-review attachment, downloading it for the first time.

    The bytes were deliberately never fetched during sync (plan §5b), so this
    has to go back to Gmail via refetch_and_store — see that function for why
    a plain re-download by the stored id cannot work.
    """
    attachment = await _get_attachment_scoped(db, attachment_id, account.id)
    message = await db.get(EmailMessage, attachment.message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="The message this attachment came from is gone")

    if not attachment.downloaded:
        try:
            await refetch_and_store(settings, account, message, attachment)
        except AttachmentFetchError:
            await db.commit()  # persist e.g. a needs_reauth flag set along the way
            raise

    attachment.review_status = ReviewStatus.imported
    attachment.review_reason = "approved"
    await _maybe_create_rule(
        db, account, message.from_address, payload.also_rule_for, SenderRuleAction.allow
    )
    await db.commit()

    response = EmailAttachmentResponse.model_validate(attachment)
    response.from_name = message.from_name
    response.from_address = message.from_address
    response.subject = message.subject
    response.received_at = message.received_at
    return response


@files_router.post("/{attachment_id}/reject", response_model=EmailAttachmentResponse)
async def reject_attachment(
    attachment_id: UUID,
    payload: ReviewDecisionRequest = ReviewDecisionRequest(),
    account: EmailAccount = Depends(get_account_for_company),
    db: AsyncSession = Depends(get_db),
) -> EmailAttachmentResponse:
    """Reject a needs-review attachment. Nothing is downloaded, and the row
    is kept so the next sync does not offer it again."""
    attachment = await _get_attachment_scoped(db, attachment_id, account.id)
    message = await db.get(EmailMessage, attachment.message_id)

    attachment.review_status = ReviewStatus.rejected
    attachment.review_reason = "rejected"
    if message is not None:
        await _maybe_create_rule(
            db, account, message.from_address, payload.also_rule_for, SenderRuleAction.deny
        )
    await db.commit()

    response = EmailAttachmentResponse.model_validate(attachment)
    if message is not None:
        response.from_name = message.from_name
        response.from_address = message.from_address
        response.subject = message.subject
        response.received_at = message.received_at
    return response


@files_router.post("/{attachment_id}/retry", response_model=EmailAttachmentResponse)
async def retry_attachment(
    attachment_id: UUID,
    account: EmailAccount = Depends(get_account_for_company),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
) -> EmailAttachmentResponse:
    """Retry a failed download. Plan §14/Phase 4 — the email counterpart of
    Slack's POST /files/{id}/retry, adapted for Gmail's expiring ids: this
    re-fetches from Gmail rather than re-issuing the original (now dead)
    download URL, since email attachments have no such thing."""
    attachment = await _get_attachment_scoped(db, attachment_id, account.id)
    if attachment.downloaded:
        # Matches Slack's retry semantics: a request against an
        # already-successful download is a no-op, not an error — the
        # caller's UI state was simply stale.
        response = EmailAttachmentResponse.model_validate(attachment)
        return response
    if attachment.review_status != ReviewStatus.imported:
        raise HTTPException(
            status_code=409,
            detail="This attachment is awaiting review — approve it instead of retrying.",
        )

    message = await db.get(EmailMessage, attachment.message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="The message this attachment came from is gone")

    try:
        await refetch_and_store(settings, account, message, attachment)
    except AttachmentFetchError as exc:
        attachment.download_failed = True
        attachment.download_error = exc.detail
        await db.commit()
        raise

    await db.commit()

    response = EmailAttachmentResponse.model_validate(attachment)
    response.from_name = message.from_name
    response.from_address = message.from_address
    response.subject = message.subject
    response.received_at = message.received_at
    return response


@sender_rules_router.get("/", response_model=list[SenderRuleResponse])
async def list_sender_rules(
    account: EmailAccount = Depends(get_account_for_company),
    db: AsyncSession = Depends(get_db),
) -> list[SenderRule]:
    rows = await db.scalars(
        select(SenderRule)
        .where(SenderRule.account_id == account.id)
        .order_by(SenderRule.action, SenderRule.pattern)
    )
    return list(rows)


@sender_rules_router.post("/", response_model=SenderRuleResponse, status_code=201)
async def create_sender_rule(
    payload: SenderRuleCreateRequest,
    account: EmailAccount = Depends(get_account_for_company),
    db: AsyncSession = Depends(get_db),
) -> SenderRule:
    pattern = payload.pattern.strip().lower()
    if not pattern:
        raise HTTPException(status_code=400, detail="A pattern is required")

    action = SenderRuleAction(payload.action)
    existing = await db.scalar(
        select(SenderRule).where(SenderRule.account_id == account.id, SenderRule.pattern == pattern)
    )
    if existing is not None:
        # Re-adding a pattern flips its action rather than 409ing — the user's
        # intent is unambiguous and a duplicate-key error would just be noise.
        existing.action = action
        await db.commit()
        return existing

    rule = SenderRule(account_id=account.id, pattern=pattern, action=action)
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@sender_rules_router.delete("/{rule_id}", status_code=204)
async def delete_sender_rule(
    rule_id: UUID,
    account: EmailAccount = Depends(get_account_for_company),
    db: AsyncSession = Depends(get_db),
) -> None:
    rule = await db.scalar(
        select(SenderRule).where(SenderRule.id == rule_id, SenderRule.account_id == account.id)
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="Sender rule not found")
    await db.delete(rule)
    await db.commit()


@files_router.post("/{attachment_id}/send-to-scanner")
async def send_attachment_to_scanner_endpoint(
    attachment_id: UUID,
    target: str = Form(default="purchase"),
    account: EmailAccount = Depends(get_account_for_company),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
) -> dict:
    # "sale" routes this same attachment to the Revenue Manager's
    # scan-to-revenue endpoint instead of the purchase Scanner's — see
    # scanner_bridge.py's own docstring. Default keeps every existing
    # caller working with no change.
    if target not in ("purchase", "sale"):
        raise HTTPException(status_code=400, detail=f"Unknown target '{target}' — expected 'purchase' or 'sale'")
    attachment = await _get_attachment_scoped(db, attachment_id, account.id)
    try:
        return await send_attachment_to_scanner(settings, attachment, account.company_id, invoice_type=target)
    except ScannerBridgeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@files_router.patch("/{attachment_id}/category", response_model=EmailAttachmentResponse)
async def update_attachment_category(
    attachment_id: UUID,
    request: CategoryUpdateRequest,
    account: EmailAccount = Depends(get_account_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    attachment = await _get_attachment_scoped(db, attachment_id, account.id)
    attachment.category = request.category
    attachment.category_source = "manual_override"
    attachment.category_confidence = 1.0
    await db.commit()

    message = await db.get(EmailMessage, attachment.message_id)
    response = EmailAttachmentResponse.model_validate(attachment)
    if message:
        response.from_name = message.from_name
        response.from_address = message.from_address
        response.subject = message.subject
        response.received_at = message.received_at
    return response


@files_router.delete("/{attachment_id}", status_code=204)
async def delete_attachment(
    attachment_id: UUID,
    account: EmailAccount = Depends(get_account_for_company),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft delete — stamps deleted_at, touches nothing in the mailbox or S3.

    Distinct from POST /reject: rejecting happens before download, for an
    attachment still sitting in needs_review; this happens after import, for
    something a user no longer wants to see. Re-syncing never resurrects it —
    persist_attachment's upsert doesn't touch this column.
    """
    attachment = await _get_attachment_scoped(db, attachment_id, account.id)
    attachment.deleted_at = datetime.now(timezone.utc)
    await db.commit()
