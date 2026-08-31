FROM python:3.12-slim

WORKDIR /app

# tesseract-ocr is a system binary pytesseract shells out to — pip installing
# the finpilot-ocr package alone does NOT provide it. Documented explicitly
# in docs/invoice-ocr-plan.md §4 as the operational detail most likely to be
# missed; this is that requirement actually being satisfied.
#
# libglib2.0-0: opencv-python-headless (Document Preprocessing, §7) still
# dynamically links against glib even in its headless build — a well-known
# gap on python:*-slim images (no GUI libs at all), not something pip
# install alone provides. Missing this surfaces as an ImportError on `import
# cv2` only inside the container, never locally where a fuller glib is
# already present — exactly the kind of "works on my machine" gap this
# project has already been bitten by once (python-multipart), addressed
# proactively here instead of waiting to hit it again.
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# The OCR and invoice-extraction libraries live outside this service
# (backend/libs/), so the build context is backend/ and both are installed
# before the service's own dependencies — same convention as
# email-connector's Dockerfile installing backend/libs/shared.
COPY libs/ocr /libs/ocr
COPY libs/invoice_extraction /libs/invoice_extraction
COPY services/ai-engine .
RUN pip install --no-cache-dir /libs/ocr \
    && pip install --no-cache-dir /libs/invoice_extraction \
    && pip install --no-cache-dir .

EXPOSE 8007
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8007"]
