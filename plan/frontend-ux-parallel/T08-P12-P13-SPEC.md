# T08｜P12 容量管理与 P13 数据生命周期 UX 设计评审规格

> 状态：设计评审稿，不是实现记录
>
> 日期：2026-08-17（Asia/Shanghai）
>
> 适用范围：P12 `/storage`、P13 `/storage/lifecycle`
>
> 本轮边界：只形成规格；未修改前端、后端、OpenAPI、数据库、Worker、Compose、README 或其他计划。

## 0. 评审结论

当前实现不能按本规格验收：

- P12 已在可见首页隐藏旧的金额摘要，但费用组件、类型、查询、Mock、能力键和生成合同仍成链存在；同时缺少真实配额、项目/数据分布、预测与统一健康事实。
- P13 当前模型和页面仍允许 `DELETE_OBJECT`、`ABORT_MULTIPART`，且把它们当成可执行的不可逆动作；这与 V1 的 Raw、Manifest、已发布版本 Manifest 业务不可删除边界冲突。
- P12/P13 目前均无真实后端路由。前端生成合同、Mock 和页面行为只能视为草案，不能作为平台已经具备容量盘点、迁移、归档或恢复能力的证据。
- P12 目标是单页容量工作台：没有 Tab、没有卡片堆、没有筛选器，也没有任何金额/账单/定价/预算概念。
- P13 只容纳归档、冷热分层、迁移、恢复和可重建缓存清理；不得提供 Raw、Manifest、已发布版本 Manifest 的业务删除入口，也不得以 Multipart 终止替代缓存清理。
- 外包/普通业务用户默认不能看到全局容量、存储拓扑或危险运维动作。角色名只影响默认产品视图，最终授权必须由服务端能力、资源作用域、允许动作三者交集决定。

上线硬门禁：

1. P12 全链搜索不再出现本文件第 2.2 节列出的旧费用能力、路由、DTO、Mock 和组件。
2. P13 前后端可执行动作枚举中不再出现业务对象删除或 Multipart 终止。
3. Raw、Manifest、已发布版本 Manifest 的服务端 `allowed_actions` 永不包含删除；前端只显示“非删除锁定”。
4. 所有危险操作必须有新鲜模拟、明确权限、作用域、影响摘要、确认和可审计幂等意图；UI 隐藏按钮不能替代服务端拒绝。
5. 新 API、表和 Worker 在本稿中均为技术设计，不得被表述为当前能力。

## 1. 依据、方法与可信度

### 1.1 权威顺序

冲突时按以下顺序裁决：

1. `plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:46-49,157-158,240-245`：Raw/Manifest/发布版本不可变，P12 去费用化，P13 收敛为冷热分层与恢复。
2. `design-system/hc-data-platform/MASTER.md:128-136,164-187,212`：图表、React/Vite、URL 状态和 P12 禁止项。
3. 后端迁移、服务与运行证据：只用于证明当前真实边界，不据此臆造尚不存在的容量或生命周期能力。
4. 当前前端页面、生成 API 与 Mock：用于审计偏差；Mock/生成文件不是生产能力证据。
5. 旧 P12/P13 计划：仅用于追溯历史，冲突内容被修订计划覆盖。

### 1.2 审查输入

已通读：

- 修订计划与 Master；前端基线/重构计划；旧 P12/P13 页面计划。
- P12/P13 当前页面、组件、样式、查询编解码、路由、feature API、adapter、schema、types、query keys、Mock、fixture、scenario、现存测试和生成 storage API 的相关操作/Schema。
- 导航、能力目录、角色能力映射、路由懒加载、共享表格与确认弹窗。
- ingest、publishing、Lance、verification、alignment 迁移与说明；对象存储适配器、Manifest 写入与冲突测试；运维 Runbook、告警、容量报告和部署证据。
- `backend/migrations/README.md` 全文，用于核对现有表与不可变性保护。

未验证项见第 12 节。

### 1.3 Skill 与外部规则状态

任务点名的 `frontend-design`、`ui-ux-pro-max`、`web-design-guidelines`、`react-best-practices` 未出现在本会话可用 Skill 目录中，因此无法读取或执行对应 `SKILL.md`。本稿没有伪称它们已运行，采用以下可追溯替代：

- 以 Master 为只读设计约束，并用前端设计/信息架构、响应式、可访问性和高风险操作四个审查视角逐项复核。
- 2026-08-17 获取最新版 [Web Interface Guidelines command.md](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md)，按文件行号记录发现。
- 获取最新版公开 [Vercel React Best Practices](https://raw.githubusercontent.com/vercel-labs/agent-skills/main/skills/react-best-practices/SKILL.md) 及与本任务直接相关的并行请求、动态加载、条件加载、派生状态、长列表规则；将 Next.js 示例转换为 React 19 + Vite 7 + TanStack Query 的等价约束。

### 1.4 修改前工作区状态

开始本任务、写入本文件之前记录的 `git status --short` 如下。它包含大量用户既有改动，本任务未触碰这些路径：

```text
 M README.md
 M backend/Dockerfile
 M compose.dev.yaml
 M deploy/compose/gateway.conf
 M frontend/docs/status/T2.md
 M frontend/src/app/theme/component-theme.ts
 M frontend/src/app/theme/global.css
 M frontend/src/app/theme/tokens.ts
 M frontend/src/features/dashboard/dashboard-charts.module.css
 M frontend/src/features/dashboard/dashboard-charts.tsx
 M frontend/src/features/storage-overview/metrics-contract.ts
 M frontend/src/features/storage-overview/routing.ts
 M frontend/src/mocks/fixtures/cleaning/index.ts
 M frontend/src/pages/p01-dashboard/components/DashboardPendingList.tsx
 D frontend/src/pages/p01-dashboard/components/DataLifecycleRail.module.css
 D frontend/src/pages/p01-dashboard/components/DataLifecycleRail.tsx
 M frontend/src/pages/p01-dashboard/page.tsx
 M frontend/src/pages/p01-dashboard/styles.module.css
 M frontend/src/pages/p02-data-sources/components/DataSourceTable.tsx
 M frontend/src/pages/p02-data-sources/styles.module.css
 M frontend/src/pages/p03-upload-jobs/components/UploadSessionTable.tsx
 M frontend/src/pages/p03-upload-jobs/styles.module.css
 M frontend/src/pages/p05-datasets/components/DatasetTable.tsx
 M frontend/src/pages/p05-datasets/styles.module.css
 M frontend/src/pages/p12-storage-overview/components/StorageCostPanel.tsx
 M frontend/src/pages/p12-storage-overview/components/StorageInventoryTable.tsx
 M frontend/src/pages/p12-storage-overview/components/StorageMultipartTable.tsx
 M frontend/src/pages/p12-storage-overview/components/StorageObjectDrawer.tsx
 M frontend/src/pages/p12-storage-overview/components/StorageOverviewPanel.tsx
 M frontend/src/pages/p12-storage-overview/components/StorageSummaryStrip.tsx
 M frontend/src/pages/p12-storage-overview/components/StorageVisualCharts.tsx
 M frontend/src/pages/p12-storage-overview/page.tsx
 M frontend/src/pages/p12-storage-overview/query-codec.ts
 M frontend/src/pages/p12-storage-overview/styles.module.css
 M frontend/src/shared/ui/data/CursorPager.tsx
 M frontend/src/shared/ui/data/DataTable.tsx
 M frontend/src/shared/ui/styles.css
?? design-system/
?? frontend/src/mocks/handlers/cleaning.handlers.test.ts
?? frontend/src/pages/p01-dashboard/components/AssetCapacityBoard.module.css
?? frontend/src/pages/p01-dashboard/components/AssetCapacityBoard.test.tsx
?? frontend/src/pages/p01-dashboard/components/AssetCapacityBoard.tsx
?? frontend/src/pages/p12-storage-overview/components/StorageOverviewPanel.test.tsx
?? frontend/src/pages/p12-storage-overview/components/StorageSummaryStrip.test.tsx
?? frontend/src/pages/p12-storage-overview/display-labels.ts
?? frontend/src/shared/ui/data/CursorPager.module.css
?? plan/BACKEND-PRODUCTION-READINESS-GAPS.md
?? plan/P01-P19-REAL-API-COVERAGE-AUDIT.md
?? plan/P01-P19-REAL-API-IMPLEMENTATION-PLAN.md
?? plan/P01-P20-FRONTEND-UX-10-TERMINAL-PROMPTS.md
?? plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md
?? plan/PRODUCT-DESIGN-DECISIONS-REQUIRED.md
```

## 2. 当前代码证据

### 2.1 P12 当前可见行为

- `frontend/src/pages/p12-storage-overview/page.tsx:351-360` 仍是“概览/对象/Multipart”三 Tab；目标 P12 必须改成无 Tab 单页。
- `frontend/src/pages/p12-storage-overview/page.tsx:121-165` 以现有 storage capabilities 控制区域，同文件 `:335-347` 显示项目 ID 和快照。它没有独立的全局作用域保护设计。
- `frontend/src/pages/p12-storage-overview/page.tsx:280-313` 已有区域级 partial error，可保留其“成功区域不被失败区域覆盖”的思想。
- `frontend/src/pages/p12-storage-overview/components/StorageSummaryStrip.tsx:61-150` 当前可见摘要只展示 Raw、Lance、当前占用和新鲜度；`frontend/src/pages/p12-storage-overview/components/StorageSummaryStrip.test.tsx:13-36` 明确断言不显示计费容量/月度费用。这说明可见页已部分收敛，但不能证明全链删除完成。
- `frontend/src/pages/p12-storage-overview/components/StorageVisualCharts.tsx:37-47,109-120` 把物理容量按月聚合成单柱，却用对象角色图例解释，语义不一致；同文件 `:36-179` 没有精确值表格替代。
- `frontend/src/pages/p12-storage-overview/components/StorageVisualCharts.tsx:12-16` 及 P12 CSS 使用硬编码色值，不满足 Master 的语义 token 方向。
- `frontend/src/pages/p12-storage-overview/routes.tsx:4-9` 已做页面级 lazy route；这是可保留的 React/Vite 基线。

### 2.2 P12 禁止概念残留搜索

本节中的禁止词仅是当前事实审计，不是功能提案。搜索式：

```bash
rg -n -i --glob 'frontend/**' --glob '!frontend/dist/**' --glob '!frontend/node_modules/**' \
  '(cost|fee|billing|price|budget|currency|money|billed|savings|费用|成本|账单|计费|价格|预算|金额|币种|月费)' \
  frontend/src frontend/docs
```

与 P12/P13 直接相关的命中如下：

| 层 | 当前证据 | 结论 |
|---|---|---|
| 能力目录 | `frontend/src/entities/capability.ts:67`、`frontend/src/features/access/capability-catalog.generated.ts:68`、`frontend/src/shared/api/generated/access.ts:907` 有 `storage.cost.read` | P12 费用能力仍是授权面的一部分 |
| 页面组件 | `frontend/src/pages/p12-storage-overview/components/StorageCostPanel.tsx:3-19` | 完整费用面板仍在仓库；当前页面未挂载不等于已删除 |
| 领域类型 | `frontend/src/features/storage-overview/types.ts:10-16,33-41,101-112` | `StorageMoney`、`billedBytes`、`monthlyCost`、`StorageCostBreakdown` 仍存在 |
| 格式化合同 | `frontend/src/features/storage-overview/metrics-contract.ts:4-9,19-31,68-84` | 仍定义 money/currency 口径和格式化函数 |
| Wire schema | `frontend/src/features/storage-overview/api/schemas.ts:22-48,76-84,213-235` | 总览和独立费用响应仍携带账单事实 |
| Adapter | `frontend/src/features/storage-overview/api/adapter.ts:9-10,45-56,73-80,203-215` | Wire 到领域层的费用适配链完整存在 |
| Client/query | `frontend/src/features/storage-overview/api/client.ts:6-7,56-67`、`frontend/src/features/storage-overview/api/queries.ts:4,27-35`、`frontend/src/features/storage-overview/api/query-keys.ts:33` | 独立费用请求、hook 和 cache key 仍存在 |
| Mock | `frontend/src/mocks/fixtures/storage-overview/index.ts:3-7,16-19,38,75-77`、`frontend/src/mocks/handlers/storage-overview.handlers.ts:3,77-82`、`frontend/src/mocks/scenarios/storage-overview.ts:34-45` | fixture、handler、allowed action 和场景能力仍支持费用链 |
| 生成 storage 合同 | `frontend/src/shared/api/generated/storage.ts:8-31,143-166,664,700-717,760-764,966-980,2028-2070` | 总览描述、DTO、endpoint 和 operation 仍包含费用 |
| 生命周期生成合同 | `frontend/src/shared/api/generated/storage.ts:1153-1160,1277,1284,1354,1505-1506,1542,1569`、`frontend/src/features/lifecycle/api/index.ts:69-74` | P13 草案也混入节省/金额确认，偏离本稿范围 |
| 历史文档 | `README.md:16`、`docs/project-directory-guide.md:169`、`docs/FRONTEND-UI-BASELINE.md:32`、`plan/frontend/frontend-page-12-storage-overview-development-plan.md:17,26,29-30` | 历史描述仍会误导后续实现，未来应在获准范围内统一修订 |

验收不是“页面上看不到”，而是上述 P12 费用组件、类型、API、Mock、能力和生成合同全链消失；文档历史命中需要在后续获准的文档任务中同步处理。

### 2.3 P12 真实能力缺口

- `plan/P01-P19-REAL-API-COVERAGE-AUDIT.md:48-49,211` 将 P12 标为 Mock-only，且无统一 inventory API；后端代码搜索不到 storage overview 路由。
- 当前 `StorageOverview` 只有 totals、role、growth、class、alerts、partial errors（`frontend/src/features/storage-overview/types.ts:18-56`），没有服务端配额、项目对比、预测区间或真实目标线字段。
- `backend/tests/load/CAPACITY-REPORT.md:3-11,41-51,76-90` 的 5 TB/日与 30% 裕量是吞吐验证目标；MinIO 结果未通过，端到端吞吐未测。它不能被转换成 P12 的存储配额或“已达标”健康结论。
- 当前表格允许每页 100 行（P12/P13 query codec），共享 `DataTable` 仅分页、未虚拟化，且 `frontend/src/shared/ui/data/DataTable.tsx:186-197` 固定 `scroll.x='max-content'`。目标必须阻止横向滚动泄漏到页面。

### 2.4 P13 当前行为与安全偏差

| 证据 | 当前行为 | 目标偏差 |
|---|---|---|
| `frontend/src/entities/lifecycle-policy.ts:19-22` | 动作类型含 `DELETE_OBJECT`、`ABORT_MULTIPART` | V1 不允许业务删除；P13 不再负责 Multipart 终止 |
| `frontend/src/features/lifecycle/api/index.ts:20-24,47`、`frontend/src/shared/api/generated/storage.ts:1100-1114,1318-1326` | Wire/生成合同允许删除和 Abort | 必须从可执行动作合同移除 |
| `frontend/src/pages/p13-storage-lifecycle/page.tsx:151-170,217-225` | UI 把删除/Abort 标为不可逆，并写“删除与清理为最终操作” | 应改成保护对象非删除锁定；仅可重建缓存清理可危险执行 |
| `frontend/src/pages/p13-storage-lifecycle/page.tsx:229-255` | 确认后调用启用策略，并生成删除/Abort 风险确认 | 当前确认模型仍认可禁用动作 |
| `frontend/src/pages/p13-storage-lifecycle/page.tsx:135-141` | `canExecute` 只检查全局 `storage.lifecycle.manage` 与模拟证据 | 未校验 `storage.lifecycle.execute`、资源 allowed action、作用域和服务端二次鉴权 |
| `frontend/src/pages/p13-storage-lifecycle/page.tsx:203-205` | 模拟按钮只看全局 capability | 未与当前策略/项目作用域 allowed action 求交集 |
| `frontend/src/pages/p13-storage-lifecycle/page.tsx:173-186` | 执行记录、恢复、Multipart Tab 都是 unavailable | 只有策略列表是可见投影；不能宣称生命周期闭环已存在 |
| `frontend/src/features/lifecycle/api/index.ts:300-315` | 有 restore mutation hook | 页面未提供恢复流程，且无真实后端证明 |
| `frontend/src/mocks/handlers/lifecycle.handlers.ts:12-22` | 只有 lifecycle page GET | 模拟/启用等写请求没有完整 Mock 闭环 |
| `frontend/src/pages/p13-storage-lifecycle/page.tsx:80-90,116-117` | URL 写入 policy/simulation ID，本地 state 初始仍为 null | 刷新/分享链接不能恢复同一上下文 |
| `frontend/src/pages/p13-storage-lifecycle/page.tsx:223-225` | 硬编码 IA/Archive 恢复时效 | 必须显示服务端返回的提供商/区域/策略事实与生效时间，未知即未知 |
| `frontend/src/pages/p13-storage-lifecycle/query-codec.ts:3-6,25-27` | 仍有 Multipart Tab 和 `abortMultipart` intent | 不属于目标 P13 IA |

`plan/P01-P19-REAL-API-COVERAGE-AUDIT.md:48-49,164` 还明确指出 P13 无后端、无 lifecycle worker。生成 API 只能作为待重做的合同草案。

### 2.5 不可变性与运维事实

- `backend/src/hc_data_platform/ingest/README.md:22-24` 要求 bucket versioning/object lock、API 身份拒绝 Delete/overwrite、Manifest 条件创建。
- `backend/src/hc_data_platform/ingest/adapters.py:33-36,82-104,141-160,216-241,280-292` 在 S3/OSS 适配层实施存在性检查、`If-None-Match` 或禁止覆盖头；`backend/src/hc_data_platform/ingest/service.py:475-507` 只允许条件创建或相同指纹对账。
- `backend/migrations/publishing/0001_publishing.sql:73-148` 对 dataset versions、publication assets、published exports 建立 UPDATE/DELETE 拒绝触发器；`backend/src/hc_data_platform/publishing/README.md:3-18` 定义发布产物哈希冻结和冲突拒绝。
- `backend/migrations/ingest/001_ingest.sql:105-120` 的 rollout objects 表没有等价不可变触发器。当前 Raw/Manifest 最强保证依赖对象存储版本化、Object Lock、IAM 和条件写；上线前必须验证部署事实，不能只凭 UI 文案。
- `backend/observability/RUNBOOK.md:19-24` 与 `backend/runbooks/pipeline-alerts.md:6-12,38-49` 提供运行告警/处置证据，可成为 P12 健康区数据源候选，但不是现有容量 API。

## 3. V1 共同安全模型

### 3.1 对象角色与存储层级分离

对象角色回答“它是什么”，存储层级回答“它现在放在哪里”，不得混为一个枚举：

| 对象角色 | V1 业务删除 | 允许的生命周期动作 |
|---|---:|---|
| Raw / Source | 永不允许 | 热层→冷层迁移、归档、恢复；保持同一稳定对象身份和内容哈希 |
| Ingest Manifest | 永不允许 | 可迁移/归档/恢复；不得修改内容或重写引用 |
| Published Version Manifest | 永不允许 | 可迁移/归档/恢复；不得删除、覆盖或改写发布版本 |
| 可重建派生缓存 | 仅满足清理资格时允许 | 热/冷迁移、恢复、模拟后清理；必须能指出重建来源和最后引用 |
| Preview / 临时缓存 | 仅满足清理资格时允许 | 同上；过期不等于可直接清理 |
| Export | 由产品保留策略另行确认 | 本稿不推导删除权限；默认只显示迁移/归档/恢复 |

“冷层”不是“已删除”，“归档”不是“不可访问”，“恢复”不是创建新业务版本。层级变化必须保留对象 ID、内容摘要、引用关系和审计链。

### 3.2 服务端授权公式

```text
effective_actions
  = identity.capabilities
  ∩ resource.allowed_actions
  ∩ scope.allowed_actions
  ∩ protection_policy.allowed_actions
  ∩ fresh_simulation.allowed_actions
```

任一交集为空即只读。前端需要展示缺失的具体条件，但后端必须再次计算；不能信任客户端提交的对象数、字节数、影响摘要或确认标记。

### 3.3 新鲜模拟与锁

危险动作提交至少绑定：`scope_id`、策略/计划版本、inventory snapshot ID、input hash、对象数、物理字节数、源/目标层级、blocked reasons、simulation expiry、impact digest、ETag、idempotency key。任何事实变化都使旧模拟失效并要求重新模拟。

## 4. P12 信息架构与交互规格

### 4.1 页面目标与范围

P12 是“容量管理”单页，不是对象浏览器或成本中心。页面只回答六个问题：

1. 当前占用、对象数、增长和数据新鲜度是多少？
2. 容量在项目/数据角色之间如何分布？
3. 是否存在服务端已配置的硬/软配额，距离阈值还有多少？
4. 数据在热层、冷层、归档层之间如何分布？
5. 哪些容量异常需要处理？
6. 在明确假设和置信区间下，何时可能触达真实阈值？

产品规则：

- 无 Tab、无筛选器、无卡片网格。使用一个页头、一个紧凑摘要带、连续分区、分隔线和表格。
- 作用域由当前项目上下文和服务端授权决定，不提供“切到全局”的页面控件。
- 普通/外包用户默认不进入本页；获得项目级只读授权后也只看当前项目摘要，不看对象键、bucket、region topology 或跨项目排行。
- P12 不提供对象删除、下载、恢复、迁移、Multipart 终止等动作。需要生命周期处理时只通过稳定资源 ID 跳到 P13 的被授权只读上下文。

### 4.2 自上而下 IA

| 顺序 | 区域 | 必须展示 | 不展示/降级规则 |
|---:|---|---|---|
| 1 | 页头与作用域 | “容量管理”、当前项目或“平台范围”标签、数据截至时间、刷新状态、健康总态 | 不显示存储账号、bucket、endpoint；外包用户不显示平台范围入口 |
| 2 | 紧凑摘要带 | 物理占用、近 7/30 日净增长、对象数、配额占用（仅已配置）、热/冷占比、异常数 | 未配置配额显示“未配置”，不能画伪目标；字段未知逐项显示“未知” |
| 3 | 容量增长 | 实际历史折线；可用时叠加预测虚线与置信带；下方精确值表 | 无历史则解释采集起始点；无预测不渲染预测图例 |
| 4 | 项目/数据分布 | 平台运维：项目排行；项目管理员：当前项目内按数据角色/数据集聚合；普通只读：只显示角色汇总 | 小于隐私阈值的分组并入“其他”；无跨项目权限不发跨项目请求 |
| 5 | 配额与存储层级 | 真实硬/软配额进度；Hot/Cold/Archive 字节与对象数；受保护对象占比 | 配额缺失不画目标线；层级未知单列，不并入热层 |
| 6 | 异常与预测 | 异常表、预计触达日、置信区间、依据窗口、建议的非破坏处置入口 | 样本不足显示原因；只允许跳转到 P13 的迁移/恢复上下文，不生成删除建议 |

### 4.3 指标定义

| 指标 | 服务端事实/计算 | 单位与精度 | 空/未知语义 |
|---|---|---|---|
| 物理占用 | inventory snapshot 中 provider-reported physical bytes；不得用逻辑引用字节替代 | B 为传输值；UI 自动选 KiB–PiB，保留 0–2 位 | 快照缺失为“未知”，不是 0 |
| 逻辑引用量 | Manifest/发布引用的逻辑字节和 | 同上 | 仅作分布解释，不与物理占用相加 |
| 对象数 | 快照内已盘点对象数 | 整数、千分位 | partial inventory 必须标 partial |
| 7/30 日净增长 | 窗口末物理占用减窗口初物理占用 | 字节及百分比；分母为 0 时百分比未知 | 不把缺日补 0 |
| 配额占用 | physical bytes / 服务端生效 quota bytes | 百分比 1 位，同时显示分子/分母 | 未配置、已过期、作用域不匹配分别显示 |
| 层级分布 | 按服务端规范化 tier 聚合 physical bytes/object count | 字节、整数、占比 1 位 | provider 未知层级单列 `Unknown` |
| 预测触达日 | 服务端在明确窗口/模型下对真实 quota 或阈值预测 | 项目时区日期 + P50/P90 或区间 | 无真实目标、样本不足、模型失败均不输出日期 |
| 健康 | freshness、inventory completeness、quota state、异常规则分别返回 | 不用自造总分；总态取最严重事实 | 某子系统失败不覆盖其他成功事实 |

所有 int64 字节在 Wire/领域层保持十进制字符串或 `BigInt`；只在计算归一化比例时转为有界 `number`，禁止把原始大整数直接转为 `Number`。

### 4.4 图表选择与理由

| 数据问题 | 图形 | 理由 | 目标线规则 | 表格替代 |
|---|---|---|---|---|
| 容量随时间变化 | 折线；实际实线、预测虚线、置信带 | 时间连续性和斜率是主要信息，折线优于当前按月单柱 | 只有服务端返回、在当前作用域生效的 quota/threshold 才画；并标名称与生效时间 | 日期、实际、净变化、预测下限/中位/上限、快照状态 |
| 项目/数据角色比较 | 降序水平条形图 | 长名称可读，跨类别比较比环图准确 | 不画目标线 | 排名、名称、物理占用、对象数、占比、变化 |
| 热/冷/归档构成 | 单一 100% 堆叠条 + 数值表 | 只表达组成，避免多张甜甜圈占空间 | 无 | 层级、字节、对象数、占比、未知原因 |
| 配额 | 紧凑进度条/子弹图 | 配额本身是真实目标，适合当前值对阈值 | 未配置即不画；软/硬阈值必须来自服务端 | 配额类型、限制、使用、剩余、生效/到期时间 |
| 异常 | 表格，不默认画图 | 异常需要状态、范围、证据和下一步，不是装饰趋势 | 无 | 本身即主要视图 |

可访问性：每个图有可见标题、文字结论、更新时间和“查看精确数据”表；图形容器 `role="img"` 使用结论型 `aria-label`，装饰图例 `aria-hidden`，颜色不是唯一编码。加载/刷新结果用 `aria-live="polite"`，错误用 `role="alert"`。

### 4.5 时间、单位与新鲜度

- 日期时间统一 `Intl.DateTimeFormat`，默认项目时区；页头明确显示 `Asia/Shanghai` 或服务端返回的 IANA timezone，不能只显示无时区的日期。
- 数字统一 `Intl.NumberFormat`。数字和单位使用不换行空格，例如 `10 GiB`；标识符包裹 `translate="no"`。
- 每一区域有独立 `observed_at`、`snapshot_id`、`fresh_until`、`completeness`。页头总新鲜度取最差状态，但点击/展开可定位具体陈旧区域。
- `fresh`：当前时间不晚于 `fresh_until`；`stale`：仍可读但明确陈旧；`partial`：快照范围不完整；`unknown`：没有可验证事实。UI 不自行写死秒数。

### 4.6 空、错与部分失败

| 状态 | 页面行为 | 下一步 |
|---|---|---|
| 首次加载 | 页头骨架 + 各区域稳定高度骨架；不显示 0 | 保持布局，读屏播报加载中 |
| 后台刷新 | 保留旧值并标“刷新中”；不闪空白 | 可取消/重试；旧值保留原截至时间 |
| 整页 403 | 不发下游容量/拓扑请求 | 说明缺少项目容量只读权限，返回安全页面 |
| 区域 403 | 隐藏该区域数据，不把 403 当空数组 | 显示“此区域未授权”，不泄漏类别/数量 |
| 区域 5xx/契约错误 | 其他区域照常显示，失败区就地错误 | 显示 request ID 与“重试此区域” |
| 空项目 | 明示“尚无已盘点对象”，配额仍可显示 | 链接到已有上传/数据源流程，不给清理建议 |
| 快照陈旧 | 保留事实、显著标 stale | 平台运维可看盘点任务；普通用户仅看更新时间 |
| 预测不可用 | 不保留空图或虚假直线 | 显示样本不足/无真实阈值/模型失败具体原因 |

## 5. P13 信息架构与安全交互规格

### 5.1 页面范围

P13 是“数据生命周期”运维工作台，只包含：

- 热层、冷层、归档层之间的策略与迁移；
- 迁移前模拟、执行历史和失败恢复；
- 归档对象恢复；
- 仅针对可重建缓存的清理；
- 非删除锁定和审计证据。

移除 Multipart Tab、Abort intent、业务对象删除、账单节省字段和基于费用的决策原因。

### 5.2 IA

P13 可使用四个任务型 Tab，因为任务上下文确实不同；URL 必须完整恢复选中项与详情：

| Tab | 主内容 | 详情/动作 |
|---|---|---|
| 策略与模拟 | 策略表：名称、作用域、对象角色、源/目标层、日程/时区、状态、版本、非删除锁 | 选中策略后显示范围、模拟影响、blocked reasons；只有授权用户可提交模拟或进入执行确认 |
| 执行记录 | 迁移/归档/缓存清理执行历史，按服务端游标分页 | 步骤、对象/字节进度、失败样本、重试/暂停资格、审计 ID；不提供删除补救动作 |
| 恢复 | 归档恢复请求与当前可用性 | 对象范围、恢复模式、服务端 SLA、到期时间、目标热层、发起人、进度；权限不足只读 |
| 审计 | 策略版本、模拟、批准、执行、恢复、缓存清理事件 | actor、capability、scope、before/after digest、request/operation ID、时间戳 |

“非删除锁定”是所有 Tab 的常驻安全带，不是可关闭的提示：

```text
受保护：Raw / Manifest / Published Version Manifest
允许：迁移、归档、恢复
禁止：业务删除、覆盖、改写引用
保护依据：policy_id · object lock mode · retention/version · verified_at
```

依据未知时显示“保护状态未验证”，并阻断所有危险动作；不得乐观显示已保护。

### 5.3 策略与模拟

策略字段：稳定 policy ID、显示名、作用域、对象角色、包含/排除条件摘要、源/目标层、窗口、IANA timezone、状态、版本、ETag、上次模拟、上次执行、保护状态、allowed actions。

模拟报告必须来自不可变 snapshot，至少包含：

- 计划动作（迁移/归档/恢复/缓存清理）与源/目标层；
- 对象数、物理字节、未知对象数、受保护对象数、正在被任务引用数；
- 预计可用性变化与服务端返回的恢复 SLA；
- 各对象角色分布、top affected datasets/projects（按权限脱敏）；
- blocked reasons、warning reasons、抽样对象 stable IDs；
- snapshot、policy set/version、input hash、impact digest、生成/过期时间。

模拟不产生执行事实。未知对象数大于 0、保护状态未知、Manifest 引用不完整、模拟过期、作用域漂移或策略版本变化时，执行按钮必须禁用。

### 5.4 危险动作分级

| 动作 | 风险 | 最低授权与确认 |
|---|---|---|
| 热→冷迁移 | 访问延迟/吞吐变化 | `simulate` + `execute`、当前作用域 allowed action、新鲜模拟、一次明确确认 |
| 冷→归档 | 可用性和恢复等待变化 | 同上；必须确认服务端 SLA、受影响对象/字节和正在运行引用 |
| 归档恢复 | 目标层容量、等待时间、临时副本到期 | `restore.request`、对象 allowed action、恢复模式和到期确认 |
| 可重建缓存清理 | 缓存命中下降、后续重建负载；符合资格的缓存内容会被物理移除 | 独立 `cache.cleanup`、新鲜清理模拟、输入确认短语、二次服务端保护校验 |

缓存清理资格必须全部满足：对象角色为可重建缓存；有可验证重建来源；无活动任务/Manifest/发布版本引用；不在 legal hold/object lock/保留期内；inventory 与引用图新鲜；服务端返回 `CACHE_CLEANUP_ALLOWED`。缺一项即阻断。Raw、Manifest、Published Version Manifest 永不进入候选集合。

是否对归档迁移和缓存清理启用双人复核是产品待确认项；未确认前平台运维可模拟但生产执行保持关闭。

### 5.5 危险确认内容

确认弹窗不使用笼统的“确认启用”。必须按以下顺序呈现：

1. 动词 + 资源：如“确认将 1,284 个对象从 Hot 迁移到 Archive”。
2. 当前身份、项目/平台作用域、所用 capability 和资源 allowed action。
3. 新鲜模拟 ID、快照 ID、策略版本、过期倒计时。
4. 影响：对象数、字节数、对象角色、受影响数据集、可用性变化、恢复 SLA、活动任务、blocked/warning reasons。
5. 非删除声明：“本次不删除 Raw、Manifest 或 Published Version Manifest，不修改内容与引用。”
6. 缓存清理另列资格证据、重建来源和预计重建负载，并要求输入服务端返回的确认短语。
7. 取消为默认焦点；提交 pending 时锁定关闭，结果以 operation ID + audit ID 返回；网络未知结果必须先按幂等键查状态，不能重复提交。

### 5.6 执行、恢复与审计状态

```text
策略草稿 → 已模拟 → 待确认/待复核 → 已排程 → 执行中 → 已完成
                         │              ├→ 已暂停 → 可恢复执行
                         │              ├→ 部分失败 → 对账 → 受控重试
                         └→ 已拒绝      └→ 失败 → 对账/回退迁移（若提供商支持）

归档对象 → 恢复已请求 → 提供商处理中 → 可读取 → 已迁回热层/临时副本到期
```

状态名以服务端合同为准；UI 不凭进度百分比推导完成。历史记录是追加式审计事实，不允许前端“撤销记录”。“回退”是新的受控迁移，不是抹除原执行。

## 6. 角色—能力矩阵

现有 `frontend/src/entities/capability.ts:67-78,114-178` 中 PROJECT_ADMIN 获得几乎全部 storage 能力，而 developer/processor 没有；仓库没有独立“平台运维”角色。这种映射粒度不足。下表是目标产品矩阵，能力候选和作用域声明均属技术设计，不能当成当前实现。

| 页面/动作 | 普通业务/外包用户 | 项目管理员 | 平台运维 |
|---|---|---|---|
| P12 入口 | 默认隐藏；显式授予项目摘要只读后可见 | 当前项目可见 | 显式平台作用域后可见 |
| P12 摘要/增长 | 当前项目、聚合、无对象键/拓扑 | 当前项目 | 项目或平台聚合，按服务端 scope |
| P12 项目分布 | 不可见 | 只看本项目内数据角色/数据集 | 可看跨项目排行，但仍按组织边界脱敏 |
| P12 quota | 只读自身项目的生效值 | 只读；修改不在 P12 | 全局/项目只读；修改走独立治理流程 |
| P12 拓扑/对象样本 | 默认不可见 | 默认不可见；诊断另授权 | 仅 `storage.topology.read` 显式授权可见 |
| P13 入口 | 默认隐藏 | 策略/执行/恢复只读可按需授权 | 显式 lifecycle read 后可见 |
| 模拟 | 无 | 可在项目内模拟 | 可在获授权作用域模拟 |
| 执行迁移/归档 | 无 | 默认无；不得因 PROJECT_ADMIN 自动获得 | `execute` + scope + allowed action + 新鲜模拟 + 确认 |
| 请求恢复 | 默认无；若产品开放，仅自己可见的资源 | 项目内显式授权 | 获授权作用域 |
| 缓存清理 | 无 | 默认无 | 独立 cleanup capability + 资格校验 + 新鲜模拟 + 强确认 |
| 审计 | 仅自己的请求结果（若获准） | 项目范围只读 | 作用域内只读/导出另授权 |

能力候选（**技术设计｜当前不存在或当前粒度不足**）：

```text
storage.capacity.summary.read
storage.capacity.project_distribution.read
storage.capacity.platform.read
storage.topology.read
storage.lifecycle.policy.read
storage.lifecycle.simulate
storage.lifecycle.execute
storage.restore.read
storage.restore.request
storage.cache.cleanup
storage.lifecycle.audit.read
```

迁移期可映射现有 `storage.overview.read`、`storage.lifecycle.read/manage/simulate/execute`、`storage.restore.*`，但不能让旧的宽能力绕过新作用域与 allowed action 校验。`storage.cost.read` 不做迁移映射，直接退出。

## 7. 1440 / 1280 ASCII 线框

线框中的框表示布局区域和分隔，不代表 Card 组件。两种宽度都要求 `body/main` 无水平滚动；宽表只允许在明确标注的表格容器内滚动，优先隐藏低优先级列并由行详情补充。

### 7.1 P12｜1440（导航后内容约 1192 px）

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│ 容量管理     当前项目：Project A · Asia/Shanghai        截至 10:30 · 健康：有 1 项异常       │
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│ 物理占用 8.42 TiB │ 7日 +231 GiB │ 30日 +0.91 TiB │ 对象 12.4M │ 配额 68% │ Hot/Cold 72/28 │
├───────────────────────────────────────────────────────────────┬──────────────────────────────┤
│ 容量增长（实际── 预测╌╌ 真实配额··）                         │ 异常与预测                   │
│  9T ┤                         ╭──╌╌                            │ ⚠ inventory 18m 陈旧         │
│  8T ┤              ╭──────────╯   ╌╌ [置信带]                 │ ⚠ 预计 43–58 天触达软配额     │
│     └──────────────────────────────────── 日期                 │ [打开精确数据表]              │
├───────────────────────────────────────────────────────────────┴──────────────────────────────┤
│ 分布：项目运维看项目排行；项目管理员看数据角色/数据集                                        │
│ Raw             █████████████████████  5.10 TiB     对象 7.1M     30日 +8.4%                 │
│ Manifest        ██                       0.18 TiB     对象 1.9M     非删除锁定                  │
│ Published Mft   █                        0.07 TiB     对象 0.4M     非删除锁定                  │
│ Derived/Cache   █████████                2.31 TiB     对象 3.0M     30日 -1.2%                 │
├──────────────────────────────────────────────┬───────────────────────────────────────────────┤
│ 配额（仅真实配置）                           │ 存储层级                                      │
│ Soft  8.8 / 10 TiB  ████████████████░░       │ Hot 72% ██████████████ Cold 23% ████ Arc 5% █ │
│ Hard  8.8 / 12 TiB  ██████████████░░░░       │ Unknown 0.3% · [精确数据表]                    │
└──────────────────────────────────────────────┴───────────────────────────────────────────────┘
```

### 7.2 P12｜1280（导航后内容约 1032 px）

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ 容量管理 · Project A              截至 10:30 Asia/Shanghai · 有 1 项异常     │
├──────────────────────────────────────────────────────────────────────────────┤
│ 8.42 TiB │ 7日 +231 GiB │ 对象 12.4M │ 配额 68% │ Hot/Cold 72/28             │
├──────────────────────────────────────────────────────────────────────────────┤
│ 容量增长：折线 + 预测置信带 + 仅真实配额线                                  │
│ [查看精确数据表]                                                             │
├──────────────────────────────────────────────────────────────────────────────┤
│ 异常与预测：陈旧 18m · 预计 43–58 天触达软配额 · 证据窗口 30d               │
├──────────────────────────────────────────────────────────────────────────────┤
│ 数据分布（水平条；名称 / 容量 / 对象数；变化进入行详情）                     │
├──────────────────────────────────────────────────────────────────────────────┤
│ 配额：Soft / Hard 真实进度                                                   │
├──────────────────────────────────────────────────────────────────────────────┤
│ 层级：Hot / Cold / Archive / Unknown 堆叠条 + 精确数据表                     │
└──────────────────────────────────────────────────────────────────────────────┘
```

1280 摘要可换行为两行，但不变成卡片；项目分布、层级和异常垂直排列，禁止为了维持双列而压缩图表或制造页面横向滚动。

### 7.3 P13 策略页｜1440

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│ 数据生命周期 · Project A   [非删除锁定：Raw / Manifest / Published Version Manifest]         │
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│ 策略与模拟 | 执行记录 | 恢复 | 审计                                                        │
├────────────────────────────────────────┬─────────────────────────────────────────────────────┤
│ 策略表                                 │ 策略详情                                            │
│ 名称       范围    层级       状态     │ policy-17 · Project A / Raw                         │
│ Raw归档    Raw     Hot→Archive Paused  │ 窗口 02:00 Asia/Shanghai · v4 · ETag …              │
│ Cache降冷  Cache   Hot→Cold    Active  │ 允许：模拟；执行：缺少 execute capability           │
│                                        ├─────────────────────────────────────────────────────┤
│ 游标分页；100行时表内优化              │ 模拟影响                                            │
│                                        │ 1,284 对象 · 2.1 TiB · 未知 0 · 保护对象 1,284      │
│                                        │ 可用性：恢复 SLA 由服务端返回 · expires 10:45       │
│                                        │ [运行模拟] [执行迁移（禁用：权限不足）]              │
├────────────────────────────────────────┴─────────────────────────────────────────────────────┤
│ blocked/warnings · snapshot · input hash · impact digest · 审计入口                          │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 7.4 P13 策略页｜1280

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ 数据生命周期 · Project A                                                    │
│ 非删除锁定：Raw / Manifest / Published Version Manifest                      │
├──────────────────────────────────────────────────────────────────────────────┤
│ 策略与模拟 | 执行记录 | 恢复 | 审计                                         │
├──────────────────────────────────────────────────────────────────────────────┤
│ 策略表：名称 / 范围 / 迁移 / 状态（其他字段进入详情）                        │
├──────────────────────────────────────────────────────────────────────────────┤
│ 选中策略详情：窗口 · 时区 · 版本 · 权限 · allowed action                     │
├──────────────────────────────────────────────────────────────────────────────┤
│ 模拟影响：对象 / 字节 / 未知 / 保护 / SLA / 过期时间                         │
│ blocked reasons                                                              │
│ [运行模拟] [执行迁移]                                                        │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 7.5 危险确认｜两种宽度共同约束

```text
              ┌──────────────────────────────────────────────────────┐
              │ 确认将 1,284 个对象从 Hot 迁移到 Archive             │
              │ Project A · storage.lifecycle.execute                │
              │ Simulation sim-… · Snapshot snap-… · 还剩 08:31      │
              ├──────────────────────────────────────────────────────┤
              │ 2.1 TiB · Raw 1,284 · 活动引用 0 · 未知对象 0        │
              │ 恢复 SLA：服务端事实 … · Warning 1                   │
              │ ✓ 不删除/覆盖 Raw、Manifest、Published Manifest      │
              │ [ ] 我已理解访问延迟与恢复等待变化                   │
              ├──────────────────────────────────────────────────────┤
              │                           [取消] [确认迁移到 Archive] │
              └──────────────────────────────────────────────────────┘
```

弹窗最大宽度 640 px、视口内纵向滚动、内容区 `overscroll-behavior: contain`；取消默认焦点。缓存清理确认还要输入短语，不能复用迁移确认的单复选框。

## 8. Web Interface Guidelines 逐行发现

外部规则文件：2026-08-17 获取的 `command.md`。

| 规则行 | 当前代码行 | 发现与约束 |
|---|---|---|
| `command.md:19` async update 要有 `aria-live` | `frontend/src/pages/p13-storage-lifecycle/page.tsx:160-167` | Job/Simulation 状态变化只是普通 Descriptions；目标用 polite live region，错误仍用 alert |
| `command.md:53,103-104` 数字单位不换行并用 `Intl` | `frontend/src/features/storage-overview/metrics-contract.ts:34-47,68-84`、`frontend/src/pages/p13-storage-lifecycle/page.tsx:32-42` | 当前自定义 bytes/money formatter、整数截断和普通空格不一致；目标共享 `Intl` formatter + 不换行空格 |
| `command.md:71` 超过 50 行虚拟化/`content-visibility` | `frontend/src/pages/p13-storage-lifecycle/query-codec.ts:21,34-35`、`frontend/src/shared/ui/data/DataTable.tsx:186-197` | 支持 100 行但表格没有虚拟化；默认 50，100 行需 profile 后用虚拟化或 `content-visibility`，同时保留表语义 |
| `command.md:79-81` URL 反映状态并可深链 | `frontend/src/pages/p13-storage-lifecycle/page.tsx:75-90,116-117` | URL 虽写 ID，本地选中/Job state 未从 URL 恢复；刷新后详情丢失。数据选择必须由 URL + query data 派生 |
| `command.md:82` destructive action 要确认 | `frontend/src/shared/ui/ConfirmDialog.tsx:5-13,29-40`、`frontend/src/pages/p13-storage-lifecycle/page.tsx:229-255` | 当前通用确认只有 resource/impact，缺 scope、capability、新鲜模拟、保护声明、确认短语和未知提交恢复 |
| `command.md:87,89` drawer 控制 overscroll、谨慎 autofocus | `frontend/src/pages/p12-storage-overview/components/StorageObjectDrawer.tsx:23-32` | Close button 强制 `autoFocus` 没有任务收益；交由 Drawer 的可访问焦点管理，详情滚动不得传到页面 |
| `command.md:93-94` 修复非预期滚动并核对 flex/grid | `frontend/src/shared/ui/data/DataTable.tsx:186-197` | `max-content` 必须被表格容器截获；P12/P13 main 的 `min-width:0; max-width:100%` 是验收项 |
| `command.md:123` 错误要给下一步 | `frontend/src/pages/p12-storage-overview/page.tsx:280-313`、`frontend/src/pages/p13-storage-lifecycle/page.tsx:45-66,187-193` | P12 区域重试可沿用；P13 应为 contract mismatch、stale simulation、unknown outcome 分别给出恢复动作 |

## 9. React 19 + Vite 7 实现约束

当前 `frontend/package.json` 已使用 React 19.1.1、Vite 7.1.3、ECharts 6、TanStack Query 5、TanStack Table 8；P12/P13 路由已 lazy。后续实现遵循：

### 9.1 图表与 bundle

- 保留页面级 lazy route；把 ECharts 包装器放入独立、静态可分析的 `lazy(() => import('./CapacityCharts'))` 边界。只有 P12 图表进入视口/容量数据成功后才加载图表 chunk。
- ECharts 只注册实际使用的 line/bar、dataset、tooltip、legend、aria、canvas renderer，不从全量 barrel 导入。P13 默认表格化，不加载图表包。
- 动态模块必须有同尺寸 skeleton 和独立错误边界；加载失败自动保留精确数据表，不能让图表失败变成整页失败。
- 不预先写 `manualChunks`。以生产 build/analyzer 证据判断；若图表 chunk 仍污染首屏，再做稳定 vendor chunk，并记录 raw/gzip 前后差异。

### 9.2 请求与 query state

- 相互独立的 summary、growth、distribution、quota/tier、anomaly 查询同时启动，禁止在组件 `useEffect` 中串行触发；权限/作用域未知前不发请求。
- query key 至少含 organization/project/region、授权 scope revision、time range/grain、timezone 和合同版本。跨项目切换时不能展示上一项目数据；`placeholderData` 只可用于同一作用域的时间窗口切换。
- 每个区域独立处理 initial pending、background fetching、success、empty、partial、forbidden、stale、contract mismatch；整页 fatal 仅限身份/作用域 bootstrap 失败。
- query function 传递 TanStack 的 `AbortSignal`；Mutation 使用服务端 idempotency key。unknown outcome 先 GET operation，不自动重放。
- P13 的 `policyId`、`simulationId`、`executionId`、`restoreTaskId` 由 URL 驱动，从 query cache/返回数据派生对象；不要用 effect 把 props/query 复制到第二份 state。

### 9.3 大数据聚合

- 浏览器不接收全对象清单来算容量。项目/角色/层级聚合和历史 bucket 在服务端或聚合 Worker 完成。
- 时间序列合同限制可视点数：默认 90 个日 bucket；最大 366 日 bucket 或 60 月 bucket。更长窗口由服务端降采样，并返回 `grain`、`bucket_timezone`、缺口标记。
- 类别排行默认 top 20 + Other；精确列表走服务端 cursor。不得一次把所有项目/数据集加载进图表。
- 只有实测超过 50/100 行的渲染瓶颈后才启用行虚拟化；必须验证键盘导航、表头、读屏和行详情。无需复杂虚拟化时可用 `content-visibility:auto` 并给稳定 intrinsic size。
- adapter/query `select` 生成稳定的不可变聚合视图；昂贵派生才 `useMemo`，简单值直接 render；不得为了“优化”给所有对象/回调加 memo。

### 9.4 格式化

- 建一个共享纯函数层：bytes、integer、decimal percent、zoned instant、date range。locale/timezone 明确作为参数，不能读隐式浏览器默认值。
- 格式化只用于展示；排序、阈值、图表 domain 使用原始 typed value。`Unknown`、`Partial`、`Stale` 是状态而非字符串数值。
- Tooltip、坐标轴、表格和确认弹窗调用同一 formatter，避免同一个字节值出现不同精度或单位。

## 10. 技术设计：新 API、表与 Worker（当前不存在）

本节所有内容均标记为 **技术设计｜非现有能力**。当前仓库没有 P12/P13 真实后端路由或 lifecycle worker；生成 storage API 也不能证明已实现。

### 10.1 API 草案

| 技术设计 API | 用途 | 关键约束 |
|---|---|---|
| `GET /projects/{id}/storage/capacity-summary` | P12 摘要、health、freshness | 项目作用域；不返回金额字段 |
| `GET /projects/{id}/storage/capacity-series` | 历史/预测 bucket | range/grain/timezone；真实 threshold 单独标 source/effective_at |
| `GET /projects/{id}/storage/capacity-distribution` | 角色/数据集/层级聚合 | 权限裁剪、top N + Other |
| `GET /projects/{id}/storage/quota-status` | 生效 hard/soft quota | quota ID、source、有效期；未配置显式状态 |
| `GET /storage/capacity/projects` | 平台项目排行 | 仅平台 scope，cursor，不接受客户端自称全局 |
| `GET /projects/{id}/storage/lifecycle-policies` | P13 策略列表 | 服务端返回 scope、protection、allowed actions |
| `POST /projects/{id}/storage/lifecycle-simulations` | 迁移/归档/缓存清理模拟 | 不接受 DELETE/ABORT 动作；返回 expiry/digest |
| `POST /projects/{id}/storage/lifecycle-executions` | 执行迁移/归档/合格缓存清理 | capability + allowed action + ETag + fresh simulation + idempotency |
| `GET /projects/{id}/storage/lifecycle-executions` | 执行历史 | cursor、operation/audit ID、partial failure |
| `POST /projects/{id}/storage/restore-requests` | 请求归档恢复 | 服务端 SLA、目标层、到期策略 |
| `GET /projects/{id}/storage/restore-requests` | 恢复队列/历史 | cursor、provider state 对账 |
| `GET /projects/{id}/storage/lifecycle-audit` | 生命周期审计 | 项目审计权限；追加事实 |

返回 envelope 统一包含 `request_id`、`contract_version`、服务端 scope、`observed_at`；int64 为十进制字符串，时间为带 offset instant 并另给 IANA timezone。

### 10.2 表草案

- `storage_inventory_runs`：盘点范围、provider revision、completeness、started/completed/fresh_until。
- `storage_capacity_snapshots`：scope + bucket time + total bytes/count；追加写，保留 source run。
- `storage_capacity_dimensions`：snapshot 按 project/data role/tier 聚合；不存对象键到跨项目摘要。
- `storage_quota_policies`：软/硬限制、来源、版本、生效/到期、ETag。
- `storage_capacity_forecasts`：输入 snapshot/窗口/模型版本、P50/P90、目标 ID、失效原因。
- `storage_tiering_policies`：迁移策略、作用域、对象角色、层级、日程时区、版本、状态。
- `storage_lifecycle_simulations`：不可变输入/影响 digest、expiry、blocked/warning facts。
- `storage_lifecycle_executions` 与 `storage_lifecycle_execution_items`：operation 状态、游标/批次、对账事实和幂等键。
- `storage_restore_requests`：provider request、SLA、target tier、temporary expiry、状态对账。
- `storage_cache_cleanup_plans`：资格证据、重建来源、引用图 revision；只保存模拟计划，不扩大可删除角色。
- `storage_lifecycle_audit_events`：追加式 actor/capability/scope/before-after digest/request-operation ID。

DDL 必须包含租户/项目 RLS 或等价强制作用域、不可变审计/模拟触发器、唯一幂等键和受保护对象拒绝约束。具体表型、分区、保留期和索引需要技术评审。

### 10.3 Worker 草案

- Inventory collector：读取 provider inventory/version/object lock 与内部 Manifest 引用，产生完整性可证明的 snapshot。
- Capacity aggregator/forecaster：按 scope/role/tier 聚合、生成有限 bucket 和带模型版本的预测；不输出无真实目标的触达日期。
- Lifecycle planner：把策略 + snapshot 编译成不可变模拟，强制排除受保护角色和未知对象。
- Migration executor/reconciler：分批、限流、幂等迁移并与 provider 状态对账；部分失败可重试，不删除源业务事实。
- Restore reconciler：追踪 provider restore 状态、可读取窗口和到期。
- Cache cleanup executor：只消费通过资格锁定的 plan，执行前再次校验引用/保护/快照；任何漂移即停止。
- Audit projector：将追加事件投影为 P13 执行/审计列表。

## 11. 设计渲染清单

每个场景至少出 1440 和 1280 两张；使用真实长度中文、稳定 ID、未知/长名称，不使用 lorem ipsum。

| 编号 | 必渲染场景 | 固定数据/状态 | 验收重点 |
|---:|---|---|---|
| R1 | P12 容量管理 | 项目管理员；真实 soft/hard quota；90 日历史；有预测置信带；4 层级含 Unknown | 无 Tab/筛选/Card；无禁止概念；目标线来源可见；有精确表 |
| R2 | P12 部分失败 | summary/growth 成功，distribution 403，quota 5xx，snapshot stale | 成功区不消失；逐区 request ID/重试；不把未授权/失败显示成 0 |
| R3 | P13 策略 | 项目管理员只读；Raw Hot→Archive；模拟成功但无 execute；非删除锁开启 | 作用域/时区/版本/allowed action 清楚；执行按钮禁用且解释原因 |
| R4 | P13 危险确认 | 平台运维；新鲜模拟；1 warning；归档迁移；另一变体为缓存清理 | 显示身份/权限/scope/digest/expiry/SLA/保护声明；取消默认焦点；缓存清理要求短语 |
| R5 | P13 非删除锁 | protection verification stale 或 Manifest 引用不完整 | 常驻锁变 warning/error；所有危险动作阻断；无任何业务删除入口 |

渲染检查还包括 200% zoom、键盘完整操作、暗/亮主题对比度、长项目名、空状态、contract mismatch、reduced motion、读屏图表替代表和 `body.scrollWidth === body.clientWidth`。

## 12. 待确认、待验证与可直接实施

### 12.1 需产品确认

- 谁拥有 quota，soft/hard 阈值如何配置；P12 只读还是允许跳到独立治理流程。
- 普通业务用户是否能申请项目级容量摘要；外包账号的默认导航和脱敏阈值。
- 迁移/归档/缓存清理是否要求双人复核、哪些环境强制开启。
- Export 的保留边界；本稿默认不推导任何删除权限。
- Hot/Cold/Archive 的产品命名、恢复模式与服务端 SLA 展示文案。
- 预测模型可接受的最小样本、置信表达和告警提前量。
- 审计保留期、导出范围和谁可以看 actor 身份。

### 12.2 需技术验证

- 生产 bucket versioning、Object Lock、IAM deny-delete/overwrite 与条件写是否逐环境实际启用；ingest rollout objects 缺 DB immutable trigger 的风险如何补齐。
- provider inventory 的延迟、完整性、版本/层级字段与内部 Manifest 引用图如何对账。
- P12 聚合 API 的事实源、snapshot consistency、int64/BigInt、timezone/day bucket 和 partial contract。
- quota 来源是否存在，若不存在则 P12 不渲染 quota 目标图。
- lifecycle API/表/Worker 的边界、幂等、并发前置、RLS、审计不可变和 provider 失败恢复。
- 缓存可重建性与零引用证明是否可由服务端强校验。
- ECharts 拆包后的生产 raw/gzip、LCP/TTI 和精确表 fallback；100 行表是否确需虚拟化。
- 生成 OpenAPI 的权威来源与再生成流程；当前前端生成文件不得手改成为“实现”。

### 12.3 可直接实施（取得代码任务授权后）

- 按第 2.2 节删除 P12 旧费用组件/类型/query/schema/Mock/capability 和合同字段，并增加全链 forbidden-term CI gate。
- P12 改成无 Tab、无筛选器、无 Card 的单页布局；修复图例/图形语义、共享 formatter、图表精确表和区域级状态。
- P13 移除 Multipart/Abort intent 与 DELETE/ABORT action；把 URL ID 变为可恢复的 query-driven selection。
- 扩展确认组件的 scope/capability/simulation/protection/typed phrase/unknown outcome contract。
- 为 R1–R5 增加 component、a11y、query-state 和 1440/1280 screenshot tests；真实后端未完成前明确标 `fixture`，不写“已联调”。

“可直接实施”只表示产品方向已由本稿约束，不代表本轮获准改代码；新 API、表、Worker 仍需先完成技术设计评审。

### 12.4 未验证/缺失资料

修订计划引用的原始方向文档 `PICO 云边协同机器人数据闭环平台.md`、`VR/具身智能多模态数据平台.md`、`统一数据管理系统技术方案.md`、`数据平台 评测.md` 经 `/home/czy` 完整递归搜索仍未找到。本稿采用修订计划中的派生结论，未声称通读这些原文。若原文存在于外部挂载，应补做方向一致性复核。

当前仓库也没有可验证的 P13 component/E2E 测试，P12 只发现 `frontend/src/pages/p12-storage-overview/components/StorageOverviewPanel.test.tsx` 与 `frontend/src/pages/p12-storage-overview/components/StorageSummaryStrip.test.tsx`；历史状态文档中的测试描述不能替代当前文件证据。

## 13. 验收与复核命令

实现阶段建议按以下命令取证；本轮规格交付的实际命令记录见文末：

```bash
# P12 全链禁止概念门禁；预期 P12 运行时代码、合同、Mock、能力目录均无命中
rg -n -i '(cost|fee|billing|price|budget|currency|money|billed|savings|费用|成本|账单|计费|价格|预算|金额|币种|月费)' \
  frontend/src/pages/p12-storage-overview frontend/src/features/storage-overview \
  frontend/src/mocks/{fixtures,handlers,scenarios}/storage-overview* \
  frontend/src/entities/capability.ts frontend/src/features/access/capability-catalog.generated.ts \
  frontend/src/shared/api/generated/{access,storage}.ts

# P13 禁止动作门禁；预期 P13 领域/API/页面/合同无业务删除或 Multipart 终止动作
rg -n '(DELETE_OBJECT|ABORT_MULTIPART|abortMultipart|IRREVERSIBLE_DELETE|IRREVERSIBLE_ABORT)' \
  frontend/src/pages/p13-storage-lifecycle frontend/src/features/lifecycle \
  frontend/src/entities/lifecycle-policy.ts frontend/src/shared/api/generated/storage.ts

# 页面横向滚动（在 Playwright 中断言 1440/1280 两档）
# expect(await page.evaluate(() => document.body.scrollWidth === document.body.clientWidth)).toBe(true)

# 常规质量门禁
npm --prefix frontend run typecheck
npm --prefix frontend run test -- --run
npm --prefix frontend run build
```

本轮实际执行/读取命令类别：

```text
git status --short
rg --files / rg -n / find
sed -n / nl -ba
获取最新版 web-interface-guidelines command.md
获取最新版公开 react-best-practices SKILL.md 及 5 个相关规则文件
```

最终审计结果：

- 对本文件执行完整禁止概念搜索；所有命中都属于现状证据、否定性范围/门禁或搜索命令，没有任何费用类功能提案。
- 对本文件执行 P13 禁止动作搜索；`DELETE_OBJECT`、`ABORT_MULTIPART`、`abortMultipart` 只出现在现状偏差证据和门禁命令中，目标 IA、API、表、Worker 和交互没有提出这些动作。
- 引用路径检查通过；四份明确列为“未找到”的方向原文和两个在线规则文件不按本地路径验证。
- Markdown fence 数为 26，成对；无行尾空白。`git diff --no-index --check /dev/null plan/frontend-ux-parallel/T08-P12-P13-SPEC.md` 无空白诊断（新文件差异使命令按 Git 语义返回 1）。
- 最终 `git status --short` 去除 `?? plan/frontend-ux-parallel/` 后与第 1.4 节初始状态一致。该目录有其他并行交付文件；T08 只写入本规格文件，未触碰它们。

本轮未运行前端 typecheck/test/build，因为没有改动运行时代码；这些命令保留为取得实现授权后的门禁。

## 14. 最高风险

最高风险不是布局，而是“Mock/生成合同看起来完整”掩盖真实安全边界：当前 P13 类型与确认流程明确允许删除/Abort，而真实后端和 Worker 又不存在；若直接接入对象存储操作，前端权限判断可能被误当成授权与保护机制。必须先把服务端非删除约束、对象锁/引用对账、作用域授权和新鲜模拟做成可验证事实，再开放任何迁移、归档、恢复或缓存清理执行入口。
