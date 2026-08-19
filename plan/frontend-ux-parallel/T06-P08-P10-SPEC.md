# T06｜P08 数据标注 + P10 标注任务 UX 规格

> 状态：效果图与实现前评审稿（不代表 API 已定稿）
> 日期：2026-08-17（Asia/Shanghai）
> 范围：P08 标注工作台、P10 标注任务；只定义 UX、页面合同、验收与待决事项
> 非目标：本文件不修改前端、后端、OpenAPI、数据库、Worker、部署配置或其他计划，也不授权发布动作

## 0. 决策摘要

1. **P08 是统一 `DataVisualizationWorkbench` 的 `Annotation` 模式，不再自带任务队列。** 相机、曲线、播放、时间轴、Lance 预览、对比和资源错误由 T02 提供；P08 只增加标签、属性、评论及标注项列表。
2. **P10 是任务入口和管理面。** 它承接我的任务、可领取任务、待审核任务、全量任务、分配、进度、截止时间与阻塞原因，不显示相机或另一套时间轴。
3. **不按角色复制页面。** 标注员、任务管理员、审核员使用相同 P10/P08 结构；服务端数据范围、capability 与对象状态共同决定可见数据和可执行动作。
4. **预览默认来自固定 Lance 版本。** 默认不出现 Raw 下载；即便未来有单独 Raw 能力，也不得把下载按钮放进 P08/P10 的主路径。
5. **区间以逻辑步为事实值。** 合同使用半开区间 `[start_step, end_step)`；默认界面显示业务步数、时长和时间，0 基技术步与映射详情只在“技术信息”中展开。
6. **草稿、提交、审核、发布严格分层。** 标注员的主操作文案固定为“保存草稿”“提交审核”；审核员可“审核通过”或“要求修改”；标注员与审核员均不能在这两页发布。
7. **不允许静默丢稿或最后写入覆盖。** 服务端自动保存、手动保存、短期本地恢复、离页拦截、冲突保全和版本追踪必须形成一个可观察状态机。
8. **1280 是完整桌面工作宽度，不是降级平板模式。** P08 在 1280 仍须同时保留可读相机、时间轴、主动作和可用的标注工具；通过 T02 的 rail/drawer 与 2×2→1×N 容器响应解决拥挤，不允许整页横向滚动。

## 1. 基线、范围和可追溯性

### 1.1 启动时工作树记录

开始审阅前执行了 `git status --short`，原样记录如下。以下均视为用户或并行任务已有改动，本任务不触碰：

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

### 1.2 已读材料与限制

- 已完整阅读总修订计划 `plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md`、Master `design-system/hc-data-platform/MASTER.md`、现有 P08/P10 页面、样式、annotation feature、实体、Mock、生成类型、viewer、相关测试与后端 annotation/preview/Lance 事实。
- 初次审阅时并行目录尚不存在；最终校验时新批次 `plan/frontend-ux-parallel/T02-DATA-VISUALIZATION-WORKBENCH-SPEC.md` 已出现，随后已完整读取 648 行并据此重整本文。`frontend/docs/status/T2.md` 仍只是 P01/P12/P19 的旧交付记录，不能代替新 T02。
- 总计划提到的四份方向文档（PICO 跨本体遥操作、VR 全身遥操作、统一机械臂规控/遥操作/采集平台、数据平台评测）在工作区及可搜索的本机路径中均未找到。本文只采用总计划已提炼的 Lance 预览、30 Hz、半开区间、审核/发布分离等规则；方向文档原文仍属未核验项。
- 任务点名的四个 skill 未出现在本会话可调用 skill 清单中，不能声称按注册 skill 执行。降级方式为：只读现有 frontend-design 参考副本、只读当前 ui-ux-pro-max 公开说明、抓取 2026-08-17 最新 Web Interface Guidelines `command.md` 与 Vercel React Best Practices，并以仓库 Master 为视觉事实源；未持久化或覆盖 Master。
- Web 审阅基准：`https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md`。
- React 审阅基准：`https://github.com/vercel-labs/agent-skills/blob/main/skills/react-best-practices/SKILL.md`。项目是 React 19 + Vite 7，不采用 Next.js 专属建议。

### 1.3 与 T02 的衔接及 T06 窄化项

T06 接受 T02 的唯一组件树、固定 identity、adapter/capability 投影、单一 selection、同构 source/edit/compare、容器响应、右侧 drawer、局部资源错误、键盘 focus scope 和性能预算（`plan/frontend-ux-parallel/T02-DATA-VISUALIZATION-WORKBENCH-SPEC.md:67-137,147-165,365-437,552-590`）。以下三处按本任务硬约束做页面级窄化，不修改 T02：

1. T02 矩阵把 Annotation 的 `QueueNavigatorSlot` 写为“标注任务队列”（`:130-135`）；T06 明确 P10 是唯一任务队列，P08 不向该 slot 注入跨任务队列。P08 左 rail 只保留共享 stream/tool 入口，标注项列表属于标签工具，可在 drawer 中打开。
2. T02 的 Annotation 主动作示例仍采用“复核”词族（`:135,147,488`）；T06 的最终用户文案固定为“提交审核”。内部 intent 可以叫 `canSubmitForReview`，不能泄漏成按钮词。
3. T02 数值示例把技术半开区间放在主读数（`:323-335`）；本任务要求业务步/时间优先，因此 P08 主界面显示“第 301–450 步（150 步）”，技术详情才显示 `[300,450)`。数据语义仍完全遵守 T02 的半开 step 合同。

## 2. 代码与合同证据

| 结论 | 仓库证据 | 对设计的约束 |
|---|---|---|
| 只允许质检通过后进入 30 Hz/Lance；预览从 Lance 生成 | `plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:47-48` | P08/P10 不提供 Raw 兜底预览，不允许人工绕过质检 |
| 审核与发布是两套授权 | `plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:49`；`backend/src/hc_data_platform/annotation/README.md:18-21` | P08 到“审核通过”为止，不出现发布按钮 |
| P08 只加业务工具，不复制可视主体 | `plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:103-125`；`design-system/hc-data-platform/MASTER.md:129-145` | T02 拥有相机/曲线/时间轴/P08 只拥有 annotation slot |
| T02 已定义唯一组件树与 adapter 边界 | `plan/frontend-ux-parallel/T02-DATA-VISUALIZATION-WORKBENCH-SPEC.md:67-137,147-165` | P08 只能注入 Annotation 数据/工具/动作，不能再扩展私有 viewer 壳 |
| T02 以容器宽度处理 1280 | `plan/frontend-ux-parallel/T02-DATA-VISUALIZATION-WORKBENCH-SPEC.md:365-393` | 工作台容器 <1120 px 时左栏 44 px rail、右栏 overlay drawer；相机卡不低于 280 px |
| 1280 当前已知溢出 | `plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:84`；`design-system/hc-data-platform/MASTER.md:123` | 1280 必须纳入效果图和视觉回归，不接受只测 1440 |
| P10 应展示分配、进度、截止与阻塞 | `plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:155` | 这些字段缺合同，不能仅在前端推算成“真实”数据 |
| 当前导航仍把 P10 指向清洗草稿 | `frontend/src/app/shell/navigation-manifest.ts:54-65`；`frontend/src/pages/p10-cleaning-drafts/routes.tsx:11-21` | P10 信息架构与路由需迁移，旧清洗页不能改名冒充标注任务 |
| 当前 P08 同时是队列和编辑器 | `frontend/src/shared/routing/route-registry.ts:21-24`；`frontend/src/pages/p08-data-annotation/AnnotationQueuePage.tsx:110-124` | 队列职责移交 P10，P08 只保留深链编辑/审核入口 |
| 当前 P08 又在工作台左栏重复任务列表 | `frontend/src/pages/p08-data-annotation/AnnotationTaskPage.tsx:794-836` | P08 删除任务队列；切任务回 P10，保留返回时的筛选和滚动位置 |
| 当前 P08 使用 `EpisodeWorkbenchCore`，尚无名为 `DataVisualizationWorkbench` 的实现 | `frontend/src/pages/p08-data-annotation/AnnotationTaskPage.tsx:46-49,850-877` | 不把现有 P08 私有壳误认成 T02 合同；迁移等待 T02 |
| 当前 1280 CSS 仍固定三栏且相机最小 380 px | `frontend/src/pages/p08-data-annotation/workbench.module.css:90-95,312-315,433-437` | 平台侧栏扣除后相机易堆叠；改用 T02 左 rail + 右 drawer，预期容器内保持可读 2×2 slots |
| 当前存在 P08 私有颜色和两套布局 CSS | `frontend/src/pages/p08-data-annotation/p08.css:1-24`；`frontend/src/pages/p08-data-annotation/workbench.module.css:1-95` | 改用 Master semantic tokens，不再维护第三套工作台视觉语言 |
| 当前主操作文案和目标文案不一致 | `frontend/src/pages/p08-data-annotation/AnnotationTaskPage.tsx:731-750` | 标注员只看到“保存草稿”“提交审核”；审核文案清晰区分 |
| 当前原始纳秒直接进入表单与时间带 | `frontend/src/features/annotation/forms/SchemaDrivenAnnotationForm.tsx:27-29,112`；`frontend/src/pages/p08-data-annotation/AnnotationTaskPage.tsx:878-915` | 默认显示步/时间；纳秒只在技术详情、复制诊断信息中出现 |
| 当前播放方向键按固定 100 ms 移动 | `frontend/src/features/viewer/EpisodeWorkbenchCore.tsx:82-107` | Annotation 模式应按 preview mapping 移动 1/10 个逻辑步，不把频率写死在 P08 |
| 当前只有短期本地恢复，不是服务端自动保存 | `frontend/src/features/annotation/drafts/local-draft.ts:45-67,90-106` | 新方案必须增加可观察的服务端自动保存；本地副本只是第二道保护 |
| 当前仅返回“我的/可领取”两种查询 | `frontend/src/features/annotation/api/client.ts:31-42,132-171` | P10 管理员全量、审核队列与分配需要正式查询合同 |
| 生成草案有 assign 与 all，但手写 client 未实现 assign | `frontend/src/shared/api/generated/annotation.ts:71-85,263,984-1002`；`frontend/src/features/annotation/api/client.ts:186-252` | P10 分配不可直接编码，须先统一 API 来源与命令样式 |
| wire 有提交/审核/参考版本，adapter 丢失它们 | `frontend/src/features/annotation/api/wire-schemas.ts:169-177`；`frontend/src/features/annotation/api/adapter.ts:182-205` | 版本、审核记录、对比基线需要正式 domain model，不读原始 record |
| 当前任务实体没有截止时间或进度口径 | `frontend/src/entities/annotation-task.ts:65-83` | P10 不用 `updatedAt` 冒充截止时间，也不按草稿条目数猜完成率 |
| 当前 annotation Mock 固定 3 相机 + 7 轴，但仍以 ns 为源范围 | `frontend/src/mocks/fixtures/annotation/index.ts:30-65,119-135` | 可复用多相机密度做效果图；不能沿用 ns 作为 P08 默认交互模型 |
| 当前 Mock 表单直接要求“开始纳秒/结束纳秒” | `frontend/src/mocks/fixtures/annotation/index.ts:153-163` | 效果 Mock 也要迁到业务步/时间 adapter，避免 UI 改了但夹具仍固化旧概念 |
| Mock 覆盖空态、冲突、局部媒体失败和 preview pending，但没有 assign handler | `frontend/src/mocks/scenarios/annotation.ts:6-10`；`frontend/src/mocks/handlers/annotation.handlers.ts:21,61-69,139-225` | 状态夹具可复用；P10 分配/改派与部分成功必须新增正式合同及 Mock，不能借 claim 冒充 |
| 前端产品草案与真实后端合同不兼容 | `plan/P01-P19-REAL-API-COVERAGE-AUDIT.md:44` | UX 字段须标“合同待补”；不得把 Mock 类型升级为事实合同 |
| 后端区间是半开逻辑步且不改 Raw/Lance | `backend/openapi/annotation.yaml:430-438,483-490`；`backend/src/hc_data_platform/annotation/README.md:3-6` | 所有标注版本都是非破坏性叠加；编辑器不得写源数据 |
| Lance 暴露稳定逻辑步，不暴露物理行地址 | `backend/src/hc_data_platform/lance_catalog/models.py:50-64,150-162` | UI 以逻辑步交互，不展示 Lance 行号/对象地址 |
| 预览合同提供时间到源步映射 | `backend/openapi/preview.yaml:48-69,78-100`；`backend/src/hc_data_platform/preview/models.py:46-82,128-156` | UI 从 descriptor 映射，不用固定 30 Hz 自行猜测每段实际时间 |
| 并发保存只允许一个胜者；需保留历史 | `backend/tests/annotation/test_annotation_contract.py:114-186,258-352` | 冲突时停止自动覆盖，保全本地改动并提供结构化重放 |
| 自审被拒绝，发布者另有职责 | `backend/tests/annotation/test_annotation_api.py:117-185`；`backend/tests/annotation/test_annotation_contract.py:189-228` | 同一人即使同时拥有能力，也要由服务端拒绝自审；UI 先行解释 |
| 当前 viewer 只测过拖拽和少量键盘微调 | `frontend/src/features/viewer/EpisodeWorkbenchCore.test.tsx:75-110` | 完整快捷键、输入框冲突、读屏与范围映射需新增测试 |
| T5 状态文档声称存在 annotation 测试，但当前源码不存在 | `frontend/docs/status/T5.md:30-34` | 历史交付描述不能当当前可执行证据，效果图后重新建立验证基线 |

## 3. 产品信息架构与路由

### 3.1 页面职责

| 页面 | 主要工作 | 不承担 |
|---|---|---|
| P10 标注任务 | 找任务、筛选、领取、分配、查看进度/截止/阻塞、进入编辑或审核 | 相机预览、时间轴编辑、标签/属性录入 |
| P08 数据标注 | 对一个固定任务和固定 Lance 基线进行查看、标注、评论、保存草稿、提交审核、审核对比 | 跨任务运营、批量分配、发布、Raw 下载 |

推荐最小迁移路由：

- P10：`/annotations/tasks`，列表筛选、Tab、排序、游标和分页均进 URL。
- P08：沿用 `/annotations/tasks/:taskId` 作为任务深链；模式由对象状态/capability 派生，必要时用 `?view=annotate|review|readonly` 表达允许的呈现，不用查询参数扩大权限。
- 旧 `/annotations`：迁移期仅重定向到 P10，并保留可安全映射的 query；不得继续渲染第二份队列。
- P08 返回 P10 时恢复原 Tab、筛选、页游标和滚动位置；通知/待办深链可以直接进 P08，但面包屑仍指向“标注任务”。
- P10 当前 `/manual/drafts` 的清洗语义应由 P11/后续清洗信息架构接管；具体迁移需路由 Owner 确认，不能在 T06 中直接删除旧入口。

## 4. 端到端工作流与状态机

```text
任务创建
  │
  ├─ 未分配 ──领取/管理员分配──> 已分配
  │                              │
  │                              ├─ 打开 P08 ──> 编辑中
  │                              │                 │
  │                              │                 ├─ 自动保存/保存草稿 ─┐
  │                              │                 │                    │
  │                              │                 └─ 提交审核 ─────────┘
  │                              │                                      │
  │                              └──────────────────────────────> 待审核
  │                                                                     │
  │                                        ┌─ 要求修改 ─> 修订中 ───────┘
  │                                        │
  │                                        └─ 审核通过 ─> 已完成
  │
  └─ 取消/失效/基线过期：进入只读历史或创建后继任务，不原地改写已提交版本

发布：在独立发布流程冻结版本；不属于 P08/P10
```

### 4.1 状态与动作规则

| 任务状态 | P10 行主动作 | P08 呈现 | 允许的版本动作 |
|---|---|---|---|
| 未分配 | 有领取能力显示“领取任务”；管理员显示“分配” | 默认不进入编辑 | 无 |
| 已分配 / 编辑中 | “继续标注” | 可编辑；显示草稿保存状态 | 自动保存、保存草稿、提交审核 |
| 待审核 | 审核员“开始审核”；标注员“查看提交” | 固定提交版本 + 可选基线/前版对比 | 审核通过、要求修改；不改提交快照 |
| 要求修改 / 修订中 | “继续修改” | 审核意见常驻，创建新草稿修订 | 保存草稿、再次提交审核 |
| 审核通过 / 已完成 | “查看标注” | 只读版本与完整审核轨迹 | 无发布动作 |
| 基线过期 | “处理基线变更” | 旧草稿只读；展示迁移/后继任务说明 | 有 `rebase` 能力才创建后继任务 |
| 已取消 / 不可用 | “查看原因” | 只读；不加载无意义编辑工具 | 无 |

每次领取、分配、保存、提交、要求修改、审核通过、基线迁移都必须生成可审计事件，并在版本轨迹显示操作者、时间、来源版本、结果和 request ID（技术详情中）。

## 5. P10 标注任务规格

### 5.1 页面结构

使用 Master 的 `StandardPageScaffold`：面包屑、标题“标注任务”、一句范围说明、一个主动作、状态概览、筛选栏、任务表、游标分页。不要使用 P08 的私有 teal 样式，不使用“信号轨道”——Master 将信号轨道限定为真实生命周期表达，任务列表不是生命周期总览。

顶部只保留一个主动作：

- 标注员：有领取能力且存在可领取任务时为“查找可领取任务”，否则无主按钮。
- 任务管理员：有创建合同后为“创建任务”；没有正式合同则不显示假按钮。
- 审核员：默认 Tab 已经是“待我审核”，不再加重复 CTA。

### 5.2 同页、不同数据范围

| 能力投影 | 默认 Tab | 可见 Tab | 默认数据范围 | 特有操作 |
|---|---|---|---|---|
| 标注员 | 我的任务 | 我的任务；有 claim 能力时可见“可领取” | 服务端从主体派生本人，不接收可伪造 assignee | 领取、进入标注 |
| 任务管理员 | 全部任务 | 全部、未分配、我的任务、待审核、已完成 | capability 允许的 project + region | 单项/批量分配、改派、查看阻塞 |
| 审核员 | 待我审核 | 待我审核、已审核；若同时是标注员也可见“我的任务” | 服务端分配给本人或授权审核范围 | 进入审核 |

规则：

- 页面结构和列顺序不因角色变化；没有能力的 Tab、列级敏感内容和操作均不渲染。
- 普通标注员的默认请求必须是 `assigned_to_me`；前端不得用 assignee ID 模拟服务端范围。
- 同一人拥有多种能力时合并 Tab，不新增“管理员版页面”；对象级拒绝仍以服务端为准。
- P10 不显示 Raw 对象位置、Raw 下载、Lance URI、纳秒、完整内部 ID；“技术信息”抽屉只给有相应读能力的人。

### 5.3 筛选、表格与批量动作

筛选栏：关键词（任务名称/采集条目业务编号）、状态、数据集、Schema、截止范围、优先级、阻塞状态、负责人（仅管理员）、排序。输入在 300 ms 停顿后或按 Enter 更新 URL；清除一个筛选只清该键，改变筛选/排序/limit 必须清游标。

推荐列（1440 全量，1280 按优先级收敛）：

| 优先级 | 列 | 显示规则 |
|---|---|---|
| P0 | 任务 | 任务标题 + 采集条目业务编号；内部 ID 只在技术详情 |
| P0 | 状态 | 中文状态 + 图标/文字，不能只靠颜色 |
| P0 | 进度 | `已标注 18/24 项 · 覆盖 76%`；口径由服务端提供，未知显示“尚未计算” |
| P0 | 负责人 | 头像/姓名；未分配明确显示；管理员可打开分配浮层 |
| P0 | 截止时间 | locale 格式 + “剩余 6 小时/已逾期 2 天”；来源字段必须为 dueAt |
| P1 | 阻塞 | 首要原因 + 共 n 项；可展开全部及恢复建议 |
| P1 | 数据范围 | 数据集、采集条目、Schema 的业务名 |
| P1 | 最近活动 | locale 格式，说明“草稿已保存/已提交/已退回”，不用裸 ISO |
| P2 | 优先级 | 中文枚举，未知值安全降级 |
| P0 | 操作 | 每行一个主动作 + 更多菜单；导航用 Link |

在 1280：合并“数据范围”为任务副标题，隐藏“优先级”独立列，截止与阻塞保持可见；表格自身可横向滚动作为最后保护，但主动作不得落到初始可视区外。

批量分配只在管理员能力下出现：先选择任务，再显示粘性批量栏“已选 12 项 / 分配负责人 / 清除选择”。提交前展示可分配、被锁定、不在范围、已提交四类计数；部分成功逐项回写，不把失败项从选择中清掉。改派必须说明对现有草稿/租约的影响，合同未定前不进入实现。

### 5.4 P10 不得推算的字段

- 截止时间不能用 `updatedAt + 固定天数` 生成。
- 进度不能仅用当前草稿条目数除以 Schema 字段数生成；区间覆盖、必填对象、质量门禁需要后端统一口径。
- 阻塞原因不能只读取 HTTP 错误；应是任务投影中的稳定 code + 本地化 message + recovery action。
- “待我审核”不能用前端拉全量后筛选；必须由服务端限定范围和稳定分页。

## 6. P08 标注工作台规格

### 6.1 与 T02 的复用合同

P08 只能通过 T02 的 `DataVisualizationWorkbench` 组合，不拥有相机网格、播放时钟或第二套时间轴：

| T02 拥有 | P08 注入 |
|---|---|
| 固定 Lance 版本、preview descriptor、step/time mapping | 当前草稿/提交/审核版本引用 |
| 固定 camera slots、曲线、动作、状态、3D 与单资源错误边界 | 标签叠层、标注区间轨道、当前选区 |
| 播放、逐步、缩放、同步游标、键盘事件边界 | 标签快捷键、设入点/出点、创建标注 |
| 原始/编辑后/对比的共享模式 | 草稿对基线、修订对上次提交、审核对比的 adapter |
| 工作台 responsive slots、持久尺寸与无整页横向滚动 | 标签/属性/评论工具面板和标注项列表 |

T02 的 `QueueNavigatorSlot` 在 P08 Annotation adapter 中为空；返回/相邻任务都经 P10 URL 状态处理。`StreamNavigator`、`ToolPaletteSlot` 和 `ModeEditorSlot` 仍按 T02 复用，不能用“去队列”为理由删除共享流选择或检查器。

P08 禁止：复制 camera card、复制曲线图、自己订阅媒体窗口、在 T02 外再画一个纳秒时间带、把 P10 队列塞进左栏。

### 6.2 桌面布局规则

- 顶部任务栏固定为两行以内：返回“标注任务”、任务业务名/状态/负责人、保存状态、版本入口、次动作“保存草稿”、唯一主动作“提交审核”。审核员将主动作替换为“完成审核”，展开后选择“审核通过”或“要求修改”。
- 中部 T02 可视区始终优先；P08 工具不能把相机卡压到 T02 的 280 px 最小可读宽度以下。预期 1280 平台壳内保持约 450 px 的 2×2 slots，不因打开检查器重排为纵向全堆叠。
- 左侧“标注项”是 annotation tool 的一部分：显示当前时间附近、未完成、全部三个视图；不显示其他任务。
- 右侧工具面板固定三个一级页签：标签、属性、评论。审核反馈在“评论”顶部常驻，不另造第四个编辑器；容器小于 1120 px 时它按 T02 成为 overlay drawer，不参与中心区宽度计算。
- 底部只使用 T02 时间轴：视频缩略、播放头、标注轨道、评论锚点和选区在同一坐标系；轨道高度按内容扩展到上限，超出后轨道内部滚动。
- 技术信息（taskId、revisionId、content hash、Lance version、`[start_step,end_step)`、request ID）放在右上“技术信息”抽屉，默认关闭。
- 长业务名允许两行；ID 使用 `overflow-wrap:anywhere`；计时、步数、版本号使用 tabular figures。

### 6.3 1440 线框

```text
┌ 平台侧栏 218 ┐┌──────────────── P08 数据标注 / Annotation ─────────────────────┐
│              ││ ‹ 标注任务  抓取水杯 · 第 14 段   [修订中]  负责人 王敏       │
│              ││ 审核意见 2 · 草稿 v7  已保存 14:32   版本记录  保存草稿 [提交审核]│
│              │├──────────────┬───────────────────────────────┬────────────────┤
│              ││ 标注项 210   │ T02 工具栏：相机/曲线/状态/对比 │ 标签 |属性|评论 │
│              ││ ○ 当前附近 8 │ ┌Front camera┬Left wrist──┐ │ 搜索标签…       │
│              ││ ○ 未完成 3   │ │ 目标叠层    │             │ │ 1 抓取开始      │
│              ││ ● 全部       │ ├Right wrist─┼Robot scene─┤ │ 2 接触           │
│              ││ #187 接触    │ │             │             │ │ 3 放置完成      │
│              ││ #188 抓取    │ └────────────┴────────────┘ │ 属性：左右手…   │
│              ││ #189 放置    │ ┌────关节/动作曲线（共享游标）─┐ │ 当前区间         │
│              ││              │ └───────────────────────────┘ │ 第301–450步     │
│              │├──────────────┴───────────────────────────────┴────────────────┤
│              ││ ▶ 00:10.000 / 02:04.000  [-][+]  [? 快捷键]                  │
│              ││ 缩略帧  |───────────────播放头────────────────────────────| │
│              ││ 标注轨  | 接近 |████ 接触 ████| 抓取 |                      │
│              ││ 评论轨  |             ◆“遮挡后边界需复核”                   │
└──────────────┘└───────────────────────────────────────────────────────────────┘
```

### 6.4 1280 线框

```text
┌ 侧栏 200 ┐┌──────────────── P08 数据标注 ────────────────────────────────┐
│          ││ ‹任务 抓取水杯·第14段 [修订中] 已保存14:32 保存草稿 [提交审核]│
│          │├────┬────────────────────────────────────────────────────────┤
│          ││[»] │ [Source] [Edit] [Compare] [标注项210] [检查器 I]       │
│          ││ S  │ ┌────────Front camera──────┬────Left wrist─────────┐   │
│          ││ T  │ │                           │                       │   │
│          ││ ?  │ ├────────Right wrist───────┼────Robot scene────────┤   │
│          ││    │ │ 单路生成中（槽位不跳动）  │                       │   │
│          ││    │ └───────────────────────────┴───────────────────────┘   │
│          ││    │ 曲线：左臂 7 DOF（其余组折叠）                         │
│          │├────┴────────────────────────────────────────────────────────┤
│          ││ ▶00:10.000 [第301–450步] 时间轴/标注轨/评论轨 [?]            │
│          ││                  ┌─检查器 overlay drawer：标签|属性|评论─┐    │
│          ││                  │ 搜索标签… · 当前区间 · 审核意见       │    │
└──────────┘└──────────────────┴───────────────────────────────────────┴────┘
```

1280 行为：按 T02 的约 1020 px 工作台容器计算，左侧收成 44 px rail，右侧标签/属性/评论为不改变中心宽度的 drawer；默认媒体 2×2，每格约 450 px，避免当前三路相机纵向堆叠。只有容器进一步缩小时才按 T02 退为 1×N，且单卡不低于 280 px。标注项由 rail 打开，曲线按组折叠，任务元数据进入详情抽屉，主动作永远留在标题栏。工作台内部可有受控滚动区，但 `body` 不出现横向滚动。

### 6.5 标签、区间、属性、评论

#### 标签

- 标签显示 Schema 中的业务名、可选快捷键与颜色纹理；颜色只辅助，不作为唯一识别。
- 支持搜索、最近使用、按类别折叠；1–9 只绑定当前可见且稳定排序的前 9 个标签，列表变化时在帮助面板明确提示。
- 已停用标签在历史版本中可读，但不能新建；未知 label code 进入只读“合同不匹配”状态，不回退为可编辑文本。
- 创建区间必须先有合法选区；创建点标注使用当前播放步。重复/重叠是否允许由 Schema 规则返回，不由 UI 猜测。

#### 区间与时间

- 存储/传输：0-based 技术区间 `[start_step,end_step)`；`start_step < end_step`。
- 默认业务显示：`第 301–450 步（150 步） · 00:10.000–00:15.000`，对应技术值 `[300,450)`。业务步从 1 起且右端显示最后一个已包含步；这是推荐口径，需产品确认。
- 时间显示仍保持半开语义：详情或帮助文本说明“结束时间点不包含在本段中”；不同频率/缺帧时，以 preview descriptor 的 mapping 为准。
- 拖拽选区必须有按钮/键盘替代：设为开始、设为结束、向前/后移动 1 步、Shift + 方向键移动 10 步。不能要求用户输入纳秒。
- 当映射不可用时禁止创建/修改区间，保留预览只读并提示“时间与逻辑步映射尚未就绪”；不得用 `ns / 33,333,333` 猜步数。

#### 属性

- 属性表单由固定 Schema 版本生成；标签切换只显示相关字段，已填值不应静默丢失。
- 必填、单位、取值范围和帮助文字常驻；枚举使用业务中文，技术 code 可在字段帮助中查看。
- 输入时只做轻量格式校验，失焦后做字段校验；“提交审核”时展示可聚焦的错误摘要并保留字段内错误。
- 大文本评论允许粘贴；用户输入必须处理空、短、长文本。非认证字段使用稳定 `name` 和合适 `autocomplete="off"`，数值字段使用合适 `inputMode`。

#### 评论

- 评论可锚定任务、单条标注或一个半开区间；锚点随版本保存，不随当前播放头漂移。
- 审核意见显示作者、角色事实、时间、关联版本和是否已处理；“要求修改”必须至少一个 reason code 和一条可执行说明。
- 标注员可回复或标记“已处理”，不能改写审核员原文；审核员可在对比模式逐条确认。
- 评论实时更新使用单一 `aria-live="polite"` 状态区域，不抢焦点。

### 6.6 键盘与快捷键

| 按键 | 行为 | 边界 |
|---|---|---|
| Space | 播放/暂停切换 | 输入框、下拉框、对话框内不触发 |
| J / K / L | 后退播放 / 暂停 / 前进播放 | T02 统一实现 |
| ← / → | 播放头移动 1 逻辑步 | 不按固定 100 ms；使用 mapping |
| Shift + ← / → | 移动 10 逻辑步 | 到边界时停止并提示 |
| [ / ] | 当前步设为区间开始/结束 | 沿用 T02；选区无效时在工具面板说明原因 |
| 1–9 | 选择当前标签快捷项 | 不在文本输入时触发 |
| Enter | 用已选标签创建标注 | 需合法选区；不会提交任务 |
| C | 新建评论 | 锚定当前标注或播放步 |
| Ctrl/⌘ + S | 保存草稿 | 阻止浏览器默认保存页面 |
| Ctrl/⌘ + Enter | 打开“提交审核”确认 | 只打开确认，不直接提交 |
| ? | 打开快捷键帮助 | 所有动作均列出可见按钮替代 |
| Esc | 取消当前拖拽/关闭顶层浮层 | 不连续关闭多个层级 |

快捷键帮助可从时间轴常驻入口打开；所有快捷键都必须有键盘可达的可见控件，不覆盖操作系统/辅助技术快捷键。

## 7. 保存、冲突、审核与版本追踪

### 7.1 草稿保存状态机

```text
已保存 ──编辑──> 未保存 ──800ms 空闲/30s 周期/手动──> 保存中
  ▲                │                                  │
  │                ├─ 本地短期恢复副本（第二道保护）   ├─ 成功 -> 已保存 14:32
  │                │                                  ├─ 网络失败 -> 保存失败，可重试
  │                └─ 离页 -> 路由拦截 + beforeunload  └─ 412/409 -> 冲突，停止自动写入
  └────────────────────────────────────────────────────────────────────────
```

实现合同：

- 每次编辑立即更新内存并写短期本地恢复；服务端自动保存采用 800 ms 空闲触发，加 30 s 最长等待上限。
- 同一任务最多一个在途保存。后续修改合并为下一次完整快照；响应按 client mutation sequence 丢弃过期回包，不能让旧响应覆盖新 UI。
- 请求包含 expected revision/ETag、content hash 与幂等键。保存成功后显示“已保存 14:32”，并在可访问状态区播报一次。
- 手动“保存草稿”立即冲刷队列；按钮请求期间显示“保存中…”，失败后文案为“重试保存”。
- 自动保存失败不阻止继续本地编辑，但标题栏常驻错误，并说明“改动仅保存在此浏览器，重新加载前请重试”。
- 不自动提交审核；提交前先冲刷待保存修改，再对相同 revision/hash 做 preflight。
- 本地恢复副本必须按 task + schema + base revision 隔离、最小化、定时过期；不能把它描述成已同步服务器。

### 7.2 离页与切任务

- `dirty`、保存失败、离线或冲突任一成立时，所有离开路径都受同一 router blocker 约束：标题返回、面包屑、P10 链接、浏览器后退、Scope 切换和深链切任务。
- `beforeunload` 只作为刷新/关页兜底，不能替代站内路由拦截。
- 对话框给出“继续编辑”“立即保存”“放弃本次未保存改动”三个明确选择；最后一项为危险动作并说明本地副本是否仍可恢复。
- 已保存状态直接离开，不弹无意义确认；返回 P10 要恢复筛选与滚动。

### 7.3 冲突处理

- 收到 409/412 后立即暂停自动保存，保留本地完整工作副本，获取最新服务端 revision。
- 对比按“新增、修改、删除、Schema/基线变化”分组；显示业务标签和步区间，技术 hash 放折叠区。
- 用户可选“重新应用到最新草稿”“复制未保存内容”“放弃本地改动”。重新应用必须经预检并创建新 revision；不得静默 last-write-wins。
- 基线或 Schema 已变且不能安全迁移时，只读展示旧草稿并创建后继任务；已提交版本和审核历史保持不可变。

### 7.4 提交和审核

- “提交审核”确认页汇总：任务、草稿版本、标注数、覆盖/必填检查、未处理审核意见、最后保存时间。主按钮仍叫“提交审核”。
- 提交失败保留编辑内容和对话框上下文；错误说明原因及下一步（重试、修正字段、刷新权限、处理冲突）。
- 提交成功将编辑器切为只读提交版本，P10 状态更新为“待审核”，标题给出可追踪 submission/version。
- 审核员默认查看“提交版本 vs 基线”；修订任务还可切到“本次提交 vs 上次提交”。审核视图不能修改提交快照。
- “要求修改”必须选择稳定 reason code 并填写可执行说明；“审核通过”可选总结。自审由服务端最终拒绝，前端提前隐藏动作并解释。
- “审核通过”不是发布。页面提示“该标注版本已通过审核，发布由版本发布流程完成”，不提供跳过授权的快捷发布。

## 8. 权限矩阵

权限判断顺序：服务端返回的数据范围 → capability → allowed action → 对象状态/归属 → 自审/租约等不变量。UI 隐藏未授权操作，但安全不能依赖隐藏。

| 能力/动作 | 标注员 | 任务管理员 | 审核员 |
|---|---:|---:|---:|
| P10 查看本人分配 | 是 | 有 read 能力时 | 同时是标注员时 |
| P10 查看项目/区域全量 | 否 | `annotation_task.read_all` 或正式等价能力 | 仅授权审核范围，不默认全量 |
| 查看可领取 | 有 claim 能力 | 是 | 不因 reviewer 身份自动获得 |
| 领取本人任务 | `annotation_task.claim` | 可有 | 否 |
| 分配/改派 | 否 | `annotation_task.assign` | 否 |
| P08 查看任务 | 已分配本人/明确 read scope | 管理范围只读，除非另有 edit | 审核范围只读/审核模式 |
| 编辑标注 | 本人 + edit/save + 可编辑状态 | 不因管理员身份自动获得 | 不编辑提交快照 |
| 保存草稿 | 本人 + save | 仅另有同等能力且合同允许 | 否 |
| 提交审核 | 本人 + submit | 不因管理员身份自动获得 | 否 |
| 评论 | 合同允许的任务/版本 | 管理说明，不改审核意见 | 审核评论 |
| 要求修改/审核通过 | 否 | 不因管理员身份自动获得 | review + 非自审 + 正确状态 |
| 查看技术信息 | 最小必要字段 | 授权范围 | 授权范围 |
| Raw 下载 | 默认否且 P08/P10 不展示 | 默认否 | 默认否 |
| 发布 | 否 | 否 | 否 |

注：现有 capability 列表只有 `annotation_task.read`，没有明确区分本人队列与全量队列。新增 capability 名称只是推荐，不是已存在合同；Owner 可改为服务端 scope projection，但必须保持 fail closed。

## 9. 页面与局部状态

| 状态 | P10 | P08 |
|---|---|---|
| 首次加载 | 表头/筛选尺寸稳定的骨架，不伪造行 | 工作台框架占位，camera slots 与时间轴保持尺寸 |
| 后台刷新 | 保留当前行、筛选、选择；状态区提示 | 保留播放位置和草稿；刷新不重建媒体订阅 |
| 空队列 | “当前没有分配给你的任务” + 有权限时“查看可领取任务” | 不适用；无 taskId 返回 P10 |
| 筛选为空 | 显示当前筛选摘要与“清除筛选” | 不适用 |
| 预览生成中 | 行显示“预览生成中”，仍可看任务详情 | 对应相机保留 16:9 占位；时间轴映射未就绪时禁止区间编辑 |
| 单相机失败 | 行只提示“1 路预览不可用” | 仅该 panel 错误，提供“重试此相机”；其他相机/曲线/草稿继续可用 |
| 全预览失败 | 可进入任务查看原因 | 编辑工具转只读；不可创建依赖映射的标注；保留已保存草稿 |
| 冲突 | 行显示“草稿冲突待处理” | 停止自动写入，打开结构化冲突面板，保全本地版本 |
| 提交失败 | 行保持编辑中 | 对话框保留检查结果与草稿，显示恢复动作；不清表单 |
| 只读 | 行动作“查看标注/查看提交” | 明确原因：待审核、已完成、他人租约、过期基线或能力不足 |
| 无权限 | 不泄露任务数量/姓名；显示范围说明 | 403 与 404 按安全策略投影，不先加载敏感预览 |
| 离线 | 保留缓存行并标明快照时间 | 可继续本地编辑；明确未同步，恢复在线后由用户确认重试 |
| 限流 | 保留旧内容，显示可重试时间 | 不重建资源；保存队列退避且手动重试可见 |
| 合同不匹配 | fail closed，不投影未知敏感字段 | 草稿只读并允许复制诊断摘要，不尝试猜枚举/Schema |
| 未知状态 | “未知状态” + 技术 code（授权者） | 禁止写动作，保留安全只读查看 |

所有异步状态用 `aria-live`/`role=status` 有节制地播报；颜色以外同时有图标和文字。焦点在失败提交后移动到错误摘要，在弹窗关闭后回到触发按钮。

## 10. P10 线框

### 10.1 1440

```text
┌ 平台侧栏 218 ┐┌──────────────────── 标注任务 ──────────────────────────────┐
│              ││ 标注 / 标注任务    管理分配、进度与审核流转      [创建任务]*│
│              ││ [我的任务 12] [可领取 7] [待我审核 4] [全部 183]*          │
│              ││ 搜索…  状态▾ 数据集▾ Schema▾ 截止▾ 阻塞▾ 负责人▾*  重置   │
│              │├───────────────────────────────────────────────────────────┤
│              ││ □ 任务                 状态       进度      负责人  截止/阻塞│
│              ││ □ 抓取水杯·第14段      修订中     18/24 76% 王敏   今天18:00│
│              ││   采集条目 RC-0821 · 手部动作 v3                 1项：遮挡  [继续标注]│
│              ││ □ 双臂叠衣·第02段      待审核     24/24 100% 李岩   明天12:00│
│              ││   采集条目 RC-0818 · 双臂协同 v2                无          [开始审核]│
│              ││ □ 抽屉开合·第09段      已逾期     尚未计算   未分配 昨天18:00│
│              ││   采集条目 RC-0804 · 轨迹事件 v1                预览生成失败 [分配]*│
│              │├───────────────────────────────────────────────────────────┤
│              ││ 快照 2026/08/17 14:35                         ‹ 1 2 3 ›   │
└──────────────┘└───────────────────────────────────────────────────────────┘
* 仅 capability 允许时出现
```

### 10.2 1280

```text
┌ 侧栏 200 ┐┌────────────────────── 标注任务 ─────────────────────────────┐
│          ││ [我的12] [可领取7] [待审核4] [全部]*             [创建任务]*│
│          ││ 搜索…  状态▾ Schema▾ 截止▾ [更多筛选2]                      │
│          │├─────────────────────────────────────────────────────────────┤
│          ││ 任务 / 数据范围           状态       进度       负责人   操作 │
│          ││ 抓取水杯·第14段           修订中     18/24 76%  王敏 [继续标注]│
│          ││ RC-0821 · 今天18:00 · 阻塞1项                              │
│          ││ 双臂叠衣·第02段           待审核     24/24      李岩 [开始审核]│
│          ││ RC-0818 · 明天12:00 · 无阻塞                               │
│          ││ 抽屉开合·第09段           已逾期     尚未计算    —    [分配]*│
│          │├─────────────────────────────────────────────────────────────┤
│          ││ 快照 14:35                                      ‹ 上一页 下一页 ›│
└──────────┘└─────────────────────────────────────────────────────────────┘
```

## 11. 效果图清单与固定示例数据

效果图必须复用同一套 token、真实业务文案和下列固定数据，避免每张图靠不同 Mock 掩盖状态问题：

| 编号 | 尺寸 | 页面/场景 | 必须展示 |
|---|---:|---|---|
| T06-01 | 1440×900 | P10 标注员“我的任务” | 12 项、1 项修订中、1 项预览生成中、一个即将到期任务；无管理员列和 Raw 下载 |
| T06-02 | 1280×800 | P10 管理员全量任务 | 紧凑筛选、负责人、截止、阻塞、批量选择栏；无整页横滚 |
| T06-03 | 1440×900 | P08 正常编辑 | 固定 2×2 camera slots、曲线、标签/属性/评论、已保存状态、业务步区间 |
| T06-04 | 1280×800 | P08 编辑中 + 单相机失败 | 左 rail、右 drawer、单相机 slot 局部重试、时间轴仍可用、主动作可见 |
| T06-05 | 1440×900 | P08 审核对比 + 要求修改 | 基线/上次提交对比、评论锚点、原因与说明；无发布动作 |
| T06-06 | 1440×900 | P10 标注员无任务 | “当前没有分配给你的任务”、范围说明；有 claim 能力才显示“查看可领取任务”，无虚构统计或管理员入口 |

固定示例：项目“灵巧操作基准”、数据集“桌面抓取 2026-Q3”、任务“抓取水杯·第 14 段”、采集条目“RC-0821”、Schema“手部动作 v3”、Lance 版本“v42”、草稿“v7”、技术区间 `[300,450)`、业务显示“第 301–450 步（150 步）· 00:10.000–00:15.000”。

效果图评审清单：1440/1280 均无 body 横向滚动；主动作首屏可见；默认无 Raw 下载；P08/P11/审核图能识别为同一 T02 内核；预览生成和单相机失败不跳布局；长中文、长 ID、200% 缩放、仅键盘路径均可用；颜色不是唯一状态信号。

## 12. Web Interface Guidelines 审阅发现

以下是基于 2026-08-17 抓取的最新 `command.md` 对当前代码的定位，供效果图确认后的实现批次处理；本次不改代码。

### `frontend/src/pages/p08-data-annotation/AnnotationQueuePage.tsx`

- `:41-49`：整行用 button 承担导航，不能中键/Cmd 打开；任务标题应使用 Link，领取仍用 button。
- `:114-116`：声明 `role=tab` 但缺少完整 tablist、roving focus 和方向键行为；改用语义完整的 Tabs 或普通链接式筛选。
- `:117-120`：筛选控件有可见 label，但需补稳定 `name`、非认证 `autocomplete` 与示例型占位文案。
- `:124`：快照时间直接输出，任务表混入 Dataset/Episode/Schema 技术词；应使用 `Intl.DateTimeFormat` 和业务名。

### `frontend/src/pages/p08-data-annotation/AnnotationTaskPage.tsx`

- `:411-414,801-807`：只在标题返回时调用 `window.confirm`，左栏切任务可绕过未保存保护；改为统一 router blocker。
- `:731-750`：现有主动作不符合业务词；目标固定为“保存草稿”“提交审核”“审核通过”“要求修改”。
- `:732,819-831`：存在看似可用但没有动作处理的按钮，必须实现、禁用并解释或删除，不能保留假 affordance。
- `:878-915`：默认暴露纳秒，违背业务值优先；技术值移入可展开详情。
- `:876`：向 viewer 传入每次 render 新建的 `onResourceError`，与 viewer effect 依赖组合可能反复授权/订阅媒体资源。

### `frontend/src/features/annotation/forms/SchemaDrivenAnnotationForm.tsx`

- `:66-69`：`watch` 每次击键把整表值推给 P08 大页面，需测量提交次数并切分订阅/局部 transient state。
- `:96-114`：可见 label 已具备；原始纳秒输入缺业务步模型、`inputMode`、稳定 autocomplete 策略，错误提交后还需错误摘要和焦点管理。

### 对话框与样式

- `frontend/src/features/annotation/ui/DangerousActionDialog.tsx:24-45`：已有 Escape 与返回焦点，但未形成完整焦点圈定/inert 背景合同。
- `frontend/src/features/annotation/handoff/AnnotationEntryAction.tsx:49-52`：Schema 选择对话框缺初始焦点、Escape、焦点圈定与返回焦点。
- `frontend/src/pages/p08-data-annotation/p08.css:54-56`：滚动对话框缺 `overscroll-behavior: contain`；需保证粘性栏不遮挡键盘焦点。
- `frontend/src/pages/p08-data-annotation/p08.css:1-24` 与 `workbench.module.css:1-95`：大量页面私有 hex/重复组件外观，应回到 Master semantic tokens；保留清晰 `:focus-visible`。

### `frontend/src/pages/p10-cleaning-drafts/page.tsx`（旧 P10，仅作反例/可复用壳）

- `:131-167,342-377,458-468`：仍是清洗草稿，且混用英文技术操作词和裸时间；不能直接改标题后复用数据合同。
- `:537-545`：搜索每次击键立即更新查询/URL；新 P10 应 debounce 或 Enter 触发并保持输入响应。
- `:375-377`：裸 ISO 时间改为 locale 格式，`datetime` 属性保留机器值。
- 可复用 `StandardPageScaffold`、共享筛选/`DataTable`、区域状态和 cursor pager 的结构，不复用清洗 DTO。

## 13. React/Vite 性能方案与测量门槛

### 13.1 架构建议

- 保留当前 route-level Vite lazy loading（`frontend/src/pages/p08-data-annotation/routes.tsx:33-41`）；T02 重型模块用静态可分析的 `React.lazy(() => import('固定路径'))`，不要套用 Next.js `dynamic`。
- Three/URDF 已由 `frontend/vite.config.ts:28-31` 分为 viewer chunk；只有用户打开 3D/点云时加载其适配器。评论编辑器、版本差异器如足够重，也按功能激活后再加载。
- 播放时钟、逐帧游标继续放 ref/external store；相机 canvas 直接订阅，不把 30/60 Hz 值提升到 P08 页面 state。React 文本读数最多 10 Hz。
- 拖拽中的预览选区留在时间轴局部；pointer up/键盘一次动作后才提交 domain change。标签/属性/评论按 slice 订阅，避免一个字段击键重渲染相机与所有轨道。
- `onResourceError`、stream descriptor、robot scene 和 track adapter 使用稳定引用；资源授权 effect 不依赖高频或内联对象。
- 派生的 coverage、分组、冲突 diff 先测量，再决定 `useMemo`/worker；不为简单布尔值滥用 memo。
- TanStack Query 管服务端状态；编辑 reducer/store 管本地工作副本；不得把未保存草稿直接塞回 query cache 当服务器事实。

### 13.2 必测场景与目标

数字是效果图确认后的工程验收预算，不是对当前代码的测量结果；还必须执行 T02 的 Medium/Large/Stress 数据集与 5 分钟内存/掉帧预算（`plan/frontend-ux-parallel/T02-DATA-VISUALIZATION-WORKBENCH-SPEC.md:552-590`），本节只补 P08/P10 页面级场景：

| 场景 | 数据规模 | 工具/指标 | 通过门槛 |
|---|---:|---|---|
| 播放同步 | 3 相机 + 21 DOF + 6 轨 | Performance + React Profiler，连续 30 s | 动画帧 p95 脚本 <16 ms；P08 页面不按帧 commit |
| 区间拖拽 | 10,000 标注段 | pointer move 到视觉反馈 | p95 <50 ms；pointer up 到属性面板 <100 ms |
| 属性输入 | 40 字段、10 轨 | 每输入 10 字的 commit 数/范围 | 相机 panel 0 次 React commit；当前字段无可感知卡顿 |
| 单相机失败/重试 | 连续编辑 2 min | 授权请求、订阅数量 | 非主动重试不重复授权；订阅无净增长 |
| 自动保存 | 快速编辑 50 次 | 请求数、顺序、丢稿 | 单一在途；最终服务器 hash 等于本地最新 hash |
| P10 列表 | 50/100/500/2,000 行夹具 | DOM nodes、滚动 FPS、heap、INP | 先以服务端 cursor 50 行为基线；>50 或实测不达标时启用 virtualization/content-visibility |
| P10 搜索 | 连续输入 12 字 | 请求数、输入延迟 | 300 ms debounce；输入 p95 <100 ms；旧请求可取消 |
| 1280 resize | 1440↔1280 连续切换 | Layout/CLS | 无 body 横滚；camera slots/时间轴不跳零高；无同步 layout read in render |

虚拟化决策：共享 `DataTable` 当前渲染当前页全部行（`frontend/src/shared/ui/data/DataTable.tsx:64-90,171-197`）。新 P10 先保持稳定 cursor 页大小 50；若允许 100+、展开行导致 DOM 膨胀，或上述测量失败，再在共享表能力中实现虚拟化，不在 P10 私有复制表格。

## 14. 产品问题、技术设计项、效果图后可直接编码项

### 14.1 必须由产品/权限 Owner 确认

1. P10 正式路由与旧 `/manual/drafts` 的去向；推荐 `/annotations/tasks` + 旧 `/annotations` 重定向。
2. 任务分配是永久 assignee、可续租 lease，还是抢占；超时、离线、改派时草稿归属如何处理。现有 `plan/PRODUCT-DESIGN-DECISIONS-REQUIRED.md:29` 的 PD-15 尚未确认。
3. “可领取”是否对所有标注员开放、领取上限和优先级；管理员是否能强制改派。
4. 审核任务如何分配；是否允许同一主体先标注后审核。推荐禁止自审，后端已有禁止自审证据。
5. “拒绝”是终态还是“要求修改”的一种 reason；本规格默认主流程只用“要求修改”，避免无恢复路径。
6. 截止时间来源、SLA 时区、逾期处理；进度的分母、覆盖率与质量门槛口径。
7. 评论的可见范围、外包隔离、@提醒、附件与保留策略。
8. 业务步是否统一 1-based。本文推荐把技术 `[300,450)` 显示为“第 301–450 步”。
9. 自动保存间隔、离线本地副本安全策略及共享设备上的清理要求。
10. 是否存在单独 Raw 下载能力；即便存在，是否只在 P06/P07 安全详情提供。本文默认 P08/P10 永不作为入口。
11. 审核对比默认基线：Lance 原始基线、上一次已提交标注，或当前已批准版本。

### 14.2 必须先完成技术/API 设计

1. 把 T02 已定义的概念合同落实为可导入的 `DataVisualizationWorkbench` mode/slot/adapter 类型，并确认 T06 窄化的空 `QueueNavigatorSlot`、业务步 formatter 与“提交审核”文案覆盖点。
2. 统一真实后端与前端产品草案：scope/path、snake_case envelope、状态枚举、ETag/revision、幂等键和命令风格只能有一个权威来源。
3. P10 列表投影：服务端范围 Tab、负责人、dueAt、progress numerator/denominator/coverage、阻塞 code、review assignee、稳定排序与 cursor snapshot。
4. create/claim/assign/reassign/review/rebase 的正式端点、部分批量结果和审计事件；当前手写 client 缺 assign，当前真实 OpenAPI 又与生成草案不同。
5. detail adapter 正式暴露 submission、review、reference annotation sets 和 version lineage，不能继续把它们作为 `record<string,unknown>` 丢弃。
6. Annotation domain 从纳秒 anchor 迁移/适配到逻辑步 anchor；定义旧 ns 草稿迁移和显示精度。
7. Lance preview session/descriptor 每路媒体、时间轴 mapping、失效/刷新/单路重试和固定版本保证。
8. 服务端自动保存：并发条件、幂等、最大 payload、离线恢复、冲突 diff/replay 与服务端 revision 历史。
9. 权限范围：本人 read 与全量 read 的可验证区分、自审不变量、字段级敏感信息投影。
10. P10 长列表是否保持 50 行 cursor 或开放更大页；如需虚拟化，优先扩展共享 `DataTable`。

### 14.3 效果图确认后可直接编码（不改变产品/API 语义）

- 把 P08 目标文案统一为“保存草稿”“提交审核”“审核通过”“要求修改”，移除无 handler 的假按钮。
- P08 导航元素换成 Link；完善 Tabs、dialog 焦点圈定/Escape/返回焦点、modal overscroll、表单 name/autocomplete/inputMode、错误摘要与 aria-live。
- 日期/数字改用 `Intl.*`，业务名优先，技术 ID/纳秒移入折叠详情。
- 用 Master semantic tokens 替换 P08 私有 hex，并删除 P08 对共享 viewer 外观的重复覆盖；具体删除要等 T02 样式合同落定。
- 稳定 `onResourceError` 等回调/对象引用；切分表单订阅并添加 Profiler 基线。
- 建立统一 router blocker，覆盖标题、面包屑、浏览器后退、Scope 变化和任务切换。
- 增加 1440/1280 视觉回归、键盘路径、单相机失败、预览生成、保存冲突、无权限与提交失败测试。

## 15. 验收门槛

### 产品与权限

- 标注员进入 P10 默认只请求和显示本人分配任务；管理员在同一结构看到授权范围；审核员默认待我审核。
- P08 没有任务队列、Raw 下载或发布动作；P10 没有相机、时间轴或标注表单。
- 所有写动作同时通过 capability、allowed action、对象状态、scope 和自审/租约约束；403/404 不泄露敏感存在性。
- “保存草稿”“提交审核”“要求修改”“审核通过”贯穿按钮、对话框、Toast、审计说明和测试名称。

### 数据与工作流

- 预览固定到 Lance 版本；每个区间从 descriptor 映射为逻辑步，传输使用 `[start_step,end_step)`。
- 默认界面不出现原始纳秒；技术详情能追踪 task/revision/hash/Lance/version/request ID。
- 自动保存、手动保存、本地恢复、离页、离线、409/412 和提交失败均不会静默丢失用户最新修改。
- 提交与审核版本不可变；要求修改产生新修订；审核通过不等于发布。

### 布局、无障碍与性能

- 1440×900 和 1280×800 均无整页横向滚动，主动作无需横滚，P08 camera slots/时间轴/标注工具同屏可用。
- 预览生成、空态、单相机失败、刷新不造成布局跳变；长中文、长 ID、200% 缩放不遮挡焦点。
- 全流程仅键盘可完成；拖拽有按钮和快捷键替代；焦点、Tab、dialog、aria-live、对比颜色均通过检查。
- React/Vite 按第 13 节场景留下测量记录；没有测量不得以“性能优化”为由增加私有缓存或复制共享组件。

## 16. 建议验证命令（实现批次执行）

本文是规格，不声称以下产品代码验证已完成。效果图确认并实现后至少执行：

```bash
cd /home/czy/hc_DataPlatform/frontend
npm run typecheck
npm run lint
npm run test -- --run
npm run build
npx playwright test --project=chromium
```

并增加定向检查：P08/P10 1440 与 1280 截图、axe/键盘测试、路由离页保护、30 Hz 播放 Profiler、10,000 区间轨道、50/100/500/2,000 任务列表、网络离线/限流/冲突、单相机失败与 Lance preview mapping contract test。

## 17. 未核验项与 T02 依赖清单

### 未核验

- 四份方向文档原文缺失，无法逐条核对其中 annotation/Lance preview/interval/review 原句。
- 新批次 T02 已完整读取，但它仍是效果规格而非已实现 API；最终组件导出、slot TypeScript 签名、container query、camera slot 排序和 compare adapter 尚未验证。
- 外部 OpenAPI root 不在本仓库；`frontend/scripts/generate-api-client.mjs` 指向的正式 data-annotation 源文件未纳入当前工作区。
- 现有状态文档声称的 P08 annotation 页面/E2E 测试在当前源码树未找到，不能复验历史通过数。
- dueAt、进度、审核分配、租约、评论权限、Raw 独立能力没有当前权威合同。
- 第 13 节是测量方案与预算，未运行浏览器 Profiler 或视觉回归；本任务未改代码。

### T02 实现阶段需兑现给 T06 的合同

1. `Annotation`/`Review`/`ReadOnly` 模式与 P08 tool slot；P08 不直接依赖 viewer 内部组件，Annotation 的 `QueueNavigatorSlot` 按 T06 留空。
2. 固定 Lance 基线、preview session、每路 stream 与 presentation time ↔ source step mapping。
3. 共享 clock、播放/逐步、selection、annotation/comment tracks、keyboard event ownership。
4. T02 已定的 1440 展开栏、1280 左 rail + 右 overlay drawer、相机最小 280 px与局部滚动边界。
5. preview generating、单资源失败、全预览失败、未授权、过期 descriptor 的稳定 error slots。
6. 原始/编辑后/版本对比的统一 adapter 与视图切换，不复制画布或时间轴。
7. 高频状态订阅与 React render budget；保证表单输入不会触发媒体重新授权/订阅。
8. focus order、拖拽替代、reduced motion、200% 缩放和无 body 横向滚动的验收基线。

在上述概念合同实现为稳定组件 API 前，可评审 P10 信息架构、P08 工具内容、工作流、权限、状态和文案；不能把本文线框中的 T02 内部布局直接编码成 P08 私有实现。
