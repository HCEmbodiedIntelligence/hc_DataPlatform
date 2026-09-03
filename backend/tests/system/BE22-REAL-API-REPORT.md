# BE22 第二拨真实 API 主链与隔离 fixture 报告

日期：2026-08-18  
责任域：仅真实主链测试、fixture/generator、清理工具和本报告。未修改生产认证、领域实现、
Compose、聚合 OpenAPI、迁移 manifest、共享 Playwright 配置或验收矩阵。

## 结论

- **fixture/隔离/诊断/清理合同：PASS（13 个定向测试）**。
- **完整 system 回归：非 PASS**，结果为 `52 passed, 1 skipped, 1 xfailed`；外部 MinIO 网络恢复
  条件未提供，BE12-008 容量仍 XFAIL。
- **第二拨真实网络 API/Worker 主链：NOT RUN**。当前没有可用的一次性 test API 配置；runtime
  OpenAPI 也没有启动 ingest Worker 的命令。annotation task 缺少公开创建命令，BE22 已提供非 HTTP、
  三重 test guard 的 DB provisioner。
- **完整浏览器主链：NOT RUN**。FE 第二拨页面和 provision/launcher 依赖未完成，API-only 结果没有
  替代浏览器 E2E。

## 修改文件

- `backend/tests/system/wave2/generate_packages.py`：确定性 MCAP/Manifest 生成器。
- `backend/tests/system/wave2/data/**`：六类最小数据包、Manifest 和 hash/size catalog。
- `backend/tests/system/wave2/fixture.py`：隔离 namespace、动态 Manifest、脱敏阶段 artifact、清理控制器。
- `backend/tests/system/wave2/cleanup.py`：test profile 专用 PostgreSQL/MinIO 幂等清理。
- `backend/tests/system/wave2/orchestrator.py`：九阶段诊断执行与 FAIL/NOT RUN 传播。
- `backend/tests/system/wave2/http_adapter.py`：真实 HTTP 双身份、审批、IDOR、P20、上传/Manifest 驱动。
- `backend/tests/system/wave2/provision.py`：无公开创建 API 时的 test-only annotation task provisioner。
- `backend/tests/system/wave2/run_main_chain.py`：opt-in 真实网络 runner。
- `backend/tests/system/wave2/README.md`：运行条件与安全边界。
- `backend/tests/system/test_wave2_fixture_contracts.py`：13 个 fixture/OpenAPI/迁移/重复运行/恢复测试。
- `frontend/e2e/real-api/README.md`：注明 BE22 fixture 与浏览器 NOT RUN 边界。
- `backend/tests/system/BE22-REAL-API-REPORT.md`：本报告。

工作树中原有的 `ACCEPTANCE-MATRIX.md`、旧 system tests 和领域代码改动未由 BE22 修改或覆盖。

## 数据包证据

权威索引为 `backend/tests/system/wave2/data/catalog.json`。生成器的 side-effect-free `build_files()`
与工作树全部文件逐字节比较通过。

| 场景 | payload bytes | payload SHA-256 | Manifest 预期 |
| --- | ---: | --- | --- |
| legal | 4637 | `2519485c1c0d93c0e759be517bfbc90df43f8d293319edbd075e53c309f8d403` | ACCEPT |
| multi_camera | 6302 | `5164b78197a7357151e335548ff2468c9f989d6565e18bb19bed3663cf227a0b` | ACCEPT |
| missing_frame | 6250 | `120d9a3cfe3131d7eb8af7df997837f007a8a6e55c68f98067b70b1eb95718ae` | ACCEPT；条件为 MISSING_FRAME，不擅自固定 QC 公式 |
| missing_topic | 3313 | `b1610fe6fd8e9c17386dd1a3d69fb98900bf90cd34f5008f964970026c1bbd40` | ACCEPT_WITH_MISSING_TOPIC |
| bad_manifest | 4637 | 与 legal 共用不可变 Raw | REJECT / MANIFEST_INVALID |
| duplicate_package | 4637 | 与 legal 完全相同 | IDEMPOTENT_REPLAY |

MCAP SDK 检查证明多相机包 front/rear 各 30 帧；缺帧包 rear 为 29 帧、front 为 30 帧；缺 Topic
包没有 `/action`。每份 Manifest 同时校验 Raw size、SHA-256 和 CRC64。

## 隔离身份、scope 与清理

- `RunScope` 为每个 `run_id` 生成两名账号名、主项目、foreign project、region 及两个精确 Raw
  prefix；所有资源以 `be22-<run-id>-` 开头。
- 真实适配器先用正式注册 API 建立 admin/contractor 两个空账户，再走 membership/capability
  申请和审批；只有 `HC_ENVIRONMENT=test` 才能签发临时测试 JWT。
- contractor 只持 `uploader + annotator` 领域角色，admin 承担审批和 distinct reviewer；对 foreign
  project 的 collection task 查询必须返回 403。
- 清理必须同时满足 `HC_ENVIRONMENT=test`、`HC_WAVE2_CLEANUP_ENABLED=1` 和数据库名精确确认；
  target 还必须等于确定性的 BE22 namespace。数据库删除和两个对象 prefix 删除都会运行，某一项失败
  不阻止其余项；相同 `run_id` 重跑可恢复，第三次清理为零副作用。
- PostgreSQL append-only 测试数据通过 `SET LOCAL session_replication_role=replica` 在单个 test DB
  transaction 中清理；异常/回滚后自动恢复 trigger 状态。该能力禁止在 staging/production 使用。

## 阶段与 artifact

runner 固定记录以下阶段：

1. register empty accounts；
2. approve membership and capabilities；
3. cross-project identity denied；
4. create collection task；
5. upload and Manifest discovery；
6. Worker Manifest/QC/alignment；
7. annotation multi-level Tags；
8. distinct reviewer Tag approval；
9. close collection task。

每阶段只写 resource ID、`X-Request-ID`、可获得的审计 event ID、状态和安全错误摘要。递归脱敏测试
证明 `access_token`、session token、密码、Authorization 和签名 URL 不进入 artifact。任一阶段 FAIL
或 NOT RUN 后，后续阶段逐项记录 NOT RUN，finally 仍执行清理；不会用 API-only PASS 替代 browser。

## OpenAPI / 迁移证据

读取并解析当前 `backend/openapi.generated.yaml`：81 paths、176 schemas。定向合同测试验证注册、会话、
membership/capability、P20 create/close、Manifest preflight/upload、annotation revision/submit/review
路径存在。

明确缺口：

- `/api/v1/projects/{project_id}/annotation-tasks` 只有 GET，没有 task create；
- 81 个 paths 中没有 start-ingest/ingest-workflow 命令；
- 因此上传后不能仅凭公开 HTTP 合同保证 Worker 被启动，也不能仅凭公开 HTTP 合同创建标注任务。

读取的实际持久化证据：

- `security/002_access_control.sql`：accounts、sessions、membership/capability request/grant、audit；
- `collection_tasks/0001_collection_tasks.sql`：P20 ACTIVE/CLOSED、scope、关闭后拒绝新包；
- `ingest/001_ingest.sql` + `002_package_manifest_uploads.sql`：project/region scope、package identity、
  manifest discovery、重复包唯一键；
- `annotation/0001_annotation.sql` + `0002_tag_schema_revisions.sql`：不可变 revision/review、Tag Schema、
  submission 和 RLS。

BE22 没有新增或修改迁移，也没有修改聚合 OpenAPI。

## 测试命令与结果

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 backend/.venv/bin/pytest -q \
  --import-mode=importlib -p pytest_asyncio.plugin \
  backend/tests/system/test_wave2_fixture_contracts.py
=> 13 passed in 0.97s

backend/.venv/bin/ruff check \
  backend/tests/system/wave2 backend/tests/system/test_wave2_fixture_contracts.py
=> All checks passed

cd backend && .venv/bin/mypy --explicit-package-bases \
  tests/system/wave2 tests/system/test_wave2_fixture_contracts.py
=> Success: no issues found in 9 source files

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 backend/.venv/bin/pytest -q \
  --import-mode=importlib -p pytest_asyncio.plugin backend/tests/system
=> 52 passed, 1 skipped, 1 xfailed in 19.64s

pnpm --dir frontend exec playwright test --config playwright.real-api.config.ts
=> exit 1; 1 skipped; reporter: Real API release gate blocked
```

非 PASS 明细：

- SKIPPED：`test_minio_network_recovery.py` 缺 MinIO variables 和 `HC_MINIO_TEST_CONTAINER`；
- XFAIL：BE12-008 尚无 5 TB/day + 30% 全链路容量通过证据。
- NOT RUN：完整 browser main-chain spec 被 skip，定制 reporter 正确返回退出码 1。

## 仍未验证项与风险

- 未执行真实网络 runner，未产生真实 resource/request/audit ID artifact；当前状态 NOT RUN。
- 未证明真实 API 上传会自动启动 Worker；公开启动合同缺失。
- 未证明 Worker 成功后自动创建 annotation task；公开创建合同缺失。BE22 provisioner 会在 test DB 中
  绑定真实 rollout、dataset/Lance version、base step count 和已发布三级 Tag Schema，但尚未真实运行。
- access capability key 与旧领域 `roles` 尚未统一。test profile issuer 只在审批成功后映射到 legacy role，不能作为
  生产授权实现证据。
- 领域审计没有统一的公开查询路径；当前只能从 access audit API 获得 access event IDs。P20/ingest/
  annotation 每阶段 audit ID 仍需正式可查询事实源。
- 没有执行两次真实 Postgres/MinIO/Temporal 网络主链；当前“两次运行无副作用”PASS 只覆盖 runner/
  fixture/cleanup 可执行合同，不冒充外部集成 PASS。
- 完整浏览器 E2E 保持 NOT RUN，依赖 FE 第二拨完成后由 BE24/FE10 执行。

## 需要 BE24 合并的最小共享变更清单

BE22 未直接修改以下共享文件。BE24 集成时需要决定并串行处理：

1. 在一次性 test Compose 中提供真实 API service/gateway，明确 `HC_ENVIRONMENT=test`，API 与 runner
   共享临时 HS256 key；生产配置不得出现该 key 或 test issuer。
2. 为 test runner 提供可写 artifact mount、`HC_TEST_POSTGRES_DSN` 与隔离 MinIO bucket；保留三重
   cleanup guard，不能把清理开关带入 staging/production。
3. 提供正式事件触发或 test-profile ingest Worker launcher；不能把相似 `/jobs` 查询接口当启动接口。
4. 真实栈调用 BE22 的非 HTTP test-profile annotation provisioner，或提供正式自动创建；必须绑定
   rollout、dataset/Lance version、base step count 和已发布 Tag Schema，不得向生产暴露任意 seed API。
5. capability→领域 permission/role 的正式映射必须由 security/domain owner 闭合；BE22 test JWT 映射
   不能进入生产。
6. FE 第二拨完成后再运行共享 Playwright 配置；更新 `ACCEPTANCE-MATRIX.md` 时保持当前 browser
   `NOT RUN`，直到真实页面网络 trace 通过。
