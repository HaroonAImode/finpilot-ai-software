"""The browser-upload document library.

Why this exists: before it, a file dragged into the app went straight to
`POST /invoices/scan` and survived only as an Invoice. There was no record
of "a document the user uploaded", so the Documents page could not show it
and users re-uploaded the same file repeatedly. Storing the upload first,
and only then optionally handing it to the scanner, is what makes that page
a real library rather than a view of three connectors.
"""
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import Document, DocumentCategory, ScannerStatus
from app.schemas.document import (
    CategoryUpdate, DocumentListResponse, DocumentResponse, SendToScannerResponse,
)
from app.services.scanner_bridge import ScannerBridgeError, send_to_scanner
from app.services.storage import StorageManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


async def _get_document_scoped(db: AsyncSession, document_id: UUID, company_id: UUID) -> Document:
    """Fetches one live document belonging to this company.

    A soft-deleted document is treated as absent (404), not as a
    still-addressable row — otherwise "deleted" would only mean "hidden
    from the list", and the file would remain fetchable by id.
    """
    document = await db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.company_id == company_id,
            Document.deleted_at.is_(None),
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.post("/upload", response_model=DocumentResponse, status_code=201)
async def upload_document(
    file: UploadFile,
    category: Optional[str] = None,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DocumentResponse:
    """Stores one uploaded file. Does **not** scan it — sending to the
    scanner is a separate, explicit step (`/send-to-scanner`), because most
    uploads are never invoices."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="This file is empty")
    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds the {settings.max_upload_size_mb}MB limit")

    try:
        resolved_category = DocumentCategory(category) if category else DocumentCategory.other
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown category '{category}'")

    # The row's id is generated up front so it can key the S3 object, keeping
    # the key short and fully owned by us (see StorageManager.store_bytes).
    document_id = uuid4()
    filename = file.filename or "upload"
    storage = StorageManager(settings)
    try:
        s3_key, file_hash = await storage.store_bytes(str(document_id), filename, content)
    except Exception as exc:
        logger.exception("Failed to store uploaded document %s", filename)
        raise HTTPException(status_code=502, detail="Could not store the uploaded file") from exc

    document = Document(
        id=document_id, company_id=company_id, filename=filename, mimetype=file.content_type,
        size=len(content), s3_key=s3_key, file_hash=file_hash, category=resolved_category,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    logger.info("Document uploaded", extra={"document_id": str(document.id), "size": len(content)})
    return DocumentResponse.model_validate(document)


@router.get("/", response_model=DocumentListResponse)
async def list_documents(
    category: Optional[str] = Query(None, description="Filter by category, or omit for all"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> DocumentListResponse:
    query = select(Document).where(Document.company_id == company_id, Document.deleted_at.is_(None))
    count_query = (
        select(func.count())
        .select_from(Document)
        .where(Document.company_id == company_id, Document.deleted_at.is_(None))
    )

    if category:
        try:
            resolved = DocumentCategory(category)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown category '{category}'")
        query = query.where(Document.category == resolved)
        count_query = count_query.where(Document.category == resolved)

    total = await db.scalar(count_query) or 0
    rows = (
        await db.execute(query.order_by(Document.created_at.desc()).offset(skip).limit(limit))
    ).scalars().all()

    return DocumentListResponse(
        documents=[DocumentResponse.model_validate(row) for row in rows],
        total=total, skip=skip, limit=limit,
    )


@router.get("/{document_id}/content")
async def get_document_content(
    document_id: UUID,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Streams the stored bytes back for inline preview.

    Streamed through this service rather than handing out a presigned URL
    so the object store is never exposed to the browser — same reasoning as
    the connectors' own content endpoints. The `inline` disposition is what
    lets the Documents page preview a PDF in the browser's own viewer.
    """
    document = await _get_document_scoped(db, document_id, company_id)
    storage = StorageManager(settings)
    try:
        s3_object = storage.s3_client.get_object(Bucket=settings.s3_bucket_name, Key=document.s3_key)
    except Exception:
        logger.exception("Failed to open document %s from object storage", document_id)
        raise HTTPException(status_code=502, detail="Document file storage is temporarily unavailable")

    safe_filename = document.filename.replace('"', "'").replace("\r", "").replace("\n", "")
    return StreamingResponse(
        iter(lambda: s3_object["Body"].read(1024 * 1024), b""),
        media_type=document.mimetype or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{safe_filename}"'},
    )


@router.patch("/{document_id}/category", response_model=DocumentResponse)
async def update_category(
    document_id: UUID, payload: CategoryUpdate,
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    document = await _get_document_scoped(db, document_id, company_id)
    document.category = DocumentCategory(payload.category)
    await db.commit()
    await db.refresh(document)
    return DocumentResponse.model_validate(document)


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: UUID,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft delete: stamps `deleted_at` and leaves the stored object alone.

    Deliberate, and worth stating plainly — this is a financial app:

    - Any Invoice already scanned from this document is **not** touched.
      That invoice holds its own stored copy, so removing the object here
      would risk orphaning a booked record's source file.
    - The row survives as evidence that the file was seen and categorised.
    - Every read path filters `deleted_at IS NULL`, so from the user's side
      the document really is gone — including by direct id lookup.

    Reclaiming storage is a separate retention job that can reason about
    what is genuinely safe to purge. It is not this endpoint's job.
    """
    document = await _get_document_scoped(db, document_id, company_id)
    document.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("Document soft-deleted", extra={"document_id": str(document_id)})


@router.post("/{document_id}/send-to-scanner", response_model=SendToScannerResponse)
async def send_document_to_scanner(
    document_id: UUID,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SendToScannerResponse:
    """Hands this document to Invoice Service's scanner.

    The document's own row records the outcome, so the Documents page can
    show that a file already became an invoice rather than inviting the
    user to send it again.
    """
    document = await _get_document_scoped(db, document_id, company_id)

    storage = StorageManager(settings)
    try:
        s3_object = storage.s3_client.get_object(Bucket=settings.s3_bucket_name, Key=document.s3_key)
        content = s3_object["Body"].read()
    except Exception:
        logger.exception("Failed to open document %s from object storage", document_id)
        raise HTTPException(status_code=502, detail="Document file storage is temporarily unavailable")

    try:
        invoice_id = await send_to_scanner(
            settings, filename=document.filename, content=content,
            mimetype=document.mimetype, company_id=company_id,
        )
    except ScannerBridgeError as exc:
        # Recorded on the row, not just returned: a failed hand-off is state
        # the Documents page should be able to show and retry from.
        document.scanner_status = ScannerStatus.failed
        await db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    document.scanner_status = ScannerStatus.sent
    document.invoice_id = invoice_id
    await db.commit()
    await db.refresh(document)

    return SendToScannerResponse(
        document_id=document.id, invoice_id=document.invoice_id,
        scanner_status=document.scanner_status.value,
        message="Sent to the scanner — check the Invoice Scanner for the extracted result.",
    )
