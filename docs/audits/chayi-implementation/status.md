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
