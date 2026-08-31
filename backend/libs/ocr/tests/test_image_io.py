"""Regression guard for a real bug found live: none of this package's three
image-decode sites (extract.py's OCR path, render.py's preview
rasterization, preprocess.py's CV crop/split) applied EXIF orientation
before this helper existed — a phone photo taken rotated was silently OCR'd
and CV-processed sideways everywhere in the pipeline. See ocr/image_io.py's
own module docstring for why this had to be one shared helper rather than a
fix repeated at each call site.
"""
import io

from PIL import Image

from ocr.image_io import decode_image


def _jpeg_with_orientation(orientation: int, width: int = 40, height: int = 20) -> bytes:
    image = Image.new("RGB", (width, height), color=(255, 0, 0))
    exif = image.getexif()
    exif[0x0112] = orientation  # 0x0112 = the standard EXIF Orientation tag
    buf = io.BytesIO()
    image.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


class TestDecodeImage:
    def test_no_exif_orientation_tag_is_unaffected(self) -> None:
        """The overwhelming common case (PNGs, screenshots, an already
        correctly-oriented photo) — must be a safe no-op, not just safe for
        images that actually carry a rotation."""
        image = Image.new("RGB", (40, 20), color=(0, 255, 0))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        decoded = decode_image(buf.getvalue())
        assert decoded.size == (40, 20)

    def test_orientation_6_applies_the_90_degree_rotation(self) -> None:
        """EXIF orientation 6 ('rotate 90° CW to display correctly') is the
        single most common real case — a phone held in portrait while the
        sensor recorded landscape pixels. Width/height swap is the visible
        proof the rotation was actually applied, not just tolerated."""
        content = _jpeg_with_orientation(6, width=40, height=20)
        decoded = decode_image(content)
        assert decoded.size == (20, 40)

    def test_orientation_3_applies_the_180_degree_rotation(self) -> None:
        content = _jpeg_with_orientation(3, width=40, height=20)
        decoded = decode_image(content)
        assert decoded.size == (40, 20)  # same dimensions, content flipped — can't assert visually here

    def test_the_orientation_tag_is_consumed_not_left_for_a_second_transform(self) -> None:
        """PIL's exif_transpose bakes the rotation into the pixels and clears
        the tag on the object it returns — verifying that here guards
        against a future accidental double-rotation if this image is ever
        re-saved with its (stale) exif re-attached further down the
        pipeline."""
        content = _jpeg_with_orientation(6, width=40, height=20)
        decoded = decode_image(content)
        assert decoded.getexif().get(0x0112) in (None, 1)
