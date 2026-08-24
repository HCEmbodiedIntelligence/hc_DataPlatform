# BE-12 deployment validation evidence

Current chart lint/template validated on 2026-08-17 from
`/home/czy/hc_DataPlatform/backend`. External runtime evidence was retained on 2026-08-14.

| Gate | Command/evidence | Result |
| --- | --- | --- |
| Helm lint | `HELM_BIN=<Helm 3.16.4> deploy/scripts/verify-chart.sh` | PASS on 2026-08-17 |
| Deterministic template | same command, namespace `hc-data-pilot` | PASS on 2026-08-17; Worker readiness renders factory import plus Temporal TCP checks |
| Secret hygiene | `tests/system/test_helm_contract.py` | PASS; values contain names/keys only |
| API/Worker images, probes, resources, rolling strategy, PDB | `tests/system/test_helm_contract.py` and rendered manifest | PASS; separate API/Worker references |
| Upgrade exercise | `deploy/PILOT-REPORT.md`; two kind candidate upgrades | HISTORICAL PASS for 2026-08-14 chart; current strict-readiness chart NOT RUN |
| Rollback exercise | same report; revisions 3, 5, and final 7 | HISTORICAL PASS; current strict-readiness chart NOT RUN |
| Data preservation | immutable Raw/Lance/Published proof hashes across rollback | PASS; all three hashes unchanged |
| Current-source API image | Dockerfile `api` target with pinned Python base digest `sha256:2199a628…`; isolated `/health/live` | PASS; image `sha256:7c4e66f2…`, 293,697,718 bytes, HTTP 200 |
| Current-source Worker image | Dockerfile `worker` target; source SHA, import, composition, and media probes | PASS; image `sha256:0c238a05…`, 465,944,651 bytes; 5 key source hashes and all 14 ports match/pass; `lerobot` absent |
| Same-day BE-01 image smoke | cached image digest `sha256:280098df…`; container health and imports | PARTIAL; live=200, Docker health=healthy, FFmpeg/x264/HLS/FFprobe pass; ready=503 without external services |
| Chart-equivalent OTel API command | `opentelemetry-instrument uvicorn …` in isolated cached-image container | PASS; live=200 |

The host did not have Helm installed, so Helm 3.16.4 was downloaded to `/tmp` for validation and
was not added to the repository. The one-click `verify-chart.sh` also passes when that binary is
placed on `PATH`. Docker Hub metadata timed out, so the current image build used the environment's
Docker mirror for the same pinned Python base digest. A same-day BE-01 cached image starts;
its `/health/live` returns 200, while `/health/ready` correctly returns 503 without PostgreSQL,
Temporal, and object storage. That isolated smoke changed no external data; the later kind pilot
used only disposable dependencies and test-owned proof objects.

The old cached image contains Temporal, boto3, SQLAlchemy, and the required FFmpeg capabilities,
but does not contain PyArrow, MCAP, or Lance. The current split-image Dockerfile addresses that
boundary. An initial superseded Worker build exposed a validation-only LeRobot/PyTorch/CUDA
stack and was cancelled after its 856.6-second dependency layer. BE-01 removed that stack; the
corrected production Worker built in about 117 seconds, passed isolated data/media imports, and
closes BE12-010.

An initial rebuild was stopped when the newly introduced BuildKit cache mount began downloading
NumPy 2.2.6, PyArrow 25.0.1, and PyLance 10.0.0. A concurrent owner build subsequently produced
the source-matching Worker audit image above. The final API build was fully cached. A redundant
Worker relabel rebuild was cancelled when its Debian FFmpeg layer attempted a 132 MB fetch; the
already verified image has identical application source and runtime media. BE-12 will perform no
further Docker changes or builds per user direction.

With the current source mounted read-only, `tests/system/container_preview_smoke.py` generated a
six-frame H.264 preview with three CMAF segments, reloaded it through FFprobe, and confirmed an
invalid input leaves no committed cache artifact. This closes preview binary/codec behavior. The
corrected Worker closes the data-dependency image gap, but complete deployed pipeline execution
still requires rebuilding the latest source and exercising its now-configured production
Activity factory against pilot dependencies.

The retained pilot proves rolling mechanics and immutable-data preservation for the chart that
was exercised. It no longer qualifies the current chart by itself: BE-12 tightened Worker
readiness on 2026-08-17 so a live-but-unconfigured Worker cannot become Ready. A current upgrade
and rollback rehearsal now has a production factory and locally validated images, but still
requires owner-managed image promotion and the pilot environment; it remains open.

## 2026-08-20 current-image local kind rehearsal

This section supersedes the prior current-chart `NOT RUN` statement for the disposable local acceptance rehearsal only; it does not claim an owner-managed production rollout.

| Gate | Current execution | Result |
| --- | --- | --- |
| Current source images | API `sha256:b313e48f…`, Worker `sha256:d043ecd3…`, frontend `sha256:5b996673…` built and loaded into new kind | PASS |
| Chart render | Helm 3.16.4 lint/template using credential-free acceptance values and digest-pinned images | PASS |
| Baseline install | migration Job complete; API, Worker, frontend Ready; in-pod `/health/ready` = 200 | PASS |
| Upgrade/rollback script | `deploy/scripts/exercise-upgrade-rollback.sh` on release `hc-final-acceptance-kind` | PASS: history 1 install → 2 upgrade → 3 rollback to 1 |
| Final rolled-back state | three deployments 1/1 available; frontend release label `final-acceptance-kind-baseline` | PASS |
| Backup/restore consistency | dedicated PostgreSQL schema dump/restore plus MinIO snapshot/delete/restore and SHA-256 comparison | PASS |

All Kubernetes secrets used only test values in the disposable namespace and are excluded from repository evidence. The exact namespace, cluster, proxy, and connected test dependencies are removed by the final acceptance cleanup record. `PRODUCTION PERFORMANCE USER-WAIVED; EXECUTION NOT RUN` remains the sole capacity exception.
