# G1 implementation evidence

Owner: this goal terminal; sequential implementation, no delegated agents.
Baseline: `4933200c1e09dd1f70ecbaabcfa9c6afa404e771` on 2026-09-28.
Execution: `/home/hc_op/workspace/hc_DataPlatform-chayi-work`, branch `feat/chayi-g1`.
Scope: W0–W6, W7 tooling/isolated rehearsal, W8 software acceptance. G2/G3 excluded.
The original three input documents and the later robot integration supplement
were copied unchanged from the original checkout; hashes are recorded below.

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

- W2/W5 状态补充（2026-09-29）：连续录制账本支持完整标准包引用和集合 ID，
  数据库核验 QC／Tag 任务／详情／全部相机预览后才 READY，无 Lance reservation。
  实际服务同步并发与拒绝测试、32 项回归、7 项 Temporal、13 项前端通过；迁移
  143/143 current，仅隔离应用。见 [状态证据及 G2 条件](continuous-ready.md)。
  连续录制新包派发尚未接通；预置合成账本不算三入口或真人验收，17 项 P0 不变。
- W2 恢复补充（2026-09-29）：Raw 桥接固定已登记的派生 command 和对象位置，
  不因 `/3`→`/4` 升级重新编码。12 项实际 PG/MinIO 登记与回归通过，包括并发、
  缺失 receipt、损坏来源及跨租户拒绝；隔离 worker 已恢复。见
  [恢复证据及限制](raw-derivation-recovery.md)。仍需真实历史版本及三入口全链路验收。
- W1/W2 阶段补充（2026-09-29）：深度窗口支持整段字节复用和选取码值无损编码，
  保留原掩码／源单位／帧映射／误差，完成实际包服务与禁网官方 reader 验证。
  测试发现当前编码依赖在全零缺帧后的帧间码值损坏，新增版本化独立关键帧配置；
  55 项单元／服务、43 项扩大回归通过，两个解码器对比旧／新配置证据已记录。
  见 [窗口、编码代价、限制及 G2 条件](depth-window.md)。窗口尚未接入入口派发，
  Raw 固定派生恢复的软件证据见上项；17 项 P0 仍 Pending，不能据此部署 G2。
- R3/W2 阶段补充（2026-09-29）：独立 uploader 1.3.0 已通过真实隔离 API 上传
  合成 RGB-D，处理失败后用同一 Raw 重试至 READY；视频/HC 字节复用、零 Lance、
  完整包禁网官方 reader/DataLoader 与独立掩码均核验。94 项回归、4 项实际包服务、
  6 项 manager 单位检查通过；0008 仅隔离应用，141/141 current。见
  [链路、限制与 G2 条件](r3-native-client.md)。旧永久失败的受控恢复、多窗口/其他
  入口、真实样本/浏览器等仍未验收，R3/17 项 P0 不标记为完成。
- R1/R2/R4 阶段补充（2026-09-29）：四个机器人仓库已建立独立工作树；manager
  候选 `1e3f3934`，独立 uploader 1.3.0 已隔离安装。平台/客户端完成 4096 总资产
  （含完成标记）契约和大包能力预检，保留旧策略额度与小包协议；页面区分切片确认。
  平台 33 项通过，上传器 65 项通过及一个旧断言修复后单独通过，大清单 v1/v2
  2 项通过，真实封存逻辑/合成 ROS 输入 4 项通过。
  详见 [机器人阶段报告](robot-capacity.md)。实际独立客户端到平台服务、深度单位、
  连续录制新包、整体安装和真机条件未完成，R1–R4/17 项 P0 均不标为完成。
- W2 阶段补充（2026-09-29）：原生完整单 Episode RGB-D 上传已接到新的标准包
  提交、官方 RealSense 预览和 Tag 标注链路，Lance 写入为零。154 项扩大回归通过；
  成功/证据错误 4 场景通过，追加 3 项失败后原始下载通过，旧 Ingest 重放 1 项通过。
  实际下载包的离线官方 reader/DataLoader 与独立 HC 掩码检查通过。
  仅隔离数据库新增 0006/0007，140/140 current。
  见 [范围、证据和回退条件](native-package.md)。共享视频/多窗口、其他入口和真实
  数据/浏览器条件仍未完成，17 项 P0 保留 Pending。
- 范围同步：原始执行方案新增机器人接入 R1–R4，已复制补充文档，按阶段推进；
  R5 属于 G2 不执行。平台手工上传不能代替机器人上传链路验收。
  2026-09-29 与原工作树逐字节核对的 SHA-256：
  `chayi.md=51cfd06544d55d0c9f0831de80259a42318857e6bc670dbb9ae25e482ee9cead`；
  `chayi-implementation-plan.md=4bd37f00c4c7314ac53be2c9c024b24404c0dacbaef2fc5f2f76f235bf6de7c1`；
  `chayi-goal-prompts.md=1991869d1d5391da88718f2f3bb0137920523c5bb1200a6e7f8cb73d4b595d8b`；
  `chayi-teleop-integration.md=0d4edebd23bc25695740e6181195ec196f74005f3f9d16986bfd6b7f2b900e0b`。

- W3 阶段补充（2026-09-29）：版本化 JSON 的事务 outbox、worker、重试/下载授权、
  P08 文件状态与下载、正式契约已接通。后端 144、前端 200、独立运行服务链路 1 项通过。
  迁移 0015/0016 仅在隔离数据库应用，138/138 current。
  见 [详细报告和 G2 回退条件](annotation-files.md)。仍为合成 RGB-D 软件证据，
  不代表 ANN-02 的实际样本/真人验收或整个 G1 完成；17 项 P0 均仍 Pending。

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

## Official RealSense media preview milestone (partial W2/W4/W5)

- User requires official RealSense depth display. PNG and new video previews now
  call the pinned `pyrealsense2.colorizer` directly: Dynamic/Jet/histogram defaults.
  Removed the handwritten palette. HC invalid pixels become SDK zero input; a
  temporary in-memory Z16 frame is display-only. No new 16-bit primary copy.
- Processing owns immutable preview publication; GET/authorization never starts
  encoding. Full scoped version/hash checks precede grants, which are audited.
  READY retries reuse completed outputs. H.265 RGB and grayscale HEVC depth stay
  byte-identical in the primary package; H.264 RGB is reused where compatible.
- P06 lazily authorizes package RGB/depth videos and refreshes through the same
  capability check. Dynamic colors are explicitly not a fixed distance scale.
  Backend grants record the display profile; complete downloads remain primary RGB-D.
- Isolated migration `0005_media_previews` and official SDK dependency layer were
  applied only to G1. API/Worker recovered healthy after correcting the derived
  image runtime user to the original 65532; production 8088 was not changed.
- Backend package/service tests: **28 passed**; additional depth/auth/endpoint
  regression: **41 passed**. Frontend source/runtime/Workbench: **58 passed**.
  TypeScript, generated API gate and changed-code static checks pass. Tests include
  pixel equality against actual SDK defaults, real PG/MinIO/gateway/video Range,
  masked physical reads, interruption/concurrency/retry and cross-scope denial.
- Detailed evidence, source links, commands, deployment conditions and limits:
  [realsense-preview.md](realsense-preview.md). Inputs remain synthetic; no browser,
  sensor recording, complete ingestion or human acceptance is claimed. The actual
  three-ingress dispatch still needs new-format integration. All 17 P0 rows remain
  pending for their required full coverage.

## Frozen package commit workflow milestone (partial W2)

- The real `commit_aligned_bundle` activity accepts a frozen Episode bundle plan
  with input/configuration, schema, alignment bytes/profile, media receipts and
  depth evidence. It writes through the package service and product projector,
  without reserving a Lance number or calling a Lance writer. A pure Arrow step
  path avoids the old adapter's mutable schema lookup. Historical collection
  snapshots remain readable when constructing mixed collections.
- `DatasetWriterWorkflow` selects the new branch from its input shape. Old inputs
  preserve their previous command sequence; mixed inputs are rejected. Verified
  a real new-package execution on isolated Temporal with attempt 1/2 after an
  injected lost successful response, plus sandbox replay. Independently generated
  history with the previous commit's workflow source and replayed it with the new
  writer. This covers this writer, not all parent-ingestion histories.
- Review found that a package commit must NOT mark a staging selector as a legacy
  dataset commit. The package branch now leaves old media authorization at 425.
  Its complete package and preview grants use their own immutable data reference.
- Recovery verifies the complete committed package before resuming publication,
  so loss of temporary Arrow/prepared media does not block a committed retry.
  The test removes only its newly created temporary Arrow, disables input staging
  access/main-package writes, and successfully retries four concurrent requests.
- Reproduced a PostgreSQL media-lease deadlock during concurrency tests; begin and
  mark now lock camera rows in UUID order. Four synchronized first attempts with
  opposite receipt orders produce one version and one display encode per camera.
  Model imports no longer eagerly load the NumPy alignment engine in the sandbox.
- **85 passed** across package, alignment, media, materialized export and workflow
  regression, including **4 new integration tests**. New tests also pass with
  UserWarning treated as an error. Static checks pass; isolated services healthy.
  Evidence and commands: [workflow-commit.md](workflow-commit.md), XML and actual
  Temporal histories alongside it.
- These service tests start with synthetic prepared inputs and seeded source
  facts; they do not exercise upload, MCAP decoding or the complete three entrances.
  Next required integration remains source-plan generation/parent dispatch,
  storage-neutral automatic annotation and continuous READY, source HC/window
  handling, and W3–W8. All 17 P0 rows remain pending for their complete scope.

## Immutable annotation data binding milestone (partial W1/W3/W5)

- New Episode tasks, drafts, revisions, submissions, reviews and approval snapshots
  now carry the full immutable data reference; new records keep Lance columns
  NULL. Legacy revision hashes retain the original document shape and LEGACY
  task lineage is readable. Automatic task/provider/review preparation contracts
  accept the new reference without inventing a physical version.
- Applied additive annotation migrations 0013/0014 only in G1. Database guards
  require an exact committed receipt in the selected collection, preserve task
  identity, and match child revision/submission/review bindings. Real tests found
  and fixed an organization omission in automatic Schema lookup; the compatibility
  binding key and explicit reads now include the organization, keeping existing RLS.
- P08 uses fixed package metadata, joint values and P06's existing RGB/official
  RealSense preview authorization. It checks all revision/review bindings and
  preserves Tag edit, submit and review commands. Legacy export cannot consume
  a package approval just because version numbers coincide.
- **139 passed** in expanded backend regression; **76 passed** after the final
  legacy-lineage addition; **153 passed** in P08/package frontend regression.
  TypeScript, Ruff and contract/client generation checks pass. Four pre-existing
  Workbench test failures were reproduced with prior source and repaired by
  correcting fixture topics and stale panel-title assertions.
- Evidence, exact commands, artifact hashes and deployment/rollback conditions:
  [annotation-bindings.md](annotation-bindings.md). API/worker restarted in G1
  and recovered healthy. Synthetic RGB-D and frontend request doubles prove
  software contracts only, not real uploads, browser flows or human acceptance.
- Parent workflows still need new source plans/dispatch and continuous READY
  integration. Revision JSON outbox/files/retry, subtask materialization, independent
  reviews, unified detail, annotated/batch delivery and migration/rehearsal remain.
  All 17 P0 rows stay Pending against their complete requirements. No G2/G3 action.
