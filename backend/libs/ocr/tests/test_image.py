"""Regression guard for a real bug found live: a fixed global threshold in
_preprocess() (previously pixels > 180 -> white, else black) produced
**zero** OCR words on 3 of 4 real photographed local-shop receipts tested,
because uneven real-photo lighting pushes large regions entirely black or
entirely white before Tesseract ever sees them. These are the actual real
documents that surfaced the bug, not synthetic re-creations — the only
reliable way to catch this class of regression is against a real photo, per
docs/invoice-ocr-plan.md's own live-verification discipline.
"""
import shutil
from pathlib import Path

import pytest
from PIL import Image

from ocr.image import ocr_image_words

FIXTURES = Path(__file__).parent / "fixtures" / "receipts"

TESSERACT_AVAILABLE = shutil.which("tesseract") is not None
requires_tesseract = pytest.mark.skipif(
    not TESSERACT_AVAILABLE,
    reason="tesseract binary not on PATH — see docs/invoice-ocr-plan.md §4.",
)


@requires_tesseract
class TestRealReceiptsProduceUsableOcrOutput:
    @pytest.mark.parametrize(
        "filename",
        ["sms_traders.jpeg", "yz_paint_hardware.jpeg", "guest_check.jpg", "samsuddin_siddiqui.webp"],
    )
    def test_at_least_some_words_are_recovered(self, filename: str) -> None:
        image = Image.open(FIXTURES / filename)
        words = ocr_image_words(image)
        assert len(words) > 0, f"{filename}: zero words recovered — the old hard-threshold bug is back"

    def test_a_well_lit_larger_photo_recovers_a_substantial_amount_of_text(self) -> None:
        """The strongest of the 4 real fixtures — a useful sanity floor
        beyond just "non-zero" for the easiest real case."""
        image = Image.open(FIXTURES / "yz_paint_hardware.jpeg")
        words = ocr_image_words(image)
        assert len(words) >= 15
