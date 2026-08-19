# T05｜P05–P07 数据集、采集条目、数据版本与冻结发布 UX 规格

> 交付状态：效果图/交互规格，可供 T02、T06、T07 和后续前后端合同设计对齐；不是生产 API 合同。
>
> 日期：2026-08-17（Asia/Shanghai）
>
> 修改边界：本轮只创建本文档，未修改生产前端、后端、OpenAPI、Worker、迁移、Compose、README、Master 或其他计划。
>
> 业务口径：面向用户统一称“采集条目”。当前代码和草案中的 `episode*` 仅作为现状证据与待迁移技术字段，不在本文中宣布为最终业务合同。

## 0. 结论先行

P05、P06、P07 应组成一条可追溯但不伪装成漏斗的资产工作流：P05 负责在当前授权项目/区域内找到数据集；P06 负责浏览单个数据集中的“采集条目”并按需进入 T02 统一工作台；P07 负责说明候选版本由哪些不可变/版本化输入组成、由谁审核、何时由发布者冻结成不可变发布版本。

本规格采用以下不可破坏约束：

1. “采集条目”是一条业务记录，不等于 `data_package_id`，也不等于方向文档/当前实现里的 rollout 或 episode 标识。
2. 只有自动质检 `PASS` 的采集条目才能进入 30 Hz 对齐和 Lance；`RISK`/`REJECT` 在此链路止步，不提供“人工改为通过并进入 Lance”。
3. 对齐结果必须保留源时间、实测误差、`valid`、`repeated` 等诊断事实；默认界面展示秒、毫秒、比例与摘要，不倾倒原始纳秒。
4. 预览按需读取 Lance，返回临时 HLS/fMP4；临时预览不是数据集版本、训练来源或永久 MP4 资产。
5. P07 必须分别展示基础 Lance、标注版本、清洗规则修订、审核结果、冻结发布版本，不把它们压成一个含糊的 `READY`。
6. “审核通过”只产生审核结论；“冻结发布”是发布者的独立动作。两者按钮、权限、确认、审计和状态均分开。
7. 所有浏览、原始/编辑对比和发布版本查看进入 T02 统一工作台；P05–P07 不再复制第二套 Viewer。

## 1. 调研边界、方法与可访问性

### 1.1 已读资料

- 完整读取 `plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md` 与 `design-system/hc-data-platform/MASTER.md`，本文不覆盖、不修改 Master。
- 完整读取 P05、P06、P07 当前 development plan、routes、pages、query codec、dataset API hooks/client/adapters/wire schemas/generated types、entities、Mock fixtures/handlers/scenarios 与现有相关测试。
- 读取真实后端的 verification、quality、alignment、Lance catalog、preview、annotation、publishing 的 README、Router、运行时组合、关键模型/Service、OpenAPI 与专项测试证据。
- 读取 `plan/P01-P19-REAL-API-COVERAGE-AUDIT.md`、`plan/P01-P19-REAL-API-IMPLEMENTATION-PLAN.md` 与 `plan/PRODUCT-DESIGN-DECISIONS-REQUIRED.md` 中 P05–P07/cleaning/scope 相关结论。
- 并行规格在初稿后出现；终检时已完整读取 `T02-DATA-VISUALIZATION-WORKBENCH-SPEC.md`、`T06-P08-P10-SPEC.md`、`T07-P09-P11-SPEC.md`，并把实际模式名、业务身份适配、标注/清洗审核层级和交接差异收敛到第 16 节。本文仍只修改 T05 文件，不回写其他 Owner 的规格。
- 四份方向文档均可访问并已阅读：
  - `/mnt/d/桌面/PICO 跨本体遥操作与机器人端录制方案.md`
  - `/mnt/d/桌面/VR 全身遥操作初版方案（固定颈部）.md`
  - `/mnt/d/桌面/统一机械臂规控、遥操作与数据采集平台建设及调试上线方案.md`
  - `/mnt/d/桌面/数据平台 评测.md`
- 第二份 VR 文档仅涉及实时控制、画面与 RGB/Depth 对齐，没有 30 Hz Lance、页面按需预览、数据版本或发布规则；不能从其缺失内容推断业务合同。其余三份相关证据在第 2 节列出。

### 1.2 本轮技能使用结果

| 技能 | 本轮使用方式 | 对规格的影响 |
|---|---|---|
| `frontend-design` | 以“工业机器人数据资产账本、P05 找数据、P06 找条目并预览、P07 判断可否审核/冻结”为具体任务审查页面 | 保留 Master 的紫蓝、系统中文字体、Ant Design、Lucide 和单一“信号轨道”；拒绝营销页 Hero、发光卡和无业务装饰 |
| `ui-ux-pro-max` | 做非持久化 design-system、UX、图表、React、React stack 检索；未写回 Master | 自动候选的金色/奶油色、Roboto、营销式布局与滚动动画不采用；保留高密度、低动效、非颜色单一编码、渐进披露、表格/状态时间线建议 |
| `web-design-guidelines` | 2026-08-17 重新获取最新 `command.md` 并逐文件核对 | 导航语义、表单标签、异步反馈、键盘焦点、URL 状态、长内容、数字本地化与可操作错误进入第 12 节 findings |
| `react-best-practices` | 只采用 React/浏览器/Vite 规则，分析当前 React 19.1.1 + Vite 7.1.3；未套用 Next.js 规则 | 保持路由懒加载；要求统一工作台重组件再分包、请求并行、URL 作可分享状态、播放时钟局部订阅、先测量再 memo/虚拟化 |

### 1.3 初始工作树记录

执行调研前先运行了 `git status --short`。以下均为本轮开始时已经存在的工作树状态；目标文件当时不存在，本轮不处理、不覆盖这些改动：

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

## 2. 当前实现与合同证据表

“真实能力存在”不等于“P05–P07 已接真实页面 API”。下表强制区分 Mock、真实 API、当前降级和文档漂移。

| 主题 | 证据 | 当前事实 | 分类与 UX 处理 |
|---|---|---|---|
| 页面真实接入总况 | `plan/P01-P19-REAL-API-COVERAGE-AUDIT.md:3-17,41-43` | P05–P07 页面级真实 API 均未接入；底层模块存在但路径/请求/响应/状态不兼容 | **真实审计 / 降级**：Browser Mock 可演示；real/off 模式不能静默回退 fixture，展示“能力尚未开放” |
| P05 查询 | `frontend/src/pages/p05-datasets/page.tsx:266-310`; `frontend/src/mocks/handlers/datasets.handlers.ts:194-289` | 列表、summary、facets、page capabilities 四区并行，Mock 支持 empty/partial/cursor/contract mismatch | **Mock 草案**：保留分区失败；正式 BFF 尚缺列表投影、facet、scope 权限过滤 |
| P05 筛选密度 | `frontend/src/pages/p05-datasets/components/DatasetFilterPanel.tsx:101-212` | 13 个控件默认全部展开 | **直接 UX 偏差**：常用 5 项，其余进入“更多筛选”；排序/每页归入视图设置 |
| P05 内容与导航 | `DatasetSummaryStrip.tsx:57-93`; `DatasetTable.tsx:41-55,82-149`; `page.tsx:266-284` | 混用 DATASETS/EPISODES/READY；导航用 Button+navigate；默认露出内部 ID；标题描述服务端实现细节 | **直接 UX 偏差**：中文业务文案；导航用 Link；ID 进技术详情；标题回答用户任务 |
| P06 数据 | `frontend/src/mocks/fixtures/datasets/core.ts:117-300`; `frontend/src/pages/p06-dataset-detail/page.tsx:480-527,789-878` | bootstrap/versions/当前技术条目/schema/source/capacity 均是 dataset draft fixture；摘要直接显示 ns/bytes，业务对象仍叫 Episode | **Mock 草案 / 命名漂移**：界面改称采集条目；底层字段暂作适配，不升级成业务合同 |
| P06 Viewer 解析 | `frontend/src/features/datasets/api/queries.ts:716-760` | 先取 bootstrap，再只取前 100 条后本地找目标，再取 revision | **功能降级**：超过首 100 条可能误报 404；正式接口必须按稳定采集条目 ID 直取 bootstrap |
| P06 媒体 | `frontend/src/pages/p06-dataset-detail/ViewerShell.tsx:8-18,270-315` | Viewer Core 同步导入；当前 revision 只能构造 unsupported stream，没有 preview session 描述符；URL/Inspector 暴露原始 ns | **功能降级**：入口应转 T02；重型工作台 lazy；真实 preview session 接入前显示“暂不可预览”而非空播放器 |
| P07 页面合同 | `frontend/scripts/generate-api-client.mjs:8-21`; `frontend/src/features/datasets/api/wire-schemas.ts:1-946`; `frontend/src/shared/api/generated/datasets.ts:700-1410` | 生成脚本明确把 OpenAPI 草案当仓库外输入；草案合同 `dataset-version-review.v1alpha1` 只有 RAW/CLEANED、REVIEWING/RETURNED/READY 与 delivery 状态 | **Mock/外部草案**：不能表达 base Lance + annotation + cleaning rules + review + frozen release 的五层组成 |
| P07 审核/发布耦合 | `frontend/src/pages/p07-version-detail/page.tsx:295-314,1035-1044`; `frontend/src/features/datasets/review-state-machine.ts:35-63` | “复核通过”同时要求 review+publish 权限并启动 Manifest/发布任务；CANDIDATE_READY 等待自动发布；无独立发布者动作 | **业务冲突**：拆为“审核通过”和“冻结发布”；当前状态机不可直接沿用 |
| P07 技术值 | `frontend/src/pages/p07-version-detail/page.tsx:579-620,1003-1030,1218-1247` | 默认显示英文枚举、token、revision ID、duration ns，并要求用户手输 ns 区间 | **直接 UX 偏差**：中文状态、业务时间/容量；精确值放技术抽屉；范围在 T02 时间轴选择 |
| 自动 QC 门禁 | `backend/src/hc_data_platform/workflow/temporal_workflows.py:225-317`; `backend/tests/workflow/test_temporal_workflows.py:507-570,759-809` | REJECT/RISK 在 alignment 前终止；只有 PASS 进入 alignment + Lance commit；测试断言 RISK writer 调用为 0 | **真实后端事实**：信号轨道和按钮严格按此门禁；不设计人工覆写入口 |
| 质量结论 | `backend/src/hc_data_platform/quality/README.md:12-26`; `backend/openapi/quality.yaml:78-103,305-341` | PASS/RISK/REJECT、发现项、指标和 ns 范围有真实只读合同 | **真实底层 API**：P06 BFF 可聚合业务摘要，但当前页面尚未接入 |
| 30 Hz 对齐 | `backend/src/hc_data_platform/alignment/README.md:3-25`; `backend/openapi/alignment.yaml:40-61,77-104` | 整数 ns 时间轴；图像/点云最近、关节线性、动作因果、离散 recent、IMU/力窗口；保留 source timestamps/error/valid/repeated | **真实底层 API**：P06 默认以 ms/比例摘要，技术详情可查看精确 ns |
| Lance 目录 | `backend/src/hc_data_platform/lance_catalog/README.md:3-19`; `backend/openapi/lance_catalog.yaml:126-187,208-235` | 共享 `aligned_steps.lance`、不可变逻辑版本、稳定 step 读取和 rollout lineage；不公开物理行地址 | **真实底层 API**：P06/P07 可消费聚合后的 lineage；当前业务条目映射尚缺 |
| 按需预览 | `backend/src/hc_data_platform/preview/README.md:3-19`; `backend/openapi/preview.yaml:48-69,101-146`; `backend/src/hc_data_platform/runtime.py:253-259` | 真实运行时从 Lance 读 step、按 annotation revision/view mode 临时编码 HLS；有 placeholder、TTL 与短期 URL | **真实底层 API**：可供 T02 适配；当前 P06 没接，不得假装已有视频 |
| 标注审核 | `backend/src/hc_data_platform/annotation/README.md:3-24`; `backend/openapi/annotation.yaml:387-424,439-581` | 不可变 annotation revision、半开 step 区间、APPROVED/NEEDS_REVISION/REJECTED、禁止自审；后续编辑撤销当前批准指针但保留历史 | **真实底层 API**：P07 可引用批准快照；不是完整 cleaning rules/version review 合同 |
| 冻结发布 | `backend/src/hc_data_platform/publishing/README.md:3-28`; `backend/src/hc_data_platform/publishing/service.py:200-323,325-374`; `backend/openapi/publishing.yaml:1-73,87-208` | 发布者预检只纳入 PASS+DERIVED_READY+批准标注；发布冻结 lineage、included/excluded ranges 与 immutable assets；复用同版本不同内容返回冲突 | **真实底层 API**：与目标业务方向一致，但请求未含独立 cleaning rule revision/review result，且未接 P07 |
| 冻结权限 | `backend/src/hc_data_platform/publishing/router.py:54-69,90-111` | preflight/publish/export 需要 `Permission.PUBLISH`，读取只需 read | **真实底层 API**：P07 要把 publish capability 与 review capability分开显示 |
| cleaning 规则合同 | `plan/P01-P19-REAL-API-COVERAGE-AUDIT.md:45-47`; `plan/PRODUCT-DESIGN-DECISIONS-REQUIRED.md:30-31` | manual issue/cleaning draft/EDL/commit 页面 API 和正式规则模型不存在 | **未实现/待产品**：P07 先显示可选 `cleaning_rule_revision` 占位模型，不声称可调用 |
| 身份关系 | `/mnt/d/桌面/PICO 跨本体遥操作与机器人端录制方案.md:299-323,413-435,517-530` | 方向明确不使用易误解的 `episode_id` 作唯一键；`data_package_id` 只标识独立包与上传幂等，不用于任务归类 | **方向证据**：采集条目必须有独立业务身份；各种技术 ID 只在技术详情关联 |
| 方向文档预览/发布 | `/mnt/d/桌面/数据平台 评测.md:224-248,252-328,365-452,496-664,668-758` | 说明 PASS→30 Hz/Lance、按需预览、非破坏性版本、审核与冻结组合 | **方向证据**：与 Master/真实模块大体一致 |
| 方向文档漂移 | `/mnt/d/桌面/数据平台 评测.md:357-365,772-791` | 已写“用按需预览整体替换永久 MP4”，但结尾旧图仍写“增量写入 Lance + 生成 MP4”；RISK 段还说可生成隔离预览 | **文档漂移**：以更新段、用户硬约束和真实工作流为准；P06 的 Lance 预览只对 PASS 可用 |
| 控制链路与版本 | `/mnt/d/桌面/统一机械臂规控、遥操作与数据采集平台建设及调试上线方案.md:190-219` | 每条技术 rollout 应记录 controller/adapter/calibration/频率/延迟/误差等；RISK 默认不进训练 | **方向证据**：进入采集条目技术详情，不默认铺满列表 |

## 3. 术语、对象与不可混淆关系

### 3.1 面向用户的名词

| 名词 | 用户理解 | 明确不等于 |
|---|---|---|
| 数据集 | 某项目/区域内一组可持续积累、版本化管理的机器人数据资产 | 单次上传、单个 Lance 文件、任务码 |
| 采集条目 | 数据集中一次可独立检查、浏览、标注/清洗并进入版本组成的业务记录 | `data_package_id`、上传 session、rollout/episode 技术 ID、任务本地序号 |
| 基础 Lance 版本 | 仅由自动 QC PASS 条目经固定配置完成 30 Hz 对齐后形成的不可变逻辑快照 | 标注版本、清洗规则、发布版本 |
| 标注版本 | 针对固定基础数据的不可变标注修订 | 可变草稿、基础 Lance |
| 清洗规则修订 | 针对固定基础/标注输入的非破坏性区间与规则操作集 | 删除 Raw、原地改 Lance、已发布版本 |
| 审核结果 | 审核员对一个精确候选组成做出的不可变结论 | 发布、Manifest 物化成功 |
| 冻结发布版本 | 发布者把精确组成与纳入/排除范围冻结后的不可变训练数据版本 | “审核通过”本身、临时预览缓存 |

### 3.2 技术身份关系（仅技术详情）

```text
采集任务/任务码（归类）
  └─ 采集条目 collection_item_id（业务稳定身份，正式字段名待确认）
       ├─ data_package_id（机器人数据包、上传幂等）
       ├─ upload/session/object IDs（传输执行）
       ├─ rollout/当前 episode IDs（现有底层/草案关联，不是业务显示名）
       ├─ source MCAP SHA-256（内容完整性）
       └─ base Lance lineage（仅自动 QC PASS 后存在）
```

规则：列表主列显示“采集条目 #0042 / 任务名 / 机器人 / 采集时间”；完整 ID、哈希、对象路径、原始 ns 和 byte 值只在“技术详情”Disclosure/Drawer 中出现，并提供复制与 `translate="no"`。

## 4. 跨页信息架构与路由

### 4.1 页面职责

| 页面 | 单一首要任务 | 保留在本页 | 必须跳转 T02 |
|---|---|---|---|
| P05 数据集 | 在当前授权范围找到要工作的数据集 | 搜索/筛选、列表、摘要、当前发布版本与待处理提醒 | 不直接打开媒体；从数据集行进入 P06 |
| P06 采集条目 | 判断某条数据是否可浏览、为什么不可浏览，并选择一个条目 | 数据集概要、采集条目列表、条目 Inspector、QC/Lance/preview 摘要 | “浏览对齐数据”→ Lance 模式；有 Raw 权限的异常条目→ Raw 诊断模式 |
| P07 数据版本 | 判断候选由什么组成、审核结论是什么、是否满足冻结发布 | 组成卡、review/freeze 状态机、纳入/排除摘要、审计、确认 | “比较版本”→审核对比模式；“查看已发布版本”→发布查看模式 |

### 4.2 建议路由语义

当前 path 可在实现阶段兼容，但业务参数/可分享状态应向以下语义收敛：

```text
/datasets
  ?q=&task=&robotId=&assetState=&createdFrom=&createdTo=
  &advanced=1&robotModelId=&scene=&storageClass=&channels=
  &sort=&after=&before=&limit=

/datasets/:datasetId
  ?tab=entries&baseLanceVersion=&entryId=&q=&task=&robotId=&qcStatus=
  &lanceStatus=&previewStatus=&after=&before=&limit=&returnTo=

/datasets/:datasetId/versions/:versionId
  ?tab=composition|entries|review|release|audit
  &entryId=&compareTo=&after=&before=&limit=&returnTo=

T02 owned route（最终 path 由 T02 决定）
  ?mode=raw-diagnostic|lance-browser|annotation|cleaning|review|published
  &datasetId=&collectionItemId=&baseLanceVersion=&annotationRevision=
  &cleaningRevision=&candidateVersionId=&compareTo=&view=original|edited|compare
  &startStep=&endStep=&cameraId=&returnTo=
```

URL 只放可分享的稳定状态。短期签名 URL、review token、ETag、Idempotency-Key、对象路径和原始大 payload 不进入 URL、Query Key 或遥测。

### 4.3 项目/区域/权限提示

- 项目与区域由全局 Shell 选择；P05 不提供伪造的“全部项目”选项。
- 筛选区上方固定一行：`上海试点 / cn-shanghai · 仅显示你有权查看的数据`，旁边是“权限说明”链接/Popover。
- 服务端 scope 是唯一事实来源。若只返回部分范围，显示 `结果受项目授权限制`，不推断隐藏数量，不显示“另有 N 条无权查看”。
- scope 改变时清除 cursor、选中数据集/条目、compare target 和 T02 selection；保留无害的显示偏好。
- 403 使用整区或整页“没有当前项目权限”；已加载的数据不得继续留屏伪装可用。

## 5. 信号轨道：血缘，不是漏斗

### 5.1 组件语义

P06/P07 使用 Master 唯一记忆点“信号轨道”，只编码单条采集条目或单个候选版本的真实血缘：

```text
[采集条目 #0042]
        │ 自动质检：通过
        ▼
[基础 Lance v42 · 30 Hz]
        ├────────► [标注 v12]
        └────────► [清洗规则 r7]
                         │ 精确组成提交审核
                         ▼
                  [审核结果：通过]
                         │ 发布者冻结
                         ▼
                  [冻结发布 v2026.08.17]
```

若自动质检不是 PASS，轨道必须在该节点真实终止：

```text
[采集条目 #0043] ── [自动质检：风险] ── ⛔ 未进入 30 Hz/Lance
                                        └─ 有权限：打开 Raw 诊断
```

### 5.2 禁止当作漏斗

- 不展示阶段“转化率”，因为采集条目、标注任务、清洗修订、审核决定和发布版本不是同一集合的一次性单调转化。
- 不把分支（标注、清洗）画成顺序必经节点；后端正式组合确认前允许显示“未使用/不适用”。
- 节点同时用图标、文本、形状表示 `完成/当前/阻塞/未开始/不适用`，颜色不是唯一信息。
- 节点只显示业务摘要；选择节点后在 Inspector 展开版本 ID、hash、配置、时间和审计人。

## 6. P05 数据集浏览规格

### 6.1 页面层级

1. Header：`数据集`；说明 `在当前项目中查找、浏览和管理机器人数据资产。`
2. Scope hint：项目/区域、授权范围、新鲜度 `截至 14:32`。
3. 摘要：数据集、采集条目、待审核版本、待冻结发布。最多 4 项，不再放五等分英文 eyebrow。
4. 常用筛选（默认可见）。
5. 已应用条件 chips + `更多筛选 (N)`。
6. 数据集表格 + 可折叠摘要 Inspector。

### 6.2 常用筛选与高级筛选

| 层级 | 字段 | 交互与合同 |
|---|---|---|
| 常用 1 | 搜索 | 名称/业务编号；明确 server-side；Enter 或“应用筛选”提交，不每键请求 |
| 常用 2 | 任务 | 可搜索 Select；展示业务名，技术 code 次级 |
| 常用 3 | 机器人 | 可搜索 Select；展示机器人名/序列摘要，ID 在选项辅助行 |
| 常用 4 | 资产状态 | 中文：可浏览、处理中、有问题、已冻结、待接收、未知；不直接显示内部 enum |
| 常用 5 | 创建日期 | 一个日期范围控件，`from` 含、`to` 不含的正式规则待确认；清晰显示项目时区 |
| 高级 | 机器人型号 | 与机器人联动但不在客户端臆测兼容关系 |
| 高级 | 场景 | facet 值中文 label；未知值安全显示“未知场景（code）” |
| 高级 | 存储层级 | 标准/低频/归档；只作筛选，不等于生命周期权限 |
| 高级 | 通道 | 多选 combobox，不使用“逗号分隔”文本输入 |
| 高级 | 通道匹配 | `包含全部所选通道` / `包含任一所选通道`，仅选通道后出现 |
| 视图设置 | 排序 | 最近活动/最近创建/名称；隐藏 ID tie-breaker 技术文案 |
| 视图设置 | 每页数量 | 20/50/100；不是业务筛选 |

行为：

- 常用筛选始终不超过 5 个；高级条件存在时按钮显示数量，关闭面板不丢条件。
- 应用任一维度或 scope 变化后回首个 cursor 窗口；`after`/`before` 互斥。
- 空结果分“当前项目暂无数据集”和“没有符合筛选的数据集”；后者主动作 `清除筛选`。
- Facets/summary 部分失败时列表仍可操作，失败区显示 `筛选建议暂不可用`/`摘要暂不可用` 与 `重试`，不清空列表。

### 6.3 表格字段

| 1440 默认列 | 1280 策略 | 展示规则 |
|---|---|---|
| 数据集 | 固定左列 | 名称 + 最多 2 个业务标签；完整 ID 不默认显示；名称为 Link |
| 当前冻结版本 | 保留 | `v2026.08.17 · 已冻结` 或 `尚未发布`，不写 Current Ready |
| 采集条目 | 保留 | `Intl.NumberFormat`、等宽数字 |
| 数据状态 | 保留 | 处理中/有问题/可浏览及原因摘要 |
| 待处理 | 合并 | `待审核 2 · 待发布 1`；为 0 时弱化，不堆多个空列 |
| 最近活动 | 收进次级行 | `2026-08-17 14:32`，时区 Tooltip/技术详情说明 |
| 操作 | 固定右列 | 主链接 `打开数据集`；行选择/摘要由独立可访问按钮提供 |

## 7. P06 采集条目规格

### 7.1 数据集详情 IA

建议 Tabs：`概览`、`采集条目`、`数据版本`、`Schema`、`来源`、`容量`。默认概览只显示 4 个决策信息：采集条目数、可浏览条目、数据时长、物理占用；不再铺 8 张等权指标卡。

`采集条目` Tab 是 P06 主任务：

- 筛选：搜索、任务、机器人、自动质检、Lance 状态；更多筛选中放采集时间、预览能力、标注/清洗状态。
- 表格：采集条目、任务/机器人、采集时间/时长、自动质检、30 Hz/Lance、人工处理、操作。
- Inspector：选中条目的业务摘要、信号轨道、预览可用性和下一步。
- 主动作只有一个，按状态在 `浏览对齐数据`、`查看 Raw 诊断`、`查看失败原因` 中选择；不能同时堆满。

### 7.2 采集条目行与状态映射

| 条件 | 行主状态 | 主动作 | 次级说明 |
|---|---|---|---|
| 自动 QC PASS + Lance ready + 有 preview capability | 可浏览 | `浏览对齐数据` | 跳 T02 `mode=lance-browser`，预览由 T02 按需创建 |
| 自动 QC PASS + 对齐/转换中 | 正在生成对齐数据 | `查看处理进度` | 无空播放器；显示阶段与最后更新时间 |
| 自动 QC PASS + Lance conversion failed | 对齐数据生成失败 | `查看失败原因` | 可重试与否只按 `allowed_actions`；不让用户人工改 QC |
| 自动 QC RISK/REJECT | 未进入 Lance | 有 Raw read 时 `查看 Raw 诊断` | 无权限时显示 `自动质检未通过；你没有 Raw 查看权限` |
| Lance ready 但无相机/无预览描述符 | 数据可浏览，暂无画面预览 | `浏览对齐数据` | T02 可显示曲线/状态/点云；媒体区有明确 no-preview 状态 |
| 后端 preview capability unavailable | 对齐数据可用，预览服务不可用 | `浏览对齐数据` | 工作台保留布局并显示 `预览服务暂不可用` + 重试 |

### 7.3 渐进技术详情

| 层级 | 默认展示 | 用户动作后展示 |
|---|---|---|
| L0 业务摘要 | `2 分 14 秒`、`1.6 GB`、自动质检通过、30 Hz 对齐完成、有效 Step 98.7% | 无 |
| L1 数据诊断 | 重复图像 12 帧、无效 Step 1.3%、相机时间误差 P95 18 ms、对齐配置/转换器短名 | 点 `查看数据诊断` 或轨道 Lance 节点 |
| L2 技术详情 | 不默认展示 | 精确 `source_timestamps_ns`、`time_error_ns`、每通道 valid/repeated、完整 bytes、IDs、hash、对象 locator（需权限）与复制按钮 |

格式规则：

- 时长默认 `2 分 14 秒`；小于 1 秒显示 ms；不在摘要写 `134000000000 ns`。
- 误差默认 ms，按数据给小数位；精确 ns 只在 L2。
- 容量使用 `Intl.NumberFormat` + IEC（KiB/MiB/GiB）或产品确认的 SI；精确 byte 放 L2。
- ID 首尾保留的截断只用于辅助识别，例如 `item_7f2a…91cd`；查看全文/复制不依赖 hover。
- 英文 enum 映射中文标签并保留未知值保护：`未知状态（FUTURE_STATE）`，同时切只读。

### 7.4 Preview 入口合同

P06 只负责选择上下文，不承载播放器：

```ts
type OpenWorkbenchIntent = {
  mode: 'raw-diagnostic' | 'lance-browser' | 'annotation' | 'cleaning' | 'review' | 'published';
  datasetId: string;
  collectionItemId: string;       // 业务稳定身份，字段名待正式确认
  baseLanceVersion?: string;      // lance/review/published 必须固定
  annotationRevision?: number;
  cleaningRevision?: string;
  candidateVersionId?: string;
  compareTo?: string;
  view?: 'original' | 'edited' | 'compare';
  startStep?: number;
  endStep?: number;
  returnTo: string;
};
```

- Lance 模式必须固定 `baseLanceVersion`，不能传 `latest/current`。
- `mode` 直接采用 T02 已定义的六模式枚举；P05–P07 当前只发出 `raw-diagnostic`、`lance-browser`、`review`、`published`，保留全枚举是为了共享跳转合同而不是扩大本页能力。
- Preview session 由 T02/adapter 创建，参数与真实 `PreviewRequestV1` 对齐：project、dataset、底层 rollout 映射、Lance version、annotation revision、camera、view mode、step window。
- P06/T02 之间传业务条目 ID；将其解析成当前后端 `rollout_id` 的责任属于服务端 projection/adapter，不把 rollout 暴露成新业务合同。
- 无 Raw 权限时完全隐藏下载/对象 locator/Raw 打开动作；仅显示不泄密的阻断原因。`raw.read` 与 `raw.download` 必须分开。

## 8. P07 数据版本、审核与冻结发布规格

### 8.1 信息架构

建议 Tabs：

1. `组成与血缘`（默认）：五层 Composition、信号轨道、纳入/排除摘要、阻断项。
2. `采集条目`：固定快照内的条目列表；打开 T02，不内嵌重复 Viewer。
3. `比较`：选择同数据集固定版本，打开 T02 `mode=review&view=compare`。
4. `审核`：检查结果、不可变审核决定、要求修改/拒绝原因；审核动作。
5. `发布记录`：冻结预检、冻结任务/同步结果、Manifest hashes、审计；发布者动作。
6. `技术详情`：Schema、required storage、operational inventory、完整 token/ID/hash；默认折叠。

### 8.2 版本组成卡

```text
候选版本 v12
├─ 基础 Lance      v42 · 30 Hz · 1,248 个采集条目 · 不可变
├─ 标注版本        annotation v12 · 已审核
├─ 清洗规则        rules r7 · 18 个排除区间 · 非破坏性
├─ 审核结果        通过 · reviewer 王** · 2026-08-17 13:41
└─ 冻结发布        尚未冻结
```

每层显示 `状态 + 业务摘要 + 固定版本`；点击后进入右侧详情。若未使用清洗规则，显示 `未使用清洗规则`，不伪造 `r0`。若正式后端尚不能提供该层，显示 `合同待开放`，不能从 Mock 推算。

### 8.3 目标状态机

```text
组成中 COMPOSING
   └─[提交审核]────────────► 审核中 UNDER_REVIEW
                                 ├─[要求修改]──► 待修改 CHANGES_REQUESTED ─► 新修订/重新提交
                                 ├─[拒绝版本]──► 已拒绝 REJECTED（只读）
                                 └─[审核通过]──► 审核通过 REVIEW_APPROVED
                                                        └─[冻结发布]──► 冻结中 FREEZING
                                                                              ├─成功► 已冻结 FROZEN（只读）
                                                                              └─失败► 冻结失败 FREEZE_FAILED
                                                                                          └─[重新冻结发布]
```

并发/未知状态：

- 409/412：进入 `版本已更新`，关闭确认弹窗，禁用写动作，保留用户未提交备注，动作 `刷新版本`；绝不自动重放另一版本上的审核/冻结。
- 未知 enum/合同不匹配：保留已验证只读事实，展示 `状态暂无法识别`，所有 mutation fail closed。
- FREEZING 时布局保持，显示可轮询 job 与 `返回版本列表`；若真实 API 同步返回 201，则直接进入 FROZEN，不伪造 job。

### 8.4 精确按钮、权限与可见条件

| UI 文案（精确） | 位置 | 所需 capability | 资源/状态条件 | 明确禁止 |
|---|---|---|---|---|
| `提交审核` | 组成与血缘/Header | `dataset_version.submit_review`（建议） | COMPOSING；组成固定；阻塞检查通过；allowed action | 不叫 Commit/提交版本；清洗/标注者不能审核或发布 |
| `审核通过` | 审核 Tab | `dataset_version.review` | UNDER_REVIEW；不是自己的提交；review preflight/ETag 有效 | 不要求 publish 权限；不启动发布/Manifest 冻结 |
| `要求修改` | 审核 Tab | `dataset_version.review` | UNDER_REVIEW；至少一条定位明确的意见/原因 | 不原地覆盖已提交修订 |
| `拒绝版本` | 审核 Tab 的次要危险动作 | `dataset_version.review` | UNDER_REVIEW；明确原因；二次确认 | 不与“要求修改”混成一个 RETURNED |
| `冻结发布` | 发布记录/Header（唯一主 CTA） | `dataset_version.publish` | REVIEW_APPROVED；publisher；freeze preflight 通过；版本名未占用；组成仍匹配 | reviewer 权限不能替代；不从 UNDER_REVIEW 直接发布 |
| `重新冻结发布` | FREEZE_FAILED | `dataset_version.publish` | 重新预检通过；同组成可幂等重试 | 组成变化后复用原版本名 |
| `比较版本` | 比较 Tab | `dataset_version.compare` 或 read | 两个同数据集固定版本可读 | 不接受 latest/current；不在 P07 实现第二套比较 Viewer |
| `查看已发布版本` | FROZEN | `dataset_version.read` | 发布 Manifest 可读 | 不把临时 preview 当发布资产 |
| `查看 Raw 诊断` | 条目阻断项 | `raw.read` | 有 source mapping | 不等于 Raw 下载；无权限时隐藏 locator |

当前 capability `dataset_version.review` 与 `dataset_version.publish` 已存在于前端语义，但当前页面错误地同时要求二者才能“复核通过”。`dataset_version.submit_review`、`dataset_version.compare`、`raw.read`、`raw.download` 的最终命名需与 T01/后端权限目录对齐。

### 8.5 冻结发布确认

弹窗标题 `确认冻结发布`，主动作仍叫 `冻结发布`，取消叫 `取消`。内容顺序：

1. Scope 与目标版本：数据集名、发布版本名、项目/区域。
2. 冻结组成：base Lance、annotation、cleaning rules、review decision 的精确版本/摘要。
3. 影响：纳入/排除采集条目与 Step 范围、预计逻辑/物理容量（若服务端提供）。
4. 阻断项：PASS/DERIVED_READY/annotation approved、版本名、权限、并发、完整 Manifest 检查。
5. 不可变警告：`冻结后不能修改；内容变化必须使用新的发布版本。`
6. 用户输入完整发布版本名确认；不使用通用“我已知晓”复选框替代资源核对。

成功文案：`版本 v2026.08.17 已冻结发布`；失败文案包含稳定错误、request ID 与下一步，如 `发布版本名已被其他内容使用，请更换版本名后重新预检。`

## 9. 页面状态矩阵

| 状态 | P05 | P06 | P07 |
|---|---|---|---|
| 首次加载 | Header/scope 固定；筛选与表格骨架 | Header/Tabs/Inspector 列宽固定；条目骨架 | 组成卡与轨道骨架；不渲染假状态节点 |
| 空数据 | `当前项目暂无数据集` + 有权限时 `创建数据集` | `该数据集还没有采集条目` + 去上传/任务的授权入口 | `尚无数据版本` 或固定版本中无条目，区分资源空与筛选空 |
| 筛选空 | 保留筛选，`清除筛选` | 保留条目筛选，`清除筛选` | 保留固定版本，不把整个版本页变空 |
| 部分失败 | summary/facets 单区 warning，列表继续 | capacity/source/preview 分区失败，条目列表继续 | Manifest/storage/inventory 独立失败；审核/冻结按所需依赖 fail closed |
| 全部失败 | PageState + `重试` + request ID | 同上，不保留旧条目伪装当前 scope | 同上；若 mutation 结果未知，提示查审计/刷新，不宣告成功 |
| 无预览 | 不适用 | `暂无画面预览`，说明无相机/描述符/服务不可用；曲线可继续 | 比较工作台保持结构，媒体面板局部 no-preview |
| 无 Raw 权限 | 不泄露 Raw 计数/locator | 隐藏 Raw 打开/下载；显示最小阻断说明 | 条目列表同规则；冻结不要求业务用户具备 Raw 下载 |
| Lance 转换失败 | 数据集行显示 `有问题` | 轨道停在 Lance 节点；`查看失败原因`；允许动作由服务端给出 | 不能进入可审核组成；preflight 显示 blocker |
| 版本冲突 409/412 | 写操作若有则刷新 | 技术条目选择失效时提示刷新 | `版本已更新`；关闭 mutation；保留草稿输入；`刷新版本` |
| 只读 | 链接可用，创建/批量动作隐藏或解释 | 浏览可用；编辑/Raw/下载按 capability 隐藏 | 清楚显示 `只读`；不铺满 disabled 按钮；审核/冻结卡说明缺少哪项权限 |
| 能力未开放 | real/off 不回退 Mock | preview/workbench adapter unavailable 有明确状态 | cleaning composition/compare/freeze contract 未开放时逐区说明，不伪造 |
| 未知 enum/合同漂移 | 未知值中文包裹，列表可读 | 该行技术状态未知，写动作关闭 | 全部 mutation fail closed，已验证事实仍可读 |
| 长内容/大数据 | 名称换行最多 2 行，表格内部滚动 | ID 技术抽屉；条目游标分页；不一次加载首 100 再找目标 | 组成项换行；Manifest/条目分页；比较由 T02 增量读取 |

## 10. 1440 / 1280 ASCII 线框

线框只表达结构、优先级和响应式折叠，不新增视觉 Token。

### 10.1 P05｜1440×900

```text
┌─导航 224─┬──────────────────────── 主内容 1216 ────────────────────────────┐
│ 数据生产 │ 数据集                                      [创建数据集]        │
│          │ 在当前项目中查找、浏览和管理机器人数据资产                    │
│          │ 上海试点 / cn-shanghai · 仅显示你有权查看的数据 [权限说明]     │
│          ├─────────────────────────────────────────────────────────────────┤
│          │ 数据集 24 │ 采集条目 12,480 │ 待审核 3 │ 待冻结发布 1          │
│          ├─────────────────────────────────────────────────────────────────┤
│          │ [搜索................] [任务⌄] [机器人⌄] [资产状态⌄] [日期⌄]  │
│          │ [更多筛选 2]  条件：场景=装配 ×  通道=front ×   [应用筛选]      │
│          ├─────────────────────────────────────────────────────────────────┤
│          │ 数据集              冻结版本     采集条目  状态      待处理 操作│
│          │ 装配线 A 数据集      v2026.08.17  3,420    可浏览    2 / 1  打开│
│          │  标签: assembly / A                                               │
│          │ 人形抓取试点         尚未发布      864    处理中    1 / 0  打开│
│          │ ···                                                              │
│          ├─────────────────────────────────────────────────────────────────┤
│          │ 20 条/页                         截至 14:32      [上一页][下一页]│
└──────────┴─────────────────────────────────────────────────────────────────┘
```

### 10.2 P05｜1280×900

```text
┌─导航 208─┬────────────────────── 主内容 1072 ────────────────────────┐
│          │ 数据集                                  [创建数据集]      │
│          │ 上海试点 / cn-shanghai · 授权范围                         │
│          │ 数据集 24 │ 条目 12,480 │ 待审核 3 │ 待发布 1             │
│          │ [搜索........] [任务⌄] [机器人⌄] [状态⌄] [日期⌄]         │
│          │ [更多筛选 2]                       [应用筛选]              │
│          ├───────────────────────────────────────────────────────────┤
│          │ 数据集(固定)      冻结版本   条目   状态/待处理  操作(固定)│
│          │ 装配线 A          v…17      3,420  可浏览 · 2/1   打开     │
│          │ 活动 14:32 · assembly / A                                │
│          │   ← 表格内容区自身可滚，整页不横向滚 →                    │
│          └───────────────────────────────────────────────────────────┘
└──────────┴───────────────────────────────────────────────────────────┘
```

### 10.3 P06｜1440×900

```text
┌─导航 224─┬──────────────────────── 主内容 1216 ────────────────────────────┐
│          │ 装配线 A 数据集                                  [返回列表]     │
│          │ [概览] [采集条目] [数据版本] [Schema] [来源] [容量]             │
│          │ 条目 3,420 │ 可浏览 3,205 │ 总时长 126 h │ 物理占用 4.2 TiB     │
│          ├───────────────────────────────────────┬─────────────────────────┤
│          │ [搜索] [任务⌄] [机器人⌄] [自动质检⌄] │ 采集条目 #0042          │
│          │ [Lance 状态⌄] [更多筛选] [应用]       │ 任务 装配 / 机器人 R-12 │
│          ├───────────────────────────────────────┤ [条目]─[QC通过]─[Lance]│
│          │ 采集条目  任务/机器人 时间/时长 QC  Lance│       ├标注 v12       │
│          │ #0042    装配/R-12 14:21  通过  可浏览│       └清洗 r7         │
│          │ #0043    装配/R-09 14:18  风险  未进入│ 有效 Step 98.7%        │
│          │ #0044    抓取/R-12 14:12  通过  失败  │ 重复图像 12 帧         │
│          │ ···                                   │ [浏览对齐数据]          │
│          │                              [分页]    │ [查看数据诊断]          │
│          └───────────────────────────────────────┴─────────────────────────┤
└──────────┴─────────────────────────────────────────────────────────────────┘
```

### 10.4 P06｜1280×900

```text
┌─导航 208─┬────────────────────── 主内容 1072 ─────────────────────────┐
│          │ 装配线 A              [采集条目⌄]               [返回列表]│
│          │ 条目 3,420 │ 可浏览 3,205 │ 126 h │ 4.2 TiB               │
│          │ [搜索......] [任务⌄] [机器人⌄] [QC⌄] [Lance⌄] [更多]     │
│          ├────────────────────────────────────────────────────────────┤
│          │ 条目(固定)  任务/机器人  采集/时长  QC/Lance  操作(固定)   │
│          │ #0042      装配/R-12    14:21/2m14s 通过/可浏览  浏览      │
│          │ #0043      装配/R-09    14:18/1m52s 风险/未进入  诊断      │
│          │   ← 表格内部滚；低优先列进入行次级信息 →                   │
│          ├────────────────────────────────────────────────────────────┤
│          │ 选中条目摘要 [条目]─[QC通过]─[Lance] 98.7% [浏览对齐数据] │
│          │ [展开 Inspector]（右侧常驻栏在 1280 折到下方/Drawer）      │
└──────────┴────────────────────────────────────────────────────────────┘
```

### 10.5 P07｜1440×900

```text
┌─导航 224─┬──────────────────────── 主内容 1216 ────────────────────────────┐
│          │ 候选版本 v12                 审核通过 · 尚未冻结   [冻结发布]   │
│          │ [组成与血缘] [采集条目] [比较] [审核] [发布记录] [技术详情]    │
│          ├────────────────────────────────────────┬────────────────────────┤
│          │ 版本组成                                │ 发布前检查              │
│          │ 基础 Lance   v42 · 30 Hz · 不可变       │ ✓ 自动 QC PASS           │
│          │ 标注版本     v12 · 已审核               │ ✓ 对齐数据完成           │
│          │ 清洗规则     r7 · 18 个排除区间         │ ✓ 审核通过               │
│          │ 审核结果     通过 · 王** · 13:41        │ ! 版本名待确认           │
│          │ 冻结发布     尚未冻结                   │ 纳入 1,203 / 排除 45     │
│          ├────────────────────────────────────────┤ [重新预检]              │
│          │ [条目]─[Lance v42]─┬─[标注 v12]        │                        │
│          │                    └─[清洗 r7]─[审核通过]─[待冻结]             │
│          ├────────────────────────────────────────┴────────────────────────┤
│          │ 最近审计：提交审核 12:20 → 审核通过 13:41                     │
└──────────┴─────────────────────────────────────────────────────────────────┘
```

### 10.6 P07｜1280×900

```text
┌─导航 208─┬────────────────────── 主内容 1072 ─────────────────────────┐
│          │ 候选版本 v12         审核通过 · 尚未冻结      [冻结发布]  │
│          │ [组成] [条目] [比较] [审核] [发布] [技术]                  │
│          ├────────────────────────────────────────────────────────────┤
│          │ 基础 Lance v42  │ 标注 v12 │ 清洗 r7 │ 审核 通过          │
│          │ [条目]─[Lance]─┬─[标注]                                  │
│          │                └─[清洗]─[审核通过]─[待冻结]                │
│          ├────────────────────────────────────────────────────────────┤
│          │ 发布前检查：✓ QC  ✓ Lance  ✓ 审核  ! 版本名待确认         │
│          │ 纳入 1,203 · 排除 45 · 版本冲突 0                         │
│          │ 技术 ID / hash / bytes 全部进入 [技术详情] Drawer          │
│          └────────────────────────────────────────────────────────────┘
└──────────┴────────────────────────────────────────────────────────────┘
```

### 10.7 P07 比较与冻结确认关键态

```text
比较 Tab（P07 只选上下文）
[基准版本 v11⌄]  对比  [候选版本 v12⌄]  [在统一工作台比较]
变化摘要：采集条目 +24 / -3 · 标注 +112 · 排除 Step +1,240
注：播放器、曲线、时间轴、original/edited/compare 全部由 T02 承载。

确认冻结发布
┌─────────────────────────────────────────────────────┐
│ 确认冻结发布                                        │
│ 装配线 A / v2026.08.17 / 上海试点                   │
│ Lance v42 · 标注 v12 · 清洗 r7 · 审核 decision-…    │
│ 纳入 1,203 条，排除 45 条；冻结后不可修改。          │
│ 输入发布版本名  [v2026.08.17....................]   │
│                          [取消] [冻结发布]           │
└─────────────────────────────────────────────────────┘
```

## 11. 效果图制作清单

| 效果图 | 1440 | 1280 | 必须包含的关键态 |
|---|:---:|:---:|---|
| P05 数据集 | [ ] | [ ] | 正常；高级筛选已应用；facets/summary 部分失败；筛选空；只读；超长名称/未知状态 |
| P06 采集条目 | [ ] | [ ] | QC PASS+Lance ready；RISK 未进入 Lance；Lance conversion failed；无预览；无 Raw 权限；技术详情展开 |
| P07 数据版本 | [ ] | [ ] | 组成中；审核中；审核通过待发布；冻结中；已冻结；冻结失败；只读/未知状态 |
| P07 比较入口 | [ ] | [ ] | 同数据集版本选择；compare summary；T02 跳转；对比版本不可读/不存在 |
| P07 冻结确认 | [ ] | [ ] | 正常预检；blocker；版本名冲突；提交中防重复；成功；未知结果/可追踪 request ID |

所有效果图同时检查：长项目名、长数据集名、长条目技术 ID、原始 ns、精确 bytes、英文未知 enum、大量空白和表格最小/最大数据量。业务视图不得为了填满空白而增加无依据 KPI；技术值不得为了“信息密度”默认倾倒。

## 12. 最新 Web Interface Guidelines 审查

来源：[`vercel-labs/web-interface-guidelines/command.md`](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md)，2026-08-17 重新获取。以下按该文件行号记录 findings；它是实现审查清单，不决定业务流程。

| 规则（source file:line） | 当前证据（repo file:line） | Finding / 规格要求 |
|---|---|---|
| `command.md:13` 图标按钮要有可访问名称 | `DatasetTable.tsx:127-150`；`MASTER.md:147-150` | 图标为装饰时 `aria-hidden`；icon-only 的更多/复制/展开必须有 `aria-label` |
| `command.md:14` 表单要有 label | `DatasetFilterPanel.tsx:101-212` | 当前可见 label 基础可复用；冻结版本名、范围、通道多选同样需要稳定 label/name |
| `command.md:16` 导航用 link、动作才用 button | `DatasetTable.tsx:45-55,137-149`; `ViewerShell.tsx:270-275` | 数据集/条目/版本打开与返回使用 `<Link>`；创建、审核、冻结、重试使用 button |
| `command.md:19` 异步结果用 live region | `p07-version-detail/page.tsx:835-846` | 审核/冻结/preview job 的 queued/running/success/failure 用 `role=status`/`aria-live=polite`，错误 `role=alert` |
| `command.md:25` 可见键盘焦点 | P05/P06 CSS 当前含大量自定义样式 | 不移除 outline；所有行链接、轨道节点、Drawer、Tabs、时间轴入口使用 Master Focus token |
| `command.md:30` 表单 name/autocomplete | `p07-version-detail/page.tsx:1218-1242` | 当前原生 input 缺 name；正式表单补 name；不适用字段用 `autocomplete="off"`，不伪填错误 autocomplete |
| `command.md:37` 就地错误 | 当前 P07 ns 区间只整体阻断 | T02 范围选择与冻结版本名在字段旁显示具体错误，并在 submit 后聚焦第一错误 |
| `command.md:43` reduced motion | `MASTER.md:154` | 轨道状态/Drawer/骨架遵循 `prefers-reduced-motion`；不做无限脉冲 |
| `command.md:45` 禁止 `transition: all` | 后续实现 CSS | 只 transition `opacity/transform/color/border-color` 等明确属性 |
| `command.md:55` 表格数字等宽 | `DatasetTable.tsx:82-117`; 当前无统一数字类证据 | 数量、容量、时长、Step 统一 `font-variant-numeric: tabular-nums` |
| `command.md:59-60` 长内容与 flex `min-width:0` | P05 长 ID/名称当前直接放 cell | 名称两行截断+全文入口；技术 ID Drawer；flex 子项显式 `min-width:0` |
| `command.md:61` 空状态要有说明/下一步 | `p05 page.tsx:251-263` | 保留 filtered-empty 的清除筛选；补资源空/权限空/无 preview 的差异文案 |
| `command.md:71` 50+ 长列表评估虚拟化 | P05 limit 可 100；P06 当前最多取 100 本地找 | 表格先用 server cursor；剖析后决定行虚拟化，不能用客户端首 100 查找替代直取 API |
| `command.md:74` controlled input 更新要便宜 | P05 13 个 controlled controls；P07 大页 state 多 | 筛选草稿局部化；播放器时钟/范围不提升到整页；确认表单拆成局部组件 |
| `command.md:79-81` URL 表达筛选/Tab/分页/展开，可深链 | P05/P06/P07 query codecs 已覆盖大部分；P06 viewer URL 使用 raw ns | 保留 query codec；改为 `startStep/endStep` 或业务秒，不在默认 URL 放冗长 raw ns；scope 切换清资源状态 |
| `command.md:103` 日期用 `Intl.DateTimeFormat` | P06/P07 多处 `new Date().toLocaleString()` | 建立共享 formatter，显式 locale/timeZone，避免 SSR/测试/浏览器差异 |
| `command.md:104` 数字用 `Intl.NumberFormat` | P06 `effectiveDurationNs/sourceBytes` 原样显示 | 时长/容量/数量统一 formatter；L2 才显示精确 ns/bytes |
| `command.md:106` 技术值 `translate=no` | 当前 `<code>` 未统一设置 | 完整 ID/hash/topic/enum 技术片段设 `translate="no"`、可复制、可查看全文 |
| `command.md:115` hover 不是唯一途径 | 当前 ID title/hover 较多 | Tooltip 只辅助；键盘 focus/click 可打开技术详情 |
| `command.md:122-123` 按钮具体、错误给下一步 | 当前 `打开`、`Episodes`、`SERVER_ERROR` 等混用 | 使用 `打开数据集`、`浏览对齐数据`、`审核通过`、`冻结发布`；错误包含重试/刷新/换版本名/联系管理员之一 |
| `command.md:141` findings 用 file:line | 本节 | 后续实现 PR 继续按此格式审查，不只写笼统“注意可访问性” |

## 13. React 19 + Vite 实现审查与约束

项目版本证据：`frontend/package.json:21-58` 为 React 19.1.1、React Router 7.8.2、Vite 7.1.3；`frontend/vite.config.ts:23-32` 已把 Three/URDF 放入 viewer chunk。只采用 React/浏览器规则，不采用 Server Component、Server Action、`next/dynamic`、Next image/font 等规则。

| 主题 | 当前证据 | 结论 | 实现约束/验收 |
|---|---|---|---|
| 路由分包 | `frontend/src/app/router/index.tsx:16-28,134-160`; P05–P07 routes 为 lazy | 已有基础正确 | 保持静态可分析 import；新 T02 route 必须 lazy，禁止变量拼动态路径 |
| 重型 Viewer | `ViewerShell.tsx:8-13` 同步导入 `features/viewer`；`ViewerShell.tsx:289-297` 渲染 Core | route shell 虽 lazy，Core 与 shell 同 chunk | T02 对媒体/Three/点云/大图表做 `React.lazy`/动态 import；先渲染稳定占位，按 modality/Tab 加载 |
| Vite manual chunks | `vite.config.ts:28-31` | Three/URDF 已分 viewer，ECharts 分 analytics | 构建后检查 chunk 图；不要为每个小组件手工切 chunk；MSW 不进入默认生产链 |
| 请求瀑布 | P05 四区并行；`queries.ts:716-747` viewer bootstrap→list→revision 串行 | P05 可保留；P06 解析有不必要瀑布与 100 条上限 | 新 `collection-item bootstrap` 一次返回稳定 mapping/preview capability，或按明确依赖并行；AbortSignal 全链传递 |
| URL query 状态 | `p05 query-codec.ts:45-146`; `p06 query-codec.ts:46-184`; `p07 query-codec.ts:66-170` | cursor/tab/selection 基础好 | 保留 parse/build/canonicalize；筛选变化清 cursor；compare/entry/Tab 可分享；token/ETag/signature 不进 URL |
| 原始 ns URL | `p06 query-codec.ts:187-246` | selection 直接写 `selectionStartNs/EndNs`，不符合渐进披露 | T02 canonical URL 优先稳定 step 区间；显示层格式化，技术层可复制精确 ns |
| 派生状态 | `p07 page.tsx:295-325` render 中计算 policy/canApprove | 计算方式本身合理，但业务规则错误 | 保持纯函数派生；拆分 review 与 publish policy，不用 Effect 复制状态 |
| 页面状态规模 | P07 page 约 1,300 行、多个 modal/mutation/input state | 变更成本与重渲染面较大 | 按 Composition/Review/Release/Technical 分区组件；组件定义放模块顶层；不为拆分而新增 barrel |
| 高频播放状态 | Viewer Core/PlaybackClock 已有局部 clock 与组件测试 | 可复用方向 | 播放光标用外部 store/useSyncExternalStore/ref 或局部订阅；不每帧 setState 到 P06/P07 页面根 |
| Controlled filters | `DatasetFilterPanel.tsx:101-212` | 13 个控件同一组件可用但过密 | 常用/高级拆区，草稿 state 局部；应用后才更新 URL/query；避免每键网络请求 |
| Memo | P05 table columns use `useMemo` | 合理但不是普遍模板 | 只 memo 昂贵、稳定边界；先用 React Profiler 证明；不要 memo 简单标签/格式化结果 |
| 大表 | P05 20/50/100；P06/P07 cursor tables | 先 server page 正确 | >50 行且性能证据成立才引入 virtualization；固定列/键盘/屏幕阅读器要回归 |
| 大时间序列 | T02 将承载 30 Hz 多模态 | 潜在大 payload/render | 服务端/adapter 窗口读取；视口聚合/降采样；>1,000 点再启 Canvas；精确值仍可查询 |
| Number/Date | `p06 page.tsx:500-525,807-827`; `p07 page.tsx:579-620` | 原始字符串/浏览器默认 locale | 共享纯 formatter：`formatCount`、`formatBytes`、`formatDurationNs`、`formatErrorNs`、`formatDateTime`；用 BigInt 避免 ns 精度丢失 |
| 未知 enum | wire adapters 已有 UNKNOWN fallback；页面仍直接输出若干 enum | 安全方向可保留 | 未知状态只读、中文包裹 raw code、上报 contract mismatch，不崩页/不启用 mutation |
| Preview cache | 真实描述符含短期 URL/过期时间 | 不能进持久缓存/遥测 | Query cache 生命周期不超过授权；过期前刷新 session；logout/scope change 清媒体 URL |
| 错误边界 | 区域 PageState 已有 | 适合部分失败 | 重型工作台各 modality 有局部 error boundary；核心身份/bootstrap 失败才整页失败 |
| 测试 | `datasets.handlers.test.ts` 仅 4 类合同；`EpisodeWorkbenchCore.test.tsx` 仅时间轴；无 P05–P07 页面真实 E2E | 覆盖不足 | 增加 query codec、permission/state machine、formatter BigInt、partial failure、409/412、preview TTL、T02 intent、1280 visual/E2E |

## 14. 建议的页面聚合合同（非现有 API）

以下只是 UX 所需最小投影，必须由后端/OpenAPI Owner 设计；不把 Mock 类型当正式合同。

### 14.1 P05 列表投影

```ts
type DatasetBrowseItem = {
  scope: { organizationId: string; projectId: string; regionCode: string };
  datasetId: string;
  displayName: string;
  labels: string[];
  currentFrozenRelease?: { releaseId: string; displayVersion: string; frozenAt: string };
  collectionItemCount: string;
  browseState: 'BROWSABLE' | 'PROCESSING' | 'PROBLEM' | 'FROZEN' | 'EMPTY' | 'UNKNOWN';
  pendingReviewCount: string;
  pendingPublishCount: string;
  lastActivityAt: string;
  allowedActions: string[];
};
```

### 14.2 P06 条目投影

```ts
type CollectionItemSummary = {
  collectionItemId: string;
  displayOrdinal?: string;
  task: { code?: string; displayName: string };
  robot: { id: string; displayName: string };
  capturedAt: string;
  durationNs: string;
  sourceBytes?: string;
  autoQc: { status: 'PASS' | 'RISK' | 'REJECT' | 'RUNNING' | 'UNKNOWN'; reasons: string[] };
  alignment: {
    status: 'NOT_ELIGIBLE' | 'QUEUED' | 'RUNNING' | 'READY' | 'FAILED' | 'UNKNOWN';
    frequencyHz?: number;
    baseLanceVersion?: string;
    validStepRatio?: number;
    repeatedFrameCount?: string;
    timeErrorP95Ns?: string;
  };
  preview: { availability: 'AVAILABLE' | 'NO_CAMERA' | 'UNAVAILABLE' | 'FORBIDDEN' | 'UNKNOWN' };
  technicalRefs?: unknown; // 仅详情权限返回；结构待合同设计
  allowedActions: string[];
};
```

门禁校验：若 `autoQc.status !== 'PASS'`，服务端不得返回可用 base Lance version；前端遇到矛盾合同必须 fail closed 并报 contract mismatch。

### 14.3 P07 Composition/Review/Freeze 投影

```ts
type VersionComposition = {
  candidateVersionId: string;
  displayVersion: string;
  scope: Scope;
  etag: string;
  state: 'COMPOSING' | 'UNDER_REVIEW' | 'CHANGES_REQUESTED' | 'REJECTED' |
         'REVIEW_APPROVED' | 'FREEZING' | 'FREEZE_FAILED' | 'FROZEN' | 'UNKNOWN';
  baseLance: { version: string; contentHash: string; frequencyHz: number };
  annotation?: { revision: string; contentHash: string; approvalId?: string };
  cleaningRules?: { revision: string; contentHash: string; operationCount: string };
  review?: { decisionId: string; result: 'APPROVED' | 'NEEDS_REVISION' | 'REJECTED'; decidedAt: string };
  frozenRelease?: { releaseId: string; manifestHash: string; frozenAt: string };
  included: { itemCount: string; stepCount: string };
  excluded: { itemCount: string; stepCount: string; reasons: Array<{ code: string; count: string }> };
  allowedActions: string[];
};
```

必须提供独立命令或等价事务边界：`submit-review`、`approve-review`、`request-changes`、`reject-review`、`freeze-preflight`、`freeze-release`。所有写命令使用 `If-Match`、`Idempotency-Key`、稳定错误码与明确当前状态；review token 不能替代 freeze preflight token。

## 15. 产品确认、技术验证、可直接实现

### 15.1 需要产品确认

1. 采集条目的稳定业务身份、正式字段名、创建边界，以及与 `data_package_id`/rollout/当前 episode 字段的一对一或一对多关系。
2. 数据集名称唯一范围、owner、创建来源、archive/delete 规则；对应现有 PD-13。
3. V1 scope 是否严格单 project + region；跨项目/跨区域浏览是否永不出现；对应 PD-01/02。
4. P05 资产状态与“待处理”口径，尤其数据集级状态如何由条目/版本聚合。
5. P07 版本 Composition 是否允许无标注、无清洗规则，标注与清洗的组合顺序/兼容规则。
6. version review 发起者、审核人数、自审禁令、`要求修改` 与 `拒绝版本` 的终态差异；对应 PD-14。
7. cleaning rule/EDL 操作集、基线、单 writer/协作、提交与回滚；对应 PD-17。
8. 冻结发布版本命名规则、版本冲突后的用户处理、同步还是异步交互预期。
9. 时区、日期范围边界、容量用 SI 还是 IEC、默认小数位与大数展示。
10. Raw 查看与 Raw 下载是否为两项权限；哪些角色可以复制 object locator/hash。

### 15.2 需要技术验证/设计

1. 为 P05–P07 设计正式聚合 BFF/OpenAPI；当前 dataset draft client 与运行时 44-path OpenAPI 不兼容。
2. 服务端提供 collection item → package/upload/rollout/Lance lineage 的 scope-safe projection，杜绝客户端拼接身份。
3. 用按条目直取的 bootstrap 替代“拉首 100 条再 find”；验证高基数索引、cursor 和 p95。
4. T02 preview adapter 接真实 `/api/v1/previews/sessions`，验证签名 URL 刷新、TTL、scope 变化清理、no-camera/placeholder/partial modality。
5. 把 current review auto-materialize 草案拆为 review decision 与 freeze release；定义兼容迁移，不让旧 `READY` 同时表示审核/发布。
6. cleaning rules 正式模型与发布输入；现有 publisher 只冻结 base Lance + approved annotation + quality/alignment facts。
7. freeze preflight 的 blocker、ETag/idempotency、同步/async job、unknown outcome 与审计查询。
8. compare summary 的结构化 diff 与 T02 窗口读取，避免 P07 文本框创建一个无结果接线的 Diff Job。
9. 大整数 formatter 使用 BigInt；验证时长、ns、bytes 不经过 JS Number 丢精度。
10. 构建与运行时验证：T02 lazy chunk、Three/ECharts 按需、MSW 不进 real bundle、Preview/Raw 不泄露到日志/Query Key。
11. 测试补齐：contract、scope/IDOR、409/412、permission revoke、partial failure、preview TTL、visual regression 1280/1440、真实主链 E2E。

### 15.3 可直接进入效果图/前端实现的事项

这些项目不改变业务语义，可在正式数据字段通过 adapter 提供后直接实施：

1. P05 将 13 个默认控件拆成 5 个常用筛选 + 高级筛选 + 视图设置。
2. 当前界面中的 `Episodes`、`Ready`、`Delivery`、`Commit` 等标签改为本规格中文业务文案；未知 enum 保留 raw code 并只读。
3. 导航 Button 改 Link；补 `aria-label`、visible label、focus、live region、inline error、reduced motion。
4. 建立共享 `Intl`/BigInt formatter 和 `TechnicalDetails` Disclosure；默认隐藏 raw ns/bytes/ID/hash。
5. P06/P07 使用真实血缘“信号轨道”，不做漏斗/转化率。
6. P06/P07 所有媒体/比较入口只生成 T02 intent，不复制 Viewer。
7. P07 视觉上彻底分开审核区与发布区；不再让“审核通过”按钮依赖 publish capability。
8. 保持 query codec/cursor canonicalization、局部失败与未知状态 fail closed。
9. 1280 将 Inspector 折到 Drawer/下方，表格自身滚动并固定身份/操作列；整页无横向滚动。
10. 制作第 11 节效果图与状态 variants；在效果图确认前不改生产业务代码。

## 16. 与 T02 / T06 / T07 的交接接口

### 16.1 给 T02｜统一数据可视化工作台

- 已完整读取 T02 当前规格（`T02-DATA-VISUALIZATION-WORKBENCH-SPEC.md:67-164,336-460`）。双方已对齐：唯一 `DataVisualizationWorkbench`、六模式、固定 identity、同构 source/edit/compare、step selection、局部资源错误、1280 rail/drawer 和 Preview 生命周期；P06 Lance browse、P07 review compare、P07 published view 都由 T02 承载。
- T05 发给 T02：`OpenWorkbenchIntent` 的业务上下文、固定版本/修订、`returnTo`、只读/权限意图、P06/P07 入口文案，以及 no-preview、Raw forbidden、Lance failed 状态。T02 返回/冻结最终 route/path、canonical query、selection/panel 恢复和错误/回跳协议。
- **当前必须阻断的命名漂移**：T02 概念 `WorkbenchIdentity` 仍使用 `episodeId`，标题/示例也出现 Episode（`T02-DATA-VISUALIZATION-WORKBENCH-SPEC.md:162-180,336-393,463-515`）。T05 入口只传 `collectionItemId`；服务端 scope-safe projection/adapter 再解析现有底层 `rollout_id`/`episodeId`。T02 不得把该技术字段回显成业务名，也不得要求 P05/P06 把 episode 宣布为最终合同。
- T02 六模式名已经收敛为 `raw-diagnostic | lance-browser | annotation | cleaning | review | published`；本文已同步。仍未冻结的是最终 path、`collectionItemId` 正式字段名、`versionId/revisionId` 与 base/annotation/cleaning/candidate 的精确映射。
- P05–P07 不保留 `EpisodeWorkbenchCore` 的第二套 shell，不拥有 media URL、播放时钟、camera slot、timeline 或 compare renderer。

### 16.2 给 T06｜P08 标注 + P10 标注任务

- 已完整读取 T06 当前规格（`T06-P08-P10-SPEC.md:89-95,151-187,246-267,369-435`）。双方已对齐：P08 是 T02 `annotation` 模式，P10 独占任务队列；标注草稿、提交快照与标注审核版本不可变；P08/P10 无发布动作、默认无 Raw 下载入口。
- T05 的 P07 Composition 只消费“固定且当前有效的 annotation revision + content hash + 标注级 approval fact”，不拥有标注编辑/任务/保存/审核状态机。T06 需提供稳定显示名、批准指针、批准失效规则、任务/审核 deep link、base Lance 兼容性，以及“无标注是否允许进入候选”的明确事实。
- T06 的“提交审核 / 审核通过 / 要求修改”是**标注级**流程；P07 的同名动作是对精确五层 Composition 的**版本级** review。接口必须携带 `reviewKind` 或等价明确类型及精确 revision，审计 UI 同时显示对象名，不能只传一个含糊的 `APPROVED`。
- 标注者只能保存/提交；只有 T06 审核 capability + 非自审条件成立才出现标注级审核动作。无论哪一种 T06 身份，都不得出现 T05 的 `冻结发布`。

### 16.3 给 T07｜P09 质量问题 + P11 非破坏性清洗

- 已完整读取 T07 当前规格（`T07-P09-P11-SPEC.md:54-60,114-154,228-318,320-364,435-455`）。双方已对齐：P09 只处理进入 Lance 后的人工作业问题；P11 是 T02 `cleaning/review` 模式；Raw 自动 QC RISK/REJECT 不由 P09/P11 覆盖；P11 审核通过不等于 P07 冻结发布。
- T05 的 P07 Composition 只消费一个**审核通过且固定**的 cleaning rules revision：revision ID、content hash、operation count、base Lance/annotation compatibility、批准 decision ID、提交者/审核者与时间。T07 拥有草稿、operation log、preview、提交、要求修改/拒绝、冲突与补偿状态机。
- P11 的 Cleaning 主动作固定叫 `提交审核`；区间使用半开 `[start_step,end_step)`。P11 的 Review 决定是**清洗修订级**事实；P07 的版本级 review 仍需对完整 Composition 单独作结论，不能复用同一个 decision ID。
- T07 还需给 P07：无清洗规则的显式状态、批准指针失效规则、基线/标注兼容结果、影响摘要、审核反馈 deep link，以及 preview/提交失败和 revision conflict 的安全投影。P07 不接收未提交草稿或过期 preview 作为冻结输入。

## 17. 验收门槛与建议验证命令

文档验收：

- [x] P05 常用筛选不超过 5 项，项目/区域/授权提示明确。
- [x] P06 全部用户文案使用“采集条目”，技术 episode/rollout 只在现状证据或技术详情。
- [x] 只有自动 QC PASS 的轨道能到 30 Hz/Lance；没有人工覆盖入口。
- [x] P06 preview 按需来自 Lance；no preview/Raw forbidden/conversion failed 可单独表达。
- [x] P07 同时看得见 base Lance、annotation、cleaning rules、review、frozen release。
- [x] `审核通过` 与 `冻结发布` 的按钮、权限、确认、审计和状态完全分开。
- [x] P05/P06/P07 的 1440/1280 线框、P07 compare/freeze key states 完整。
- [x] 所有浏览/对比跳 T02，不复制 Viewer。
- [x] loading/empty/partial failure/no preview/no Raw permission/Lance fail/conflict/read-only/unavailable 全覆盖。
- [x] Web guideline findings 有 source file:line，React 约束明确为 React 19 + Vite。
- [x] 产品确认、技术验证、可直接实现三类清单与 T02/T06/T07 交接完整。

建议执行：

```bash
check_status=0; git diff --no-index --check /dev/null plan/frontend-ux-parallel/T05-P05-P07-SPEC.md || check_status=$?; test "$check_status" -eq 1
rg -n "采集条目|自动质检|30 Hz|Lance|按需预览|审核通过|冻结发布|1440|1280|T02|T06|T07|file:line|React 19|Vite" plan/frontend-ux-parallel/T05-P05-P07-SPEC.md
git status --short -- plan/frontend-ux-parallel/T05-P05-P07-SPEC.md
```

专项现有测试（只证明已有 Mock/Viewer/底层模块，不证明本规格已实现）：

```bash
(cd frontend && pnpm exec vitest run src/mocks/handlers/datasets.handlers.test.ts src/features/viewer/EpisodeWorkbenchCore.test.tsx src/app/shell/dataset-context.test.ts)
(cd backend && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/pytest -p pytest_asyncio.plugin tests/workflow/test_temporal_workflows.py tests/preview tests/publishing tests/lance_catalog)
```

本轮实跑结果：前端 `3 files / 17 tests passed`；后端按上述显式 async plugin 口径，目标集合 `57 tests passed`。首次后端命令未显式加载 async plugin，曾使 3 个协程用例以“async functions are not natively supported”停止收集；修正命令后 Temporal 文件 `4 passed`，这不是业务断言失败。

未验证项：正式 collection-item 身份与 API、P05–P07 页面 BFF、cleaning rules 合同、拆分后的 review/freeze API、T02 最终 route/adapter、真实 auth/scope bootstrap、真实 Preview 浏览器联调、1280/1440 视觉回归、生产容量与端到端性能。它们均不得用当前 Mock 成功替代验收。
