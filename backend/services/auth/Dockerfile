FROM python:3.12-slim

WORKDIR /app

# The shared library is a path dependency, so it must be copied in before the
# service's own install can resolve it.
COPY libs/shared /libs/shared
COPY services/auth .
RUN pip install --no-cache-dir /libs/shared && pip install --no-cache-dir .

EXPOSE 8001
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8001"]
