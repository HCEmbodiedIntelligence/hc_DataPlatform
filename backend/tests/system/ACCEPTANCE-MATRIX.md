# BE-12 acceptance matrix

Current source audit: 2026-08-17. Retained external pilot evidence: 2026-08-14. `PASS` means the
named evidence directly exercised the stated scope; `XFAIL`, `PARTIAL`, and `NOT RUN` are not
release acceptance.

| Requirement | Authoritative evidence | Status |
| --- | --- | --- |
| Executable current-state release blockers | `test_release_blockers.py` | 8 passed, 1 XFAIL: BE12-008 capacity |
| Legal/truncated/bad-CRC/missing-topic MCAP | generator, manifest hashes, and `test_fixture_contracts.py` | PASS; regeneration was byte-identical and 11 fixture checks passed |
| 28 Hz/time-backward/black-frame/empty-point-cloud QC | structured fixtures plus `test_fixture_contracts.py` | PASS |
| upload→manifest→verify→QC→align→Lance→preview→annotation→review→publish→LeRobot reload | `test_e2e_pipeline.py` | PASS for in-memory/reference ports |
| Duplicate messages create no duplicate Rollout/Step/Revision/Version | E2E replay assertions and `test_fault_injection.py` | PASS for reference ports |
| SHA/QC/Lance/DB/transcode/export interruption recovery | six injected side-effect/interruption tests | PASS for deterministic reference ports |
| Actual Worker kill after durable Lance-style commit | `test_temporal_worker_recovery.py`; real Temporal Worker subprocess | PASS; SIGKILL, attempt 2 takeover, one commit/version |
| Actual object-store network partition | `test_minio_network_recovery.py`; disposable MinIO container pause | PASS; real timeout and same-session recovery |
| Temporal workflow execution | `INTEGRATION-EVIDENCE.md`; Temporal test environment | PASS; 3 tests |
| PostgreSQL security + annotation adapters | `INTEGRATION-EVIDENCE.md`; disposable PostgreSQL 16.10 | PASS; 2 tests after applying annotation migration |
| MinIO multipart adapter | `INTEGRATION-EVIDENCE.md`; disposable MinIO `RELEASE.2025-07-23T15-54-02Z` | PASS; 1 test |
| FFmpeg HLS/CMAF and failed-decode atomicity | current source in cached runtime image via `container_preview_smoke.py` | PASS; FFprobe reload and no partial artifact |
| Production Worker executes all pipeline activities | composition gate, current image, and historical kind pilot | PARTIAL; all 14 production ports construct in the image, but current pilot execution is not rerun |
| Production Worker dependency scope | BE12-010 source gate | PASS; production target installs the data extra and excludes validation-only LeRobot/PyTorch/CUDA dependencies |
| Unconfigured Worker ports fail non-retryably | BE12-009 source gate plus historical pilot | PASS in source; old pilot retried, current configured pilot not rerun |
| Frozen public OpenAPI paths are mounted | runtime `app.openapi()` audit | PASS; every frozen fragment path is present |
| Interpolated Step retains both source timestamps | BE12-005 reproduction plus E2E | PASS; `(0, 10)` survives the shared Step contract |
| Upload/workflow/QC/Lance/transcode/export metric contract | source gate, metric contract, and pilot OTLP capture | PARTIAL; source emitters pass, current pilot capture not rerun |
| Runtime logs contain locators and no token/signature | source gate, pilot logs, and `verify_logs.py` | PARTIAL; contextual source calls pass, current pilot logs not rerun |
| Dashboard/alerts contain project/resource/workflow and Runbook | `test_observability_assets.py` | PASS |
| Helm lint/template | Helm 3.16.4 plus `deploy/VALIDATION.md` | PASS on current split-image, strict-readiness chart (2026-08-17) |
| Kubernetes upgrade/rollback | `deploy/PILOT-REPORT.md`; two real kind cycles | PARTIAL; preserved-data cycles pass for the 2026-08-14 chart, but the current strict-readiness release has not been rehearsed |
| 20 GiB control plane and 50 concurrent sessions | signed-JWT in-process probe plus historical kind API probe | PARTIAL; current source passes 50/50 in-process, current pilot not rerun |
| 5 TB/day plus 30% headroom | `tests/load/CAPACITY-REPORT.md` | NOT PASSED; E2E unavailable and MinIO upload P50 56.48 MB/s < 75.23 MB/s |
| Runtime image smoke | current-source API/Worker images plus isolated containers | PASS; API live=200; Worker source hashes/imports/media pass and `lerobot` is absent |
| Full backend regression | `pytest --import-mode=importlib -p pytest_asyncio.plugin` | PASS as regression command; 262 passed, 6 skipped, 1 explicit capacity XFAIL |
| Full format/lint/type gates | Ruff format/check and mypy | PASS; 158 files formatted, Ruff clean, mypy clean for 95 source files |
| Normal repository `pytest` command | `.venv/bin/pytest -q` | PASS as regression command; 262 passed, 6 skipped, 1 explicit capacity XFAIL in 63.81 s |

BE-12 cannot be marked complete while any row is `XFAIL`, `PARTIAL`, `NOT RUN`, `NOT PASSED`, or
`FAIL`.
