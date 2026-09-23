# OpenArm / native LeRobot v3 integration (B)

The PROCESS entry now selects a schema driven native profile or the existing
Unitree G1 profile. `robot_type` is retained. Native `action` and
`observation.state` must be named, finite float32 vectors with unique names;
their dimensions may differ for generic sources. Cameras are discovered from
video features. The supported native time grid is integer FPS in 1–240 Hz.
Unsupported features, inconsistent episode offsets, tasks, vector schemas,
timestamps, video ranges/counts/shapes and capture metadata fail processing with
`LEROBOT_*` / `OPENARM_*` diagnostics. No deployment or schema migration is needed
for B's changes.

## Canonical data and provenance

| Source | Canonical topic | Representation |
|---|---|---|
| `observation.state` | `/humanoid/observation/state` | `names`, `positions`, optional per-axis `units` |
| `action` | `/humanoid/action` | `names`, `values`, optional per-axis `units` |
| `observation.images.<id>` | `/camera/<id>/image` | Original video slice / frame PTS; normal media worker |
| source identity / capture mapping | `/metadata/source` | Source frame, episode, task, original float timestamp and source mapping |

Native frame `i` has canonical time `round(i * 1e9 / fps)`; float32 source time
must equal the corresponding declared grid value. The original float value is
retained in source metadata. Alignment uses nearest with 1 µs tolerance and
never interpolates actions. G1 retains its original QC version, 30 Hz alignment
and tolerance. OpenArm keeps 14 revolute radians and two single-finger metre
values, without doubling gripper displacement or converting it to an angle.
The viewer and training export retain per-axis names/units; numeric training
vectors are not JSON strings.

`openarmx` / `openarmx_<digits>` selects `openarmx-v1`, requires the contracted
16 axes and capture context. `capture-context.json`, `source_mapping.jsonl`
and the completion marker are checked against committed asset facts. Episode
source IDs, task strings, outcomes, invalid intervals, model/calibration hashes,
physical-sync declaration and configuration references remain source facts.
Any qualified episode overlapping an invalid source interval is rejected.
Source outcomes are not silently converted into approved platform annotations.
Platform annotation/review records and half-open tag intervals remain separate.

Export `meta/annotations.json` embeds capture context, original source episode
and source asset SHA/size inventory for each episode. The export's per-frame
source metadata retains source mapping, including noncontiguous source episode
frame ranges. All original files (including depth MCAP, pointcloud MCAP, depth
index and trace) are accessible via the existing scoped raw file listing and
`assets:read?path=...&download=true`. Their SHA256 can be verified against the
embedded inventory; these sidecars are not embedded a second time into the
training ZIP. Signed URLs are intentionally not persisted in the export.

Labels use the existing dataset tag-schema binding and configuration API.
The default UI can configure arbitrary task-specific labels. Existing G1 label
bindings are reused. QC is supplied by
`LeRobotPipeline(..., quality_profile_factory=...)`; the default native profile
requires all declared streams and evaluates their actual FPS, state/action and
camera quality through the existing engine. E can inject a deployment-specific
factory without changing the reader.

## C: committed source discovery and processing

Public Python boundary:

```python
from hc_data_platform.lerobot_imports.committed import (
    normalize_committed_manifest,
    discover_committed_source,
    DiscoveredLeRobotV1,
)
```

1. C authenticates the robot, resolves the authoritative task/tenant/robot,
   verifies every completed asset, and commits `RawSource`. C supplies the
   current worker request scope; it must match `RawSource` organization,
   project and region. The Raw must be COMMITTED, LEROBOT_V3 and task-bound.
2. Supply `assets=[{"path": relative_path, "object_key": platform_key,
   "size": verified_size, "sha256": verified_sha256, "crc64": optional_crc}]`.
   These are C's authoritative DB facts, not unchecked robot locators.
   `normalize_committed_manifest(raw, assets)` returns
   `raw-upload-manifest/v1`; persist it at `raw.manifest_key` before discovery.
   Preserve immutable bytes and the source content hash. B does not write to
   C's core service or choose C's commit transaction ordering.
3. Call `result = discover_committed_source(storage, raw)` using
   `ObjectStoragePort`. This reads bounded, hash-checked metadata from platform
   object keys, validates episode inventory and produces
   `committed-lerobot-discovery/v1` with `plan`, `episodes` and
   `source_manifest_sha256`. `episodes` contains native metadata and capture
   `source_episode_id` / `outcome` when provided. Discovery does not imply QC
   PASS or READY. No browser session or shared robot filesystem is required.
4. `result.plan` is `lerobot-import-plan/v1`; ordered tasks are
   `lerobot-episode-task/v1`. Each task ID is
   `lerobot:{raw_source_id}:episode:{index}`. Browser hex IDs and C's
   `raw-<32 hex>` IDs are accepted. Repeated discovery returns the same plan.
5. With the existing registered workflow, submit:

```python
from hc_data_platform.workflow.lerobot_workflow import (
    LeRobotImportWorkflow, LeRobotImportWorkflowInput,
)

job = await temporal.execute_workflow(
    LeRobotImportWorkflow.run,
    LeRobotImportWorkflowInput(
        task=result.plan.episode_tasks[0],
        episode_count=len(result.plan.episode_tasks),
    ),
    id=f"robot-lerobot:{raw.raw_source_id}",
    task_queue=configured_task_queue,
)
```

Use the shared Pydantic data converter and C/E's durable scheduling/idempotency
policy. Alternatively C's workflow can call the registered prepare activity
for each task, then the normal ingest child workflow and native state update;
do not mark an episode READY just because discovery succeeded. Existing
`LeRobotPipeline` recognizes already-ready episodes. A deliberate reprocess
must use the existing processing-attempt API/identifier. C aggregates actual
episode status/QC/artifacts into its own processing result contract.

## E: assembly and release requests

- Merge B with C at the common baseline. Keep the existing runtime's
  `LeRobotPipeline` injection and registrations in
  `workflow/lerobot_workflow.py`; the real B test exercises these registrations.
  No changes to shared `runtime.py`, `workflow/worker.py`, route assembly or
  migration registry are included in B.
- Wire C's committed LEROBOT_V3 branch to the boundary above and its processing
  status aggregation. Pass opaque asset `object_key` values through the
  normalized manifest; B's localization, original downloads and export source
  reads all support them. Configure worker/media task queues consistently.
- Publish a task-appropriate tag schema before PROCESS; bind a valid active
  collection task and enabled robot data source. Browser tests inject a test
  principal/target validator; deployment authentication/target provisioning
  remains E's assembly responsibility.
- Generate/upload the OpenArm model through the existing robot-model asset
  mechanism. `python -m hc_data_platform.tools.openarm_model_bundle
  /description /new/output` reads a supplied `openarmx_description` package,
  rewrites package mesh references, copies meshes and emits `robot.config.json`.
  Bind `left_gripper/right_gripper` to
  `openarmx_left/right_finger_joint1` with SAME direction; URDF mimic drives
  finger2. The utility validates prismatic type and mimic multiplier/offset.
  The model is a template; replace its example robot identity when registering
  the target robot. Platform never opens a robot-side local path.
- Retain the old offline deployment and database. Deploy only through E's
  normal integration/release process. U4 on another computer, real robot task
  semantics, physical synchronization and production model binding require
  E/user acceptance; B's A bundle is explicitly synthetic exporter output.

## Reproduction

All platform tests run through
`/home/hc_op/workspace/openarm-data-integration-plan/g0/scripts/compose.sh B`.
The required initial regression is `g0/scripts/test-platform.sh B`.
Its default environment skips infrastructure tests; enable them explicitly:

```bash
bash /home/hc_op/workspace/openarm-data-integration-plan/g0/scripts/compose.sh B run \
  --rm --no-deps --user 1000:1000 \
  -v /home/hc_op/workspace/openarm-integration/B/platform/artifacts/g0:/delivery \
  --entrypoint python \
  -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  -e OPENARM_REAL_INFRA=1 -e OPENARM_ROUNDTRIP_OUTPUT=/delivery/roundtrip \
  -e HC_TEST_POSTGRES_DSN=postgresql://hc:hc@postgres:5432/hc_data \
  -e HC_MINIO_ENDPOINT=http://minio:9000 -e HC_MINIO_BUCKET=openarm-b-tests \
  -e HC_MINIO_ACCESS_KEY=minio -e HC_MINIO_SECRET_KEY=minio-local-only \
  migration -m pytest tests/lerobot_imports tests/publishing \
  tests/tools/test_lerobot_platform_upload.py -q \
  -p pytest_asyncio.plugin -p no:cacheprovider
```

The roundtrip test creates/drops isolated B test databases and uses production
PostgreSQL, MinIO, Temporal, QC, alignment, media, Lance, annotation, review and
publishing. Only the authentication principal and target selection are test
injections; QC/status/media are not mocked. Three inputs are tested: E's frozen
valid-openarm, E's valid-generic (2 axes / 1 camera), and A's final exporter
bundle. A's exact source/commit/content SHA is recorded with the fixture.

Actual reader verification uses a separate `.reader-compose` venv inside the
B Compose container: Python 3.12.8, `lerobot[dataset]==0.6.1`, CPU PyTorch and a
64-package frozen dependency inventory. `bash scripts/check_openarm_reader_b.sh
--install` reproduces the environment and all six reads (omit `--install` for
an existing environment). `scripts/verify_openarm_reader.py ROOT SOURCE OUTPUT`
reads every tensor/video and an 8-sample batch crossing the 6-frame
episode boundary, checks exact float32 vectors, names, time/episode/frame/task
identities, CHW video shapes and image correspondence against the source.
Training video re-encoding is lossy; mean absolute image error is bounded at
8/255. Logs, source and roundtrip receipts, ZIPs, dependency lock and environment
details are under `artifacts/g0/`; final status is in `artifacts/g0/HANDOFF.md`.
Source and training directories are mounted read-only. HF network access is
disabled during reads. `TORCHINDUCTOR_CACHE_DIR=/tmp/torch` is required for the
numeric container UID. The reader accepts standard tensors and ignores the
platform's `hc.*` info extensions; consumers needing source context/annotations
must read `meta/annotations.json` explicitly.

The initial default image build could not reach Docker Hub's pinned Python
base manifest. `artifacts/g0/offline.compose.yaml` and `Dockerfile.offline`
record the B-only fallback built from the already present
`hc-offline/backend:71eaa560` image; source was mounted from B's worktree.
`platform-environment.json` inventories its actual dependencies. E should
rebuild the normal pinned image in its release environment. This fallback
does not alter the original offline image, deployment or database.
