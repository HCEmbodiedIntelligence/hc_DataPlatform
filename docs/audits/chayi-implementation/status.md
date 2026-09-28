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

- W0: worktree created and environment configuration prepared. Runtime startup,
  sample inventory and baseline tests not yet verified.
- W1–W8: pending. This is a progress ledger, not an acceptance report.

## Remaining external conditions

- No human role acceptance has run in this goal.
- Real sample identities, hashes, depth units/calibration and authorization must
  be inventoried before claiming real-data acceptance.
- Model depth training compatibility is outside the claim; no model is specified.
