"""Invoice OCR/structuring — Phase 2b (docs/invoice-ocr-plan.md): wires
Stage 1 (`ocr.extract_text`) and Stage 2 (`invoice_extraction.extract_invoice`)
behind one HTTP endpoint. No RabbitMQ, no queue — this service has nothing
to consume from yet, since Invoice Service (Phase 3) doesn't exist. Not
registered through the Gateway either; this is an internal endpoint other
backend services call directly (matching how
email-connector/slack-connector's scanner_bridge.py already call
INVOICE_SERVICE_URL directly), not a user-facing route.

Deliberately stateless: no database, no persistence, no company/tenancy
context. This endpoint's entire job is "take a file, return structured
data" — ownership, storage, and the Needs Review workflow around that data
belong to whichever service calls this (Invoice Service, Phase 3), not here.
"""
import base64
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import Response

from ocr import UnsupportedFileType, extract_documents_with_engine
from ocr.preprocess import preprocess_image
from ocr.render import PageOutOfRange, render_page_png

from invoice_extraction import extract_invoice

from app.core.config import Settings, get_settings
from app.schemas.ocr import AdditionalDocumentResponse, ExtractedInvoiceResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ocr", tags=["ocr"])


def _parse_known_vendors(raw: Optional[str]) -> Optional[list[str]]:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="known_vendors must be a JSON array of strings") from exc
    if not isinstance(parsed, list) or not all(isinstance(v, str) for v in parsed):
        raise HTTPException(status_code=400, detail="known_vendors must be a JSON array of strings")
    return parsed


@router.post("/extract", response_model=ExtractedInvoiceResponse)
async def extract(
    file: UploadFile,
    known_vendors: Optional[str] = Form(
        default=None,
        description="JSON array of known vendor names — optional, enables Rule 3.4/7.3 fuzzy vendor matching.",
    ),
    settings: Settings = Depends(get_settings),
) -> ExtractedInvoiceResponse:
    content = await file.read()
    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=413, detail=f"File exceeds the {settings.max_upload_size_mb}MB limit"
        )

    vendors_list = _parse_known_vendors(known_vendors)

    try:
        # Document Preprocessing (docs/invoice-ocr-plan.md §7) runs inside
        # here for an image upload — background-removing smart crop, and
        # multi-document split when the photo confidently contains more
        # than one receipt. A PDF, or an image with nothing confident to
        # act on, comes back as a single result, byte-for-byte the same
        # input this endpoint always received — so this is a strict
        # superset of the previous behavior, never a regression on it.
        document_results = extract_documents_with_engine(
            content, file.filename or "upload", mimetype=file.content_type,
            engine=settings.ocr_engine, paddleocr_url=settings.paddleocr_service_url,
            ocr_language=settings.ocr_language,
        )
    except UnsupportedFileType as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # A Tesseract/PyMuPDF failure (corrupted file, an OCR engine crash on
        # unusual input) is an external-tool failure, not a bug in this
        # service's own logic — surfaced as a clean 502, not an opaque 500.
        logger.error("OCR extraction failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Could not extract text from this file") from exc

    primary_ocr_result, _primary_bytes = document_results[0]
    logger.info(
        "OCR extraction stage complete",
        extra={
            "method": primary_ocr_result.method, "pages": primary_ocr_result.pages,
            "confidence": round(primary_ocr_result.confidence, 3), "documents_found": len(document_results),
        },
    )

    primary_invoice = extract_invoice(primary_ocr_result, known_vendors=vendors_list)
    logger.info(
        "Structuring stage complete",
        extra={
            "review_status": primary_invoice.review_status,
            "document_confidence": round(primary_invoice.document_confidence, 3),
            "line_item_count": len(primary_invoice.line_items),
        },
    )
    response = ExtractedInvoiceResponse.from_extracted_invoice(primary_invoice)

    if len(document_results) > 1:
        stem = Path(file.filename or "upload").stem
        additional: list[AdditionalDocumentResponse] = []
        for index, (ocr_result, image_bytes) in enumerate(document_results[1:], start=2):
            invoice = extract_invoice(ocr_result, known_vendors=vendors_list)
            additional.append(
                AdditionalDocumentResponse(
                    extracted=ExtractedInvoiceResponse.from_extracted_invoice(invoice),
                    image_base64=base64.b64encode(image_bytes).decode(),
                    # Always .png: Document Preprocessing re-encodes every
                    # crop to PNG regardless of the original format (see
                    # ocr.preprocess's own _encode_png) — naming it .jpg
                    # here just because the original upload was one would
                    # describe bytes that aren't actually a JPEG.
                    filename=f"{stem}_doc_{index}.png", mimetype="image/png",
                )
            )
        response.additional_documents = additional
        logger.info(
            "Document preprocessing split this upload into %d documents", len(document_results),
            extra={"additional_document_count": len(additional)},
        )

    return response


@router.post("/auto-crop")
async def auto_crop(
    file: UploadFile,
    settings: Settings = Depends(get_settings),
) -> Response:
    """Document Preprocessing's own smart-crop (`ocr.preprocess.preprocess_image`),
    exposed standalone — the Scanner's camera-capture flow's own fast, OCR-free
    preview crop shown immediately after a photo is taken, reusing the exact
    same proven boundary-detection algorithm `/extract` already runs as part
    of its own preprocessing stage for every image upload, rather than a
    separate client-side reimplementation (see docs/superpowers/specs/
    2026-09-05-camera-capture-scanner-design.md for why a client-side
    approach was tried twice and dropped). No LiteParse, no OCR, no invoice
    structuring — a single fast OpenCV pass, nothing this endpoint's own
    caller has to wait seconds for.

    Always returns exactly one image: the first/best detected document
    region in reading order when `preprocess_image` found one confidently,
    or the original bytes byte-for-byte untouched when nothing confident was
    found (`preprocess_image`'s own "never guess" contract, unchanged here).
    A live camera capture only ever has one real document in frame at a
    time, so this deliberately never exposes `preprocess_image`'s own
    multi-document split — that's `/extract`'s own concern for a phone-
    gallery photo that might catch two receipts side by side, not a live
    capture's.
    """
    content = await file.read()
    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=413, detail=f"File exceeds the {settings.max_upload_size_mb}MB limit"
        )

    try:
        documents = preprocess_image(content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return Response(content=documents[0].image_bytes, media_type="image/png")


@router.post("/render-page")
async def render_page(
    file: UploadFile,
    page: int = Form(..., description="0-indexed page number to render."),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Rasterizes one page of a document to PNG — the source image the
    bounding-box review UI overlays field boxes onto. Stateless like
    /extract; the caller (Invoice Service) owns fetching the original file
    bytes and streaming this response onward.
    """
    content = await file.read()
    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=413, detail=f"File exceeds the {settings.max_upload_size_mb}MB limit"
        )

    try:
        png_bytes = render_page_png(content, file.filename or "upload", file.content_type, page)
    except UnsupportedFileType as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PageOutOfRange as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Page render failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Could not render this page") from exc

    return Response(content=png_bytes, media_type="image/png")
