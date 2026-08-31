"""LiteParse's documented external-OCR-server contract
(OCR_API_SPEC.md) — POST /ocr, one image in, {results: [{text, bbox,
confidence, polygon}]} out. Not registered through the Gateway; an
internal service ai-engine's LiteParse client calls directly, matching
every other internal service-to-service call in this codebase.

Deliberately stateless per request (the PaddleOCR *engine* itself is a
process-wide singleton, app.core.engine.get_configured_engine — model
weights loaded once, not per request).
"""
import asyncio
import io
import logging

import numpy as np
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from app.core.config import Settings, get_settings
from app.core.engine import get_configured_engine
from app.schemas.ocr import OcrResponse, OcrResultItem

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ocr"])


@router.post("/ocr", response_model=OcrResponse)
async def ocr(
    file: UploadFile,
    language: str = Form(default="en"),
    settings: Settings = Depends(get_settings),
) -> OcrResponse:
    content = await file.read()
    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds the {settings.max_upload_size_mb}MB limit")
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    # PaddleOCR's .predict() only accepts a numpy array or a file path —
    # confirmed live it rejects raw bytes outright ("Not supported input
    # data type"). Decoded via PIL to RGB (not cv2's native BGR) — also
    # confirmed live this isn't just a convenience choice: RGB detected
    # more real text on this project's own sample receipt (including a
    # stylized logo/vendor-name region BGR missed entirely), not just an
    # equivalent re-encoding.
    try:
        image = Image.open(io.BytesIO(content)).convert("RGB")
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=400, detail="Could not decode this file as an image") from exc
    image_array = np.array(image)

    try:
        engine = get_configured_engine(settings, language)
        # engine.predict() is a synchronous, CPU-bound call (real inference,
        # not I/O) — run in a worker thread rather than directly in this
        # async handler. Confirmed live this matters: calling it inline
        # blocks uvicorn's single event loop for the full inference
        # duration, so a second request (even a trivial /health check)
        # cannot be served until the first finishes — a real concurrency
        # bug found via a genuinely overlapping pair of requests, something
        # single-request local testing never exercised.
        results = await asyncio.to_thread(engine.predict, image_array)
    except Exception as exc:
        logger.error("PaddleOCR inference failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="OCR processing failed") from exc

    if not results:
        return OcrResponse(results=[])

    page = results[0]
    items = [
        OcrResultItem(
            text=text,
            bbox=(float(box[0]), float(box[1]), float(box[2]), float(box[3])),
            confidence=float(score),
            polygon=[(float(x), float(y)) for x, y in poly.tolist()] if poly is not None else None,
        )
        for text, score, box, poly in zip(
            page.get("rec_texts", []), page.get("rec_scores", []),
            page.get("rec_boxes", []), page.get("rec_polys", []),
        )
    ]
    logger.info("OCR complete", extra={"language": language, "result_count": len(items)})
    return OcrResponse(results=items)
