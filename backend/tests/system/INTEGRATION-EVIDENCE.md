# BE-12 integration evidence

Retained external evidence date: 2026-08-14; current source/image checks: 2026-08-17. Disposable
containers were stopped and removed after each check. Secret values were test-only and are
intentionally not retained.

| Boundary | Runtime | Test command | Result |
| --- | --- | --- | --- |
| Golden fixture determinism | host filesystem | regenerate and compare every pre/post SHA-256; `test_fixture_contracts.py` | PASS; byte-identical regeneration, 11 passed |
| Temporal workflow execution | Temporal Python test environment | `pytest -q tests/workflow/test_temporal_workflows.py` | 3 passed in 27.27 s |
| Security + annotation persistence | `postgres:16.10-bookworm` on loopback; annotation migration applied first | targeted security and annotation PostgreSQL tests with their documented DSN variables | 2 passed |
| Multipart object storage | `minio/minio:RELEASE.2025-07-23T15-54-02Z` on loopback | `pytest -q tests/ingest/test_minio_integration.py` with documented MinIO variables | 1 passed |
| Runtime image | cached same-day `hc-data-platform-backend:be01`, digest `sha256:280098df…` | container health, module discovery, and FFmpeg capability probes | live 200; Docker health healthy; x264/HLS/FFprobe pass |
| OTel API bootstrap | chart-equivalent `opentelemetry-instrument uvicorn …` in cached image | isolated container `/health/live` | PASS; 200 and instrumented Python process remained live |
| Temporal process kill | real Activity Worker SIGKILL after one durable commit | `test_temporal_worker_recovery.py` | PASS; retry attempt 2, one commit, workflow succeeded |
| MinIO network outage | disposable labelled container paused during `list_parts` | `test_minio_network_recovery.py` | PASS; real timeout, same session recovered, one Raw + manifest |
| Kubernetes upgrade/rollback | kind v0.27.0/Kubernetes v1.32.2; `deploy/PILOT-REPORT.md` | PASS; two cycles, 2/2 API + 2/2 Worker, immutable proofs preserved |
| Pilot OTel transport | sanitized sink summary in `results/pilot-otlp-summary.json` | HISTORICAL PASS for transport; run predates current domain emitters and must be repeated |
| Preview encode + atomic failure | current source bind-mounted read-only into the cached runtime image | `container_preview_smoke.py` | H.264 64×48, 6 frames/1.5 s, 3 CMAF segments; failed decode left no artifact |
| Current-source API image | Dockerfile `api` target; pinned Python base digest | cached build plus isolated `/health/live` | PASS; image `sha256:7c4e66f2…`, 293,697,718 bytes, live=200 |
| Current-source Worker image | Dockerfile `worker` target; installed-source SHA comparison | isolated imports, composition, and media smoke | PASS; image `sha256:0c238a05…`, 465,944,651 bytes; 5 key source hashes and 14 ports match/pass; `lerobot` absent |
| Production activity composition | project `.venv` | import/invoke pilot factory and inspect dataclass fields | PASS; all 14 production Activity dependencies are non-null |
| Current release gates | project `.venv` | `test_release_blockers.py -rxX` | 8 passed, 1 XFAIL: BE12-008 capacity |
| BE-12 system/load suite | project `.venv` | `pytest -q tests/system tests/load` with explicit asyncio plugin | 44 passed, 1 external MinIO skip, 1 capacity XFAIL in 18.08 s |
| Full host regression | project `.venv` | default `.venv/bin/pytest -q` | PASS as regression command; 262 passed, 6 skipped, 1 capacity XFAIL in 63.81 s |

The retained 2026-08-14 runtime image readiness endpoint returned 503 because PostgreSQL,
Temporal, and object storage were deliberately not attached. That obsolete combined image did
not contain PyArrow, MCAP, or Lance. The current split API image builds and starts. The current
Worker image matches the workspace SHA-256 for `runtime.py`, `core/app.py`,
`workflow/activities.py`, `core/context.py`, and `ingest/router.py`; it proves data imports,
fourteen-port composition, and media tools while excluding the optional LeRobot/PyTorch/CUDA
validation stack. BE12-010 is closed. This composition has not yet run against pilot Temporal.

The six current skips are two PostgreSQL tests, the existing MinIO adapter test, two FFmpeg
preview tests, and the BE-12 MinIO network-pause test. Targeted disposable PostgreSQL/MinIO runs
close the database, multipart, and real network-outage behaviors. `container_preview_smoke.py`
mounts the current source read-only into the runtime image and reproduces both successful CMAF/HLS
generation with FFprobe reload and failed-decode atomicity. The host suite still reports these
skips because their external processes are intentionally not kept running. The prior owner Ingest
router regression (BE12-011) is closed, and BE-12's signed-JWT load probe uses production
`create_app` middleware with an explicit in-memory runtime backend and passes.
