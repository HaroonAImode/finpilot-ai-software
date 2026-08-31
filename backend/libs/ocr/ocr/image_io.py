"""Shared image-decode helper — every site in this package that opens raw
image bytes (extract.py's OCR path, render.py's preview rasterization,
preprocess.py's CV crop/split) goes through this one function, so the fix
below applies everywhere at once instead of needing to be repeated (and
re-forgotten) per call site.

Real bug this exists to fix, confirmed live: none of the three sites called
PIL's `ImageOps.exif_transpose`, so a phone photo taken in portrait with the
phone rotated (the EXIF orientation tag records the rotation; the actual
pixel data stays landscape) was decoded, OCR'd, and CV-cropped in its literal
sideways pixel orientation everywhere in this pipeline — Tesseract/PaddleOCR
reading a sideways receipt, and the contour detector in preprocess.py scoring
boundaries against a sideways frame. `exif_transpose` bakes the recorded
rotation into the actual pixel data once, right here, so every downstream
consumer (OCR, CV contours, the bounding-box preview render) agrees on the
same, correctly-oriented image — critical for render.py specifically, since
its output is what the review UI overlays extracted-field bounding boxes
onto; if only the extraction path corrected orientation, the boxes would be
computed in one orientation while the preview showed another.
"""
import io

from PIL import Image, ImageOps


def decode_image(content: bytes) -> Image.Image:
    """Opens image bytes and applies any EXIF-recorded rotation/flip to the
    actual pixel data, returning an image already in its intended-viewing
    orientation. A no-op for an image with no EXIF orientation tag (most
    PNGs, screenshots, and already-correctly-oriented photos) — this is
    always safe to call unconditionally."""
    image = Image.open(io.BytesIO(content))
    return ImageOps.exif_transpose(image)
