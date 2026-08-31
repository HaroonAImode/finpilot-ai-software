"""POST /ocr — LiteParse's documented OCR_API_SPEC.md contract. Uses a real
sample receipt image (not synthetic) since the whole point of this service
is real-world OCR accuracy — the same discipline this project's other
services already follow.
"""
import asyncio
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.main import app

FIXTURES = Path(__file__).parent.parent.parent.parent.parent / "libs" / "ocr" / "tests" / "fixtures" / "receipts"
client = TestClient(app)


class TestHealth:
    def test_health_check(self) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestOcrEndpoint:
    def test_a_real_receipt_is_ocrd_correctly(self) -> None:
        """Asserts the table headers this project's own Stage 2 label/header
        matching depends on (LABEL_VARIANTS/LINE_ITEM_HEADER_KEYWORDS) —
        confirmed live across repeated runs to read consistently. The
        specific receipt-number/date digits are NOT asserted here: repeated
        runs against this identical image showed PaddleOCR's CPU inference
        is not perfectly deterministic on small/ambiguous text (observed
        '1726' in some runs, '2018126' in another, for the same printed
        receipt number) — a real, honest limitation worth documenting
        rather than a flaky test worth papering over with a retry."""
        content = (FIXTURES / "yz_paint_hardware.jpeg").read_bytes()
        response = client.post("/ocr", files={"file": ("receipt.jpeg", content, "image/jpeg")}, data={"language": "en"})
        assert response.status_code == 200
        body = response.json()
        texts = [r["text"] for r in body["results"]]
        assert "Date" in texts
        assert "Particulars" in texts
        assert any("Hathori" in t for t in texts)

    def test_every_result_has_the_documented_shape(self) -> None:
        content = (FIXTURES / "yz_paint_hardware.jpeg").read_bytes()
        response = client.post("/ocr", files={"file": ("receipt.jpeg", content, "image/jpeg")})
        body = response.json()
        assert len(body["results"]) > 0
        for item in body["results"]:
            assert isinstance(item["text"], str) and item["text"]
            assert len(item["bbox"]) == 4
            assert 0.0 <= item["confidence"] <= 1.0

    def test_an_empty_file_is_a_clean_400(self) -> None:
        response = client.post("/ocr", files={"file": ("empty.jpg", b"", "image/jpeg")})
        assert response.status_code == 400

    def test_a_non_image_file_is_a_clean_400(self) -> None:
        response = client.post("/ocr", files={"file": ("not-an-image.txt", b"plain text content", "text/plain")})
        assert response.status_code == 400


class TestConcurrency:
    """Regression guard for a real bug found live in Docker: OCR inference
    is a blocking, CPU-bound call — running it directly inside the async
    route handler blocked uvicorn's single event loop for the whole
    inference duration, so a trivial /health check couldn't be served
    until a slow /ocr request finished. Something single-request local
    testing never exercised, only found once two real requests genuinely
    overlapped."""

    async def test_a_health_check_is_not_blocked_by_an_in_flight_ocr_request(self) -> None:
        content = (FIXTURES / "yz_paint_hardware.jpeg").read_bytes()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
            ocr_task = asyncio.create_task(
                async_client.post("/ocr", files={"file": ("receipt.jpeg", content, "image/jpeg")})
            )
            await asyncio.sleep(0.2)  # let the OCR request actually start before racing /health against it

            t0 = time.monotonic()
            health_response = await async_client.get("/health")
            health_elapsed = time.monotonic() - t0

            assert health_response.status_code == 200
            # A blocked event loop would make this wait for the entire OCR
            # call (several seconds); a healthy one answers near-instantly
            # regardless of what else is in flight.
            assert health_elapsed < 1.0

            ocr_response = await ocr_task
            assert ocr_response.status_code == 200
