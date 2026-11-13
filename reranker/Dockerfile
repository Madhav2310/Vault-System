FROM ghcr.io/astral-sh/uv:0.10.2 AS uv

FROM python:3.12.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_PREFERENCE=only-system UV_PYTHON_DOWNLOADS=never PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /srv
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
COPY app.py models.py ./
# Bake model weights into the image so the service never needs network at runtime.
ARG PREFETCH_MODELS="Xenova/ms-marco-MiniLM-L-6-v2"
ENV FASTEMBED_CACHE_PATH=/opt/models
RUN python -c "import os; from fastembed.rerank.cross_encoder import TextCrossEncoder as T; [T(m, cache_dir='/opt/models') for m in os.environ.get('PREFETCH_MODELS', '${PREFETCH_MODELS}').split(',')]" \
    && useradd --create-home --uid 10001 vault && chown -R vault /opt/models
USER vault
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
