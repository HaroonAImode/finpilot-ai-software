FROM python:3.12-slim

WORKDIR /app

# opencv-python (a paddleocr/paddlex transitive dependency) needs libgl1/
# libglib2.0-0 on a slim base image, and paddlepaddle's compiled core
# (libpaddle) needs libgomp1 (GNU OpenMP runtime) — confirmed via a real
# Docker build failure during the migration: `ImportError: libgomp.so.1:
# cannot open shared object file`, not present on python:3.12-slim by
# default. Without these the import fails at container startup (or model-
# baking build time, see below), a much worse place to discover it than
# right here.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY services/paddleocr .
RUN pip install --no-cache-dir .

# Bakes the PP-OCRv5 *mobile* detection+recognition models (see
# app/core/engine.py's own docstring for why mobile, not the library's own
# medium-tier default — a measured CPU-latency tradeoff, confirmed live)
# into the image at build time. Doing this at container startup (or worse,
# on the first real request) would mean every fresh `docker compose up`
# silently stalls or fails without network access to the model hoster.
# This is the actual mechanism satisfying the "clone and run, no hidden
# manual steps" requirement — models ship with the image, nothing is
# downloaded at runtime.
ENV PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

# Caps PaddlePaddle's OpenMP thread pool — confirmed live this is required,
# not precautionary: without it, CPU usage on a real run spiked to
# 2000%+ (thread oversubscription — each concurrent/overlapping inference
# call spawns its own full-width thread pool, and they thrash fighting
# each other for the same physical cores instead of actually finishing
# faster). A modest fixed cap keeps one inference call's CPU footprint
# bounded and predictable regardless of how many requests overlap.
ENV OMP_NUM_THREADS=4

# Bakes whichever tier app.core.config declares (get_engine's own defaults
# mirror it), so switching PADDLEOCR_MODEL_TIER is a config change plus a
# rebuild rather than a code edit in two places.
RUN python -c "\
from app.core.config import get_settings; \
from app.core.engine import get_engine; \
s = get_settings(); \
get_engine('en', 'cpu', False, s.detection_model_name, s.recognition_model_name)"

EXPOSE 8008
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8008"]
