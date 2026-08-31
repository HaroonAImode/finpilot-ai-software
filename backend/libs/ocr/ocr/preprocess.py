"""Document Preprocessing — a new stage ahead of LiteParse/OCR
(docs/invoice-ocr-plan.md §7), for image uploads only (a PDF page is
already a clean, single, born-digital page — the problem this solves is
specific to a phone-camera photo: excess background/desk visible around
the receipt, or more than one receipt caught in one shot).

Two things happen here, both computer-vision-based (OpenCV), no ML model:

1. **Smart crop.** Detect the document's actual boundary within the frame
   and crop to it plus a small safety margin, instead of feeding LiteParse
   a photo that is mostly background/desk/hand.
2. **Multi-document split.** If the frame confidently contains two or more
   separate documents, crop each one out as its own image, so each is
   independently sent to LiteParse/OCR rather than being read as one
   run-on document.

**The one rule every threshold below exists to enforce**: when detection is
not confident, do nothing — hand the original, whole, untouched image back
rather than guess. A missed crop costs nothing (LiteParse still reads the
whole receipt, just with some background around it, exactly like today
before this stage existed). A wrong crop can permanently cut off a total,
a signature, or a stamp. This module is built so that mistake is the one
it is structurally biased against, even at the cost of leaving obviously-
croppable background in some of the time.

Confidence is a single, legible 0.0-1.0 heuristic per candidate — how large
it is relative to the frame, and how rectangular its actual contour is
compared to its own bounding box — never a learned/black-box score, so a
person debugging a bad crop can read _score_candidates and see exactly why
a candidate was or was not trusted.
"""
import logging
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from ocr.image_io import decode_image

logger = logging.getLogger(__name__)

#: A candidate below this share of the frame is not confidently "a
#: document" — likely a stray edge, a shadow, or a piece of clutter.
_MIN_DOCUMENT_AREA_RATIO = 0.08
#: A single candidate this close to the *entire* frame already has
#: essentially no background to crop away — cropping to 97%+ of the original
#: risks nibbling a real edge for no actual benefit.
_MAX_SINGLE_DOCUMENT_AREA_RATIO = 0.97
#: contour_area / its own axis-aligned bounding-box area — how "boxy" the
#: candidate is. 1.0 is a perfect axis-aligned rectangle; a real document
#: photographed at a moderate angle still lands well above this, while a
#: hand, a shadow, or a torn/irregular edge does not.
_MIN_RECTANGULARITY = 0.70
#: The blended size+shape confidence a candidate must clear before this
#: module acts on it at all (crop *or* split). Below this: keep the
#: original, whole image — the module's one hard rule, enforced here.
_MIN_CONFIDENCE_TO_ACT = 0.55
#: Two candidates overlapping more than this are almost certainly the same
#: physical document detected twice (its outer edge and an inner shadow
#: line, for example), not two separate ones.
_MAX_MULTI_DOC_OVERLAP_RATIO = 0.15
#: Added on every side of a crop, sized relative to the image so it scales
#: with resolution — 2% of the shorter side. Cheap insurance against a
#: boundary estimated a few pixels too tight actually clipping real content.
_SAFETY_PADDING_RATIO = 0.02

# --- Content-valley multi-document split (see _find_content_valley) -------
#: A candidate document region is only ever probed for a hidden valley —
#: never split on brightness/color alone — when it's already large enough
#: to plausibly *be* two merged documents rather than one. Set well above
#: _MIN_DOCUMENT_AREA_RATIO: a normal single document legitimately covers
#: this much of the frame too, so this alone never triggers a probe, it
#: just skips the cost on frames too small to be two merged documents.
_MIN_AREA_RATIO_TO_PROBE_FOR_VALLEY = 0.35
#: The valley's own edge density must fall below this fraction of the
#: *smaller* of its two flanking content peaks — a real gap between two
#: printed documents reads as close to empty; a document's own sparser
#: margin or whitespace never drops this low relative to its own content.
_MAX_VALLEY_DENSITY_RATIO = 0.15
#: A valley narrower than this share of the region's own width is more
#: likely a gutter inside one document's own layout than real separating
#: background between two physically separate documents. Calibrated
#: against real, live-found false positives, not guessed — this threshold
#: went through three rounds of tightening against real documents that
#: kept finding new failure shapes: a genuine inter-document gap measured
#: 31% of the region's width on a real clean side-by-side photo, while a
#: real single, correctly-one-document A4 invoice using a common two-
#: column template (a left "Payable To"/item column, a right totals
#: column) produced its own column-gutter valley at 18.4% width — which
#: this check must reject, or it fragments that one document's own item
#: description away from its own Grand Total. 0.25 sits with real margin
#: on both sides of that measured gap. See _split_by_content_valley's own
#: docstring for why this module also tried and abandoned a row-based
#: (stacked-document) version of this same check entirely — a much more
#: common single-document false-positive shape that no threshold on this
#: axis alone could safely separate from a real stacked-document gap.
_MIN_VALLEY_WIDTH_RATIO = 0.25
#: A valley within this share of either edge is trimming background at the
#: edge of one document, not separating two — smart-crop's job, not a
#: split.
_MIN_VALLEY_MARGIN_RATIO = 0.12
#: A resulting half must show real content (density above
#: peak * this ratio) across at least _MIN_HALF_CONTENT_COVERAGE of its own
#: length — not merely have a single tall peak somewhere within it.
#: Calibrated against a real false positive: a single-page voucher with one
#: dense content band and a large blank margin below it produced a "peak"
#: on both sides of a wide, real-looking valley, and the resulting second
#: "document" was 93% blank (just a footer line) — real content covered
#: only ~7% of that half's own length, while a real second document's own
#: half measured ~20% coverage on a genuine multi-receipt photo. These two
#: ratios sit with margin on either side of that measured gap.
_MIN_HALF_CONTENT_FLOOR_RATIO = 0.3
_MIN_HALF_CONTENT_COVERAGE = 0.12

BBox = tuple[int, int, int, int]


@dataclass
class DetectedDocument:
    """One document this stage decided LiteParse/OCR should see as its own,
    independent input. `image_bytes` is always a valid, decodable image
    (PNG when actually cropped; the caller's original bytes, untouched,
    when nothing confident was found) — never a partially-processed or
    placeholder value.
    """

    image_bytes: bytes
    #: (x0, y0, x1, y1) in the *original* image's pixel space — (0, 0,
    #: width, height) when this is the untouched original, so a caller can
    #: always tell "was this actually cropped" from whether the bbox is
    #: smaller than the source image.
    bbox: BBox
    #: 0.0 means exactly one thing, always: `image_bytes` is the caller's
    #: original bytes, byte-for-byte, untouched — whether that is because
    #: nothing plausible was found at all, or because the one candidate
    #: found already covers virtually the whole frame. Never a fabricated
    #: non-zero number for a fallback; a caller can check `confidence > 0`
    #: as the one true "was this actually cropped" test.
    confidence: float


def _decode(content: bytes) -> np.ndarray:
    image = decode_image(content).convert("RGB")
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def _encode_png(bgr: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        raise ValueError("Could not encode a cropped region back to an image")
    return buf.tobytes()


def _candidate_contours(bgr: np.ndarray) -> list[np.ndarray]:
    """Otsu's threshold picks its own cutoff per image rather than a fixed
    brightness value, so this holds up across very different lighting a
    phone photo could have (a dim room, an overexposed flash, ...). Combined
    with plain Canny edges — Otsu alone can miss a document whose color is
    close to its background; Canny alone can miss a boundary that is a soft
    shadow rather than a hard edge. Morphological closing then bridges the
    small gaps either method leaves, so a document's outline comes back as
    one closed contour instead of several fragments.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    edges = cv2.Canny(blurred, 50, 150)
    combined = cv2.bitwise_or(thresh, edges)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    closed = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return contours


def _score_candidates(contours: list[np.ndarray], image_area: int) -> list[tuple[BBox, float]]:
    """Every contour big and boxy enough to plausibly be a real, separate
    document, each with a legible confidence — never a forced top-N; a
    frame with nothing that qualifies simply returns an empty list, which
    is exactly the signal the caller needs to fall back to the original.
    """
    scored: list[tuple[BBox, float]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        rect_area = w * h
        if rect_area == 0:
            continue
        area_ratio = rect_area / image_area
        if area_ratio < _MIN_DOCUMENT_AREA_RATIO:
            continue
        rectangularity = cv2.contourArea(contour) / rect_area
        if rectangularity < _MIN_RECTANGULARITY:
            continue
        # Rewards being boxy; only rewards *more* size up to the point a
        # candidate already clearly reads as "a whole document" — a
        # candidate twice the minimum area is not twice as trustworthy.
        size_score = min(1.0, area_ratio / _MIN_DOCUMENT_AREA_RATIO)
        confidence = rectangularity * size_score
        scored.append(((x, y, x + w, y + h), confidence))
    return scored


def _overlap_ratio(a: BBox, b: BBox) -> float:
    """Intersection area as a share of the *smaller* box — deliberately not
    IoU: two boxes where one is fully nested in the other (an outer edge
    contour and an inner shadow-line contour of the same real document)
    should read as "the same thing twice" even though their union makes IoU
    look modest."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    intersection = (ix1 - ix0) * (iy1 - iy0)
    smaller = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
    return intersection / smaller if smaller else 0.0


def _dedupe(candidates: list[tuple[BBox, float]]) -> list[tuple[BBox, float]]:
    """Keeps the highest-confidence candidate out of any group that
    substantially overlaps — see _overlap_ratio's own docstring."""
    candidates = sorted(candidates, key=lambda c: c[1], reverse=True)
    kept: list[tuple[BBox, float]] = []
    for bbox, confidence in candidates:
        if any(_overlap_ratio(bbox, kept_bbox) > _MAX_MULTI_DOC_OVERLAP_RATIO for kept_bbox, _ in kept):
            continue
        kept.append((bbox, confidence))
    return kept


def _smooth(density: np.ndarray) -> np.ndarray:
    """A moving average sized relative to the profile's own length, so a
    single sparse column/row of noise doesn't fragment a real valley (or a
    real content peak) into several short ones."""
    n = len(density)
    if n == 0:
        return density
    kernel_size = max(3, n // 60)
    return np.convolve(density, np.ones(kernel_size) / kernel_size, mode="same")


def _content_valley(smoothed: np.ndarray) -> Optional[tuple[int, int]]:
    """Given an already-smoothed 1D edge-density profile (column sums or
    row sums across one candidate region), looks for a single contiguous
    run that is close to empty relative to real content on *both* sides of
    it — the signature a content-free gap between two separate documents
    leaves, found live to be detectable even when a color/brightness
    threshold cannot separate them (docs/invoice-ocr-plan.md's own
    real-photo finding: a pale wood desk is not reliably darker than white
    receipt paper, but it is reliably far less edge-dense than a printed
    document is).

    Returns (start, end) in the profile's own index space, or None if
    nothing in the profile looks like a genuine inter-document gap rather
    than normal in-document sparsity.
    """
    n = len(smoothed)
    if n == 0:
        return None

    margin = int(n * _MIN_VALLEY_MARGIN_RATIO)
    min_width = max(1, int(n * _MIN_VALLEY_WIDTH_RATIO))
    if n - 2 * margin < min_width:
        return None

    # One threshold, one reference scale (the profile's own global peak) —
    # used both to find a candidate "empty" run and to confirm real content
    # flanks it. Deliberately not two separately-derived thresholds (a
    # global one to find the run, a local flank-relative one to validate
    # it): that compounding made the check stricter than either threshold
    # alone, and rejected a real, clearly-visible valley live on a real
    # photo — the run's own near-edge values sat just above the *smaller*
    # flank's locally-rescaled cutoff even though both flanks were
    # unambiguously real content by the same global measure.
    peak = max(smoothed.max(), 1.0)
    threshold = peak * _MAX_VALLEY_DENSITY_RATIO

    best: Optional[tuple[int, int]] = None
    i = margin
    while i < n - margin:
        if smoothed[i] > threshold:
            i += 1
            continue
        j = i
        while j < n - margin and smoothed[j] <= threshold:
            j += 1
        width = j - i
        if width >= min_width:
            left_peak = smoothed[:i].max() if i > 0 else 0.0
            right_peak = smoothed[j:].max() if j < n else 0.0
            # Both sides must be unambiguously real content — comfortably
            # above the gap threshold, not merely on the right side of it.
            if left_peak > threshold * 2 and right_peak > threshold * 2:
                if best is None or width > (best[1] - best[0]):
                    best = (i, j)
        i = j

    return best


def _split_by_content_valley(
    bgr: np.ndarray, bbox: BBox, confidence: float, image_area: int,
) -> Optional[list[tuple[BBox, float]]]:
    """Attempts to refine one already-confident candidate region into two,
    by looking for a content-free vertical gap running through it — see
    _content_valley's own docstring for the real-photo failure mode this
    recovers (two receipts merged into one contour because the desk
    between them isn't reliably darker than the paper).

    Deliberately column-only (side-by-side documents), never row-based
    (stacked documents), and deliberately not by choice of a cleaner
    signal but by hard-won evidence: a real single tall receipt's own
    layout (header block, then a gap, then its item table, then another
    gap, then its totals) routinely produces a wide, low-density *row*
    valley that looks exactly like an inter-document gap by every measure
    tried — found live, this fragmented more than one single real
    receipt's own vendor name away from its own Grand Total on a real
    35-receipt dataset. A genuine full-height blank *column* running the
    entire vertical extent of a single document is, by contrast, not a
    shape a normal one-column receipt layout produces at all (text and
    tables fill the width; they do not leave a floor-to-ceiling vertical
    gap through their own content) — column valleys were never observed
    to false-positive across either real test set, so that signal alone
    stays, and the row-based path this module also implemented and
    disproved live is not carried forward.

    Deliberately only ever a *refinement* of a region this module was
    already going to treat as one confident, real document — never a new,
    independent detection path of its own. If no valid vertical valley is
    found (the overwhelming common case: one real document, two documents
    stacked instead of side-by-side, or two documents that overlap/touch
    with no actual gap — found live, none of these are recovered, only a
    genuinely side-by-side separation is), returns None and the caller
    keeps its original, single-document behaviour unchanged.
    """
    x0, y0, x1, y1 = bbox
    region_bgr = bgr[y0:y1, x0:x1]
    gray = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150)

    density = _smooth(edges.sum(axis=0) / 255.0)
    valley = _content_valley(density)
    if valley is None:
        return None
    start, end = valley
    split_at = (start + end) // 2

    first_bbox: BBox = (x0, y0, x0 + split_at, y1)
    second_bbox: BBox = (x0 + split_at, y0, x1, y1)

    # Both halves must independently still look like a real, sizeable
    # document — otherwise this is more likely one document with an
    # unusually sparse band (a wide margin, a mostly-blank section) than
    # two real documents, and the original single-region result is safer.
    for half in (first_bbox, second_bbox):
        hw, hh = half[2] - half[0], half[3] - half[1]
        if (hw * hh) / image_area < _MIN_DOCUMENT_AREA_RATIO:
            return None

    # A peak *somewhere* in each flank (checked above, inside
    # _content_valley) isn't enough on its own — found live on a real
    # single-page voucher with generous whitespace: its one dense content
    # band plus a large blank margin below it still produced a "peak" on
    # both sides of a wide valley, and the resulting second "document" was
    # almost entirely blank (just a footer line — no financial content lost
    # here, but still a spurious duplicate document, and not something to
    # rely on always being this harmless). Each half must show real content
    # across a *meaningful share of its own extent*, not just a peak
    # anywhere within it.
    floor = max(density.max(), 1.0) * _MIN_HALF_CONTENT_FLOOR_RATIO
    if density[0:split_at].mean() <= 0 or (density[0:split_at] > floor).mean() < _MIN_HALF_CONTENT_COVERAGE:
        return None
    if density[split_at:].mean() <= 0 or (density[split_at:] > floor).mean() < _MIN_HALF_CONTENT_COVERAGE:
        return None

    return [(first_bbox, confidence), (second_bbox, confidence)]


def _pad(bbox: BBox, width: int, height: int) -> BBox:
    x0, y0, x1, y1 = bbox
    pad = int(round(min(width, height) * _SAFETY_PADDING_RATIO))
    return (max(0, x0 - pad), max(0, y0 - pad), min(width, x1 + pad), min(height, y1 + pad))


def _crop(bgr: np.ndarray, bbox: BBox) -> bytes:
    x0, y0, x1, y1 = bbox
    return _encode_png(bgr[y0:y1, x0:x1])


def preprocess_image(content: bytes) -> list[DetectedDocument]:
    """The one entry point. Always returns at least one `DetectedDocument`
    — length 1 for "nothing to split" (the overwhelming common case,
    whether or not a crop happened), length 2+ only when multiple documents
    were confidently detected. Raises `ValueError` only for bytes that do
    not decode as an image at all — the same failure shape `PIL.Image.open`
    already has, not a new error type callers need to learn.
    """
    try:
        bgr = _decode(content)
    except Exception as exc:
        raise ValueError(f"Could not decode image for preprocessing: {exc}") from exc

    height, width = bgr.shape[:2]
    image_area = width * height
    whole_image = DetectedDocument(image_bytes=content, bbox=(0, 0, width, height), confidence=0.0)
    if image_area == 0:
        return [whole_image]

    contours = _candidate_contours(bgr)
    candidates = _dedupe(_score_candidates(contours, image_area))
    confident = [(bbox, conf) for bbox, conf in candidates if conf >= _MIN_CONFIDENCE_TO_ACT]

    if len(confident) >= 2:
        # Reading order: top-to-bottom, then left-to-right within a row —
        # so "doc_1"/"doc_2" naming matches how a person would describe
        # "the top one" and "the bottom one".
        confident.sort(key=lambda c: (c[0][1], c[0][0]))
        results = [
            DetectedDocument(image_bytes=_crop(bgr, padded := _pad(bbox, width, height)), bbox=padded, confidence=conf)
            for bbox, conf in confident
        ]
        logger.info("Document preprocessing: detected %d separate documents in one image", len(results))
        return results

    if len(confident) == 1:
        bbox, confidence = confident[0]
        area_ratio = ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])) / image_area
        if area_ratio >= _MIN_AREA_RATIO_TO_PROBE_FOR_VALLEY:
            split = _split_by_content_valley(bgr, bbox, confidence, image_area)
            if split is not None:
                split.sort(key=lambda c: (c[0][1], c[0][0]))
                results = [
                    DetectedDocument(
                        image_bytes=_crop(bgr, padded := _pad(split_bbox, width, height)),
                        bbox=padded, confidence=conf,
                    )
                    for split_bbox, conf in split
                ]
                logger.info(
                    "Document preprocessing: a content-free gap split one candidate region into %d documents",
                    len(results),
                )
                return results
        if area_ratio >= _MAX_SINGLE_DOCUMENT_AREA_RATIO:
            # Already fills the frame — nothing meaningful to crop away.
            # confidence=0.0 here too, deliberately: this field's contract
            # is "0.0 means image_bytes is the untouched original," full
            # stop, regardless of *why* nothing was cropped. A candidate
            # that happens to cover nearly the whole frame is exactly the
            # shape a spurious full-image "detection" on a textureless or
            # noisy photo takes — confirmed live: uniform random noise
            # reliably produces one large blob here, not zero candidates.
            return [DetectedDocument(image_bytes=content, bbox=(0, 0, width, height), confidence=0.0)]
        padded = _pad(bbox, width, height)
        return [DetectedDocument(image_bytes=_crop(bgr, padded), bbox=padded, confidence=confidence)]

    # No candidate cleared the confidence bar at all — the module's one hard
    # rule: pass the original through untouched rather than guess.
    logger.info("Document preprocessing: no confident boundary found — passing the original image through unchanged")
    return [whole_image]
