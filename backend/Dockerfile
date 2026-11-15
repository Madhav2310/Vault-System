# api / workers / beat share this image; the command differs per service.
FROM ghcr.io/astral-sh/uv:0.10.2 AS uv

# Web UI build stage. The build context is backend/, so the frontend comes in as a named context:
#   docker build --build-context frontend=../frontend backend   (compose: additional_contexts)
# Without it the stage builds an empty UI and /app answers 404.
FROM scratch AS frontend
FROM node:22.12-slim AS ui
WORKDIR /ui
COPY --from=frontend . .
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund && npm run build; else mkdir dist; fi

FROM python:3.12.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=60 \
    UV_CONCURRENT_DOWNLOADS=4 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_PREFERENCE=only-system \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken
WORKDIR /srv
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project \
 && python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"  # bake the tokenizer file into the image
COPY alembic.ini ./
COPY app ./app
COPY prompts ./prompts
COPY config ./config
COPY --from=ui /ui/dist ./ui_dist
RUN uv sync --frozen --no-dev \
 && useradd --create-home --uid 10001 vault \
 && mkdir -p /data/blobs && chown vault /data/blobs
USER vault
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
