# Kairos backend image (used by both `api` and `worker` compose services).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# PyPI index is configurable at build time (no scattered hardcoded mirror).
# Override with: --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=https://pypi.org/simple
# Chromium download host. Defaults to the npmmirror Playwright CDN mirror
# (reliable from CN region where storage.googleapis.com TLS is flaky). Override
# with --build-arg PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.playwright.dev to use the
# official CDN. Playwright's `install` appends `/builds/<browser>/<rev>/...`.
ARG PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright
# Whether this image includes browser-worker deps (playwright ~48MB + chromium).
# The API image does not need it; the worker (browser role) does. Default: 0.
# When enabled, chromium is installed via the pinned Playwright driver itself
# (`playwright install chromium`), so the binary revision always matches the
# installed playwright package — never a hardcoded executable path.
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
        pip install --no-cache-dir --index-url "$PIP_INDEX_URL" --timeout 120 --retries 5 '.[browser]' \
        && PLAYWRIGHT_DOWNLOAD_HOST="$PLAYWRIGHT_DOWNLOAD_HOST" python -m playwright install --no-progress chromium \
        && sed -i 's|deb.debian.org/debian|mirrors.tuna.tsinghua.edu.cn/debian|g; s|security.debian.org/debian-security|mirrors.tuna.tsinghua.edu.cn/debian-security|g' /etc/apt/sources.list.d/debian.sources \
        && apt-get update -y \
        && apt-get install -y --no-install-recommends --fix-missing \
            libnss3 libnspr4 libdbus-1-3 libatk1.0-0t64 libatk-bridge2.0-0t64 \
            libcups2t64 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
            libxfixes3 libxrandr2 libgbm1 libasound2t64 libpango-1.0-0 \
            libcairo2 libatspi2.0-0t64 libx11-6 libxcb1 libxext6 \
        && apt-get clean \
        && rm -rf /var/lib/apt/lists/*; \
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

# OCI annotation: connects the pushed GHCR package to the source repository so
# GITHUB_TOKEN-based pushes can keep writing the same private package (GHCR
# bootstrap pattern). Placed last so it never invalidates the dependency layers.
# Metadata only — no runtime effect.
LABEL org.opencontainers.image.source=https://github.com/GehrmannMerlin/Kairos

EXPOSE 8000

# Default: run the API. The `worker` compose service overrides the command.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
