"""Document Preprocessing (docs/invoice-ocr-plan.md §7) — boundary
detection, smart-crop with safety padding, multi-document split, and the
one rule every threshold in the module exists to enforce: when detection
is not confident, hand back the original image untouched rather than guess.

Synthetic images generated at test time (a plain-colored "document"
rectangle against a contrasting background), same reasoning as
test_extract.py's own synthetic PDFs/images: nothing here needs a real
scanned receipt to prove the boundary/confidence/fallback logic behaves
correctly — that real-world validation is a separate pass, once real
sample documents are available (see docs/invoice-ocr-plan.md §6's own
"only answerable by testing against real sample documents" note).
"""
import io

import numpy as np
import pytest
from PIL import Image, ImageDraw

from ocr.preprocess import preprocess_image


def _canvas(width: int, height: int, bg_color: tuple[int, int, int]) -> Image.Image:
    return Image.new("RGB", (width, height), bg_color)


def _to_png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _document_photo(
    width: int = 800, height: int = 600, box: tuple[int, int, int, int] = (150, 100, 650, 500),
    bg_color: tuple[int, int, int] = (60, 60, 60), doc_color: tuple[int, int, int] = (255, 255, 255),
) -> bytes:
    """A synthetic "photo": a solid-color rectangle (the "document") on a
    contrasting solid-color background (the "desk/table" a phone photo
    would show around it)."""
    image = _canvas(width, height, bg_color)
    ImageDraw.Draw(image).rectangle(box, fill=doc_color)
    return _to_png_bytes(image)


def _two_document_photo(width: int = 1000, height: int = 600) -> bytes:
    image = _canvas(width, height, (60, 60, 60))
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 50, 400, 550), fill=(255, 255, 255))
    draw.rectangle((600, 50, 950, 550), fill=(255, 255, 255))
    return _to_png_bytes(image)


def _two_documents_merged_by_a_pale_background(width: int = 1000, height: int = 600) -> bytes:
    """Unlike _two_document_photo above (two separately-contoured white
    rectangles on a dark background — the easy case _candidate_contours
    already handles directly), this simulates the real live-found failure
    mode _split_by_content_valley exists for: the background *between* the
    two documents is close enough in brightness to the documents themselves
    (a pale wood desk vs. white paper, confirmed live not reliably
    separable by Otsu) that the whole span — both "documents" plus the gap
    between them — forms one single bright, boxy contour. What still
    distinguishes them is real content (simulated here as horizontal
    lines, standing in for printed text): each document has its own dense
    block of lines, with a wide, genuinely content-free gap between them.
    """
    image = _canvas(width, height, (60, 60, 60))
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 50, 950, 550), fill=(245, 245, 245))
    for y in range(80, 520, 20):
        draw.line((80, y, 380, y), fill=(20, 20, 20), width=4)
        draw.line((620, y, 920, y), fill=(20, 20, 20), width=4)
    return _to_png_bytes(image)


def _single_document_with_a_two_column_layout(width: int = 1000, height: int = 600) -> bytes:
    """A real, common single-document shape found live to be a false
    positive during calibration: one A4 invoice template with two content
    columns and a narrower gutter between them than any genuine
    inter-document gap measured on a real photo — must never be split into
    two, or a document's own item description is separated from its own
    total sitting in the other column."""
    image = _canvas(width, height, (60, 60, 60))
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 50, 950, 550), fill=(245, 245, 245))
    for y in range(80, 520, 20):
        draw.line((80, y, 430, y), fill=(20, 20, 20), width=4)
        draw.line((570, y, 920, y), fill=(20, 20, 20), width=4)
    return _to_png_bytes(image)


def _single_document_with_a_sparse_second_half(width: int = 1000, height: int = 600) -> bytes:
    """Another real, live-found false-positive shape: a single document
    whose content is concentrated in one dense block with a large blank
    margin after it (a voucher's prose paragraph followed by a big blank
    area before its footer, say) — a wide low-density run exists, but the
    "second half" it would produce is almost entirely blank, not a second
    real document."""
    image = _canvas(width, height, (60, 60, 60))
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 50, 950, 550), fill=(245, 245, 245))
    for y in range(80, 200, 20):
        draw.line((80, y, 900, y), fill=(20, 20, 20), width=4)
    # A short, sparse mark near the very end — not a real second document's
    # worth of content, just a footer-sized fragment.
    draw.line((80, 530, 200, 530), fill=(20, 20, 20), width=4)
    return _to_png_bytes(image)


class TestSingleDocumentCrop:
    def test_crops_to_the_documents_boundary(self) -> None:
        content = _document_photo(box=(150, 100, 650, 500))
        results = preprocess_image(content)

        assert len(results) == 1
        result = results[0]
        assert result.confidence > 0
        # Actually cropped — smaller than the full 800x600 frame.
        cropped = Image.open(io.BytesIO(result.image_bytes))
        assert cropped.width < 800
        assert cropped.height < 600

    def test_the_crop_includes_safety_padding_beyond_the_tight_boundary(self) -> None:
        content = _document_photo(box=(150, 100, 650, 500))
        result = preprocess_image(content)[0]
        x0, y0, x1, y1 = result.bbox
        # The tight boundary is exactly (150, 100, 650, 500) — padding must
        # extend outward from it, not land exactly on it.
        assert x0 < 150
        assert y0 < 100
        assert x1 > 650
        assert y1 > 500

    def test_result_bytes_are_a_real_decodable_image(self) -> None:
        content = _document_photo()
        result = preprocess_image(content)[0]
        image = Image.open(io.BytesIO(result.image_bytes))
        image.verify()

    def test_a_document_filling_almost_the_entire_frame_is_returned_unchanged(self) -> None:
        # 790x590 of an 800x600 frame — ~97.1% of the area, over the
        # "nothing meaningful to crop" threshold.
        content = _document_photo(width=800, height=600, box=(5, 5, 795, 595))
        result = preprocess_image(content)[0]
        assert result.bbox == (0, 0, 800, 600)
        assert result.image_bytes == content


class TestMultiDocumentSplit:
    def test_detects_two_separate_documents(self) -> None:
        content = _two_document_photo()
        results = preprocess_image(content)
        assert len(results) == 2
        for result in results:
            assert result.confidence > 0
            Image.open(io.BytesIO(result.image_bytes)).verify()

    def test_split_documents_are_ordered_left_to_right(self) -> None:
        content = _two_document_photo()
        results = preprocess_image(content)
        assert results[0].bbox[0] < results[1].bbox[0]

    def test_split_bboxes_do_not_overlap(self) -> None:
        content = _two_document_photo()
        results = preprocess_image(content)
        a, b = results[0].bbox, results[1].bbox
        assert a[2] <= b[0] or b[2] <= a[0]  # one is entirely left of the other


class TestContentValleySplit:
    """_split_by_content_valley — a real-photo-driven addition, not part of
    the original contour-based split above: recovers the case two separate
    documents get merged into *one* contour because the background between
    them isn't reliably darker than the documents themselves (confirmed
    live: a pale wood desk vs. white receipt paper), using content density
    rather than brightness to tell them apart. See the module's own
    constants for the real false positives that shaped every threshold
    here — two rounds of real-document testing found and fixed two
    different single-document shapes this could otherwise wrongly split
    apart, fragmenting one document's own data across the two halves.
    """

    def test_two_documents_merged_into_one_contour_are_still_split_by_content(self) -> None:
        content = _two_documents_merged_by_a_pale_background()
        results = preprocess_image(content)
        assert len(results) == 2
        for result in results:
            assert result.confidence > 0
            Image.open(io.BytesIO(result.image_bytes)).verify()

    def test_a_two_column_single_document_layout_is_not_split(self) -> None:
        """Regression guard for a real bug found live: a common single-page
        invoice template (a left item-description column, a right totals
        column) produced a real, wide-looking gutter between its two
        columns — narrower than a genuine inter-document gap, but wide
        enough that an earlier, looser threshold split it in two, sending
        an item description and its own total to different "documents"."""
        content = _single_document_with_a_two_column_layout()
        assert len(preprocess_image(content)) == 1

    def test_a_document_with_a_sparse_trailing_section_is_not_split(self) -> None:
        """Regression guard for a real bug found live: a single voucher
        with one dense content block and a large blank margin after it
        produced a wide low-density run whose "second half" would be
        almost entirely blank — a spurious duplicate document, not a real
        second one, even though nothing about it fragments real content."""
        content = _single_document_with_a_sparse_second_half()
        assert len(preprocess_image(content)) == 1


class TestConservativeFallback:
    def test_a_blank_textureless_image_returns_the_original_untouched(self) -> None:
        # A perfectly uniform canvas has no edge or threshold boundary
        # anywhere — zero candidate contours, the "found nothing at all" path.
        content = _to_png_bytes(_canvas(400, 400, (128, 128, 128)))

        results = preprocess_image(content)

        assert len(results) == 1
        assert results[0].confidence == 0.0
        assert results[0].image_bytes == content
        assert results[0].bbox == (0, 0, 400, 400)

    def test_random_noise_also_returns_the_original_untouched(self) -> None:
        # Confirmed live: Otsu+Canny+morphology on pure noise reliably
        # produces one large blob covering nearly the whole frame, not zero
        # candidates — a *different* code path (the "already fills the
        # frame" branch) from the blank-canvas case above, but the same
        # required outcome: the original bytes are preserved untouched.
        rng = np.random.default_rng(seed=42)
        noise = rng.integers(0, 256, size=(400, 400, 3), dtype=np.uint8)
        content = _to_png_bytes(Image.fromarray(noise, mode="RGB"))

        results = preprocess_image(content)

        assert len(results) == 1
        assert results[0].confidence == 0.0
        assert results[0].image_bytes == content
        assert results[0].bbox == (0, 0, 400, 400)

    def test_a_candidate_too_small_to_be_a_document_is_ignored(self) -> None:
        # A tiny 40x30 rectangle in a large frame — far below the
        # minimum-area confidence bar, not a plausible document.
        content = _document_photo(width=800, height=600, box=(380, 285, 420, 315))
        result = preprocess_image(content)[0]
        assert result.confidence == 0.0
        assert result.image_bytes == content

    def test_undecodable_bytes_raise_value_error(self) -> None:
        with pytest.raises(ValueError):
            preprocess_image(b"not an image at all")
