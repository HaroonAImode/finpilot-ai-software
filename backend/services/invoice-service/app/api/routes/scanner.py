"""AI Invoice Scanner — architecture report §5.3. Upload a file, hand it to
AI Engine (Phase 2b) for extraction, persist the result. Synchronous, not
queue-based: AI Engine's rules-based extraction has no external API call in
it (docs/invoice-ocr-plan.md §2a), so it typically completes in well under a
second — there is nothing to gain from a background job here today, and
`/scan/{job_id}` still exists so the API shape does not need to change if a
future queue-based version becomes worth it.
"""
import base64
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import AIJob, AIJobStatus, Invoice
from app.schemas.invoice import InvoiceResponse, ScanJobResponse
from app.services.ai_engine_client import AIEngineError, auto_crop_image, extract_invoice_data
from app.services.invoice_builder import build_invoice
from app.services.storage import StorageManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/invoices", tags=["scanner"])


async def _known_vendors(db: AsyncSession, company_id: UUID) -> Optional[list[str]]:
    """Rule 3.4/7.3's fuzzy vendor match needs a company's existing vendor
    names to match against — pulled fresh per scan rather than cached,
    since the list is cheap to query and staleness here would mean a
    recently-added vendor doesn't get matched."""
    rows = (
        await db.execute(
            select(Invoice.vendor_name)
            .where(Invoice.company_id == company_id, Invoice.vendor_name.isnot(None))
            .distinct()
        )
    ).all()
    names = [r[0] for r in rows]
    return names or None


@router.post("/scan", response_model=ScanJobResponse)
async def scan_invoice(
    file: UploadFile,
    type: str = Form(default="purchase"),
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ScanJobResponse:
    if type != "purchase":
        raise HTTPException(
            status_code=400,
            detail="Only 'purchase' invoices can be scanned — sales invoices are generated, not uploaded.",
        )

    content = await file.read()
    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds the {settings.max_upload_size_mb}MB limit")

    file_hash = hashlib.sha256(content).hexdigest()
    existing = await db.scalar(
        select(Invoice)
        .where(Invoice.company_id == company_id, Invoice.file_hash == file_hash)
        .options(selectinload(Invoice.items))
    )
    if existing is not None:
        # Rule 7.1 — this exact file was already scanned for this company.
        # Not an error: hand back the existing invoice so the caller's UI
        # can show "already scanned" instead of a failure, and nothing is
        # reprocessed or duplicated.
        job = AIJob(company_id=company_id, invoice_id=existing.id, status=AIJobStatus.done, completed_at=datetime.now(timezone.utc))
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return ScanJobResponse(
            job_id=job.id, status="done", invoice_id=existing.id,
            invoice=InvoiceResponse.model_validate(existing),
        )

    job = AIJob(company_id=company_id, status=AIJobStatus.processing)
    db.add(job)
    await db.commit()
    await db.refresh(job)

    known_vendors = await _known_vendors(db, company_id)

    try:
        extraction = await extract_invoice_data(
            settings, filename=file.filename or "upload", content=content,
            mimetype=file.content_type, known_vendors=known_vendors,
        )
    except AIEngineError as exc:
        job.status = AIJobStatus.failed
        job.error = str(exc)
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    storage = StorageManager(settings)
    try:
        s3_key, _ = await storage.store_bytes(str(job.id), file.filename or "upload", content)
    except Exception as exc:
        job.status = AIJobStatus.failed
        job.error = f"Extraction succeeded but the file could not be stored: {exc}"
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(status_code=502, detail="Could not store the uploaded file") from exc

    invoice = build_invoice(
        company_id=company_id, extraction=extraction, filename=file.filename or "upload",
        mimetype=file.content_type, size=len(content), s3_key=s3_key, file_hash=file_hash,
    )
    db.add(invoice)
    await db.flush()

    job.invoice_id = invoice.id
    job.status = AIJobStatus.done
    job.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(invoice, attribute_names=["items", "updated_at"])

    logger.info(
        "Invoice scanned", extra={"invoice_id": str(invoice.id), "status": invoice.status.value, "confidence": round(invoice.document_confidence, 3)},
    )

    # Document Preprocessing (docs/invoice-ocr-plan.md §7) — populated only
    # when the uploaded photo confidently contained more than one separate
    # document. Each split gets its own AIJob/Invoice pair, built the exact
    # same way the primary one just was above, so a split document is a
    # first-class record — editable, categorizable, appears on Saved
    # Records — not a second-class attachment tucked inside the primary one.
    # Best-effort per split: one bad split document is logged and skipped,
    # never turns an otherwise-successful scan into a failed response.
    additional_invoice_ids: list[UUID] = []
    for additional in extraction.additional_documents:
        image_bytes = base64.b64decode(additional.image_base64)
        additional_file_hash = hashlib.sha256(image_bytes).hexdigest()

        # Same Rule 7.1 dedup as the primary upload — re-scanning the same
        # photo must not create duplicate split invoices either.
        existing_split = await db.scalar(
            select(Invoice).where(Invoice.company_id == company_id, Invoice.file_hash == additional_file_hash)
        )
        if existing_split is not None:
            additional_invoice_ids.append(existing_split.id)
            continue

        split_job = AIJob(company_id=company_id, status=AIJobStatus.processing)
        db.add(split_job)
        await db.flush()

        try:
            split_s3_key, _ = await storage.store_bytes(str(split_job.id), additional.filename, image_bytes)
        except Exception as exc:
            split_job.status = AIJobStatus.failed
            split_job.error = f"Extraction succeeded but this split document could not be stored: {exc}"
            split_job.completed_at = datetime.now(timezone.utc)
            logger.warning(
                "Could not store split document %s for scan job %s: %s", additional.filename, job.id, exc,
            )
            continue

        split_invoice = build_invoice(
            company_id=company_id, extraction=additional.extracted, filename=additional.filename,
            mimetype=additional.mimetype, size=len(image_bytes), s3_key=split_s3_key, file_hash=additional_file_hash,
        )
        db.add(split_invoice)
        await db.flush()

        split_job.invoice_id = split_invoice.id
        split_job.status = AIJobStatus.done
        split_job.completed_at = datetime.now(timezone.utc)
        additional_invoice_ids.append(split_invoice.id)

    if extraction.additional_documents:
        await db.commit()
        logger.info(
            "Document preprocessing split this upload into multiple invoices",
            extra={"primary_invoice_id": str(invoice.id), "additional_invoice_ids": [str(i) for i in additional_invoice_ids]},
        )

    return ScanJobResponse(
        job_id=job.id, status="done", invoice_id=invoice.id, invoice=InvoiceResponse.model_validate(invoice),
        additional_invoice_ids=additional_invoice_ids,
    )


@router.get("/scan/{job_id}", response_model=ScanJobResponse)
async def get_scan_status(
    job_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> ScanJobResponse:
    job = await db.scalar(select(AIJob).where(AIJob.id == job_id, AIJob.company_id == company_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Scan job not found")

    invoice_response = None
    if job.invoice_id:
        invoice_row = await db.scalar(
            select(Invoice).where(Invoice.id == job.invoice_id).options(selectinload(Invoice.items))
        )
        if invoice_row:
            invoice_response = InvoiceResponse.model_validate(invoice_row)

    return ScanJobResponse(
        job_id=job.id, status=job.status.value, invoice_id=job.invoice_id, invoice=invoice_response, error=job.error,
    )


@router.post("/auto-crop-preview")
async def auto_crop_preview(
    file: UploadFile,
    company_id: UUID = Depends(get_company_id),
    settings: Settings = Depends(get_settings),
) -> Response:
    """The Scanner's camera-capture flow's own fast preview crop — proxies
    to AI Engine's standalone smart-crop endpoint (no OCR, no persistence,
    no Invoice/AIJob created) so a freshly-captured photo can show an
    auto-cropped preview before the user has even confirmed Category/
    Payment Method, let alone committed to actually scanning it. Requires
    login (company_id) like every other route here, even though nothing is
    company-scoped in what it actually does — this is still an
    authenticated-user-only feature, not a public endpoint.
    """
    content = await file.read()
    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds the {settings.max_upload_size_mb}MB limit")

    try:
        png_bytes = await auto_crop_image(
            settings, filename=file.filename or "capture.jpg", content=content, mimetype=file.content_type,
        )
    except AIEngineError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(content=png_bytes, media_type="image/png")
