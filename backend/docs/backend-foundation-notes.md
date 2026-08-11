# Backend foundation notes

## Install and run

The project is locked to Python 3.12 and exact package versions in `uv.lock`.

```bash
cp .env.example .env
uv sync --all-groups --frozen
docker compose up -d postgres minio minio-init
uv run alembic upgrade heads
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`GET /healthz` is the process liveness probe. `GET /readyz` executes `SELECT 1` through
the async database engine. Domain routers are found with `importlib` and `pkgutil`; a
missing or broken optional domain is warned and skipped instead of preventing startup.

Run verification with:

```bash
make test
uvx ruff check app/core app/platform app/domains/ingest app/main.py \
  tests/core tests/ingest tests/conftest.py alembic
```

## Local PostgreSQL and MinIO

`docker compose up -d postgres minio minio-init` starts PostgreSQL 16 and private MinIO,
then creates the `platform-ingest` bucket. The API container is available with
`docker compose up --build api`. S3-compatible signing is configured with
`S3_ENDPOINT_URL`, `S3_REGION`, `S3_BUCKET`, and a server-side access key. Presign and
upload-authorization TTLs are capped at 900 seconds; long-term keys are never returned by
ordinary domain projections.

## Alembic workflow

```bash
uv run alembic current
uv run alembic upgrade heads
uv run alembic downgrade -1
uv run alembic revision -m "change description"
```

Migration files use `<domain>_<seq>_<slug>.py`, for example
`ingest_0002_normalized_history.py`. Foundation owns only the idempotency, outbox, and
audit migration. In parallel development, independent domain heads are expected. Each
domain keeps its own `down_revision`; after all branches land, an owner inspects
`alembic heads` and creates an explicit merge revision. A domain must never rewrite
another domain's migration or guess its future revision ID.

## Stable core exports

- `errors`: `DomainError` and the 401/403/404/409/410/412/422/429/500 typed errors,
  exact error envelope, and FastAPI handlers.
- `db`: async `engine`, callable `async_sessionmaker`, transactional `get_session`, and
  shared declarative `Base`.
- `context`: frozen `RequestContext`, `get_context`, and fail-closed `require(*caps)`.
- `pagination`: `CursorParams`, `cursor_params`, opaque cursor encoding/decoding, and
  `build_page`; callers use keyset ordering plus an immutable ID tie-breaker.
- `idempotency`, `outbox`, `audit`: transaction-local command replay, event insertion,
  registered audit events, and recursive sensitive-field redaction.
- `etag`, `ids`, `int64`, `logging`: conditional writes, typed IDs, decimal-string
  int64 wire values, and structured safe logging.

Example route boundary:

```python
@router.post("/things")
async def create_thing(
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require("dataset.create"))],
):
    async def mutate():
        # Add the aggregate, write_audit(...), and emit_event(...) here.
        return {"id": "thing_..."}

    return await with_idempotency(session, key, scope, mutate)
```

## Cross-domain ports: hard boundary

A domain must never import another domain's `models.py` or `service.py`. Cross-domain
reads go only through Protocols from `app.platform.ports`; callers receive safe mappings
and pass the current `RequestContext` so the provider can enforce scope.

Exports are:

- `DatasetVersionPort.get_version`
- `EpisodePort.get_episode`
- `RobotAssetPort.get_calibration_set`
- `SchemaRegistryPort.get_schema_version`
- `ObjectStoragePort.presign_put`, `presign_get`, and `head`

The matching in-memory implementations live in `app.platform.ports.fakes`.

```python
class EpisodeReader:
    def __init__(self, episodes: EpisodePort) -> None:
        self.episodes = episodes

    async def load(self, episode_id: str, ctx: RequestContext):
        episode = await self.episodes.get_episode(episode_id, ctx)
        if episode is None:
            raise NotFoundError(code="EPISODE_NOT_FOUND")
        return episode
```

## Ingest invariants

The Ingest write model keeps provider ETag and content SHA-256 in separate columns.
Manifest bytes and hashes, Part attempts, VerificationRuns/stages/findings, Quarantines,
registration receipts, and upload events are append-oriented facts. Every successful
write operation runs through idempotency, registered audit, and outbox insertion in the
request transaction. Every list uses opaque keyset cursors; offset pagination is absent.

The conditional production authorization and worker-adapter assumptions are recorded in
`app/domains/ingest/OPEN_QUESTIONS.md` and fail closed where upstream policy is not yet
approved.
