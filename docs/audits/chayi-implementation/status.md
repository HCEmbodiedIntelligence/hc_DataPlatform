# G1 implementation evidence

Owner: this goal terminal; sequential implementation, no delegated agents.
Baseline: `4933200c1e09dd1f70ecbaabcfa9c6afa404e771` on 2026-09-28.
Execution: `/home/hc_op/workspace/hc_DataPlatform-chayi-work`, branch `feat/chayi-g1`.
Scope: W0–W6, W7 tooling/isolated rehearsal, W8 software acceptance. G2/G3 excluded.
The three input documents were copied unchanged from the original checkout.

## Verified starting state

- Original checkout has only the three untracked input documents.
- Original project `hc-data-platform-restore-test` runs eight services; gateway 8088.
- API bind mount is `/home/hc_op/workspace/hc_DataPlatform/backend:/app:ro`.
- New isolated project is `hc-chayi-g1`, gateway 48088, dedicated PostgreSQL/MinIO
  volumes and Temporal server/namespace/queues; environment is explicitly authored.
- Existing writer remains Lance at baseline. No new format has been claimed ready.
- No business data, old storage or robot motion has been modified.

## Requirement ledger

States are implementation / software verification / real-data verification / human
acceptance. “Pending” is not passed. Evidence is added as commands actually run.

| P0 | Work | Current state | Required evidence |
| --- | --- | --- | --- |
| DATA-01 | W1/W2 | Pending | All three ingestion paths, no new Lance writes |
| DATA-02 | W1/W2 | Pending | Offline official RGB-D reader and DataLoader |
| DATA-03 | W2/W5 | Pending | Failed processing still allows authorized raw download |
| DATA-04 | W1/W2/W4/W5 | Pending | Source/window/frame mapping agrees in package/API/UI |
| DATA-05 | W1/W2 | Pending | Interrupted, failed, retried and concurrent commits |
| DATA-06 | W1/W2 | Pending | Units, numeric semantics, depth profile/mask/error/time |
| SEG-01 | W2 | Pending | Whole recording and long recording windows |
| SEG-02 | W3/W5 | Pending | Tag edits preserve media; explicit derived Episode |
| ANN-01 | W1/W3/W7 | Pending | Complete tags and immutable review bindings |
| ANN-02 | W3/W5 | Pending | Versioned JSON outbox, failure/retry/download |
| ANN-03 | W3/W6 | Pending | Explicit subtask track, gaps/overlap/cross-track tests |
| QC-01 | W4/W5 | Pending | Three independent reviews, warn/skip/unknown |
| UI-01 | W4/W5 | Pending | Real API browser at 1440/1024; human tasks separately |
| UI-02 | W5/W6 | Pending | Nearby complete/RGB/RGB-D/annotated/raw download |
| UI-03 | W1/W5 | Pending | Data-version labels with legacy details retained |
| EXP-01 | W6 | Pending | Multi-Episode RGB-D batch and reuse evidence |
| MIG-01 | W7 | Pending | Inventory/checkpoint/resume/pointer rollback on copy |

## Work log

- W0: independent worktree and eight isolated services verified running (gateway 48088).
  Temporal namespace `chayi-g1` registered; original 8088 services remain running.
  Real sample inventory remains pending.
- W1–W8: pending. This is a progress ledger, not an acceptance report.

## Remaining external conditions

- No human role acceptance has run in this goal.
- Real sample identities, hashes, depth units/calibration and authorization must
  be inventoried before claiming real-data acceptance.
- Model depth training compatibility is outside the claim; no model is specified.

## Depth contract milestone (partial W1/W2)

- User clarification: retain official HEVC/gray12le single-channel encoding and
  record actual profile. Explicit x265 `main12` rejects i400 in this environment;
  tested output reports `Rext`. The original three input documents remain unchanged.
- Versioned profile defaults remain 12-bit/log/0.01–10 m/shift 3.5 m/lossless=1.
  Source scale is explicit; quantization occurs after invalid/saturation detection.
- Streaming mask and source evidence: bounded Parquet row groups containing PNG
  flags, exact source ns/sequence/dtype/scale, missing/repeated/time-error status.
  No extra source-precision array or MKV is generated.
- Continuous raw preparation now checks camera + source sequence at equal MCAP
  timestamps, emits evidence assets, and verifies decoded HEVC code hashes/PTS.
- `compose.sh run --rm --no-deps test tests/publishing/test_depth_contract.py
  tests/continuous_recordings/test_openarm_raw.py
  tests/publishing/test_lerobot_materialized_export.py --fail-on-skipped
  --fail-on-xfailed -q`: **23 passed**, evidence `depth-tests.xml`.
- `compose.sh run --rm --no-deps reader /app/tools/validate_depth_contract.py`:
  six deterministic numerical probes match official LeRobot 0.6.1 quantization
  and mm/m inverse. Official video decode + independent HC mask passed offline;
  evidence `depth-official-crosscheck.json`. This comparative script imports
  platform code: it does NOT prove standalone package/DataLoader acceptance.
- Error gate separates clipping and uses half-cell bound + 20 µm float32
  tolerance; official mm output adds up to 0.5 mm integer rounding.
- Still pending: real RGB-D data evidence, complete-package writer/commit,
  metadata/sidecar propagation through three inputs, original-source calibration
  and unselected-frame inventory, standalone reader and all remaining P0 flows.

## Package commit milestone (partial W1)

- New `episode_store` data references include full tenant/dataset/Episode/rollout
  identity, data version, content hash, format and schema; no fake Lance version.
- Additive migrations `episode_store/0001_episode_packages.sql` and
  `0002_commit_pointer_guard.sql` applied only in `hc-chayi-g1`. Separate binding,
  version, attempt, collection and transactional event tables use organization/
  project/region RLS. Committed versions are immutable; pointer guard rejects
  PREPARING versions even via direct SQL.
- Package validator checks independent v3 indexes/time/media, required modalities,
  depth profile/source/mask/code hashes; bounded object uploads and read-back hashes
  precede immutable manifest/receipt and the visibility transaction.
- `compose.sh run --rm --no-deps test tests/episode_store/test_commit_services.py
  --fail-on-skipped --fail-on-xfailed -q`: **9 passed** against actual isolated
  PostgreSQL and MinIO. Evidence: `episode-commit-tests.xml`.
- Covered partial object interruption, object receipt before DB, lost response,
  four concurrent identical requests, changed-content conflicts, missing modality,
  existing corrupt destination, tenant guard, non-superuser PostgreSQL RLS across
  organization/project/region, actual DB trigger failure and rollback/recovery,
  pointer guard, immutable committed rows and depth mask tampering.
- Inputs here are deterministic synthetic fixtures: these prove commit mechanics,
  not real ingestion or complete G1 acceptance. Runtime still uses the legacy
  writer until W2 integration; the new event table is not yet consumed.

## Standalone writer and readers milestone (partial W1/W2)

- Attempt-scoped prepared assets verify frozen source media/evidence hashes without
  calling or weakening the READY-only export resolver. RGB H.265 and matching depth
  are copied byte-for-byte; conversion receipts record no transcode/requantization.
- Streaming writer emits numerical Parquet in 1024-row groups, exact int64 source
  nanoseconds, standard task/episode metadata, and HC source/quality/calibration
  documents. Original inferred numerical precision is retained. A one-element
  vector uses the official scalar Parquet representation; its original shape and
  structured semantics are separately recorded and reconstructed for platform reads.
- Validation now rejects numerical dtype/shape mismatches and depth evidence whose
  target timestamps or supplied aligned source mapping differ from the Episode.
  Depth statistics exclude invalid pixels, independently of official video decode.
- Fixed-version readers resolve authorization from PostgreSQL before every access,
  verify manifests/files, cache bounded private disk copies, and prune numerical
  columns/row groups. Binary depth returns float32 mm/m, independent validity and
  saturation flags, profile and source mapping; invalid pixels become NaN. A PNG
  pseudocolor derivative is available separately from the measurements.
- Combined isolated regression command: `compose.sh run --rm --no-deps test
  tests/episode_store tests/publishing/test_depth_contract.py
  tests/continuous_recordings/test_openarm_raw.py
  tests/publishing/test_lerobot_materialized_export.py --fail-on-skipped
  --fail-on-xfailed -q`: **36 passed**. Evidence `episode-reader-tests.xml`.
- New reader tests exercise actual PostgreSQL/MinIO, reverse depth seeks, binary
  round-trip, foreign tenant/Episode/rollout rejection, row-group/column pruning,
  precision and structured-state round-trip, and warm seeks without object downloads.
- Official offline `LeRobotDataset` + `DataLoader` directly opened the copied RGB-D
  root with no platform imports and network disabled (LeRobot 0.6.1). Shapes are
  RGB `[4,3,16,16]`, depth `[4,1,16,16]` in mm. Independent HC mask/application and
  source mapping passed; measured fixture errors `[0,0,1,1]` mm. Evidence:
  `standalone-rgbd-reader.json`; reproducible script `backend/tools/read_standalone_rgbd.py`.
- The four-frame fixture is explicitly synthetic, not real-data, long-recording,
  browser, or model-training acceptance. 17 P0 statuses above remain pending.
- Remaining: runtime/three-ingress integration, common read routing and API/UI,
  source units/calibration inventory, long-file chunking, mixed collection versions,
  annotations/reviews/download delivery/migration, and real/human acceptance.
