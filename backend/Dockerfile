ARG PYTHON_IMAGE=python:3.12.8-slim@sha256:2199a62885a12290dc9c5be3ca0681d367576ab7bf037da120e564723292a2f0
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.3@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc
ARG POSTGRES_IMAGE=postgres:16.10-bookworm@sha256:61c57e5eeda4d69232c97cb23ae5d95d170a1a2fd0b40a6b4f34fb56819b056d
FROM ${UV_IMAGE} AS uv
FROM ${POSTGRES_IMAGE} AS postgres-tools
FROM ${PYTHON_IMAGE} AS dependencies

ENV PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./

FROM dependencies AS age-tools
ARG AGE_RELEASE_URL=https://github.com/FiloSottile/age/releases/download/v1.3.1/age-v1.3.1-linux-amd64.tar.gz
ADD --checksum=sha256:bdc69c09cbdd6cf8b1f333d372a1f58247b3a33146406333e30c0f26e8f51377 \
    ${AGE_RELEASE_URL} \
    /tmp/age.tar.gz
RUN tar --extract --gzip --file=/tmp/age.tar.gz --directory=/tmp \
    && install -m 0755 /tmp/age/age /usr/local/bin/age \
    && age --version | grep --fixed-strings --line-regexp 'v1.3.1'

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
RUN /opt/venv/bin/python -c "import lance, mcap, mcap_ros2.decoder, pyarrow"

FROM project AS postgres-maintenance-builder
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable --extra database

FROM project AS object-maintenance-builder
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable --extra backup

FROM project AS configuration-maintenance-builder
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable --extra backup

FROM project AS temporal-maintenance-builder
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable --extra backup

FROM project AS backup-maintenance-builder
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable --extra backup

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
RUN /opt/venv/bin/python -c "import lance, mcap, mcap_ros2.decoder, pyarrow, watchfiles"

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
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN install -d -o 65532 -g 65532 -m 0750 \
    /tmp/hc-data/alignment \
    /tmp/hc-data/aligned-media \
    /tmp/hc-data/robot-model-upload-staging
RUN ffmpeg -hide_banner -encoders 2>&1 | grep --quiet libx264 \
    && ffmpeg -hide_banner -encoders 2>&1 | grep --quiet libvpx-vp9 \
    && ffmpeg -hide_banner -muxers 2>&1 | grep --quiet hls \
    && ffprobe -version >/dev/null
USER 65532:65532

FROM runtime-base AS postgres-maintenance
USER 0:0
COPY --from=postgres-tools \
    /usr/lib/postgresql/16/bin/pg_basebackup \
    /usr/lib/postgresql/16/bin/pg_controldata \
    /usr/lib/postgresql/16/bin/pg_dump \
    /usr/lib/postgresql/16/bin/pg_restore \
    /usr/lib/postgresql/16/bin/pg_verifybackup \
    /usr/lib/postgresql/16/bin/pg_waldump \
    /usr/local/bin/
COPY --from=postgres-tools \
    /usr/lib/x86_64-linux-gnu/libpq.so.5.18 \
    /usr/lib/x86_64-linux-gnu/libldap-2.5.so.0.1.8 \
    /usr/lib/x86_64-linux-gnu/liblber-2.5.so.0.1.8 \
    /usr/lib/x86_64-linux-gnu/libsasl2.so.2.0.25 \
    /usr/lib/x86_64-linux-gnu/
RUN ln -s libpq.so.5.18 /usr/lib/x86_64-linux-gnu/libpq.so.5 \
    && ln -s libldap-2.5.so.0.1.8 /usr/lib/x86_64-linux-gnu/libldap-2.5.so.0 \
    && ln -s liblber-2.5.so.0.1.8 /usr/lib/x86_64-linux-gnu/liblber-2.5.so.0 \
    && ln -s libsasl2.so.2.0.25 /usr/lib/x86_64-linux-gnu/libsasl2.so.2 \
    && install -d -o 65532 -g 65532 -m 0750 /backup-staging
COPY --from=postgres-maintenance-builder /opt/venv /opt/venv
COPY migrations /app/migrations
LABEL org.opencontainers.image.component="postgres-maintenance"
USER 65532:65532
ENTRYPOINT ["hc-postgres-backup"]
CMD ["--help"]

FROM runtime-base AS object-maintenance
USER 0:0
COPY --from=age-tools /usr/local/bin/age /usr/local/bin/age
RUN install -d -o 65532 -g 65532 -m 0750 /backup-staging
COPY --from=object-maintenance-builder /opt/venv /opt/venv
COPY migrations /app/migrations
LABEL org.opencontainers.image.component="object-maintenance"
USER 65532:65532
ENTRYPOINT ["hc-object-backup"]
CMD ["--help"]

FROM runtime-base AS configuration-maintenance
USER 0:0
COPY --from=age-tools /usr/local/bin/age /usr/local/bin/age
RUN install -d -o 65532 -g 65532 -m 0700 /backup-staging
COPY --from=configuration-maintenance-builder /opt/venv /opt/venv
LABEL org.opencontainers.image.component="configuration-maintenance"
USER 65532:65532
ENTRYPOINT ["hc-configuration-backup"]
CMD ["--help"]

FROM runtime-base AS temporal-maintenance
USER 0:0
COPY --from=age-tools /usr/local/bin/age /usr/local/bin/age
RUN install -d -o 65532 -g 65532 -m 0700 /backup-staging
COPY --from=temporal-maintenance-builder /opt/venv /opt/venv
LABEL org.opencontainers.image.component="temporal-maintenance"
USER 65532:65532
ENTRYPOINT ["hc-temporal-backup"]
CMD ["--help"]

FROM runtime-base AS backup-maintenance
USER 0:0
COPY --from=age-tools /usr/local/bin/age /usr/local/bin/age
COPY --from=postgres-tools \
    /usr/lib/postgresql/16/bin/pg_dump \
    /usr/lib/postgresql/16/bin/pg_restore \
    /usr/local/bin/
COPY --from=postgres-tools \
    /usr/lib/x86_64-linux-gnu/libpq.so.5.18 \
    /usr/lib/x86_64-linux-gnu/libldap-2.5.so.0.1.8 \
    /usr/lib/x86_64-linux-gnu/liblber-2.5.so.0.1.8 \
    /usr/lib/x86_64-linux-gnu/libsasl2.so.2.0.25 \
    /usr/lib/x86_64-linux-gnu/
RUN ln -s libpq.so.5.18 /usr/lib/x86_64-linux-gnu/libpq.so.5 \
    && ln -s libldap-2.5.so.0.1.8 /usr/lib/x86_64-linux-gnu/libldap-2.5.so.0 \
    && ln -s liblber-2.5.so.0.1.8 /usr/lib/x86_64-linux-gnu/liblber-2.5.so.0 \
    && ln -s libsasl2.so.2.0.25 /usr/lib/x86_64-linux-gnu/libsasl2.so.2 \
    && install -d -o 65532 -g 65532 -m 0700 /backup-staging
COPY --from=backup-maintenance-builder /opt/venv /opt/venv
COPY migrations /app/migrations
LABEL org.opencontainers.image.component="backup-maintenance"
USER 65532:65532
ENTRYPOINT ["hc-platform"]
CMD ["backup", "--help"]

FROM runtime-media AS worker
COPY --from=worker-builder /opt/venv /opt/venv
COPY migrations /app/migrations
LABEL org.opencontainers.image.component="worker"
HEALTHCHECK --interval=10s --timeout=7s --start-period=30s --retries=6 \
    CMD ["python", "-m", "hc_data_platform.workflow.healthcheck", "--timeout", "5"]
CMD ["hc-data-worker"]

FROM runtime-media AS api
COPY --from=api-builder /opt/venv /opt/venv
COPY migrations /app/migrations
LABEL org.opencontainers.image.component="api"
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]
CMD ["uvicorn", "hc_data_platform.core.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime-media AS api-dev
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
