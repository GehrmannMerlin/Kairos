# Kairos backend image (used by both `api` and `worker` compose services).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# PyPI index is configurable at build time (no scattered hardcoded mirror).
# Override with: --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=https://pypi.org/simple
# Whether this image includes browser-worker deps (playwright ~48MB). The API
# image does not need it; the worker (browser role) does. Default: 0.
ARG KAIROS_INCLUDE_BROWSER=0

WORKDIR /app

# ---- Layer 1: dependency manifest -> install deps (cache boundary) ----
# setuptools package discovery (pyproject `include = ["app*"]`) needs the `app`
# package to exist, so we install a stub package here. This layer only rebuilds
# when pyproject.toml / alembic.ini changes — NOT when application source changes
# (OCI registry layer reuse / incremental pull depends on this boundary).
COPY pyproject.toml alembic.ini ./
RUN mkdir -p app && touch app/__init__.py && \
    if [ "$KAIROS_INCLUDE_BROWSER" = "1" ]; then \
        pip install --no-cache-dir --index-url "$PIP_INDEX_URL" --timeout 120 --retries 5 '.[browser]'; \
    else \
        pip install --no-cache-dir --index-url "$PIP_INDEX_URL" --timeout 120 --retries 5 .; \
    fi

# ---- Layer 2: application source (rebuilds on any app change) ----
# Deps are already satisfied from Layer 1, so this re-install is fast (no
# downloads) and the real `app` package is copied into site-packages.
COPY app ./app
COPY alembic ./alembic
COPY scripts ./scripts
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" --timeout 120 --retries 5 .

EXPOSE 8000

# Default: run the API. The `worker` compose service overrides the command.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
