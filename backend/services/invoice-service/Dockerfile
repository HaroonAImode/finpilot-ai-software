FROM python:3.12-slim

WORKDIR /app

# libglib2.0-0: invoice_extraction now imports the finpilot-ocr package
# (see libs/ocr/pyproject.toml's own dependency comment), whose __init__.py
# unconditionally imports preprocess.py's cv2 (opencv-python-headless still
# dynamically links against glib even in its headless build) — missing this
# surfaces as an ImportError only inside the container. Same gap, same fix,
# as ai-engine's own Dockerfile already documents.
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# The shared library (JWT verification) lives outside this service, so the build
# context is backend/ and it is installed before the service's own dependencies.
# libs/ocr installed first because invoice_extraction requires it at import
# time but deliberately doesn't declare it as a pip dependency (see
# libs/invoice_extraction/pyproject.toml's own comment) — same install order
# ai-engine's Dockerfile already uses for the same two libraries.
# P1-E: invoice_extraction added the same way ai-engine's own Dockerfile
# already installs it — category_classifier.py reuses its generic
# Candidate/select_candidate primitives (see pyproject.toml's own comment).
COPY libs/shared /libs/shared
COPY libs/ocr /libs/ocr
COPY libs/invoice_extraction /libs/invoice_extraction
COPY services/invoice-service .
RUN pip install --no-cache-dir /libs/shared \
    && pip install --no-cache-dir /libs/ocr \
    && pip install --no-cache-dir /libs/invoice_extraction \
    && pip install --no-cache-dir .

EXPOSE 8002
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8002"]
