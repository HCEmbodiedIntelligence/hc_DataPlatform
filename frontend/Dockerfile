ARG NODE_IMAGE=public.ecr.aws/docker/library/node:22.23.0-alpine@sha256:ab07539e0988b63558ff621f5fbe1077054c39d9809112974fb79993949d41cd
ARG NGINX_IMAGE=ghcr.io/nginx/nginx-unprivileged:1.29.4-alpine@sha256:a6c4f61f456b85b8fdf7ec7ab28cc3e299440e6fb4a9dea520e5fd8fd440025e

FROM ${NODE_IMAGE} AS frontend-dev

ENV NODE_ENV=development

WORKDIR /app
RUN corepack enable && chown node:node /app
USER node
COPY --chown=node:node package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY --chown=node:node . .

EXPOSE 5173
HEALTHCHECK --interval=5s --timeout=3s --start-period=15s --retries=10 \
    CMD ["wget", "--quiet", "--output-document=-", "http://127.0.0.1:5173/"]
CMD ["pnpm", "dev", "--host", "0.0.0.0", "--port", "5173"]

FROM ${NODE_IMAGE} AS build

ARG APP_VERSION=dev
ARG VCS_REF=unknown

ENV VITE_API_BASE_URL=/api/v1 \
    VITE_SSE_BASE_URL=/api/v1/events \
    VITE_MOCK_MODE=off \
    VITE_BUILD_VERSION=${APP_VERSION} \
    VITE_RELEASE_ENV=production

WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build:container \
    && if find dist -type f -name '*.map' -print -quit | grep -q .; then \
         echo "public source maps are forbidden in the frontend image" >&2; exit 1; \
       fi

FROM ${NGINX_IMAGE} AS runtime

ARG APP_VERSION=dev
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
ARG SOURCE_URL=https://example.invalid/hc-data-platform

LABEL org.opencontainers.image.title="HC Data Platform Frontend" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="${SOURCE_URL}"

COPY container/nginx.conf /etc/nginx/conf.d/default.conf
COPY container/docker-entrypoint.sh /opt/hc/bin/docker-entrypoint.sh
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html

USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["wget", "--quiet", "--output-document=-", "http://127.0.0.1:8080/healthz"]
ENTRYPOINT ["/bin/sh", "/opt/hc/bin/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
