# Real API / BE24 共享集成验收矩阵

当前源码与 artifact 审计：2026-08-18 12:40（Asia/Shanghai）。状态只允许 `PASS`、
`FAIL`、`PARTIAL`、`NOT RUN`、`XFAIL`；skip、XFAIL、未启动和 API-only 结果不能转换为
Browser PASS。证据位于 `artifacts/test-gates/latest/`，该目录是运行产物，不入库。

| 门禁/要求 | 当前证据 | 状态 |
| --- | --- | --- |
| Compose Real API/Worker 合同 | `compose-real-api-config.log`、`compose-test-config.log`、`static-contracts.xml`；test profile 含真实 Postgres、MinIO、Temporal、migration/check、API、gateway、正式 Worker 与 test-only runner；MinIO 有 `hc.test.disposable=true` | PASS |
| 生产 Router composition | `runtime-openapi-contract.log`：85 paths、95 HTTP operations，`duplicate_router_mounts=[]`；dashboard/access/collection_tasks/storage/BR02 路由均只挂载一次 | PASS |
| Runtime/formal OpenAPI | production-composed `create_app().openapi()` 与 fragments 的 path、method、request/response schema ref、security、operationId 全部一致，`contract_issue_count=0`；BE23 历史 56 个 operationId drift 于本时点闭合 | PASS |
| HTTP docs / 内部 schema | 默认生产 gateway 的 `/docs`、`/redoc`、`/openapi.json` 关闭；CLI 仍从 app 对象重现 schema；BR03 production exposure tests 包含在 static gate | PASS |
| 公开启动面 | `runtime-openapi-contract.log`：`forbidden_public_starts=[]`，没有公开 start-ingest 或 annotation-task create；启动只来自持久化 outbox | PASS |
| Runtime 类型与 P01 接线 | `frontend-generated-api-drift.log`、`frontend-typecheck.log`、P01 focused Vitest 2 PASS；无 region storage/月数，activity 为事件流，coverage 可 `BLOCKED`，pending 四来源，发布血缘字段来自生成类型 | PASS |
| 共享前端错误客户端 | runtime 返回扁平 RFC 9457 Problem Details；当前 `frontend/src/shared/api/http-client.ts` 仍只解析旧式 `{error: {...}}`，真实错误的 code/detail/request ID 尚未签收，见 BE24→FE04–FE09 交接 | PARTIAL |
| Static 聚合门禁 | Compose、OpenAPI、生成类型、typecheck、29 contracts、Ruff format/lint、mypy 133 source、Prettier、artifact scan 全部退出 0 | PASS |
| Fresh migration/checksum | manifest 26 项；空库升级后 `expected=applied=26`，`missing=[]`、`checksum_drift=[]`、`unknown=[]` | PASS |
| 旧 15 项升级/重放/负向诊断 | `migration-existing-*` 只追加当前 11 项，re-upgrade `applied_now=[]`；unknown 与 checksum drift 均被识别，恢复后 current | PASS |
| 外部依赖集 | `external-integration.xml`：9 个测试文件、14 passed、0 skipped/XFAIL；覆盖 annotation、collection task、dashboard、ingest、publishing lineage、access/security、storage | PASS |
| MinIO pause/recover | `minio-network-recovery.xml`：disposable label 校验、真实 pause 失败、finally unpause、同 session 恢复与单份 Raw/Manifest，1 passed | PASS |
| Worker replay / kill 恢复 | `worker-replay.xml`：真实 Temporal replay 与 Worker 进程 kill/takeover，5 passed、0 skipped/XFAIL | PASS |
| BR02 正式启动链 | production outbox repository、Temporal launcher、持久化 input resolver、正式 activities 与自动 annotation dependency；BE22 issuer/cleanup/decoder 只挂在 test runner/profile | PASS |
| BE22 real gateway/API/Worker 主链 ×2 | `be22/be24-proof-a.json`、`be22/be24-proof-b.json`：每份 9/9 stages PASS，含注册、审批、IDOR、P20、Manifest/QC/Lance、自动 task、Tag、distinct reviewer、close；前后 database/Raw/Lance cleanup 全 CLEAN | PASS |
| Public-security 可执行套件 | `public-security.xml`：97 passed、0 skipped/XFAIL；5xx 固定脱敏，当前 artifact scan 0 finding | PASS |
| 公网安全覆盖完整性 | BR03 已闭合历史 G-HTTP500/G-ARTIFACT/docs/metrics 配置项；G-RATE、G-SESSION 仍 FAIL，逐路由 authz/scope/audit/idempotency/page 与跨域拓扑仍 PARTIAL，见保留的 BE23/BR03 矩阵 | PARTIAL |
| Artifact 脱敏 | 每个 gate 后置 sanitizer/scanner；最终 `artifact-scan.log` 为 `finding_count=0`，历史失败结构及 `.redaction.json` 审计侧车保留 | PASS |
| 第二波真实浏览器主链 | `real-api-e2e-not-run.json`，退出码 2；FE04–FE09 尚未完成，没有 `VITE_MOCK_MODE=off` Playwright 网络证据 | NOT RUN |
| 5 TB/day + 30% 容量 | `test_release_blockers.py` 保持 expected-fail；没有 production-like 至少 30 分钟全链路证据 | XFAIL |
| 全量后端严格回归 | `backend-strict-regression.xml`：480 passed、15 skipped、1 xfailed，严格 reporter 退出 1，未过滤；14 个外部依赖与 1 个 network skip 已在上述独立真实门禁闭合，容量 XFAIL 仍阻断 | FAIL |

当前关键 SHA-256：runtime OpenAPI `aefb7ddefd1122a0db460607acee4d6339b5002d21764f1f875d531e289ebfd9`；
formal aggregate `352cee7b6d3cf8fc08209b81b48c5876edbce7a595ae98eb7033eee6de2eac0f`；
生成类型 `755894b5c58629cc7b1c2421c3462d30803de5a2237521fab6014f6080faf209`；
两次主链 artifact 分别为 `05e4d69edbb576b2d299f7fb27f046fa989f476ddedc4cfe261e25db38357edc`
和 `de146c64fb1b25c585240e165710c724b8694dc3aedbc5d84aa8336155931f0b`。

BE22、BE23、BR01–BR03 历史报告未删除或覆盖；`be24-chain-a.json` 与
`be24-final-a.json` 也保留了集成过程中发现并修复的失败/NOT RUN 审计轨迹。只要仍存在
`FAIL`、`PARTIAL`、`NOT RUN` 或 `XFAIL`，整体生产发布不能标为 PASS。

## 历史 BE-12 验收矩阵（不覆盖上述当前结果）

Current source audit: 2026-08-17. Retained external pilot evidence: 2026-08-14. `PASS` means the
named evidence directly exercised the stated scope; `XFAIL`, `PARTIAL`, and `NOT RUN` are not
release acceptance. The rows below are retained pilot evidence and are not a rerun against the
concurrently changing source tree audited above.

| Requirement | Authoritative evidence | Status |
| --- | --- | --- |
| Executable current-state release blockers | `test_release_blockers.py` | XFAIL: 8 passed, BE12-008 capacity remains expected-failing |
| Legal/truncated/bad-CRC/missing-topic MCAP | generator, manifest hashes, and `test_fixture_contracts.py` | PASS; regeneration was byte-identical and 11 fixture checks passed |
| 28 Hz/time-backward/black-frame/empty-point-cloud QC | structured fixtures plus `test_fixture_contracts.py` | PASS |
| upload→manifest→verify→QC→30 Hz align→all-camera MP4→Lance refs→annotation→review→publish→LeRobot reload | `test_temporal_workflows.py`, `test_e2e_pipeline.py` | PASS for workflow/reference ports |
| Duplicate messages create no duplicate Rollout/Step/Revision/Version | E2E replay assertions and `test_fault_injection.py` | PASS for reference ports |
| SHA/QC/Lance/DB/media publication/export interruption recovery | fault injection plus aligned-media exact-receipt tests | PASS for deterministic reference ports |
| Actual Worker kill after durable Lance-style commit | `test_temporal_worker_recovery.py`; real Temporal Worker subprocess | PASS; SIGKILL, attempt 2 takeover, one commit/version |
| Actual object-store network partition | `test_minio_network_recovery.py`; disposable MinIO container pause | PASS; real timeout and same-session recovery |
| Temporal workflow execution | `INTEGRATION-EVIDENCE.md`; Temporal test environment | PASS; 3 tests |
| PostgreSQL security + annotation adapters | `INTEGRATION-EVIDENCE.md`; disposable PostgreSQL 16.10 | PASS; 2 tests after applying annotation migration |
| MinIO multipart adapter | `INTEGRATION-EVIDENCE.md`; disposable MinIO `RELEASE.2025-07-23T15-54-02Z` | PASS; 1 test |
| FFmpeg canonical MP4, exact 30 Hz/1800-frame/60-second timeline | `tests/aligned_media/test_aligned_media_service.py` | PASS with real FFmpeg/FFprobe when installed |
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
| 5 TB/day plus 30% headroom | `tests/load/CAPACITY-REPORT.md` | FAIL; E2E unavailable and MinIO upload P50 56.48 MB/s < 75.23 MB/s |
| Runtime image smoke | current-source API/Worker images plus isolated containers | PASS; API live=200; Worker source hashes/imports/media pass and `lerobot` is absent |
| Full backend regression | `pytest --import-mode=importlib -p pytest_asyncio.plugin` | PARTIAL; 262 passed, 6 skipped, 1 explicit capacity XFAIL |
| Full format/lint/type gates | Ruff format/check and mypy | PASS; 158 files formatted, Ruff clean, mypy clean for 95 source files |
| Normal repository `pytest` command | `.venv/bin/pytest -q` | PARTIAL; 262 passed, 6 skipped, 1 explicit capacity XFAIL in 63.81 s |

BE-12 cannot be marked complete while any row is `XFAIL`, `PARTIAL`, `NOT RUN`, or `FAIL`.

## 2026-08-20 final P01–P20 acceptance reconciliation

The tables above preserve the historical BE-12 gap audit. They are not the current status of the user-approved P01–P20 functional acceptance scope. Current execution uses the finalized shared worktree and isolated real dependencies:

| Current final gate | Result |
| --- | --- |
| Runtime OpenAPI/client and static backend/frontend gates | PASS |
| P01–P20 real browser API suites | PASS: 35 cases, all `VITE_MOCK_MODE=off`, no MSW/route-interception transport |
| Auth register/login empty-account regression | PASS |
| P18/P20 cross-page upload smoke and second cleanup | PASS |
| Whole backend suite | PASS: 606 passed, `--fail-on-skipped` |
| Current local kind current-image/chart migration + readiness | PASS |
| Current local kind actual upgrade then rollback | PASS: Helm revisions 1 → 2 → 3, final revision rolled back to 1 and all deployments Ready |
| PostgreSQL + object-store backup/restore consistency drill | PASS; restored reference/object SHA-256 matched and temporary resources were removed |
| Real external-auth 429 edge | PASS: opt-in gateway policy returned 429 after normal 201 authentication |

The only intentionally non-PASS row is capacity gate `BE12-008`: `PRODUCTION PERFORMANCE USER-WAIVED; EXECUTION NOT RUN`. It must not be converted into a production capacity claim. Historical `PARTIAL`/`NOT RUN` language above is superseded for this final functional scope, not erased as audit history.
