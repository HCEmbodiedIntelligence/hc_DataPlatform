# HC Data Platform backend

Python 3.12 / FastAPI backend foundation plus the P02-P04 Ingest bounded context.

## Local development

```bash
cp .env.example .env
uv sync --all-groups
docker compose up -d postgres minio minio-init
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Run verification with `make test`. The API uses `/api/v1`; liveness and
readiness probes are `/healthz` and `/readyz`.

Every scoped API call requires `Authorization`, `X-Organization-Id`,
`X-Client-Version`, and `Accept-Language`. Mutations additionally require an
`Idempotency-Key`; mutations of an existing aggregate require `If-Match`.
