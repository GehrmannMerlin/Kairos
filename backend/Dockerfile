# Kairos backend image (used by both `api` and `worker` compose services).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY app ./app
COPY alembic ./alembic
COPY scripts ./scripts

RUN pip install --no-cache-dir . \
    && playwright install --with-deps chromium

EXPOSE 8000

# Default: run the API. The `worker` compose service overrides the command.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
