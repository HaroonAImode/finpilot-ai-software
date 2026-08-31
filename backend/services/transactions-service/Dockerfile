FROM python:3.12-slim

WORKDIR /app

# The shared library (JWT verification, internal service auth) lives outside
# this service, so the build context is backend/ and it is installed first.
COPY libs/shared /libs/shared
COPY services/transactions-service .
RUN pip install --no-cache-dir /libs/shared && pip install --no-cache-dir .

EXPOSE 8003
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8003"]
