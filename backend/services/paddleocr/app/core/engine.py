"""PaddleOCR engine wrapper — a process-wide singleton per language, since
constructing PaddleOCR() loads real model weights (confirmed live: ~4s even
from a warm local model cache, unacceptable per-request latency) — must be
built once at service startup and reused, not per-request.

Result-shape notes, confirmed live against this project's own real receipt
fixtures during the migration, not assumed from documentation:
- `.predict(path_or_array)` returns a list (one entry per input image; this
  service always passes exactly one) of `OCRResult`, dict-like with
  `rec_texts` (list[str]), `rec_scores` (list[float], 0-1), `rec_boxes`
  (numpy array, shape (N,4), axis-aligned [x0,y0,x1,y1]), and `rec_polys`
  (list of (4,2) numpy arrays, four-point polygons) — this maps directly
  onto LiteParse's own documented OCR_API_SPEC.md response shape with no
  reshaping needed beyond `.tolist()`.
- `enable_mkldnn=False` is required on this project's Windows dev host —
  PP-OCRv6 + oneDNN raises `NotImplementedError:
  ConvertPirAttribute2RuntimeAttribute` on CPU inference otherwise (a
  PaddlePaddle 3.3.1 PIR-compiler/oneDNN incompatibility). See
  app.core.config.Settings.paddleocr_enable_mkldnn's own docstring for the
  Linux-container caveat.
- Explicitly requesting the PP-OCRv5 *mobile* detection+recognition models
  (rather than trusting the library's own default, which resolved to the
  much heavier PP-OCRv6 *medium* tier) is a deliberate, measured choice:
  with mkldnn disabled, the medium tier took 50-70+ seconds per image on
  this CPU (confirmed live, unacceptable for a synchronous request) versus
  the mobile tier's ~8 seconds for comparable real-world accuracy on this
  project's own test receipts (both correctly read the table headers and
  line items; only trivial digit-level differences on a handwritten date).
  This is a genuine CPU-latency/model-size tradeoff being made
  deliberately, not a default accepted without measuring it.
"""
import logging
from functools import lru_cache

from paddleocr import PaddleOCR

from app.core.config import Settings

logger = logging.getLogger(__name__)


@lru_cache
def get_engine(
    lang: str, device: str, enable_mkldnn: bool,
    det_model: str = "PP-OCRv5_server_det", rec_model: str = "PP-OCRv5_server_rec",
) -> PaddleOCR:
    """Cached per (lang, device, enable_mkldnn, model) combination — in
    practice this service is configured with one fixed combination via
    Settings, so this is effectively a single long-lived instance, built
    once on first use (the first request pays the model-load cost; every
    later request reuses it).

    The model tier comes from Settings (see paddleocr_model_tier) rather
    than being fixed here. Defaults spelled out in the signature so the
    Dockerfile's build-time model-baking step can call this directly with
    no Settings/env plumbing and still bake the tier actually shipped.
    """
    logger.info(
        "Constructing PaddleOCR engine (lang=%s, device=%s, enable_mkldnn=%s, det=%s, rec=%s)",
        lang, device, enable_mkldnn, det_model, rec_model,
    )
    return PaddleOCR(
        lang=lang,
        device=device,
        enable_mkldnn=enable_mkldnn,
        text_detection_model_name=det_model,
        text_recognition_model_name=rec_model,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )


def get_configured_engine(settings: Settings, lang: str) -> PaddleOCR:
    return get_engine(
        lang, settings.paddleocr_device, settings.paddleocr_enable_mkldnn,
        settings.detection_model_name, settings.recognition_model_name,
    )
