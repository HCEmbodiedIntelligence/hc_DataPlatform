ARG PYTHON_IMAGE=python:3.12.8-slim@sha256:2199a62885a12290dc9c5be3ca0681d367576ab7bf037da120e564723292a2f0
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.3@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS dependencies

ENV PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./

FROM dependencies AS project
COPY src ./src

FROM project AS api-builder
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable --extra database --extra storage --extra data

FROM project AS worker-builder
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable \
    --extra database \
    --extra workflow \
    --extra storage \
    --extra data
RUN /opt/venv/bin/python -c "import lance, mcap, pyarrow"

FROM dependencies AS api-dev-builder
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-install-project \
    --extra dev \
    --extra database \
    --extra storage \
    --extra data

FROM dependencies AS worker-dev-builder
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-install-project \
    --extra dev \
    --extra database \
    --extra workflow \
    --extra storage \
    --extra data
RUN /opt/venv/bin/python -c "import lance, mcap, pyarrow, watchfiles"

FROM ${PYTHON_IMAGE} AS runtime-base

ARG APP_VERSION=dev
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
ARG SOURCE_URL=https://example.invalid/hc-data-platform

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HC_MIGRATIONS_DIR=/app/migrations

WORKDIR /app
LABEL org.opencontainers.image.title="HC Data Platform Backend" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="${SOURCE_URL}"

USER 65532:65532

FROM runtime-base AS runtime-media
USER 0:0
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN ffmpeg -hide_banner -encoders 2>&1 | grep --quiet libx264 \
    && ffmpeg -hide_banner -muxers 2>&1 | grep --quiet hls \
    && ffprobe -version >/dev/null
USER 65532:65532

FROM runtime-media AS worker
COPY --from=worker-builder /opt/venv /opt/venv
COPY migrations /app/migrations
LABEL org.opencontainers.image.component="worker"
HEALTHCHECK --interval=10s --timeout=7s --start-period=30s --retries=6 \
    CMD ["python", "-m", "hc_data_platform.workflow.healthcheck", "--timeout", "5"]
CMD ["hc-data-worker"]

FROM runtime-base AS api
COPY --from=api-builder /opt/venv /opt/venv
COPY migrations /app/migrations
LABEL org.opencontainers.image.component="api"
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]
CMD ["uvicorn", "hc_data_platform.core.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime-base AS api-dev
ENV PYTHONPATH=/app/src
COPY --from=api-dev-builder /opt/venv /opt/venv
COPY src /app/src
COPY migrations /app/migrations
LABEL org.opencontainers.image.component="api" \
      org.opencontainers.image.description="HC Data Platform API development image"
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=15s --retries=10 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]
CMD ["uvicorn", "hc_data_platform.core.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/src"]

FROM runtime-media AS worker-dev
ENV PYTHONPATH=/app/src
COPY --from=worker-dev-builder /opt/venv /opt/venv
COPY src /app/src
COPY migrations /app/migrations
LABEL org.opencontainers.image.component="worker" \
      org.opencontainers.image.description="HC Data Platform Worker development image"
HEALTHCHECK --interval=10s --timeout=7s --start-period=30s --retries=6 \
    CMD ["python", "-m", "hc_data_platform.workflow.healthcheck", "--timeout", "5"]
CMD ["watchfiles", "--filter", "python", "--target-type", "command", "python -m hc_data_platform.workflow.worker", "/app/src"]
