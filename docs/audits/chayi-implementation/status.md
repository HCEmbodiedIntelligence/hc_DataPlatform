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

## Read/download HTTP milestone (partial W2/W4/W6)

- Production API composition now exposes explicit Episode data-version routes for
  version listing, package metadata, selected step windows, depth metadata/NPZ/PNG,
  and complete offline ZIP download. Legacy routes remain for historical data.
- Exact organization/project/region capability checks precede binding resolution;
  no caller-supplied rollout or object prefix controls file access. Read requires
  `dataset_version.read`; complete download requires `export.download`. Missing
  bindings, forbidden scope, invalid windows and failed integrity have distinct errors.
- Complete ZIP includes every manifest member and the manifest/commit receipt.
  Media are copied unchanged, with bounded working memory and ZIP64 support.
  Repeated fixture downloads produced identical ZIP bytes and every file SHA-256
  matched the committed package. ZIP caching, queued large/batch delivery and API
  access audit integration remain outstanding.
- Actual isolated `gateway:8080 -> API -> PostgreSQL/MinIO` was exercised with a
  registered account and normal login session; only fixture membership/grants were
  bootstrapped directly. Anonymous access, foreign organization, wrong region/
  Episode, oversized range and revoked download permission were checked. Warm
  cache did not bypass revocation. Credentials are not part of the reports.
- `compose.sh run --rm --no-deps test tests/episode_store
  tests/security/test_runtime_router_auth.py
  tests/core/test_object_store_endpoint_config.py --fail-on-skipped --fail-on-xfailed
  -q`: **45 passed**, one existing Starlette/httpx deprecation warning.
  Evidence `episode-http-tests.xml`, `http-reader-evidence.json`.
- The actual downloaded ZIP was extracted and read in the offline official reader
  container with no platform imports: RGB-D DataLoader, independent HC masks,
  mapping and fixture error checks passed. Evidence `http-rgbd-reader.json`.
- These are synthetic software/service-chain checks. They do not satisfy real
  recording, three-ingress, UI/browser or human acceptance. New writer dispatch,
  worker outbox wiring and all other pending items above still require work.

## Collection publication milestone (partial W1/W2)

- Dataset collection snapshots now pin complete scoped Episode references. A new
  Episode data version replaces only that Episode in a new immutable collection;
  collection ordinal is independent of Episode count. New references do not have
  a fabricated `lance_version`.
- Additive migration `episode_store/0003_collection_publication.sql` was applied
  only to the isolated database. An advisory lock serializes publication; object
  verification precedes the collection head/publication/event transaction. Lost
  responses return the original publication on retry, even after newer versions.
- The common logical reader routes new package references to Parquet and frozen
  historical references to actual Lance versions. Historical Episode/rollout
  bindings come from persisted regional revisions, not identifier parsing.
  Current/list/detail/step reads reject versions outside the selected region;
  legacy PostgreSQL queries also explicitly constrain organization, including
  when the service database role can bypass RLS.
- `EpisodePackageService.write_and_publish` composes the writer, package commit
  and collection publication and is available to activity dependencies. The
  existing ingestion activities have NOT yet switched to this hand-off.
- Isolated service tests (PostgreSQL, MinIO, real historical Lance fixture):
  `tests/episode_store/test_collections_services.py tests/lance_catalog/test_adapters.py
  tests/lance_catalog/test_lance_integration.py --fail-on-skipped --fail-on-xfailed`:
  **12 passed**, including two publication interruption points, four concurrent
  Episodes, reprocessing/history, mixed physical readers and scope boundaries.
  Evidence: `episode-collection-tests.xml`. Fixture data are synthetic.
- Ruff passes the new collection modules/tests and changed legacy adapter/activity
  code. Full `runtime.py` checking still reports two pre-existing import-order and
  two pre-existing long-line findings in robot-processing composition.
- Main processing dispatch, storage-neutral schema registration, product
  projection and event consumption remain pending; no P0 row is marked complete.

## Prepared-media and schema milestone (partial W1/W2)

- Additive `episode_store/0004_dataset_schemas.sql` applied only to the isolated
  database. New logical schemas are immutable and keyed by organization/project/
  region/dataset. Concurrent identical registration is idempotent, changed
  definitions/fingerprints fail, and identical display IDs in another tenant or
  region can register independently without any Lance schema/table writes.
- Internal `BundleAssetsLoader` resolves each frozen media selector against the
  actual scoped media repository, checks attempt lineage and immutable receipt
  fields, and streams objects into private temporary files with size/SHA-256
  verification. Original H.265/gray12le streams are copied without encoding.
- Depth requires its declared feature contract plus profile/mask/mapping evidence
  whose object hashes are in the authorized frozen source inventory. Missing
  contracts, foreign inventories, changed media receipts, same-size byte corruption
  and foreign request scopes fail before any Episode is made visible. Temporary
  files are removed after both success and failure.
- This uses the internal processing port, not `PostgresExportAssets`. A real
  PostgreSQL media publication can be READY while its Dataset commit is absent;
  the package builder can read that frozen publication, while the unchanged public
  aligned-media authorization still returns 425. No READY guard was weakened.
- `EpisodePackageService.write_from_aligned_bundle` composes this port with package
  and collection commits; runtime injects the real media repository. The fixture
  exercises the actual original-video generation service, PostgreSQL and MinIO,
  forbids calls to the encoder/Lance writer, checks byte identity and numerical/
  source-mapping reads. The Arrow locator is an unused synthetic fixture locator;
  this is NOT an upload/alignment/Temporal end-to-end or real recording test.
- Combined regression: `tests/episode_store tests/lance_catalog/test_adapters.py
  tests/lance_catalog/test_lance_integration.py tests/workflow/test_workflow.py
  tests/security/test_runtime_router_auth.py tests/core/test_object_store_endpoint_config.py
  --fail-on-skipped --fail-on-xfailed -q`: **67 passed**, one existing Starlette/httpx
  deprecation warning. Evidence: `episode-prepared-tests.xml`. Changed package
  modules/tests pass Ruff and `git diff --check`.
- Remaining integration: freeze new-format plans in new workflow inputs while
  preserving old histories; branch actual commit activities and both resolver
  composition roots; carry native/continuous HC sidecars; project collection-based
  product versions and annotation bindings. Original subwindows currently fail
  explicitly in this port until exact window materialization is implemented;
  they are never silently treated as a full aligned Episode. All 17 P0 rows remain
  pending for the broader required coverage.

## Product projection milestone (partial W2)

- The real PostgreSQL product projector accepts committed Episode collections,
  verifies the persisted collection and package receipt, and pins each projected
  stream to its complete scoped data reference. RGB and depth have distinct stream
  kinds. No fabricated Lance number is used in product versions or workflow results.
- Append and reprocessing preserve prior immutable product snapshots; replacing
  an Episode keeps its ordinal. Collection ordinal is not treated as Episode count.
  Product writes are transactional and retries preserve the original target.
  Shared source packages are counted once in source-byte capacity projections.
- Activity output models accept new-format receipts with identity checks while
  retaining legacy histories. New ingestion dispatch and automatic annotation
  still need integration before the processing entrances can switch to this path.
- Frontend strict wire schemas accept the new references and reject cross-scope
  references and mixed legacy/package stream bindings. Viewer sources still need
  new-format numeric and media routing; schema acceptance alone is not UI acceptance.
- Regression with isolated PostgreSQL/MinIO and historical Lance fixtures:
  `tests/episode_store tests/dataset_registry/test_ingest_projection_postgres.py
  tests/lerobot_imports/test_native_viewer_projection.py tests/lance_catalog/test_adapters.py
  tests/lance_catalog/test_lance_integration.py tests/workflow/test_workflow.py
  tests/security/test_runtime_router_auth.py tests/core/test_object_store_endpoint_config.py
  --fail-on-skipped --fail-on-xfailed -q`: **72 passed**, one existing Starlette/httpx
  deprecation warning. Evidence: `episode-projection-tests.xml`.
- New service tests cover concurrent repeated projection, interruption of the
  product transaction, retry, second Episode, same-Episode reprocessing, immutable
  prior versions and forged ready receipts. Both continuous-style and MCAP-style
  projection inputs are synthetic; these are NOT upload/Temporal end-to-end tests.
- Frontend wire/adapters/viewer adapter regression: **13 passed**, evidence
  `episode-projection-frontend-tests.xml`; full `tsc -b --pretty false` passed.
  Changed backend modules/tests pass Ruff (runtime's prior findings excluded).
- Isolated frontend exhausted host inotify watchers. Polling is enabled only in
  the G1 compose override; frontend recovered healthy without changing host limits.
  An offline frontend-test service provides a writable test mount. All 17 P0 rows
  remain pending for their full required acceptance scope.

## Fixed-version numeric viewer milestone (partial W2/W5)

- P06 numeric streams now select the package step reader from the explicit
  product binding. Legacy streams keep the real Lance reader. Both use a shared
  logical decoder for numeric vectors, named joints/units, poses, points and events.
  New package streams display an Episode data-version description.
- Requests pin organization/project/region/dataset/Episode/rollout/data version,
  hash, schema, storage format and frame count. The source rejects mismatched
  responses, incomplete or duplicate steps, unordered/out-of-range timestamps and
  absent channel validity. Per-channel invalid samples are omitted independently
  of aggregate row validity. Nanosecond arithmetic stays in bigint.
- Requests exceeding the backend's 4096-step window limit are paged sequentially,
  with cancellation checked before/after each request. This removes the hard
  reader-window failure; large-recording rendering/memory performance is still
  pending and is not inferred from the 5000-step frontend fixture.
- Added an Episode API fragment and renderer; regenerated the dataset fragment,
  aggregate OpenAPI, production-composed runtime client and operation allowlist.
  The generation gate initially found the five missing Episode routes; it now
  passes formal/runtime checks. Regeneration also includes the already-existing
  robot recording retry route and dataset working-version field omitted by the
  previous generated artifacts. No auth boundary was weakened.
- Isolated frontend tests: **84 passed**, including the actual scoped HTTP client
  and runtime operation gate, legacy decoder, numeric package source, adapters and
  contracts. Responses in these frontend tests are fetch stubs; they do NOT count
  as real browser/API, ingestion or sensor-data acceptance. Evidence:
  `episode-viewer-frontend-tests.xml`. TypeScript build-mode checking passed.
- Isolated backend tests: **51 passed** across core, product contracts, decoder
  binding validation and real product projection services. Evidence:
  `episode-viewer-contract-tests.xml`. An earlier invocation had 4 core failures
  because test command overrides moved staging outside the production-approved
  ephemeral path. Removing those overrides fixed the invocation; the production
  validation and tested code were not relaxed. One existing Starlette/httpx warning.
- Reproducibility: render `scripts/render_episode_store_openapi_fragment.py` and
  `scripts/render_dataset_registry_openapi_fragment.py` in the backend test image;
  run `python -m hc_data_platform.core.openapi` then its `--runtime --output ...`
  variant. Set `HC_ROBOT_MODEL_ASSET_ROOT=/tmp/g1-openapi/robot-models` for the image's
  non-root user. Supply the emitted runtime YAML to frontend
  `node scripts/generate-api-client.mjs --runtime-contract ...`; `--check` passed
  with runtime SHA-256 `c24d0f86b5690f1340faf0817aa2dfb9e319bdaa633cf32b6dcf090e3aecddd9`.
- Remaining critical path: package RGB/depth media in P06; frozen new-format
  processing plans and actual commit activity/resolver dispatch; storage-neutral
  automatic annotation and continuous READY state (currently require Lance);
  native HC sidecars/window materialization, unified detail/download UI, and the
  remaining W3–W8 work. No P0 acceptance row is newly marked complete.
