"""Calls AI Engine's OCR/structuring endpoint — a plain internal HTTP POST,
not a RabbitMQ publish. Same reasoning as Email/Slack connectors'
scanner_bridge.py: the queue and the rest of the architecture report's
event-flow are target-state, not built (see architecture report §11b),
so every internal hand-off in this codebase today goes over HTTP with
graceful degradation instead of assuming infrastructure that doesn't exist.
"""
import json
import logging
from typing import Optional

import httpx

from app.core.config import Settings
from app.schemas.ai_extraction import ExtractedInvoiceSchema

logger = logging.getLogger(__name__)


class AIEngineError(Exception):
    """Raised when AI Engine can't be reached, times out, or rejects the
    file — callers surface this as a clear failure on the AIJob rather than
    letting an httpx exception escape as an opaque 500."""


async def extract_invoice_data(
    settings: Settings, *, filename: str, content: bytes, mimetype: Optional[str],
    known_vendors: Optional[list[str]] = None,
) -> ExtractedInvoiceSchema:
    url = f"{settings.ai_engine_url.rstrip('/')}/api/v1/ai/ocr/extract"
    data = {"known_vendors": json.dumps(known_vendors)} if known_vendors else {}

    try:
        async with httpx.AsyncClient(timeout=settings.ai_engine_timeout_seconds) as client:
            response = await client.post(
                url,
                files={"file": (filename, content, mimetype or "application/octet-stream")},
                data=data,
            )
    except httpx.TimeoutException as exc:
        raise AIEngineError("AI Engine did not respond in time. Try again.") from exc
    except httpx.RequestError as exc:
        # AI Engine not running is a real, expected state during local dev
        # (it's a separate container) — this is the path most failures take
        # today, same as Invoice Service itself was for the connectors
        # before this service existed.
        raise AIEngineError(f"Could not reach AI Engine at {url}. Check the service is running.") from exc

    if response.status_code == 400:
        detail = response.json().get("detail", "AI Engine rejected this file")
        raise AIEngineError(detail)
    if response.status_code >= 400:
        logger.error("AI Engine returned %s: %s", response.status_code, response.text)
        raise AIEngineError(f"AI Engine returned an unexpected error ({response.status_code})")

    return ExtractedInvoiceSchema.model_validate(response.json())


async def auto_crop_image(
    settings: Settings, *, filename: str, content: bytes, mimetype: Optional[str],
) -> bytes:
    """Calls AI Engine's standalone smart-crop endpoint — the Scanner's
    camera-capture flow's own fast, OCR-free preview crop shown immediately
    after a photo is taken, before the file is ever persisted as an
    Invoice. Same call shape/error handling as render_invoice_page below;
    unlike that one, the input here is a not-yet-uploaded photo straight
    from the browser, not an already-saved invoice's own stored file."""
    url = f"{settings.ai_engine_url.rstrip('/')}/api/v1/ai/ocr/auto-crop"

    try:
        async with httpx.AsyncClient(timeout=settings.ai_engine_timeout_seconds) as client:
            response = await client.post(
                url,
                files={"file": (filename, content, mimetype or "application/octet-stream")},
            )
    except httpx.TimeoutException as exc:
        raise AIEngineError("AI Engine did not respond in time. Try again.") from exc
    except httpx.RequestError as exc:
        raise AIEngineError(f"Could not reach AI Engine at {url}. Check the service is running.") from exc

    if response.status_code == 400:
        detail = response.json().get("detail", "AI Engine could not process this image")
        raise AIEngineError(detail)
    if response.status_code >= 400:
        logger.error("AI Engine returned %s: %s", response.status_code, response.text)
        raise AIEngineError(f"AI Engine returned an unexpected error ({response.status_code})")

    return response.content


async def render_invoice_page(
    settings: Settings, *, filename: str, content: bytes, mimetype: Optional[str], page: int,
) -> bytes:
    """Calls AI Engine's page-rasterization endpoint — the bounding-box
    review UI's document image. Same call shape/error handling as
    extract_invoice_data above."""
    url = f"{settings.ai_engine_url.rstrip('/')}/api/v1/ai/ocr/render-page"

    try:
        async with httpx.AsyncClient(timeout=settings.ai_engine_timeout_seconds) as client:
            response = await client.post(
                url,
                files={"file": (filename, content, mimetype or "application/octet-stream")},
                data={"page": str(page)},
            )
    except httpx.TimeoutException as exc:
        raise AIEngineError("AI Engine did not respond in time. Try again.") from exc
    except httpx.RequestError as exc:
        raise AIEngineError(f"Could not reach AI Engine at {url}. Check the service is running.") from exc

    if response.status_code == 400:
        detail = response.json().get("detail", "AI Engine rejected this page request")
        raise AIEngineError(detail)
    if response.status_code >= 400:
        logger.error("AI Engine returned %s: %s", response.status_code, response.text)
        raise AIEngineError(f"AI Engine returned an unexpected error ({response.status_code})")

    return response.content
