# 集成收口记录

日期：2026-08-11  
状态：收口完成

## 结论

- 前端 P01–P19 六件套齐全；TypeScript、Vitest、ESLint、生产构建和 Chromium 主流程全部通过。
- 后端运行时 OpenAPI 与当前 8 份公开合同精确一致，共 256 个唯一 operation ID；storage 22/22，robotics 110/110。
- Ruff 已纳入固定 dev 依赖并可直接运行。
- 7 个并行 Alembic head 已在全部域完成后合并为唯一 `merge_0001`。
- 三角色 capability 集合、未知 capability fail-closed、统一 Query Key、统一错误 envelope，以及 ManualIssue/ReviewFinding 隔离均已复验。

## 规格快照差异

- `/home/czy/plan/codex-goal-dispatch.md` 记录 robotics 为 78 个 operation；现行 robotics OpenAPI 实际包含 110 个唯一 operation。实现与测试以现行 OpenAPI 为事实源，运行时为 110/110。
- datasets 的较早快照为 28 个 operation；现行 OpenAPI 为 29 个，新增的 `authorizeExportDownload` 已实现。
- annotation 有 3 个仅供域间编排的内部路由。它们保留运行能力但通过 `include_in_schema=False` 排除在公开 OpenAPI 外，公开合同保持 12/12。

## 改动清单与原因

### 后端工具链与一致性

- `backend/pyproject.toml`、`backend/uv.lock`：把 Ruff 0.16.2 纳入 dev dependency，消除“有配置但命令不可运行”的环境缺口。
- `backend/app/**/*.py`、`backend/alembic/**/*.py`、`backend/tests/**/*.py`：按仓库 Ruff 配置执行 import、现代语法和格式规范化；未改变领域合同。
- `backend/app/domains/annotation/router.py`：将 3 个内部集成 operation 从公开 schema 隐藏，避免运行时 OpenAPI 泄漏非公开合同。
- `backend/app/domains/datasets/router.py`：补齐 `authorizeExportDownload`，只允许已完成导出生成短期 `no-store` 授权，并保持作用域与幂等约束。
- `backend/app/domains/platform/__init__.py`、`backend/app/domains/platform/router.py`：补齐统一 `getAsyncJob` 投影，执行作用域、派生 capability、ETag/304 和 Retry-After 检查。
- `backend/tests/core/test_runtime_operation_alignment.py`：新增运行时/规格 operation 精确集合、异步 Job 作用域与导出授权集成测试。

### 后端 storage

- `backend/app/domains/storage/{models.py,schemas.py,repository.py,service.py,router.py,__init__.py}`：完成 inventory、capacity/cost、reference/protection、multipart、lifecycle policy/simulation/execution/restore 全部 22 个 operation；统一作用域、并发、幂等、半开区间与 no-store 语义。
- `backend/alembic/versions/storage_0001_storage_lifecycle.py`：增加 storage/lifecycle 持久化事实迁移。
- `backend/tests/storage/test_storage_operations.py`：覆盖 22/22 operation 和关键状态门槛。

### 后端 robotics / calibration / schema

- `backend/app/domains/robotics/router.py`、`robots/router.py`、`calibrations/router.py`、`schemas/router.py`：组合实现现行合同全部 110 个 operation。
- `backend/app/domains/robotics/{models/__init__.py,repository.py,service.py,__init__.py}`：补齐模型资产、机器人/组件、标定、Schema、验证报告、异步 Job 与 Dataset Schema Snapshot 的状态和持久化逻辑。
- `backend/app/domains/robotics/service.py`：使完成事件与 OpenAPI `x-audit-events` 精确一致，并收紧 capability、样本窗口、时间区间和 canonical hash 检查。
- `backend/alembic/versions/robotics_0001_calibration_schema.py`：增加 robotics/calibration/schema 持久化事实迁移。
- `backend/tests/robotics/test_robotics_operations.py`：覆盖三组路由的主流程。
- `backend/tests/robotics/test_robotics_contracts.py`：锁定 110/110 operation、capability/registry、幂等与审计事件、禁止跨域导入、半开区间和 canonical hash。

### 后端迁移与状态

- `backend/alembic/versions/merge_0001_domain_heads.py`：在所有域完成后无模式变更地合并 access、annotation、cleaning、datasets、ingest、robotics、storage 七个 head。
- `backend/docs/status/T10.md`：把 storage 0/22、robotics 0/78 的过期状态更新为 22/22 与现行 110/110，并记录最终门禁。

### 前端 Playwright、Mock 与代码质量

- `frontend/public/mockServiceWorker.js`：从当前 MSW 包提供同版本 worker，使 browser Mock 能以正确 MIME 注册。
- `frontend/playwright.config.ts`：webServer 改为本地 Vite shim，冻结 API/SSE/Mock/build/release 环境；将 loopback 加入 `NO_PROXY/no_proxy`，防止代理的 400 响应误判 Vite 已就绪。
- `frontend/.eslintignore`：排除 MSW 官方生成 worker，避免对第三方生成文件报 lint 错误。
- `frontend/src/mocks/handlers/ingest.handlers.ts`：修正 MSW action 路径中的冒号转义。
- `frontend/src/features/{lifecycle,robot-models,robots,calibrations,data-schemas,access}/api/index.ts`：移除重复 `/api/v1` 前缀，所有请求统一相对冻结的 API base URL。
- `frontend/src/app/providers/{ScopeProvider.tsx,ToastProvider.tsx}`：对 provider 文件中有意导出的 hook 添加局部 Fast Refresh 说明，不放宽全局 ESLint。
- `frontend/src/pages/p13-storage-lifecycle/query-codec.ts`：用不可变字段覆盖清理互斥游标，保持 canonical 往返并消除未使用变量。
- `frontend/tests/contracts/viewer.spec.tsx`：修正 fake timer 的异步 `act`，消除测试竞态和 lint 问题。
- `frontend/tests/e2e/p04-upload-detail.spec.ts`：在授权场景中验证 `latest/current` 稳定 ID fail-closed，避免 capability guard 掩盖页面合同。
- `frontend/tests/e2e/p02-*.spec.ts-snapshots/`、`p03-*.spec.ts-snapshots/`、`p04-*.spec.ts-snapshots/`：补齐 390/768/1024/1440 四档 Chromium 基线。

### 前端 P13–P18 六件套

- `frontend/src/pages/p13-storage-lifecycle/` 至 `p18-access/`：六页均具备 `routes.tsx`、`query-codec.ts`、页面和样式。
- `frontend/src/features/{lifecycle,robot-models,robots,calibrations,data-schemas,access}/`：补齐领域 API、状态机/规则、route builder、约束与 capability catalog；所有 Query Key 走 `makeQueryKey`。
- `frontend/src/mocks/fixtures/management/index.ts`、`src/mocks/scenarios/management.ts`、`src/mocks/handlers/{lifecycle,robotics,access}.handlers.ts`：增加冻结 fixture、场景和 handler。
- `frontend/tests/contracts/{lifecycle,robotics,access}.spec.ts`：覆盖 wire schema、codec 往返与三角色 capability 精确集合。
- `frontend/tests/e2e/p13-storage-lifecycle.spec.ts` 至 `p18-access.spec.ts`：增加六页 Chromium 主流程。
- `frontend/docs/status/T7.md`：落地 P13–P18 六件套矩阵、合同边界和验证结果。

## 运行时 operation 对齐

| 域 | 现行规格 | 运行时 | 结果 |
| --- | ---: | ---: | --- |
| ingest | 29 | 29 | 精确一致 |
| datasets | 29 | 29 | 精确一致 |
| cleaning | 27 | 27 | 精确一致 |
| storage | 22 | 22 | 精确一致 |
| robotics/calibration/schema | 110 | 110 | 精确一致 |
| access/audit | 26 | 26 | 精确一致 |
| platform async job | 1 | 1 | 精确一致 |
| annotation | 12 | 12 | 精确一致 |
| **总计** | **256** | **256** | **无缺失、无多余、无重复** |

## Capability 与共享合同复验

- canonical capability：76/76。
- reserved capability：18/18。
- `PROJECT_DEVELOPER` ceiling：34/34。
- `PROJECT_DATA_PROCESSOR` ceiling：19/19。
- 未知 capability 在前后端均 fail-closed。
- ManualIssue 与 ReviewFinding 实体互不 import；ReviewFinding 在清洗链路始终只读。
- 前端所有领域 Query Key 通过统一五元组 `makeQueryKey` 构造。
- 错误 envelope、作用域校验、幂等键与条件写语义由共享合同测试覆盖。

## 最终验证

### 前端

- `./node_modules/.bin/tsc -b --pretty false`：通过，0 error。
- `./node_modules/.bin/vitest run`：23 files，114 passed，0 failed。
- `./node_modules/.bin/eslint . --max-warnings=0`：通过，0 error / 0 warning。
- `./node_modules/.bin/vite build`：通过。
- `./node_modules/.bin/playwright test --project=chromium`：38 passed，0 failed；由 Playwright 自行启动冻结环境的 Vite。

### 后端

- `env -u PYTHONPATH -u AMENT_PREFIX_PATH uv run ruff check .`：通过。
- `env -u PYTHONPATH -u AMENT_PREFIX_PATH uv run pytest -q`：46 passed，0 failed。
- 运行时 OpenAPI：256 operations，256 unique，0 duplicate。
- `env -u PYTHONPATH -u AMENT_PREFIX_PATH uv run alembic heads`：仅 `merge_0001`。
- `uv run alembic upgrade head --sql`：PostgreSQL 离线升级脚本完整生成；107 个表和 77 个索引无重复名称。当前工作机未提供可连接的 PostgreSQL 实例，因此未对外部数据库执行在线迁移。

## 操作约束确认

- 未修改 `/home/czy/plan` 下任何文件。
- 未执行 `git add`、`git commit` 或 `git push`。

---

## 专家修复终端复验增补（2026-08-11 11:07:26 +0800）

本节保留上方既有集成记录，并以当前工作区实跑结果更新最终证据。

- 后端全仓现为 `61 passed`，Ruff 全仓通过；新增 Annotation、Cleaning、
  Dataset 三域 68 个公开 operation 的完整 happy path、失败矩阵和 frozen set
  测试，以及 SQLite migration、ID prefix、role ceiling 与 tag 分布回归。
- 空 SQLite 数据库已经真实完成 `upgrade heads -> merge_0001 -> downgrade base`；
  Access、Robotics、Storage 均明确依赖 `foundation_0001`，Annotation/Cleaning 的
  命名 schema 在 SQLite 映射为默认 schema。
- 运行时 OpenAPI 仍为 256 / 256 unique，0 untagged；Dataset、Cleaning、
  Annotation 分别为 29 / 27 / 12。
- 补齐了三个运行时集成缺口：Dataset Review Return 注入 successor Draft 适配器；
  Cleaning 注入 scope-safe DatasetVersion 适配器；所有 `new_id` 字面量 prefix 已登记。
- 前端 TypeScript、114 个 Vitest、ESLint、Vite build 和 Chromium 38 个 E2E 均通过。
- Playwright Mobile/WebKit 仍为环境阻塞：宿主缺少 WebKitGTK/GTK4/GStreamer 等
  动态库，38 个 Mobile case 均在浏览器启动前失败，不属于业务断言失败，也未记录为通过。
- Annotation Submit 的 ManualIssue 权威 gate 仍是上游条件依赖；生产实现按规格
  fail-closed，不在本地根据 severity 猜测 policy。

完整逐项状态、改动文件、命令和阻塞原因见 `docs/INTEGRATION-ISSUES.md` 的
“专家修复终端处理记录”。未修改 `/home/czy/plan`，未执行提交或推送。

### 监听增量修正（2026-08-11 11:17:19 +0800）

台账复检新增的 fail-closed 测试进一步暴露了 HTTP 状态偏差：冻结 Annotation
OpenAPI 为 `ISSUE_GATE_UNAVAILABLE` 声明 503，但代码与测试使用 500；Preflight
也把权威门禁不可用包装成 200。现已增加共享 `ServiceUnavailableError`，Preflight
和最终 Submit 均返回 retryable 503，且不可用 Preflight 不落 advisory 记录。全仓
Ruff 通过，后端当前为 `62 passed`。

### 幂等恢复增量修正（2026-08-11 11:28:13 +0800）

NEW-8 的 503 复检发现共享幂等 claim 在 SQLite SAVEPOINT 下可能越过外层回滚。
现已为 aiosqlite 配置显式外层事务，并在 `with_idempotency` 中实现 request hash
校验、`FAILED_RETRYABLE`/过期 lease 接管和带 `Retry-After` 的真实并发 409。
Annotation 回归证明同 key 的 503→依赖恢复可成功重试、并发请求收到 409 +
`Retry-After`、最终 Submit 再次失去 gate 时仍为 503 且零业务写入。Ruff 全仓通过，
后端当前为 `64 passed`。

### 跨资源幂等身份增量修正（2026-08-11 11:40:59 +0800）

NEW-8-R2 证明仅使用 actor/scope/operation/body 会让相同 key/body 在不同资源路径间
错误重放。Annotation、Cleaning 与 Dataset 的集中写包装器现均把稳定路径资源身份
加入幂等 scope；create operation 仍只使用客户端已知的请求身份，不把服务端新生成
ID 错误加入首次身份。跨 Task、Draft、Version 三条 HTTP 回归通过，全仓 Ruff 通过，
后端当前为 `67 passed`。

### Router 直调幂等身份增量修正（2026-08-11 11:49:13 +0800）

NEW-8-R3 发现 Cleaning/Dataset router 绕过集中 service method、直接调用 `_write()`
的路径仍缺资源身份。现已补齐 Issue resolve、Draft lease、Export download、Version
diff/manifest/download、Dataset/Version deletion 的稳定 path 身份，并增加 AST 门禁，
锁定 23 个资源型集中写调用必须显式声明 `idempotency_resource_id`。Cleaning 跨
Draft lease HTTP 回归通过，全仓 Ruff 通过，后端当前为 `68 passed`。

### Mobile/WebKit 环境恢复与前端回归收口（2026-08-11 12:48:22 +0800）

- 另一终端补齐 WebKitGTK/GTK4/libsoup 等宿主依赖后，原 ENV-1 已关闭；本终端使用
  `/tmp` 独立输出目录复验，最终 Chromium + Mobile 全量为 `76 passed (1.1m)`。
- 收紧 Dataset/Ingest 的共享 Job Mock 资源归属，P02 在稳定 polling 状态截图；修复
  Dataset handler 误接管 Ingest Job 和四档截图跨越 SSE 重连计时器的问题。
- P12 对象详情关闭按钮打开时立即聚焦，Escape 由 dialog 同步处理，消除 WebKit 下
  effect listener 安装竞态；双浏览器并发重复三轮 `12 passed`。
- P12/P19 合同失败提示在 12 workers 压力下允许 15 秒应用启动边界，仍保留严格
  fail-closed 与敏感字段不泄漏断言；两页双浏览器重复三轮 `30 passed`。
- WebKit 依赖带来的 CJK 字体修复使旧 Chromium P02/P03/P04 缺字基线失效。逐图确认
  只有字体度量/换行差异后重建相关 Chromium 基线；新增 Mobile 基线均由最终全量
  回归验证。
- 前端类型检查与定向 ESLint 均通过。后端证据保持 Ruff 全仓通过、`68 passed`。
  当前只剩 Annotation ManualIssue 权威 gate 的外部合同/policy/watermark 阻塞。

### 独立复检命令勘误（2026-08-11 12:57:31 +0800）

独立复检记录已确认 ENV-1 与 NEW-9～NEW-11 关闭，但其中三条定向命令把当前
Playwright project `mobile` 写成了不存在的 `webkit`。字面命令已验证会 exit=1，
`--project=mobile --list` 可正确列出测试；台账已追加勘误。此前使用现行 `mobile`
project 实跑得到的 4/12/30 passed 及全量 76 passed 证据不变。

独立复检随后已用勘误后的 `--project=chromium --project=mobile` 逐字重跑：P02
`4 passed`、P12 重复三轮 `12 passed`、P12/P19 高并发重复三轮 `30 passed`；
NEW-12 保持关闭，未发现新回归。
