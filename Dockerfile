FROM ghcr.io/astral-sh/uv:0.13.9 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --all-extras --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --all-extras

FROM python:3.12-slim
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_CLIENT_SHOW_ERROR_DETAILS=none \
    STREAMLIT_CLIENT_TOOLBAR_MODE=viewer \
    DATALYST__FILES__DIR=/data/files
RUN groupadd --system datalyst && useradd --system --gid datalyst --home /app datalyst
RUN mkdir -p /data/files && chown -R datalyst:datalyst /data
WORKDIR /app
COPY --from=build --chown=datalyst:datalyst /app/.venv /app/.venv
COPY --chown=datalyst:datalyst src ./src
COPY --chown=datalyst:datalyst ui ./ui
USER datalyst
EXPOSE 8000 8501
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import os, sys, urllib.request; port = os.getenv('HEALTH_PORT', '8000'); path = os.getenv('HEALTH_PATH', '/health'); urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=4); sys.exit(0)"
ENTRYPOINT ["datalyst"]
CMD ["api", "--host", "0.0.0.0", "--port", "8000"]
