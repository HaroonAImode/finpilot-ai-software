import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.tenancy import get_installation_for_company
from app.db.session import get_db
from app.models import Conversation, File, Installation, SyncJob, SyncStatus
from app.schemas.sync import (
    CategoryUpdateRequest,
    ConversationListResponse,
    ConversationResponse,
    FileListResponse,
    FileResponse,
    SyncJobResponse,
    SyncSettingsRequest,
    SyncSettingsResponse,
    SyncStartRequest,
    SyncStartResponse,
)
from app.services.download_manager import DownloadManager
from app.services.reconciliation import FileRetryManager
from app.services.scanner_bridge import ScannerBridgeError, send_file_to_scanner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"])
files_router = APIRouter(prefix="/files", tags=["files"])
conversations_router = APIRouter(prefix="/conversations", tags=["conversations"])


async def _get_file_scoped(db: AsyncSession, file_id: UUID, installation_id: UUID) -> File:
    # A soft-deleted file 404s here too, not just from the list — otherwise
    # "delete" would only hide it from one screen while every direct link
    # (preview, content, retry) kept working as if nothing happened.
    file_obj = await db.scalar(
        select(File).filter(
            File.id == file_id, File.installation_id == installation_id, File.deleted_at.is_(None),
        )
    )
    if not file_obj:
        raise HTTPException(status_code=404, detail="File not found")
    return file_obj


async def _build_conversation_list(db: AsyncSession, installation: Installation) -> dict:
    """Shared by GET / (read-only) and POST /refresh (after a live discovery
    pass), so the two never drift into returning differently-shaped data."""
    counts = dict(
        (
            await db.execute(
                select(File.conversation_id, func.count(File.id))
                .where(File.installation_id == installation.id)
                .group_by(File.conversation_id)
            )
        ).all()
    )

    rows = await db.scalars(
        select(Conversation)
        .where(Conversation.installation_id == installation.id)
        .order_by(Conversation.name)
    )

    conversations = []
    needs_attention = 0
    for row in rows:
        if row.last_error:
            needs_attention += 1
        conversations.append(
            ConversationResponse(
                id=row.id,
                slack_conversation_id=row.slack_conversation_id,
                name=row.name,
                conversation_type=row.conversation_type.value
                if hasattr(row.conversation_type, "value")
                else str(row.conversation_type),
                file_count=counts.get(row.id, 0),
                last_synced=row.last_synced,
                last_error=row.last_error,
                last_error_hint=ConversationResponse.hint_for(row.last_error),
            )
        )

    return {
        "conversations": conversations,
        "total": len(conversations),
        "needs_attention": needs_attention,
    }


@conversations_router.get("/", response_model=ConversationListResponse)
async def list_conversations(
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Conversations FinPilot knows about, with sync status per conversation.

    Reads only what previous syncs already stored — no Slack calls, so this is
    cheap enough to poll while a sync runs.
    """
    return await _build_conversation_list(db, installation)


@conversations_router.post("/refresh", response_model=ConversationListResponse)
async def refresh_conversations(
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
) -> dict:
    """Discover conversations directly from Slack — metadata only, no message
    walk, no downloads — and upsert them before returning the same shape as
    GET /.

    The plain GET only ever shows what a previous sync already stored, which is
    empty for a brand-new connection: nothing has been walked yet, so there is
    nothing to pick from. This is what the sync picker calls first, so the list
    is always complete even before the first real sync has ever run. It reuses
    get_or_create_conversation, so a DM's real name is resolved the same way a
    full sync resolves it (see the DM-naming fix), and an existing row that
    predates that fix gets repaired here too, not only during a full sync.
    """
    from app.core.security import TokenCipher
    from app.services.rate_limiter import get_rate_limiter
    from app.services.slack.discovery import DiscoveryPersistenceService, DiscoveryService

    cipher = TokenCipher(settings.token_encryption_key)
    bot_token = cipher.decrypt(installation.bot_token_encrypted)
    persistence = DiscoveryPersistenceService(db, installation.id)

    try:
        async with DiscoveryService(bot_token, settings, get_rate_limiter()) as discovery:
            async for conv_data in discovery.list_conversations():
                await persistence.get_or_create_conversation(
                    conv_data, settings.slack_client_id, discovery=discovery
                )
    except Exception as exc:
        logger.error("Failed to refresh conversations: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Could not reach Slack to discover conversations")

    await db.commit()
    return await _build_conversation_list(db, installation)


@router.get("/settings", response_model=SyncSettingsResponse)
async def get_sync_settings(
    installation: Installation = Depends(get_installation_for_company),
) -> dict:
    return {"sync_window_days": installation.sync_window_days}


@router.put("/settings", response_model=SyncSettingsResponse)
async def update_sync_settings(
    payload: SyncSettingsRequest,
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Set how far back syncs look. null means all history.

    Changing the window clears every incremental cursor for this installation.
    Widening from 3 months to 12 would otherwise fetch nothing new: each
    conversation would still resume from its last processed message, and the
    older messages the user just asked for would never be requested. Clearing
    the cursors makes the next sync re-walk the new window once.
    """
    installation.sync_window_days = payload.sync_window_days

    await db.execute(
        update(Conversation)
        .where(Conversation.installation_id == installation.id)
        .values(last_seen_ts=None)
    )
    await db.commit()

    return {"sync_window_days": installation.sync_window_days}


@router.post("/", response_model=SyncStartResponse)
async def start_sync(
    payload: SyncStartRequest = SyncStartRequest(),
    full_resync: bool = Query(
        default=False,
        description="Re-walk everything inside the window, ignoring what was already processed.",
    ),
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        # window_days uses model_fields_set rather than "is not None", because
        # None is itself a meaningful choice here — "All time" in the picker is
        # window_days: null, which must be told apart from the field being left
        # out of the request entirely (meaning "don't touch the setting").
        if "window_days" in payload.model_fields_set:
            installation.sync_window_days = payload.window_days
            await db.execute(
                update(Conversation)
                .where(Conversation.installation_id == installation.id)
                .values(last_seen_ts=None)
            )

        # conversation_ids are resolved to Slack ids inside the orchestrator,
        # scoped to this installation, rather than here — the worker runs in a
        # separate process with its own DB session, and resolving there means
        # the tenant check happens right next to the query that uses it rather
        # than being trusted across a process boundary.
        conversation_ids = (
            [str(cid) for cid in payload.conversation_ids] if payload.conversation_ids else None
        )

        sync_job = SyncJob(
            installation_id=installation.id, status=SyncStatus.queued,
            total_conversations=0, conversations_processed=0,
            files_discovered=0, files_downloaded=0, files_failed=0,
        )
        db.add(sync_job)
        await db.commit()
        await db.refresh(sync_job)

        from app.worker import run_sync
        run_sync.delay(str(sync_job.id), full_resync=full_resync, conversation_ids=conversation_ids)

        return {"sync_job_id": sync_job.id, "status": "queued", "message": "Sync started successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start sync: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{sync_id}", response_model=SyncJobResponse)
async def get_sync_status(
    sync_id: UUID,
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    sync_job = await db.scalar(select(SyncJob).filter(SyncJob.id == sync_id, SyncJob.installation_id == installation.id))
    if not sync_job:
        raise HTTPException(status_code=404, detail="Sync job not found")
    return SyncJobResponse.from_orm(sync_job)


@files_router.get("/", response_model=FileListResponse)
async def list_files(
    category: str = Query(None, description="Filter by category"),
    file_type: str = Query(None, description="Filter by file type"),
    conversation_id: UUID = Query(None, description="Only files from this conversation"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    query = select(File).filter(File.installation_id == installation.id, File.deleted_at.is_(None))
    if category and category != "all":
        query = query.filter(File.category == category)
    if file_type:
        query = query.filter(File.file_type == file_type)
    if conversation_id:
        # Scoped by installation_id above, so this cannot leak a conversation
        # (and therefore files) belonging to a different company even if a
        # caller guesses a valid conversation_id from another tenant.
        query = query.filter(File.conversation_id == conversation_id)

    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    files = await db.scalars(query.offset(skip).limit(limit))

    return {
        "files": [FileResponse.from_orm(f) for f in files],
        "total": total or 0,
        "page": skip // limit,
        "page_size": limit,
    }


@files_router.get("/{file_id}", response_model=FileResponse)
async def get_file(
    file_id: UUID,
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    file_obj = await _get_file_scoped(db, file_id, installation.id)
    return FileResponse.from_orm(file_obj)


@files_router.get("/{file_id}/preview")
async def get_file_preview_url(
    file_id: UUID,
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    file_obj = await _get_file_scoped(db, file_id, installation.id)
    if not file_obj.downloaded or not file_obj.s3_key:
        raise HTTPException(status_code=409, detail="This file is not available for preview yet")
    return {
        "url": f"/api/v1/slack/files/{file_id}/content",
        "filename": file_obj.filename,
        "mimetype": file_obj.mimetype or "application/octet-stream",
    }


@files_router.get("/{file_id}/content")
async def stream_file_content(
    file_id: UUID,
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
) -> StreamingResponse:
    file_obj = await _get_file_scoped(db, file_id, installation.id)
    if not file_obj.downloaded or not file_obj.s3_key:
        raise HTTPException(status_code=409, detail="This file is not available for preview yet")

    try:
        s3_object = DownloadManager(settings).s3_client.get_object(Bucket=settings.s3_bucket_name, Key=file_obj.s3_key)
    except Exception:
        logger.exception("Failed to open file %s from object storage", file_id)
        raise HTTPException(status_code=502, detail="Preview storage is temporarily unavailable")

    safe_filename = file_obj.filename.replace('"', "'").replace("\r", "").replace("\n", "")
    return StreamingResponse(
        iter(lambda: s3_object["Body"].read(1024 * 1024), b""),
        media_type=file_obj.mimetype or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{safe_filename}"'},
    )


@files_router.patch("/{file_id}/category", response_model=FileResponse)
async def update_file_category(
    file_id: UUID,
    request: CategoryUpdateRequest,
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    file_obj = await _get_file_scoped(db, file_id, installation.id)
    file_obj.category = request.category
    file_obj.category_source = "manual_override"
    file_obj.category_confidence = 1.0
    await db.commit()
    return FileResponse.from_orm(file_obj)


@files_router.delete("/{file_id}", status_code=204)
async def delete_file(
    file_id: UUID,
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft delete — stamps deleted_at, touches nothing in Slack or S3.

    This mirrors a real Slack message, unlike a browser upload: a hard
    delete would just come back on the next sync (discovery.py re-persists
    by slack_file_id), and there is no "delete it from Slack too" button in
    this product. Hiding it here is the whole feature.
    """
    file_obj = await _get_file_scoped(db, file_id, installation.id)
    file_obj.deleted_at = datetime.now(timezone.utc)
    await db.commit()


@files_router.post("/{file_id}/retry", response_model=FileResponse)
async def retry_file_download(
    file_id: UUID,
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _get_file_scoped(db, file_id, installation.id)  # 404s if it's not this company's file
    try:
        file_obj = await FileRetryManager(db).retry_file_download(file_id)
        return FileResponse.from_orm(file_obj)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@files_router.post("/{file_id}/send-to-scanner")
async def send_file_to_scanner_endpoint(
    file_id: UUID,
    target: str = Form(default="purchase"),
    installation: Installation = Depends(get_installation_for_company),
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
) -> dict:
    # "sale" routes this same file to the Revenue Manager's scan-to-revenue
    # endpoint instead of the purchase Scanner's — see scanner_bridge.py's
    # own docstring. Default keeps every existing caller (the Documents
    # page's "Send to Scanner") working with no change.
    if target not in ("purchase", "sale"):
        raise HTTPException(status_code=400, detail=f"Unknown target '{target}' — expected 'purchase' or 'sale'")
    file_obj = await _get_file_scoped(db, file_id, installation.id)
    try:
        return await send_file_to_scanner(settings, file_obj, installation.company_id, invoice_type=target)
    except ScannerBridgeError as e:
        raise HTTPException(status_code=502, detail=str(e))
