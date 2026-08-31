"""Tesseract OCR over a single image — used both for actual image uploads
(JPG/PNG/WhatsApp screenshots) and for pages rasterized out of a scanned PDF
(pdf.py). See docs/invoice-ocr-plan.md §2 for why this exists at all instead
of sending the image straight to a vision LLM.
"""
from PIL import Image, ImageOps
import pytesseract

from ocr.models import PositionedWord

# Below this shorter-side pixel size, Tesseract is prone to finding
# literally nothing even after preprocessing — confirmed against a real
# 387x516 phone-camera receipt photo, which produced zero usable words at
# native size but recovered some once upscaled. A second OCR pass against a
# larger copy is only worth the extra cost for genuinely small images —
# tested on larger real photos (899x1599, 768x1024) upscaling produced
# *fewer* usable words than the native-size pass, so this is deliberately
# not applied unconditionally.
_UPSCALE_BELOW_PX = 700
_UPSCALE_FACTOR = 2

# The confidence floor (Tesseract's own 0-100 scale) a word must clear to
# count toward picking the stronger of two preprocessing variants (_score
# below) — a variant that "found" a handful of near-zero-confidence words
# isn't actually better evidence than one that found none. Words below this
# are still kept if that variant wins; this only decides *which* variant wins.
_CONFIDENT_THRESHOLD = 40


def _preprocess(image: Image.Image) -> Image.Image:
    """Grayscale + auto-contrast. Replaces a fixed global threshold
    (previously: pixels lighter than 180 -> white, darker -> black) that
    tested destructive on real phone-camera photos — confirmed empirically
    against 4 real receipts, that fixed cutoff produced literally zero
    recognizable words on 3 of them (uneven lighting/shadows in a real photo
    push large regions entirely black or entirely white before Tesseract
    ever sees them), while auto-contrast adapts per-image and matched or
    beat the old approach on every one of those same images."""
    grayscale = image.convert("L")
    return ImageOps.autocontrast(grayscale)


def _run_tesseract(image: Image.Image, page: int, scale: float) -> list[PositionedWord]:
    """`scale` maps this image's pixel coordinates back to the *original*
    upload's pixel space (1.0 for a same-size variant, 0.5 for a 2x-upscaled
    one) — bounding boxes must stay in the original image's coordinate
    space no matter which internal variant produced the winning OCR read,
    since that's the same space page_dimensions and the rendered preview
    (ocr.render) both use; otherwise a bbox from the upscaled path would
    silently misalign with everything downstream."""
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    words: list[PositionedWord] = []
    for i, text in enumerate(data["text"]):
        if not text.strip():
            continue
        conf = int(data["conf"][i])
        if conf < 0:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        words.append(
            PositionedWord(
                text=text, x0=x * scale, y0=y * scale, x1=(x + w) * scale, y1=(y + h) * scale,
                page=page, confidence=conf / 100.0,
            )
        )
    return words


def _score(words: list[PositionedWord]) -> int:
    return sum(1 for w in words if w.confidence >= _CONFIDENT_THRESHOLD / 100)


def ocr_image_words(image: Image.Image, page: int = 0) -> list[PositionedWord]:
    """Returns every word Tesseract found, each with its own bounding box
    and confidence — the raw material both `ocr_image` (below) and Stage
    2's table reconstruction (docs/research/Invoice_OCR_Rules_Based_Extraction_Report.md
    Rule 4.2) need. Words Tesseract couldn't score at all (conf == -1,
    non-text regions) are dropped here rather than passed on with a fake
    confidence value.

    For a small source image, also tries a second, upscaled OCR pass and
    keeps whichever variant found more confident words — a cheap way to
    treat OCR output as evidence to weigh rather than a single fixed
    procedure to trust blindly, without paying the extra pass's cost on
    every document (only triggered below _UPSCALE_BELOW_PX; see that
    constant's docstring for why unconditional upscaling isn't used).
    """
    processed = _preprocess(image)
    candidates = [_run_tesseract(processed, page, scale=1.0)]

    if min(image.size) < _UPSCALE_BELOW_PX:
        upscaled = processed.resize(
            (processed.width * _UPSCALE_FACTOR, processed.height * _UPSCALE_FACTOR), Image.LANCZOS,
        )
        candidates.append(_run_tesseract(upscaled, page, scale=1.0 / _UPSCALE_FACTOR))

    return max(candidates, key=_score)


def words_to_readable_text(words: list[PositionedWord]) -> str:
    """Groups words back into lines by y-position (rather than a flat
    space-joined blob of every word on the page) — a flat join would
    scramble multi-column or tabular content, exactly what an invoice's
    line-item table is, which matters for how readable the result is for
    Stage 2's field/table extraction. Shared by `ocr_image` below and
    pdf.py's scanned-PDF fallback, so both produce text the same way.
    """
    if not words:
        return ""

    tolerance = 8.0
    sorted_words = sorted(words, key=lambda w: (w.y0, w.x0))
    lines: list[list[PositionedWord]] = []
    for word in sorted_words:
        if lines and abs(word.y0 - lines[-1][-1].y0) <= tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])

    return "\n".join(" ".join(w.text for w in sorted(line, key=lambda w: w.x0)) for line in lines)


def ocr_image(image: Image.Image) -> tuple[str, float]:
    """Returns (text, confidence). Confidence is the mean per-word score
    across everything Tesseract could score."""
    words = ocr_image_words(image)
    if not words:
        return "", 0.0
    confidence = sum(w.confidence for w in words) / len(words)
    return words_to_readable_text(words), confidence
