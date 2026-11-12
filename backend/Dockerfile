# api / workers / beat share this image; the command differs per service.
FROM ghcr.io/astral-sh/uv:0.10.2 AS uv

FROM python:3.12.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_PREFERENCE=only-system \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /srv
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY app ./app
COPY prompts ./prompts
RUN uv sync --frozen --no-dev \
 && useradd --create-home --uid 10001 vault \
 && mkdir -p /data/blobs && chown vault /data/blobs
USER vault
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
