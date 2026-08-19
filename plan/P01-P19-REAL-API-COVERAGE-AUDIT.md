# P01–P19 真实后端 API 覆盖审计

> 审计日期：2026-08-17  
> 审计范围：当前工作树中的 README、`plan/`、前端运行时代码与 Mock、后端 FastAPI/OpenAPI/Service/Repository/迁移、Temporal Worker、测试与部署文件。  
> 结论口径：页面能渲染、MSW 能返回数据、存在同名后端资源，均不等于页面已经接入真实 API。

## 1. 结论摘要

当前没有任何一个 P01–P19 页面达到 A、B 或 C。等级统计为：**A=0、B=0、C=0、D=9、E=10**。

- P01–P19 在“页面级真实 API”口径下全部仍是 Browser Mock-only；其中 P03、P04、P05、P06、P07、P08 有后端底层能力，但前后端路径、请求体、响应体或状态语义不兼容，不能计为真实链路。
- P01 已复验：`VITE_MOCK_MODE=off` 时显示“工作台聚合能力尚未开放”，四个 dashboard 请求均为 0；`browser` 时才命中四个 MSW handler。
- 更早的全局阻断是：真实模式没有实现 principal/token/scope/capability bootstrap。P02–P19 在正常启动路径中先被 `RouteCapabilityGuard` 拦截，不会发出领域请求。
- 后端真实完成的是摄取、验证、质量、对齐、Lance 目录、预览、标注/复核、发布/导出、任务、审计写入和 Outbox 等底层数据管线，不是 P01–P19 的页面查询合同。
- 运行时 OpenAPI 与 `backend/openapi.generated.yaml` 都只有 44 条路径，四条 dashboard 路径均不存在。前端生成类型来自仓库外的 OpenAPI 草案目录，且当前运行时客户端没有引用生成类型。
- 后端当前测试结果良好，但只证明已有底层能力：283 passed、4 skipped、1 xfailed。它不能证明 P01–P19 的页面 API 或 E2E 已完成。
- 当前可支持“后端本地开发 + 前端 Browser Mock 演示”；不能支持 P01–P19 真实 API 联调、当前试点验收或生产发布。

## 2. 审计口径与等级

| 等级 | 本审计采用的判定条件 |
|---|---|
| A | 正式 OpenAPI、真实 Router→Service→Repository/Worker→DB 链路、前端真实接入和可靠集成/E2E 测试均存在，可进入验收。 |
| B | 核心页面真实链路可用，仅次要能力、边界条件或测试未完成。 |
| C | 至少有一个页面核心用户操作可以通过当前前端成功调用兼容的真实后端；只有相似底层接口不算。 |
| D | 页面和 Mock 合同较明确，但页面主要依赖 Mock，真实页面接口未实现或未接入。 |
| E | 除真实 API 缺失外，资源模型、指标公式、权限或状态机仍有会改变正式合同的产品决策。 |

因此，底层 ingest/annotation/Lance API 虽真实存在，但没有任何页面被抬高到 C：当前真实模式没有完成鉴权引导，且页面 wire contract 与后端合同不兼容。

## 3. P01–P19 完成度矩阵

“测试状态”只统计当前仓库可执行的测试，不沿用已经从当前源码归档中移除的历史结果。

| 页面编号 | 页面名称 | 前端路由 | UI 状态 | Mock 状态 | 真实 API 状态 | 后端接口状态 | 数据库支持 | Worker 支持 | 测试状态 | 当前降级行为 | 产品设计缺口 | 技术实现缺口 | 风险 | 完成等级 | 证据 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P01 | 工作台 | `/dashboard` | 完成（Mock UI） | 完整：4 个聚合 handler | 未接入；off 模式明确禁用 4 个 query | 不存在；OpenAPI/Router/Service/Repository/测试均无 dashboard | 部分：有 ingest/workflow/quality/annotation/publishing 事实表，但无完整聚合事实 | 初版同步查询可不需要；若采用滚动聚合则缺失 | 无当前页面/真实集成/E2E；本次仅做双模式运行复验 | off 显示“工作台聚合能力尚未开放”，0 个 dashboard 请求 | 公式、范围、待办分类、活动语义、权限、时区、实时性 | 正式合同、聚合查询、缺失事实模型、权限、缓存/一致性、前端切换 | 误把跨域异步状态拼成同一时点；高基数聚合和越权汇总 | **E** | `frontend/src/pages/p01-dashboard/page.tsx:158-210`; `frontend/src/features/dashboard/api/client.ts:20-89`; `frontend/src/mocks/handlers/dashboard.handlers.ts:39-73`; `backend/openapi.generated.yaml`（无 dashboard 路径） |
| P02 | 数据源 | `/ingest/sources` | 完成（Mock UI） | 完整：列表/详情/写操作草案 | 未接入 | 不存在页面接口 | 未设计数据源/凭据/连接器模型 | 不需要常驻 Worker；连接测试是否异步待设计 | 仅 Mock handler 测试覆盖部分契约；无真实集成/E2E | off 模式先被能力门禁阻断，不发领域请求 | 数据源类型、凭据托管、所有权、连通性检查与删除规则 | 数据模型、密钥引用、CRUD/测试连接 API、审计 | 凭据泄漏、SSRF、连接器供应链风险 | **D** | `frontend/src/app/router/index.tsx:97-117`; `frontend/src/features/ingest/api/client.ts:54-99`; `frontend/src/mocks/handlers/ingest.handlers.ts`; `backend/openapi/ingest.yaml`（无 data-sources） |
| P03 | 上传任务 | `/ingest/uploads` | 完成（Mock UI） | 完整：列表/选项/创建/命令草案 | 未接入；同名资源不兼容 | 部分：后端有 upload-session 创建/命令，但没有页面列表/creation-options，创建 wire contract 不同 | 部分：`upload_sessions`、对象、分片、rollout 已有；缺页面投影/筛选 | 已支持真实摄取工作流 | Mock handler 有局部单测；后端 ingest 单测/系统测试存在；无前后端集成/E2E | off 模式先被能力门禁阻断 | 创建参数、去重范围、失败/暂停/取消规则、列表可见范围 | 统一创建合同；页面列表投影、游标/筛选；命令状态映射 | 重复上传、状态竞态、超大列表、前后端同名假兼容 | **D** | `frontend/src/features/ingest/api/client.ts:54-166`; `backend/src/hc_data_platform/ingest/router.py:79-229`; `backend/openapi/ingest.yaml:1-63`; `backend/migrations/ingest/001_ingest.sql:38-126` |
| P04 | 上传详情 | `/ingest/uploads/:uploadId` | 完成（Mock UI） | 完整：bootstrap/objects/verification/events/retry 草案 | 未接入 | 部分：后端有 session detail、parts、命令；无页面 bootstrap、对象页、验证运行和事件合同 | 部分：session/object/part/quality/workflow 数据分散，无统一详情投影 | 摄取、验证和对齐 Worker 已支持底层执行 | 后端模块测试存在；无页面真实集成/E2E | off 模式先被能力门禁阻断 | 进度口径、事件范围、可重试阶段、对象失败展示 | 聚合详情投影、对象分页、事件源、重试命令与错误映射 | 进度回退、事件乱序、轮询压力、误重试 | **D** | `frontend/src/features/ingest/api/client.ts:54-140`; `backend/src/hc_data_platform/ingest/router.py:110-229`; `backend/migrations/quality/0001_quality.sql:15-54`; `backend/src/hc_data_platform/workflow/temporal_workflows.py:547-555` |
| P05 | 数据集列表 | `/datasets` | 完成（Mock UI） | 完整：列表/能力/摘要/facets/创建草案 | 未接入 | 部分：Lance catalog 有底层 dataset/version API；没有页面列表合同 | 部分：catalog datasets/versions 已有；缺产品级项目/区域、摘要/facet 投影 | 底层 writer/reconciliation 支持；列表本身不需要 | Mock dataset handler 有局部单测；后端 catalog 测试存在；无真实集成/E2E | off 模式先被能力门禁阻断 | 数据集身份、所有权、创建来源、可见性、归档规则 | 页面查询/创建 API、facet 聚合、权限过滤、合同适配 | 大数据量筛选、名称唯一性、跨区域可见性 | **D** | `frontend/src/features/datasets/api/index.ts`; `frontend/src/features/datasets/api/wire-schemas.ts`; `frontend/src/mocks/handlers/datasets.handlers.ts`; `backend/migrations/lance_catalog/0001_lance_catalog.sql:15-54`; `backend/openapi/lance-catalog.yaml` |
| P06 | 数据集详情 | `/datasets/:datasetId`（另有 viewer 子路径） | 完成（Mock UI） | 完整：bootstrap/versions/episodes/schema/source/capacity 草案 | 未接入 | 部分：Lance/preview 有底层能力；无页面 bootstrap、episode 列表、容量合同 | 部分：dataset/version/lineage 有；无 episode 产品投影和完整容量账本 | writer/preview 已支持底层执行 | viewer core 有组件单测；后端底层测试；无页面真实集成/E2E | off 模式先被能力门禁阻断 | episode 定义、版本可见性、容量口径、来源关系 | 详情 BFF/投影、episode 索引、容量统计、预览授权 | 高基数 episode、Lance 与 Postgres 一致性、预览越权 | **D** | `frontend/src/features/datasets/api/index.ts`; `frontend/src/features/datasets/api/queries.ts`; `frontend/src/features/viewer/EpisodeWorkbenchCore.test.tsx`; `backend/migrations/lance_catalog/0001_lance_catalog.sql:31-74`; `backend/openapi/preview.yaml` |
| P07 | 版本详情与审批 | `/datasets/:datasetId/versions/:versionId` | 完成（Mock UI） | 完整：manifest/schema/storage/inventory/checks/命令/diff 草案 | 未接入 | 部分：后端有版本、发布和 reconciliation 底层 API；页面审批合同不存在 | 部分：catalog/publishing facts 已有；缺页面检查/审批状态投影 | publish/export/reconciliation 已支持底层执行 | 后端 publishing/catalog 测试存在；无页面真实集成/E2E | off 模式先被能力门禁阻断 | 审批人、检查项、退回原因、版本状态机、diff 语义 | 统一状态机、审批命令、检查投影、幂等与并发版本 | 双重审批、状态漂移、发布后不可逆数据 | **D** | `frontend/src/features/datasets/api/index.ts`; `frontend/src/features/datasets/review-state-machine.ts`; `backend/openapi/publishing.yaml`; `backend/migrations/publishing/0001_publishing.sql:4-71`; `backend/src/hc_data_platform/workflow/temporal_workflows.py:547-555` |
| P08 | 标注任务 | `/annotations`、`/annotations/tasks/:taskId` | 完成（Mock UI） | 完整：列表/详情/命令草案 | 未接入；前后端路径和命令风格不兼容 | 部分：真实 annotation Router/Service/Repository 存在，但项目/区域范围、路径和响应不同 | 部分：task/revision/operation/review/mutation 已有；缺前端合同所需投影 | 同步标注为主；预览由 Worker 支持 | 后端 annotation 测试存在，viewer 有组件单测；无前后端集成/E2E | off 模式先被能力门禁阻断 | 任务分配、复核角色、锁、超时、区域范围 | 选择统一合同、补 scope/投影、命令幂等/并发控制 | 丢失更新、复核越权、操作顺序不一致 | **D** | `frontend/src/features/annotation/api/client.ts`; `backend/openapi/annotation.yaml`; `backend/migrations/annotation/0001_annotation.sql:6-95`; `backend/src/hc_data_platform/annotation/router.py` |
| P09 | 人工问题 | `/manual/issues` | 完成（Mock UI） | 完整：列表/详情/命令草案 | 未接入 | 页面接口不存在；annotation 的 EXCLUDE/RESTORE 不是独立 issue 模型 | 未设计 issue、证据、指派、状态历史 | 缺专用异步能力；是否需要取决于规则 | 只有 Mock；无真实单测/集成/E2E | off 模式先被能力门禁阻断 | 问题分类、来源、严重度、指派、SLA、关闭/重开规则 | issue 模型、状态机、查询/命令、审计、与 annotation 关系 | 问题重复、错误关闭、SLA/权限争议 | **E** | `frontend/src/features/cleaning/api/manual-issues.adapter.ts`; `frontend/src/features/cleaning/api/manual-issues.schemas.ts`; `frontend/src/mocks/handlers/cleaning.handlers.ts`; `plan/BACKEND-DATA-PIPELINE-IMPLEMENTATION-PLAN.md:339-344`; `backend/migrations/annotation/0001_annotation.sql:50-67` |
| P10 | 清洗草稿 | `/manual/drafts` | 完成（Mock UI） | 完整：列表/创建/命令草案 | 未接入 | 页面接口不存在 | 未设计 draft、变更集、基线、锁、提交记录 | 缺失；是否异步生成待产品/技术设计 | 只有 Mock；无真实单测/集成/E2E | off 模式先被能力门禁阻断 | 草稿来源、所有权、协作、失效、提交/放弃状态机 | draft/changeset 模型、乐观锁、命令 API、审计 | 并发覆盖、基线漂移、不可重放变更 | **E** | `frontend/src/features/cleaning/api/cleaning-drafts.adapter.ts`; `frontend/src/features/cleaning/api/cleaning-drafts.schemas.ts`; `frontend/src/mocks/handlers/cleaning.handlers.ts`; `plan/BACKEND-DATA-PIPELINE-IMPLEMENTATION-PLAN.md:339-344` |
| P11 | 清洗草稿详情 | `/manual/drafts/:draftId` | 完成（Mock UI） | 完整：bootstrap/items/preview/commit 草案 | 未接入 | 页面接口不存在；现有 preview 不是 EDL/草稿提交合同 | 未设计 EDL/草稿 item/commit；Lance revision 不能直接替代 | preview 底层存在，草稿生成/应用/回滚 Worker 缺失 | viewer core 有组件单测；无草稿真实单测/集成/E2E | off 模式先被能力门禁阻断 | EDL 操作语义、冲突处理、预览一致性、提交和回滚规则 | 可重放变更格式、预览隔离、原子提交、补偿工作流 | 数据损坏、半提交、预览与结果不一致 | **E** | `frontend/src/features/cleaning/api/edl.adapter.ts`; `frontend/src/features/cleaning/api/edl.schemas.ts`; `backend/openapi/preview.yaml`; `plan/BACKEND-DATA-PIPELINE-IMPLEMENTATION-PLAN.md:339-344` |
| P12 | 存储概览 | `/storage/overview` | 完成（当前为概览/对象/multipart 三个只读 Tab） | 完整覆盖当前三 Tab；cost client/handler 仍在但 UI 已移除 | 未接入 | 不存在页面接口 | 部分：rollout objects、publication assets 有大小；无统一对象库存、存储级别历史和可靠计费事实 | 当前缺库存聚合；初版查询或定时采集待设计 | 只有 Mock 页面数据；无真实集成/E2E | off 模式先被能力门禁阻断 | 存储角色、去重、更新时间，以及成本是否仍属 P12 范围 | inventory fact、对象分页、聚合查询/采集；清理或隔离孤立 cost 草案 | 全桶扫描、重复计量、敏感对象路径、残留合同误用 | **D** | `frontend/src/pages/p12-storage-overview/page.tsx:1-21,170-331`; `frontend/src/features/storage-overview/api/client.ts:56-66`; `frontend/src/mocks/handlers/storage-overview.handlers.ts:77-82`; `backend/migrations/ingest/001_ingest.sql:67-120`; `backend/migrations/publishing/0001_publishing.sql:15-71` |
| P13 | 生命周期 | `/storage/lifecycle` | 部分：页面骨架/只读状态为主 | 部分：仅 lifecycle-page 主读取 | 未接入 | 不存在 | 未设计 policy、simulation、restore、execution ledger | 缺 simulation/execution/restore Worker | 只有局部 Mock；无真实单测/集成/E2E | off 模式先被能力门禁阻断 | 规则优先级、保留期、legal hold、审批、恢复 SLA | 策略模型、仿真、执行账本、幂等批处理、恢复工作流 | 误删、合规违规、批量任务失控、恢复失败 | **E** | `frontend/src/features/lifecycle/api/index.ts`; `frontend/src/features/lifecycle/state-machines.ts`; `frontend/src/mocks/handlers/lifecycle.handlers.ts`; `plan/BACKEND-DATA-PIPELINE-IMPLEMENTATION-PLAN.md:339-344` |
| P14 | 机器人型号 | `/settings/robot-models` | 部分：列表为主 | 部分：仅列表 | 未接入 | 不存在 | 未设计 model/version/asset/compatibility | 缺资产校验/发布 Worker | 只有 Mock；无真实单测/集成/E2E | off 模式先被能力门禁阻断 | 型号版本、资产格式、兼容矩阵、发布/废弃规则 | 模型/版本/资产存储、校验、发布 API/Worker | 不兼容资产、供应链、超大文件 | **E** | `frontend/src/features/robot-models/api/index.ts`; `frontend/src/mocks/handlers/robotics.handlers.ts`; `plan/BACKEND-DATA-PIPELINE-IMPLEMENTATION-PLAN.md:339-344` |
| P15 | 机器人 | `/settings/robots` | 部分：列表为主 | 部分：仅列表/组件草案 | 未接入 | 不存在 | 仅 ingest 中有非规范化 `robot_id`；无 robot/component/model 目录 | 目录 CRUD 不需要；远程发现是否需要待确认 | 只有 Mock；无真实单测/集成/E2E | off 模式先被能力门禁阻断 | 唯一标识、组件拓扑、型号绑定、区域迁移、停用规则 | 规范 robot/component 模型、引用完整性、CRUD/导入 API | 身份冲突、历史数据断链、区域越权 | **E** | `frontend/src/features/robots/api/index.ts`; `frontend/src/features/robots/constraints.ts`; `backend/migrations/ingest/001_ingest.sql:3-36`; `plan/BACKEND-DATA-PIPELINE-IMPLEMENTATION-PLAN.md:339-344` |
| P16 | 标定集 | `/settings/calibrations` | 部分：列表为主 | 部分：仅列表 | 未接入 | 不存在 | 未设计 calibration/version/validity/attachment | 缺解析、校验、兼容检查/发布 Worker | 只有 Mock；无真实单测/集成/E2E | off 模式先被能力门禁阻断 | 标定类型、有效期、覆盖关系、批准和回滚规则 | 标定模型、资产校验、版本发布、机器人绑定 | 错标定污染数据、过期使用、格式不可信 | **E** | `frontend/src/features/calibrations/api/index.ts`; `frontend/src/features/calibrations/publish-rules.ts`; `frontend/src/mocks/handlers/robotics.handlers.ts`; `plan/BACKEND-DATA-PIPELINE-IMPLEMENTATION-PLAN.md:339-344` |
| P17 | 数据 Schema | `/settings/data-schemas` | 部分：列表为主 | 部分：仅列表 | 未接入 | 不存在页面 registry；Lance `schema_snapshots` 不是发布式 Schema Registry | 部分：有不可替代 registry 的 schema snapshot；缺名称/版本/兼容策略 | 缺兼容性校验/发布能力 | 只有 Mock；无真实单测/集成/E2E | off 模式先被能力门禁阻断 | 版本规则、兼容级别、审批、废弃和迁移语义 | registry 模型、兼容 checker、发布 API、消费者引用 | 破坏兼容、旧数据不可读、版本分叉 | **E** | `frontend/src/features/data-schemas/api/index.ts`; `frontend/src/features/data-schemas/registry-rules.ts`; `backend/migrations/lance_catalog/0001_lance_catalog.sql:2-13`; `plan/BACKEND-DATA-PIPELINE-IMPLEMENTATION-PLAN.md:339-344` |
| P18 | 访问控制 | `/settings/access` | 部分：成员/变更 UI | 部分：bootstrap/members，写操作未完整 | 未接入 | 部分基础：JWT、角色权限、RLS/审计存在；无成员/邀请/变更页面 API | 未设计 principal/member/group/project-region binding；角色定义仅代码内 | 不需要 Worker；外部 IdP 同步待设计 | security 单测/部分外部 DB 测试；无页面真实集成/E2E | off 模式因没有真实授权 bootstrap 而首先阻断自己和其他页面 | 身份源、角色模型、项目/区域授权、邀请/移除/自锁保护 | auth bootstrap、成员绑定模型、变更 API、前后端 capability 映射 | 越权、锁死管理员、权限缓存陈旧 | **E** | `frontend/src/features/access/api/index.ts`; `frontend/src/app/shell/PlatformShell.tsx:295-363`; `frontend/src/shared/scope/shell-store.ts:27-44`; `backend/src/hc_data_platform/security/auth.py:13-38`; `backend/migrations/security/001_core.sql:24-64` |
| P19 | 审计日志 | `/settings/audit` | 完成（Mock UI） | 完整读取；导出明确 unavailable | 未接入 | 部分：有审计写入表/Service，无页面读取 Router | 部分：`core.audit_events` 已有；缺产品查询字段、保留/导出模型 | 读取不需要；大导出可选异步 Worker 缺失 | security 审计单测存在；无页面真实集成/E2E | off 模式先被能力门禁阻断 | 可见范围、敏感字段脱敏、保留期、导出审批 | 只读查询/详情/facets、脱敏、稳定游标、导出策略 | 审计数据泄漏、不可抵赖、查询量和保留成本 | **D** | `frontend/src/features/audit/api/client.ts`; `frontend/src/mocks/handlers/audit.handlers.ts`; `backend/migrations/security/001_core.sql:24-44`; `backend/openapi.generated.yaml`（无 audit read 路径） |

## 4. Mock 与真实模式的共同断点

### 4.1 真实模式的全局授权引导尚未接线

`createPlatformRouter()` 只向 `PlatformShell` 传入页面可用性，没有传入 `authorizationLoader` 或 scope options（`frontend/src/app/router/index.tsx:188-210`）。Shell store 的 principal、token、scope 和 authorization 初始值都为空（`frontend/src/shared/scope/shell-store.ts:27-44`），而 `PlatformShell` 在 loader 缺失时把 scope 选择标为失败（`frontend/src/app/shell/PlatformShell.tsx:295-363`）。`useCapabilities` 将缺失 snapshot 判为 failed，`RouteCapabilityGuard` 随后阻止 P02–P19 发领域请求（`frontend/src/shared/auth/use-capabilities.ts:12-35`; `frontend/src/app/router/RouteCapabilityGuard.tsx:10-31`）。

Browser Mock 启动时由 `frontend/src/mocks/index.ts:4-14` 注入 fixture session/scope/capabilities，掩盖了该断点。

### 4.2 权限模型未对齐

- 前端声明约 76 个细粒度 capability；`dashboard.read` 仍属于 reserved capability（`frontend/src/entities/capability.ts:4-103`）。
- 后端只有 uploader/annotator/reviewer/publisher/admin 五类角色及粗粒度权限（`backend/src/hc_data_platform/security/auth.py:13-38`）。
- 仓库中没有把 JWT claim、项目/区域 scope 和前端 capability snapshot 连接起来的正式接口。

### 4.3 错误合同未对齐

前端客户端按 `{ error: { code, message, request_id, details } }` 解析（`frontend/src/shared/api/http-client.ts:47-56,102-134`）；后端返回扁平 RFC 9457 Problem Details，并使用 `application/problem+json`（`backend/src/hc_data_platform/core/errors.py:8-21`; `backend/src/hc_data_platform/core/app.py:43-51`）。真实调用时前端会丢失稳定错误码、request id 和 details。

### 4.4 前端生成类型不是后端当前事实源

`frontend/scripts/generate-api-client.mjs:8-27` 从仓库外 `OPENAPI_ROOT` 的 8 个领域草案生成类型；`frontend/src/shared/api/generated/` 在运行时客户端中没有消费者。README 也明确警告这些是 external drafts，Mock/draft 不等于真实后端（`README.md:146,173,185-192,216-219`）。正式接入前必须将生成源切到本仓库经 `app.openapi()` 校验的 OpenAPI，或明确版本化的唯一合同仓库。

## 5. P01 工作台专项审计

### 5.1 四个接口是否进入正式 OpenAPI

| 能力 | 前端草案路径 | Browser Mock | 正式 OpenAPI | 后端 Router/Service/Repository/测试 |
|---|---|---|---|---|
| dashboard snapshot | `/projects/{project_id}/regions/{region_id}/dashboard/snapshot` | 有 | **无** | **全部无** |
| dashboard activity | `/projects/{project_id}/regions/{region_id}/dashboard/activity` | 有 | **无** | **全部无** |
| dashboard coverage | `/projects/{project_id}/regions/{region_id}/dashboard/coverage` | 有 | **无** | **全部无** |
| dashboard pending-items | `/projects/{project_id}/regions/{region_id}/dashboard/pending-items` | 有 | **无** | **全部无** |

证据：客户端草案见 `frontend/src/features/dashboard/api/client.ts:20-89`；四个 handler 见 `frontend/src/mocks/handlers/dashboard.handlers.ts:39-73`；运行时 `app.openapi()` 与 `backend/openapi.generated.yaml` 均为 44 paths，搜索 `dashboard` 无结果。

### 5.2 双模式运行复验

| 模式 | 观察结果 | 网络观察 |
|---|---|---|
| `VITE_MOCK_MODE=off` | 出现“工作台聚合能力尚未开放” | dashboard 请求 0；query 在 `page.tsx:158-180` 被禁用 |
| `VITE_MOCK_MODE=browser` | KPI、趋势、覆盖、待办均显示 fixture | 点击覆盖区后，四条唯一 dashboard 路径均由 MSW 返回 |

该行为与 README 的声明一致（`README.md:89-93`），不能把 browser 模式的完整显示视为真实 API 完成。

### 5.3 每项指标可用与缺失的数据来源

| 能力 | 可利用的真实事实 | 当前不能证明/缺失的事实 | 审计结论 |
|---|---|---|---|
| activity | `ingest.rollout_objects.file_size/committed_at`；`ingest.upload_sessions.status`；workflow jobs | “accepted unique bytes”的去重边界；terminal/succeeded/failed 的对象或会话分母；活动究竟是上传桶还是事件流 | 可做技术原型，但公式必须先确认。当前计划称“活动序列”，实际 Mock schema 是上传吞吐桶，存在文档偏差。 |
| snapshot.storage | RAW 可部分来自 rollout objects；DERIVED/PUBLISHED 可部分来自 publication assets | preview cache 无库存表；published export 无完整 size；无对象角色、storage class 历史、跨副本/去重、账单事实 | 不能从现有表完整计算 Mock 的 `by_role/history`。需要库存事实或外部 inventory adapter。 |
| snapshot.episodes | rollout、verification/QC、Lance version/lineage 可作为底层阶段证据 | 没有规范化 episode 表，也没有 uploaded/validated/viewable 的产品定义 | 不能直接把 rollout 数当 episode 数。需要 episode 投影及口径。 |
| snapshot.work | annotation tasks/reviews 可提供部分工作项 | manual issue、cleaning draft、version review 的独立模型不存在 | 只能覆盖一部分，不可伪造 Mock 的所有计数。 |
| coverage | `collection_jobs/rollouts` 有 `robot_id/task_id` | 没有 robot group 和 task taxonomy 目录；没有“应覆盖”的 denominator | numerator 可探索，denominator 必须由计划/目录规则定义。 |
| pending-items | failed upload/workflow、QC RISK/REJECT、annotation task 可成为候选 | manual issue、cleaning draft、lifecycle alert 不存在；优先级、去重、action target、可见性未定义 | 应先建立 pending source catalog；不能直接照搬 Mock 六种枚举。 |

相关事实表：`backend/migrations/ingest/001_ingest.sql:3-126`、`backend/migrations/workflow/001_jobs.sql:3-15`、`backend/migrations/workflow/002_job_state_machine.sql:1-49`、`backend/migrations/quality/0001_quality.sql:15-54`、`backend/migrations/lance_catalog/0001_lance_catalog.sql:2-94`、`backend/migrations/annotation/0001_annotation.sql:6-95`、`backend/migrations/publishing/0001_publishing.sql:4-71`。

### 5.4 范围、时区、权限、缓存和一致性

- **范围**：现有草案路径强制单个 project + region。建议第一版禁止跨项目、跨区域汇总；跨区域必须另立权限和数据驻留决策。
- **时间范围**：建议统一 `from` inclusive、`to` exclusive，数据库存 UTC，按项目默认时区切桶；当前前端硬编码 `Asia/Shanghai` 假设（`frontend/src/features/dashboard/types.ts:8-10`），没有项目时区事实源。
- **权限**：Router 必须在查询前校验 project/region；Repository 条件也必须携带 scope，不能只靠前端 capability。跨域 union 应以用户可见资源集合为交集。
- **一致性**：四个接口若分别查询，`as_of` 可能不同。建议 snapshot/activity/coverage 响应都返回 `as_of`，P01 以同一 refresh token/读时点请求；pending 使用独立游标。
- **缓存**：首版先使用索引化 SQL 和明确查询预算，不在无容量证据时引入新的缓存基础设施。达到阈值后再选择聚合表/物化视图；缓存键必须含 scope、range、timezone 和 permission fingerprint。
- **Worker**：第一版同步聚合不强制需要 Worker。若要求分钟级大范围统计，推荐由 Outbox/Temporal 更新 rollup 表，并保留按源事实重算能力；这属于新增能力，当前 Worker 不支持。

### 5.5 必须先由产品/业务确认

1. activity 是上传吞吐时间桶，还是用户/系统事件流；若两者都要，应拆成两个合同。
2. accepted unique bytes 的去重键、成功/失败/terminal 分母。
3. episode 的 uploaded、validated、viewable 精确定义及一次重跑是否重复计数。
4. storage 各 role 的包含项、副本/临时文件/删除标记和计费口径。
5. coverage 的机器人组、任务全集和 denominator 来源。
6. pending 类别、优先级、去重、SLA、动作跳转和关闭条件。
7. 默认范围、允许的最大范围、项目时区和夏令时处理。
8. project/region 权限，是否允许跨区域聚合。
9. 数据新鲜度与页面允许的“陈旧”提示阈值。
10. 空数据、部分源失败和无权限时分别如何展示。

### 5.6 已足够明确、可在决策后直接编码的工作

- 将四条 dashboard 路径加入正式 OpenAPI，并使用统一 scope、时间、游标、Problem Details 和 `as_of` 规范。
- 为已存在事实表编写 scope-safe Repository 查询骨架；所有未知口径保留显式 domain policy，不写死 Mock 数值或枚举。
- 实现 Router→Service→Repository 分层、权限校验、查询时延/错误/陈旧度指标和 request-id 日志。
- 为 activity 上传桶、已有 annotation/workflow pending 来源编写单元与 Postgres 集成测试。
- 将 P01 的编译期 Mock gate 改为真实 capability gate，但只有真实合同和 auth bootstrap 通过验收后才能启用。
- 为 off 模式保留真实的 403/404/部分源失败/空状态，不回退到 fixture。

### 5.7 P01 从 Mock-only 到真实可用的具体任务链

`P01 产品口径确认 → 正式 OpenAPI → 缺失事实/索引迁移 → Repository 聚合 → Service 一致性/权限 → 4 个 Router → 单元/Postgres 集成 → auth bootstrap → 前端生成类型与 client → 真实模式组件测试/E2E → 性能与越权测试 → 试点观测`

详细任务、依赖和验收标准见 `plan/P01-P19-REAL-API-IMPLEMENTATION-PLAN.md`。

## 6. 后端实际完成度与前端使用情况

| 后端能力 | 当前真实实现 | P01–P19 前端实际使用 |
|---|---|---|
| Ingest/upload session、parts、rollout | Router/Service/Repository/迁移/Worker/测试存在 | P03/P04 没有使用；前端合同不兼容 |
| Verification、quality、alignment | workflow/activity/测试存在 | P04/P06/P07/P01 没有页面合同接入 |
| Lance catalog/version/lineage/reconciliation | Router/Repository/迁移/Worker/测试存在 | P05–P07 没有使用页面合同 |
| Preview | 真实 session/Worker 存在 | P06/P08/P11 没有真实接入 |
| Annotation/review | Router/Service/Repository/迁移/测试存在 | P08 路径、scope、wire contract 不兼容；P09–P11 不是等价模型 |
| Publishing/export | Router/Repository/迁移/Worker/测试存在 | P07 没有真实接入 |
| JWT/scope/idempotency/audit/outbox | 基础能力与测试存在 | P18/P19 没有页面 API；前端 auth bootstrap 未接线 |
| Dashboard、data sources、storage/lifecycle、robotics/calibration/schema registry、members | 未实现或只有不等价底层事实 | 对应页面全部依赖 Mock |

Worker 当前注册 7 个 workflow 和 9 个 activity（`backend/src/hc_data_platform/workflow/temporal_workflows.py:547-555`; `backend/src/hc_data_platform/workflow/activities.py:485-495`; `backend/src/hc_data_platform/workflow/worker.py:37-71`），不包含 dashboard、生命周期、机器人资产、标定、Schema Registry 或授权同步能力。

## 7. 文档与代码偏差

| 文档陈述 | 当前代码/测试事实 | 处理结论 |
|---|---|---|
| 早期 T2–T7/status 和 UI baseline 记录 P01/P12/P19 contract/E2E/visual 通过 | 当前前端源码有 7 个测试文件/28 tests；无 Playwright 配置和 E2E；scaffold notes 明确历史 suite 已移除 | 保留历史记录，但不得当作当前验收证据。见 `frontend/docs/frontend-scaffold-notes.md:99-101`。 |
| 页面计划都列出 API 需求 | 每份计划都标注“Mock-backed draft/后端未确认” | 这些路径只能作需求输入，不能视为正式合同。见 `plan/frontend/frontend-page-01-dashboard-development-plan.md:3-5,23-30` 及 P02–P19 同类文件。 |
| P12 页面计划包含费用区域和 cost-breakdown | 当前 P12 page/routing/T2 已移除 cost Tab，但 API client、query、schema 和 Mock handler 仍保留 cost 草案 | 当前 UI 口径按三个只读 Tab审计；PD-18 必须决定成本是否退出 V1，并清理/隔离残留草案，不能把孤儿 handler 计为页面完成。 |
| P01 计划把 activity 描述为“活动序列” | 当前 schema 是 uploads 的 accepted bytes/succeeded/failed 时间桶 | 产品语义冲突，必须确认后再定合同。 |
| 前端可生成 API types | 生成源是仓库外草案，当前 runtime client 不消费 generated types | 不能用于证明前后端合同一致。 |
| 旧验收矩阵记录 262/276 passes | 本次完整后端运行是 283 passed、4 skipped、1 xfailed | 用本次运行作为当前测试事实；矩阵中的试点/容量状态仍有效。 |
| Backend plan 标题可能被理解成整个平台后端完成 | 计划正文明确不是完整 P01–P19，且不含 robot/calibration/lifecycle/schema registry/独立 manual issue/EDL | 以正文边界为准。见 `plan/BACKEND-DATA-PIPELINE-IMPLEMENTATION-PLAN.md:7-20,339-344`。 |

## 8. 测试、运行和环境验证

| 验证 | 本次结果 | 能证明什么 / 不能证明什么 |
|---|---|---|
| `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/pytest -q --import-mode=importlib -p pytest_asyncio.plugin` | 283 passed、4 skipped、1 xfailed、1 warning | 证明现有后端模块回归；不证明页面 API。Skipped 为外部 Postgres/MinIO 等；XFAIL 为容量。 |
| `.venv/bin/python -m hc_data_platform.core.openapi --check` | PASS | 证明生成 OpenAPI 与当前应用一致；同时确认无 dashboard/page API。 |
| `pnpm exec tsc -b` | PASS | 前端类型检查通过。 |
| `pnpm exec vitest run --reporter=verbose` | 7 files、28 tests PASS | 包含审计期间新增的 cleaning Mock scope 5 cases；仍只证明少量 shell/viewer/Mock/http 测试，不是 P01–P19 E2E。 |
| `pnpm exec vite build` | PASS，存在 >500 KB chunk warning | 证明可构建；不证明真实 API。 |
| `pnpm exec eslint . --max-warnings=0` | FAIL：无 ESLint config | 当前没有可运行的 lint 门禁。 |
| `pnpm exec playwright test --list` | FAIL：No tests found，且无当前 Playwright 配置 | 当前不存在可执行页面 E2E。 |
| P01 off/browser 双模式 Playwright 脚本 | off 0 请求并显示 unavailable；browser 命中 4 个 Mock | 直接复验 P01 的 Mock-only 状态。 |
| `docker compose -f compose.dev.yaml config -q` | PASS | Compose 语法有效。 |
| `docker compose -f compose.dev.yaml ps --format json` | api/frontend/gateway/postgres/minio/temporal/temporal-ui/worker 运行；主要服务 healthy，worker 无 healthcheck | 本地栈能启动；默认前端仍是 browser Mock（`compose.dev.yaml:152-167`）。 |
| `git diff --check` | PASS | 当前工作树没有空白错误；不等于功能验收。 |

## 9. 环境成熟度判断

| 目标 | 判断 | 理由 |
|---|---|---|
| 本地开发可用 | **部分可用** | 后端真实数据管线和 Browser Mock 前端可独立开发；P01–P19 真实前后端联调不可用。 |
| 集成测试可用 | **后端部分可用，整站不可用** | 后端 in-process/reference tests 完整；4 个外部依赖场景被 skip；无前端真实 API 集成/E2E。 |
| 试点环境可用 | **未达到** | 当前 chart 的完整鉴权/可观测 pipeline pilot 没有重跑，Worker/metrics/log capture 仍 PARTIAL。 |
| 生产发布可用 | **未达到** | 容量 XFAIL、5TB/day 目标未通过、当前升级/回滚 NOT RUN、页面真实 API 缺失、安全/权限合同未闭合。 |

生产就绪逐项证据和阻断条件见 `plan/BACKEND-PRODUCTION-READINESS-GAPS.md`。

## 10. 无法在本次审计中验证的事项

- 未获得真实 IdP、生产 JWT claim、组织/项目/区域成员数据，因此无法验证生产授权语义。
- 4 个需要外部 Postgres/MinIO 环境的 pytest 场景本次被 skip，未补造环境或伪造通过结果。
- 没有当前集群访问和 chart 试点证据，未重跑 Helm upgrade/rollback、日志/指标采集或全流水线 pilot。
- 没有 5 TB/day 等价负载环境，本次没有重跑容量测试；沿用当前报告中的 XFAIL/未达标结论。
- 没有真实对象存储 inventory/账单、robot/calibration/schema、成员目录数据，无法验证 P01/P12–P19 的数据可计算性。
- 没有产品批准的公式、状态机、权限矩阵和保留策略；相关结论均标为“需要产品确认”，未把 Mock 合同提升为正式合同。

## 11. 2026-08-17 前端效果评审说明

效果图评审后来确认的空账户注册、项目/权限申请、P20 无分派任务、数据上传页、Manifest 自动发现、多级 Tag、标注/清洗合并和四类容量均属于后续产品/合同输入，**不改变本审计对当前代码的覆盖等级**。截至本审计基线，这些效果图没有对应的真实 API/E2E 证据，不能据此把任何 D/E 页面提升为 A/B/C。

后续范围扩展为 P01–P20，实施任务见 `plan/P01-P20-POST-EFFECT-REVIEW-IMPLEMENTATION-PLAN.md`。
