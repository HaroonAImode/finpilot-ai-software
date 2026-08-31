FROM python:3.12-slim

WORKDIR /app

# The shared library (JWT verification) lives outside this service, so the build
# context is backend/ and it is installed before the service's own dependencies.
COPY libs/shared /libs/shared
COPY services/email-connector .
RUN pip install --no-cache-dir /libs/shared && pip install --no-cache-dir .

EXPOSE 8011
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8011"]
