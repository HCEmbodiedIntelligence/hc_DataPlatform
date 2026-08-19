# T03｜P01 数据工作台与 P20 采集任务 UX 规格

> 文档状态：效果图输入与产品评审稿，不是 API、数据库或实现合同
> 适用页面：P01 数据工作台、P20 采集任务
> 编制日期：2026-08-17
> 设计基线：`design-system/hc-data-platform/MASTER.md`（只读）
> 边界：本文只定义信息架构、交互、状态、口径候选与效果图要求，不授权业务编码。

## 0. 先读结论

P01 应从“六张等权 KPI + 存储与 Mock 漏斗”改为同一入口、按能力降级的运营工作台。首屏只回答三件事：**我现在要处理什么、真实数据链路停在哪里、下一步能去哪里**。页面的唯一重点视觉元素是业务真实的“信号轨道”，不是装饰性仪表盘。

P20 是独立的“采集任务”域，不是 P03 上传记录的换名版本。它管理任务定义、8 位任务码、人员/PICO/机器人分派、有效期与采集进度；P03/P04 继续管理云端上传会话与上传详情。`task_code` 只用于归类，不能承担认证、授权、唯一数据标识或幂等键；独立数据包以机器人生成的 `data_package_id` 识别。

当前还不能进入业务编码：P01 四条正式聚合 API 均不存在，P20 也没有正式列表/创建/编辑/分派合同。本文中的新字段、状态和深链参数均标注为“UX 要求”或“候选”，需产品决策和后续 OpenAPI 设计后才能实现。

## 1. 范围、证据等级与仓库保护

### 1.1 本文做什么、不做什么

本文交付：

- P01 当前实现与真实/Mock 边界审计；
- P01 信息架构、权限降级、1440/1280 线框、信号轨道与页面状态；
- P20 列表、创建、编辑、分派、任务码、进度、详情和审计线框及候选状态机；
- P01/P20/P03/P04/Raw 诊断之间的深链要求；
- 指标定义候选、效果图清单、Web 规范发现、React 实现约束；
- 必须由产品确认的问题、推荐选项、替代选项和被阻塞任务。

本文不做：

- 不修改前端、后端、OpenAPI、迁移、Mock、测试或主设计系统；
- 不把现有 Browser Mock 字段提升为正式业务合同；
- 不创造按“内部员工/外包/管理员”复制的页面；
- 不让 P20 进入 PICO—Gateway—机器人实时控制闭环；
- 不把 `SAVED`、云端 `Received`、QC `Pass/Risk/Reject` 混成一个“完成数”；
- 不设计“人工强制 QC 通过并直接进入 Lance”的动作。

### 1.2 证据等级

| 等级 | 含义 | 本文使用方式 |
|---|---|---|
| A｜方向事实 | 四份方向文档明确描述的业务约束 | 可以作为设计不可违背项，但方向文档自身仍可能处于评审稿状态 |
| B｜代码事实 | 当前工作树中的路由、组件、OpenAPI、迁移、模型和测试 | 只描述“当前有什么”，不推断未来产品规则 |
| C｜已批准共享方向 | 改版计划与主设计系统的共同基线 | 用于信息架构、视觉、状态和交付门禁 |
| D｜UX 要求 | 为形成可用页面所必需、但尚无正式合同支持的需求 | 明确标记，进入产品与技术设计待办 |
| E｜候选方案 | 本文为评审提供的默认选择 | 只有决策记录与合同落地后才可编码 |

### 1.3 开始工作时的工作树基线

以下为本终端开始工作时记录的 `git status --short`。这些修改均视为用户/其他终端资产，本文没有修改或回退它们：

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

## 2. 业务事实与身份边界

### 2.1 不可违背的链路事实

- PICO 只控制机器人端录制，不保存机器人采集数据；多人、多台 PICO 和多种机器人可使用同一任务码（`PICO 跨本体遥操作与机器人端录制方案.md:16-18`）。
- 任务码由云平台生成，是 8 位数字；前 7 位为任务/批次编号，第 8 位为 Verhoeff 校验位。离线校验只能发现输入错误，不能证明任务存在（同文档 `:223-238`）。
- PICO 本地“完成一条”只在机器人返回 `SAVED` 后增加；离线时不能显示云平台全局完成数或剩余数（同文档 `:240-250`）。
- `task_code` 与 `local_index` 都不唯一；`data_package_id` 是独立数据包身份，由机器人录制程序在接受开始请求时生成随机 UUID，并持久化 `recording_request_id → data_package_id` 映射（同文档 `:301-323`）。
- 云端按 `task_code` 归类，按 `data_package_id` 唯一保存和幂等接收；任务码、机器人 ID、时间、本地序号都不能单独作为唯一键（同文档 `:413-435`）。
- 自动 QC 结果必须拆分为 `PASS`、`RISK`、`REJECT`：PASS 才可进行 30 Hz 对齐；RISK 可预览/隔离并等待人工质检但默认不进入训练 Manifest；REJECT 保留 Raw MCAP、不进入正式训练数据集、可以重采（`数据平台 评测.md:224-248`）。
- 30 Hz 对齐不得静默伪造缺失数据；Lance 增量写入发生在 QC 通过之后（同文档 `:254-295`）。
- 页面预览按需读取 Lance 的对齐图像，使用相同 `step_index`，无效帧必须显式展示异常提示（同文档 `:357-423`）。
- 清洗/标注是非破坏性、版本化操作，区间为半开区间 `[start_step, end_step)`；Raw MCAP 与基础 Lance 不被原地修改（同文档 `:496-510`、`:657-664`）。
- 审核与发布是不同阶段；Reviewer 结果至少有 APPROVED、REJECTED、NEEDS_REVISION，最终链路在审核后再冻结版本/Manifest（同文档 `:668-690`、`:772-791`）。
- 控制频率、链路延迟或跟踪误差超过阈值时应标为 RISK，默认不能进入训练集（`统一机械臂规控、遥操作与数据采集平台建设及调试上线方案.md:205-219`）。

因此，P01 和 P20 的统一主链路定义为：

```text
采集任务 → 机器人 SAVED → 云端 Received → 自动 QC
                                      ├─ PASS → 30 Hz 对齐 → Lance → 标注/清洗 → 审核 → 发布
                                      ├─ RISK → Raw 只读诊断 / 隔离 / 人工质检；不得直接绕过 QC 门禁
                                      └─ REJECT → Raw 保留 / 可重采；不得进入 Lance
```

### 2.2 身份与显示用途

| 标识 | 业务用途 | 是否可作为数据唯一键 | 是否可作为授权凭据 | P01/P20 显示原则 |
|---|---|---:|---:|---|
| `collection_task_id`（UX 要求，名称待定） | P20 采集任务的稳定内部身份 | 对“任务对象”应唯一 | 否 | 深链和审计用；默认显示名称，完整 ID 次级可复制 |
| `task_code` | 云端任务/批次归类，PICO 离线输入 | 否 | 否 | 8 位分组显示、可复制；始终伴随“归类码，不是登录码/数据 ID”说明 |
| `pico_instance_id` | PICO 安装实例 | 是 | 否 | 分派/诊断中显示业务别名，技术 ID 次级 |
| `robot_id` | 机器人身份 | 是 | 否 | 显示机器人名/序列与状态，ID 可展开 |
| `collection_session_id` | 一次连续采集工作段 | 是 | 否 | 详情关联表与诊断上下文 |
| `recording_request_id` | 录制命令幂等 | 是 | 否 | 仅诊断/审计，不作为普通筛选主标签 |
| `data_package_id` | 机器人保存的一份独立数据包；上传幂等 | 是 | 否 | P20 进度、P03/P04、Raw 诊断之间的核心关联键 |
| `uploadId/session_id` | 云端上传会话 | 对上传会话唯一 | 否 | P03/P04 路径身份；不得与采集任务或数据包混称 |
| `rollout_id` | 当前 Ingest Manifest 已有身份 | 当前合同内稳定 | 否 | 与数据包映射需正式合同确认，不能默认二者相等 |

## 3. P01 当前实现审计

### 3.1 路由、权限和模式事实

- P01 当前路由为 `/dashboard`，并使用路由级懒加载（`frontend/src/pages/p01-dashboard/routes.tsx:4-12`）。
- 导航把 P01 设为无必需 capability（`frontend/src/app/shell/navigation-manifest.ts:30-36`）；页面内部却检查 `dashboard.read`（`frontend/src/pages/p01-dashboard/page.tsx:158-163`）。
- `dashboard.read` 只存在于 `RESERVED_CAPABILITIES`，而 `isCapability` 的集合只来自 canonical capabilities，所以当前授权快照不能把它解析成有效 capability（`frontend/src/entities/capability.ts:83-112`）。
- 页面把 `dashboardReadEnabled` 定义为“Browser Mock 编译期开关且 routeAllowed”，即便未来授权含义修好，真实模式仍不会启用请求（`page.tsx:158-180`）。
- 非 Browser Mock 编译模式直接显示“工作台聚合能力尚未开放”，明确不会发请求（`page.tsx:188-210`）。这是当前正确的诚实降级，不应被占位数据替换。
- MSW 启动还要求编译期 `VITE_MOCK_MODE` 与运行时配置同时为 `browser`（`frontend/src/main.tsx:29-31`），而 P01 页面只看编译期开关。Browser 构建配运行时 off 时可能出现“页面以为可读、MSW 未启动、向真实后端请求不存在路径”的分裂状态。

### 3.2 四条 Dashboard 请求逐项复核

| 请求 | 客户端代码 | Query 启用/缓存 | Browser Mock | 正式 OpenAPI/后端 | 当前结论 |
|---|---|---|---|---|---|
| `GET …/dashboard/activity` | `frontend/src/features/dashboard/api/client.ts:36-47` | `api/queries.ts:12-19`；60s stale | 有；默认首屏请求 | 无 | Mock-only；现类型是上传时间桶，不等于“最近活动事件流” |
| `GET …/dashboard/snapshot` | `api/client.ts:49-60` | `api/queries.ts:22-29`；300s stale | 有；默认首屏请求 | 无 | Mock-only；存储、Episode、工作项口径都未批准 |
| `GET …/dashboard/coverage` | `api/client.ts:62-68` | `api/queries.ts:32-39`；300s stale | 有；用户点开后请求 | 无 | Mock-only；缺少批准的任务全集、机器人组与分母 |
| `GET …/dashboard/pending-items` | `api/client.ts:70-90` | `api/queries.ts:42-64`；60s stale | 有；首屏 5 条，抽屉 50 条 | 无 | Mock-only；来源、优先级、SLA、去重和深链未定义 |

补充证据：正式 `backend/openapi/ingest.yaml:1-90` 只有 upload session 相关路径，`backend/openapi.generated.yaml` 没有 dashboard 路径；既有审计也记录四条路径均无正式 OpenAPI、Router、Service、Repository 与后端测试（`plan/P01-P19-REAL-API-COVERAGE-AUDIT.md:83-99`）。本次只做静态复核，没有把既有 Browser/off 模式运行记录冒充为本次新跑结果。

### 3.3 当前 UI 与业务目标的差距

| 现状 | 证据 | 问题 | 改版要求 |
|---|---|---|---|
| 六张等宽 KPI | `DashboardSummaryStrip.tsx:66-129`、`styles.module.css:27-43` | 采集接收、存储、质量、Episode、人工问题、清洗等异质指标被赋予相同视觉权重；1280 时仍密集 | 移除“六等分首屏”；改为一张处置摘要 + 非等权链路轨道 + 领域卡片 |
| 首屏优先展示资产容量 | `page.tsx:267-290` | 回答“占了多少空间”多于“哪里停住、我要做什么” | 我的待办与链路阻塞优先；容量下沉到 P12 或次级上下文 |
| 存储图、存储历史与 Episode 漏斗占据主图 | `dashboard-charts.tsx:289-362` | P01 变成存储仪表盘；Mock 的 uploaded→validated→viewable 被画成单调漏斗但不是已批准链路 | 只对真正顺序、同一 cohort 的阶段画轨道；非同分母数值并列而不连成漏斗 |
| 图标题写死“24 小时” | `dashboard-charts.tsx:264-287` | 页面可切 7d/30d，但标题产生错误承诺 | 所有标题、bucket 与 as-of 由当前 URL 时间条件生成 |
| 待办显示内部枚举与原始时间 | `DashboardPendingList.tsx:11-56` | 业务用户难理解，时间缺少统一时区格式 | 使用产品文案映射、`Intl`、相对时间 + 完整时间；未知枚举显式降级 |
| 待办声称可点击但目标“暂不可用” | `DashboardPendingList.tsx:59-67` | 没有可执行下一步，破坏“我的待办”核心价值 | 只有稳定深链就显示动作；没有目标时显示阻塞原因且不伪装为链接 |
| 覆盖表直接显示 robot/task ID | `DashboardCoverageTable.tsx:8-27` | 技术标识取代业务名称，分母来源不明 | 等批准 collection plan 与目录后才设计；名称为主、ID 次级、明确分母 |
| BigInt 直接转 Number | `DashboardSummaryStrip.tsx:22-33`、`dashboard-charts.tsx:107-109,211-225` | 大数可能丢精度或溢出，图与文字不一致 | 先以 BigInt 安全缩放到显示单位，再转图表安全数值；保留精确表格 |
| 一个 Effect 创建三张图 | `dashboard-charts.tsx:101-260` | activity 更新会重建 storage/history；依赖过宽 | 按数据域拆图与 Effect，窄化依赖，独立失败和刷新 |
| ECharts 四个动态 import 串行 await | `dashboard-charts.tsx:112-116` | 形成不必要的加载瀑布 | 独立 import 并行；保留路由/重型图表懒加载 |

当前测试只找到 `AssetCapacityBoard.test.tsx` 对 Mock fixture 与零值的组件测试；未找到 P01 真实模式、权限降级、四域部分失败、URL 时间范围或深链 E2E。后续验收不能用“组件能渲染 fixture”替代这些路径。

## 4. P01 目标信息架构

### 4.1 产品、用户与单一任务

- 产品：公网部署的工业机器人数据生产与治理平台。
- 用户：内部运营/研发/审核/发布者，以及按项目授权的采集、标注、清洗人员。
- 单一任务：进入当前项目与区域后，在 10 秒内判断**哪一阶段异常或积压、哪些是我的动作、点击后去哪处理**。
- 视觉性格：可信、精密、可审计的“机器人遥测账本”；不是通用 BI、营销大屏或霓虹驾驶舱。

具体视觉合同沿用 Master，不另造局部主题：

| 维度 | P01/P20 采用方式 |
|---|---|
| 色彩 | 主动作 `#5965D8`、信号 `#3B82C4`、页面 `#F0F2F8`、表面 `#FFFFFF`；Pass `#2F7D66`、Risk `#A86512`、Reject `#B84646`。实现时统一走语义 Token，不在组件散写 Hex |
| 字体 | 系统中文字体；正文基准 14px；技术 ID 才用等宽字体；业务名称优先，完整 ID/哈希按需展开 |
| 密度 | 8/10，但靠分组和渐进展开实现；不把字号压到 11px 以下，不用卡片数量制造“专业感” |
| 圆角/阴影 | 沿用 10/12/18/24px 层级；阴影只表示层级，无彩色光晕、玻璃拟态或全屏渐变 |
| 动效 | 2/10；只用于状态变化、抽屉和光标，150–250ms，支持 reduced motion；轨道不持续流动 |
| 组件 | 延续 Ant Design 与 Lucide；一个页面最多一个主 CTA；图标按钮有可访问名称 |
| 焦点 | 2–4px 可见 focus ring；表格、Tab、抽屉与弹层保持自然键盘顺序，无 hover-only 关键信息 |

### 4.2 全员同页、能力降级原则

所有用户进入相同 `/dashboard` 页面、使用相同模块顺序。差异只来自：

1. 数据范围：后端返回当前用户在当前 project + region 可见的对象集合；
2. 模块读取能力：看得到完整数据、受限汇总、无权限占位或聚合未开放状态；
3. 动作能力：有能力则显示明确动词，无能力则只读或不显示动作；
4. 业务对象分派：采集/标注/清洗人员默认只看到分配给自己或自己提交的数据；
5. 后端再次校验：前端模块隐藏绝不是安全边界。

禁止按角色分叉成“外包工作台”“审核员工作台”“管理员工作台”。布局相同，内容和动作退化。

### 4.3 模块顺序与权限退化矩阵

下表中的能力名称描述的是**语义**，不是批准后的 capability 字符串；字符串需由权限 Owner 统一定义。

| 顺序 | 模块 | 首要问题 | 完整读取 | 范围受限 | 无读取能力 | 聚合未开放/部分失败 | 允许动作 |
|---:|---|---|---|---|---|---|---|
| 1 | 我的待办 | 我下一步做什么？ | 当前 scope 中分派给我的、我可执行的待办 | 只显示本人/本人提交来源，并标“仅我的范围” | 保留模块位置，显示“没有待办读取权限”，不泄漏数量 | 其他模块继续显示；待办区显 request id、as-of、重试 | 按动作能力显示“继续采集/查看上传/诊断/标注/清洗/审核/发布” |
| 2 | 采集与接收 | 机器人保存的数据到云端了吗？ | 任务、SAVED、Received、上传中/失败的可核对数 | 仅分配任务与本人提交/授权机器人汇总 | 显示无权限，不显示推断总量 | SAVED 或 Received 任一源失败时分别标未知，禁止用差值伪造 | 去 P20 任务、P03 上传记录、P04 上传详情 |
| 3 | 自动质检 | 哪些数据被门禁拦住？ | Pass/Risk/Reject/处理中，按采集任务可追溯 | 仅可见采集条目 | 无权限占位 | QC 源失败不影响接收区；标最后成功时间 | Risk/Reject 去 Raw 只读诊断；有权限者可发起重采，不能“强制通过” |
| 4 | 数据处理 | PASS 后到 Lance 了吗？ | 待 30 Hz、处理中、Lance ready、失败 | 授权条目/数据集范围 | 无权限占位 | 转换域单独失败 | 去处理作业/采集条目；具体目的页待 Owner 定义 |
| 5 | 标注与清洗 | 有多少可领取、进行中、待审核、退回？ | 能力范围内的分阶段工作量 | 只显示本人任务 | 无权限占位 | 标注与清洗可各自独立失败 | 去 P08/P11 等既有功能；动作由 claim/edit/submit 能力控制 |
| 6 | 审核与发布 | 哪些版本需要审核或冻结？ | 待审、需修改、待发布、最近发布 | 只显示本人有权审核/发布范围 | 无权限占位 | 审核与发布分域失败 | “审核”和“发布”绝不合并；批准不等于发布 |
| 7 | 最近活动 | 最近发生了什么？ | 只显示已授权对象的审计型业务事件 | 只显示相关/本人事件 | 无权限占位 | 活动流不可用不影响待办 | 点击稳定业务对象；不把吞吐时间桶叫活动流 |

### 4.4 1440 × 900 线框

```text
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ 数据工作台  / 当前项目：A / 区域：华东-1       [最近24小时⌄]  数据截至 10:32  [刷新] │
│ 仅当前 project + region；部分模块可能按本人分派范围显示                              │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ 需要你关注  12                                                            [查看全部待办] │
│ 3 个上传失败 · 5 个 QC Risk · 4 个待审核     最早超时：今天 14:00     [处理最高优先项] │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ 信号轨道（同一 scope / 明确 as-of；不同分母不连成漏斗）                               │
│ 采集任务 → 机器人SAVED → 云端Received → 自动QC → 30Hz → Lance → 标注 → 清洗 → 审核 → 发布│
│ 18进行中    1,248设备计数   1,106云端包   P980/R91/X35  72等待   908就绪   …     …     …    │
│             ▲离线源         ▲142待核对    [91 风险去 Raw 诊断]                          │
├───────────────────────────────────────────────────────┬──────────────────────────────────┤
│ 我的待办（优先级 / 截止时间 / 阻塞原因）  7/12 宽     │ 采集与接收                    5/12 │
│ [QC Risk] 夹爪遮挡 · 逾期2h                 [诊断]    │ 进行中任务 18   云端接收 1,106      │
│ [上传失败] 包 …82de · 重试耗尽              [详情]    │ 上传中 23       上传失败 3           │
│ [待审核] 清洗修订 #7 · 今天14:00            [审核]    │ “SAVED-Received”仅在已对账时显示     │
├───────────────────────────────────────────────────────┼──────────────────────────────────┤
│ 自动质检与处理                                        │ 标注、清洗、审核与发布              │
│ PASS 980 | RISK 91 | REJECT 35 | QC处理中 27         │ 标注进行中 41  清洗待审 8            │
│ 30Hz等待 72 | 转换中 18 | Lance就绪 908 | 失败 6     │ 需修改 5  待审核 12  待发布 3       │
│ [查看 Risk/Reject]                                    │ [进入我的工作]                       │
├───────────────────────────────────────────────────────┴──────────────────────────────────┤
│ 最近活动（业务事件，不是吞吐曲线）                                         [查看审计] │
│ 10:31 数据包 …a91f 已接收  · 10:28 QC 标为 RISK · 10:20 版本 v12 审核通过            │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

说明：

- 首屏只有一个视觉主句“需要你关注”，不是六张同权 KPI；数字是结构示意，不是口径或 fixture。
- 主动作最多一个，默认“处理最高优先项”；其他动作降为文本链接。
- 信号轨道是共享设计系统规定的唯一重点视觉元素。节点面积不编码数值，避免大数字制造虚假比较；数值、分母、异常均用文本与状态标签表达。
- 容量/存储只作为需要时的次级跳转到 P12，不占首屏主叙事。

### 4.5 1280 宽度线框

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ 数据工作台 / 项目A / 华东-1          [24小时⌄]  截至10:32 [刷新]             │
├──────────────────────────────────────────────────────────────────────────────┤
│ 需要你关注 12  · 3上传失败 · 5 QC Risk · 4待审核          [处理最高优先项]   │
├──────────────────────────────────────────────────────────────────────────────┤
│ 信号轨道（允许在容器内换为两行，不允许整页横向滚动）                        │
│ 采集 → 机器人SAVED → 云端Received → QC(P/R/X) → 30Hz → Lance               │
│                                                    ↘ 标注 → 清洗 → 审核 → 发布│
├──────────────────────────────────────────────┬───────────────────────────────┤
│ 我的待办（8/12）                             │ 采集与接收（4/12）              │
│ 3–5 行，低优先项进入“查看全部”               │ 任务/接收/上传失败               │
├──────────────────────────────────────────────┴───────────────────────────────┤
│ 自动质检与数据处理（单行紧凑分组；详细趋势按需展开）                        │
├──────────────────────────────────────────────┬───────────────────────────────┤
│ 标注与清洗                                  │ 审核与发布                      │
├──────────────────────────────────────────────┴───────────────────────────────┤
│ 最近活动（最多 4 行）                                                         │
└──────────────────────────────────────────────────────────────────────────────┘
```

1280 规则：

- 页面本体不得横向滚动；表格可在自身容器内降列或滚动，但主动作始终可见。
- 信号轨道可在“30Hz/Lance”和“审核/发布”处组合标签，不缩到 11px 以下，不依赖 hover 才能读。
- 待办保留标题、优先级、截止、动作四项；内部 ID、来源摘要进入展开区。
- 折叠低优先图表，不用把 1440 的每个模块机械压窄。

### 4.6 信号轨道语义与状态

| 状态 | 表达 | 禁止做法 |
|---|---|---|
| 正常 | 中性色轨道 + 成功节点；显示精确数量与 as-of | 用绿色面积/环形进度暗示未经定义的完成率 |
| 积压 | 节点旁琥珀标记 + 数量 + 最老等待时长 | 只写“异常”而无数量/下一步 |
| 失败/拒绝 | 红色标记、明确 Risk/Reject/失败类别、可执行深链 | 把 RISK 与 REJECT 合并；红色全屏背景 |
| 数据未知 | 灰色虚线节点、“数据源不可用/无权限/尚未开放” | 以 0 替代未知；回退 Mock |
| 刷新中 | 保留旧值，局部标“更新中” | 整页清空闪烁 |
| 陈旧 | 显示最后成功时间与超过阈值的提示 | 静默展示旧值 |
| 分母不同 | 节点并列并标明口径，不计算总体转化率 | 连成漏斗或用阶段宽度表达转化 |

### 4.7 P01 页面级状态合同

| 状态 | 页面行为 |
|---|---|
| 首次加载 | 骨架保持模块布局，禁止假数字；焦点不被抢走 |
| 正常 | 显示 scope、时间窗、时区、as-of 和数据范围说明 |
| 空数据 | 说明是“当前条件无数据”还是“尚未创建任务”，给符合权限的下一步 |
| 部分失败 | 成功域继续可用；失败模块显示错误、request id、最后成功时间和局部重试 |
| 无页面读取权限 | 同一 `/dashboard` 骨架中显示无权限，不泄漏模块计数 |
| 模块无权限 | 保留位置与简短原因；不通过 0 推断业务不存在 |
| 只读 | 内容不变，编辑动作消失或明确只读；深链仍需目标页再次鉴权 |
| 聚合能力未开放 | 沿用诚实的 feature-unavailable；不得向不存在路径发请求或显示 fixture |
| 会话过期 | 使用全局认证恢复流程，保留安全的内部 `returnTo`；不把 401 显示为空数据 |
| 未知枚举 | 局部显示“未知状态”与原值的诊断入口，页面其他部分继续工作 |

## 5. P20 采集任务 UX 规格

### 5.1 页面边界与导航

P20 管理“计划和分派”，P03/P04 管理“云端上传执行”。建议导航由拥有全局 IA 的终端在后续改为：

```text
采集与接收
├─ P20 采集任务
├─ P03 上传记录（当前文案“上传任务”应避免与 P20 混淆）
└─ P02 数据源
```

这是 IA 要求，不是本终端的代码修改。当前导航仍是“数据接入 / P03 上传任务 / P02 数据源”（`frontend/src/app/shell/navigation-manifest.ts:38-44`）。

P20 的核心对象是“采集任务”，它应包含：

- 稳定内部任务身份、业务名称、任务定义/版本；
- 云平台生成的 8 位 task code；
- project、region 与业务批次范围；
- 有效期与采集目标；
- 人员、PICO 设备、机器人分派；
- SAVED、Received、QC、30Hz/Lance 的分维度进度；
- 修改、分派、状态变化与敏感查看的审计历史。

当前 `ingest.collection_jobs` 只有 `project_id`、`region_code`、`task_id`、`collection_job_id`、单个 `robot_id`、状态和时间戳（`backend/migrations/ingest/001_ingest.sql:3-14`）；`CollectionJob` 模型同样没有 task code、人员/PICO 分派、有效期、目标或分阶段进度（`backend/src/hc_data_platform/ingest/models.py:101-109`）。正式 Ingest OpenAPI 只有上传会话路径，Manifest 虽有 `task_id`、`collection_job_id`、`robot_id`，但没有 P20 CRUD/list（`backend/openapi/ingest.yaml:1-90,221-241`）。所以以下都是 UX 要求，不能据此声称后端已支持。

### 5.2 P20 列表

```text
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ 采集任务   管理任务定义、归类码、分派和采集进度                         [新建采集任务] │
│ [搜索名称/任务码] [状态⌄] [有效期⌄] [负责人⌄] [机器人⌄] [仅我的任务□] [重置]       │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ 任务                  状态/有效期       分派                   采集进度          操作 │
│ 双臂装箱 A 批         进行中 · 08/31    8人 · 12 PICO · 6机   目标1200             │
│ 任务码 1234 5678      还有14天           2项分派风险             SAVED 824           │
│                                                                  Received 731         │
│                                                                  QC P640/R58/X21 [详情]│
├──────────────────────────────────────────────────────────────────────────────────────┤
│ 夜间抓取验证          暂停 · 至08/20    2人 · 2 PICO · 1机    目标80              │
│ 任务码 2048 1936      已暂停3小时        设备已离线                Received 53     [详情]│
└──────────────────────────────────────────────────────────────────────────────────────┘
```

列表要求：

- 名称是主标识，任务码分组为 `1234 5678` 便于核对，复制时仍复制 8 位纯数字。
- 状态、有效期、分派风险和多阶段进度分列；禁止把 `Received/目标` 称为总完成率。
- 默认排序候选：需要处理的分派/有效期/QC 风险优先，其次最近更新；最终规则待确认。
- 行点击进入详情，行尾只保留一个显式“详情”；高风险命令不放在表格内。
- 支持 URL 可复现的搜索、状态、有效期、负责人、机器人、仅我的任务、排序与游标。
- 大于 50 行时采用游标分页；虚拟化要在真实数据量和 Profiler 测量后决定。

### 5.3 新建与编辑

推荐使用单页分段表单而非强制 Stepper；用户可看见整体上下文，保存草稿后再发布启用。1440 主栏 8/12，右侧 4/12 固定显示校验摘要。

```text
┌────────────────────────────────────────────────────────────────────────────────────┐
│ 新建采集任务                                                     [保存草稿] [启用] │
├────────────────────────────────────────────────────┬───────────────────────────────┤
│ 1 基本信息                                         │ 检查摘要                      │
│ 任务名称* [________________________]                │ ✓ 名称完整                    │
│ 项目/区域  [当前项目] [华东-1]（创建后不可随意迁移）│ ! 目标与有效期待填写          │
│ 批次/说明 [________________________]                │ ! 尚未分派机器人              │
│                                                    │                               │
│ 2 任务定义（版本化）                               │ 任务码                        │
│ 动作/场景* [装箱⌄] 目标条件* [____________]         │ 保存草稿后由云平台生成         │
│ 验收与采集说明 [多行、可预览]                      │ 它只用于归类，不是数据 ID/密码 │
│                                                    │                               │
│ 3 目标与有效期                                     │ 启用影响                      │
│ 目标数据包数* [1200]  开始 [日期时间]  结束 [日期时间]│ 分派用户可在有效期内使用任务码 │
│ 时区 [项目时区，只读/明确]                         │ 定义修改将创建新版本（候选）   │
│                                                    │                               │
│ 4 分派                                             │ [查看全部校验问题]             │
│ 人员 8 [管理]  PICO 12 [管理]  机器人 6 [管理]     │                               │
└────────────────────────────────────────────────────┴───────────────────────────────┘
```

字段与校验候选：

| 分组 | 字段 | 规则/交互 | 合同状态 |
|---|---|---|---|
| 基本 | 名称、说明、批次标签 | 名称必填；唯一范围待产品确认；说明不承载 secret | UX 要求 |
| Scope | project、region | 创建时绑定；跨区域任务是否允许为 P0 决策 | UX 要求 |
| 定义 | 场景/动作、采集说明、验收条件、需要模态 | 使用批准目录；自由文本只补充，不替代版本化结构 | UX 要求 |
| 目标 | 目标数据包数 | 不能用 PICO 本地条数作为云端唯一事实；修改目标需审计 | UX 要求 |
| 有效期 | start/end + IANA timezone | 开始早于结束；DST/日界线按项目时区；结束不自动伪造“已完成” | UX 要求 |
| 分派 | 人员、PICO、机器人 | 三者分别选择和校验，不要求一一配对；同一 task code 可多人多设备多机器人 | 方向事实 + UX 要求 |
| 任务码 | 8 位数字 + Verhoeff | 云端生成；UI 只校验格式/校验位并提示“存在性需在线确认” | 方向事实；生成/回收合同未定义 |
| 并发 | revision/etag | 编辑冲突时展示差异，不静默覆盖 | 技术要求，字段待合同 |

编辑规则候选：

- 草稿状态可编辑全部非系统字段；启用后 `task_code`、scope 与已产生数据含义的定义不可原地改写。
- 启用后的说明性修订可创建新 revision 并记录生效时间；目标/有效期变更必须写审计原因。
- 已结束/取消任务只读；允许复制为新任务，但必须生成新内部任务身份，task code 是否复用需产品决策。
- 编辑页面始终显示当前 revision、最后修改人、最后修改时间和未保存状态。

### 5.4 分派体验

```text
┌───────────────────────────────────────────────────────────────────────────────┐
│ 管理分派 · 双臂装箱 A 批                                    [取消] [保存分派] │
├───────────────────────┬───────────────────────────────────────────────────────┤
│ 类型                  │ 已选 / 可选                                            │
│ ● 人员                │ [搜索姓名/账号] [仅可用□]                              │
│ ○ PICO 设备           │ ☑ 李明  采集者  有效授权至09/30  最近活跃 2h           │
│ ○ 机器人             │ ☑ 王宁  采集者  授权将在任务结束前过期  [风险]          │
│                       │ ☐ 陈晓  无当前项目访问权限             [不可选原因]    │
├───────────────────────┴───────────────────────────────────────────────────────┤
│ 当前：8 人 · 12 PICO · 6 机器人   冲突：1 项授权过期 · 2 台机器人维护中       │
│ 保存影响：新增对象立即可见；移除分派不删除历史数据，不撤销已保存/已接收数据    │
└───────────────────────────────────────────────────────────────────────────────┘
```

分派要求：

- 人员、PICO、机器人分别管理，使用复选和批量添加；不设计拖拽关系图。
- 每个不可选项必须给原因：无项目权限、区域不匹配、停用、维护、有效期冲突或已达并发限制。
- 移除分派是高风险动作：确认影响、生效时间；保留历史采集和审计，不删除数据。
- 只向有“管理分派”动作能力的用户显示保存；其他用户使用完全相同内容但只读。
- 设备最近在线状态只能作为运营信号，不进入授权判断，也不能承诺 PICO 实时在线。

### 5.5 任务码体验

```text
┌──────────────────────────────────────────────────────────┐
│ 采集任务码                                               │
│ 1234 5678                                      [复制]    │
│ 已启用 · 生成于 2026-08-17 09:00 · 有效至 08-31 18:00   │
│                                                          │
│ 用于：PICO/机器人离线输入，以及云端任务/批次归类         │
│ 不用于：登录、设备认证、访问授权、数据唯一标识或上传幂等 │
│ ⓘ 离线校验通过只说明数字/校验位正确，不证明云端任务存在  │
└──────────────────────────────────────────────────────────┘
```

| 场景 | UI 行为 |
|---|---|
| 任务草稿未生成 | 显示“保存后由云平台生成”；禁止用户手填一个看似有效的码 |
| 已生成 | 显示 `1234 5678`、复制按钮、生成时间、状态；复制反馈使用 `aria-live` |
| 离线输入说明 | 明确“PICO/机器人只做 8 位与 Verhoeff 校验；校验通过不代表云端任务存在或当前有效” |
| 分享 | 只允许复制任务码和最小采集说明；不把内部 ID、token、签名 URL 放进二维码/链接 |
| 失效 | 显示失效原因与时间；历史数据仍按该 code 可追溯 |
| 冲突/复用 | UI 不自行声称全局唯一；若同 code 存在多 revision/历史任务，在线查询必须要求稳定任务 ID 消歧 |

安全文案必须固定表达：**“任务码用于数据归类，不是账号、密码、授权码或数据包 ID。”**

### 5.6 详情、进度与审计

```text
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ 双臂装箱 A 批  [进行中]  任务码 1234 5678 [复制]     [暂停任务] [更多⌄]             │
│ 2026-08-17 09:00 — 2026-08-31 18:00 · Asia/Shanghai · 当前项目/华东-1               │
│ [概览] [分派] [数据包] [任务定义 v3] [审计]                                         │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ 信号轨道                                                                            │
│ 目标 1200 │ SAVED 824（设备上报）│ Received 731（云端唯一包）│ QC P640/R58/X21/处理中12 │
│           │ 最后设备同步10:20    │ 最后接收10:31              │ [58项去Raw诊断]          │
│           └─ 93 条差异：仅 61 条已具备可核对关联；32 条来源离线/身份未回传，不做结论 ─┘│
├──────────────────────────────────────────────────────┬───────────────────────────────┤
│ 分派摘要                                             │ 风险与阻塞                    │
│ 8 人 · 12 PICO · 6 机器人                           │ 3 上传失败 [查看上传记录]     │
│ 1 人授权将在有效期内过期 · 2 台设备 24h 未同步       │ 58 QC Risk [Raw只读诊断]      │
│ [管理分派]                                           │ 21 Reject [发起重采]          │
├──────────────────────────────────────────────────────┴───────────────────────────────┤
│ 最近数据包                                                                        │
│ …a91f | Robot-06 | SAVED 10:12 | Received 10:31 | QC RISK | [上传详情] [诊断]      │
│ …82de | Robot-02 | SAVED 10:08 | 未接收      | —       | [核对设备/上传]          │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

进度原则：

- 目标、SAVED、Received、QC、Lance 是不同观测面，逐项显示来源、as-of 与未知数；不合成单一百分比。
- `SAVED` 是设备/机器人侧确认的计数，可能延迟同步；`Received` 以云端已提交的 distinct `data_package_id` 为准。只有当两端有稳定关联、相同 scope、相同截止时点时才显示“待核对差异”。
- QC 必须分别显示 Pass/Risk/Reject/处理中/未知，不得将 Risk 人工点击为 Pass 后直接进入 Lance。
- 每个异常数字必须有稳定深链或明确“目标能力尚未开放”；无深链就不伪装成可点击。
- 进度卡显示数据来源和最后同步时间；超过阈值显示陈旧，不自动变成 0。

审计 Tab：

```text
时间                操作者/来源       事件                 变更摘要             关联
2026-08-17 10:00    王宁              更新有效期            08/30 → 08/31        revision 3
2026-08-17 09:31    系统              接收数据包            data_package …a91f  upload …42
2026-08-17 09:20    李明              移除机器人分派        Robot-03             原因…
```

- 支持事件类型、操作者、日期筛选，URL 可复现；默认倒序、游标分页。
- 业务摘要可读，完整前后值按需展开；secret、signed URL、token、原始凭据永不展示。
- 系统事件与人工事件区分来源；自动接收/QC 的服务身份可追踪。
- 审计读取本身受 scope 与权限限制，导出不是默认动作。

### 5.7 P20 候选状态机（待产品确认）

当前后端 `collection_jobs` 状态是 REGISTERED/COLLECTING/PAUSED/COMPLETED/FAILED/CANCELLED，但它是 Ingest 底层 collection job，不足以直接定义 P20 计划任务。P20 暂用以下产品候选，不要求前后端照抄枚举名：

```text
                       到开始时间
 [DRAFT] --启用------> [SCHEDULED] ------------> [ACTIVE] <------恢复------ [PAUSED]
    |                       |                        |  \                    /
    | 取消                  | 取消                   |   \暂停--------------/
    v                       v                        |
 [CANCELLED] <--------------+                        +--人工结束--> [CLOSED]
                                                     |
                                                     +--取消------> [CANCELLED]

 [EXPIRED] = 派生显示态：now > end_at 且任务未 CLOSED/CANCELLED；
              系统是否自动关闭、允许补录或要求人工确认，尚未决定。
```

状态约束候选：

| 状态 | 可编辑 | 可分派 | 可采集 | 说明 |
|---|---|---|---|---|
| DRAFT | 全量 | 可预配 | 否 | task code 生成时机待确认；不可对 PICO 宣称有效 |
| SCHEDULED | 受限/版本化 | 是 | 未到开始时间不可采集 | 提前验证授权、设备、机器人与有效期冲突 |
| ACTIVE | 仅说明、目标、有效期等受审计字段 | 是 | 是 | 任务定义语义变更应新 revision |
| PAUSED | 同 ACTIVE | 是 | 否 | 已保存/已接收数据不受影响 |
| CLOSED | 否，只读 | 否 | 否 | 达到目标不应自动等于关闭，除非产品明确 |
| CANCELLED | 否，只读 | 否 | 否 | 不删除历史数据、分派、事件或 task code 关联 |
| EXPIRED（派生） | 由最终策略决定 | 默认否 | 默认否 | 需产品决定宽限期/补录/自动关闭 |

失败不建议作为任务主生命周期状态：上传失败、QC Risk/Reject、Lance 转换失败是数据包/处理阶段状态，应在信号轨道和阻塞列表中表达，不把整个任务变成 FAILED。

## 6. 页面间深链与参数要求

### 6.1 当前可复用事实

- P03 当前路径 `/ingest/uploads`，需要 `upload.read`（`frontend/src/pages/p03-upload-jobs/routes.tsx:12-20`）。
- P04 当前路径 `/ingest/uploads/:uploadId`，是 P03 导航所有的隐藏详情路由，也需要 `upload.read`（`frontend/src/pages/p04-upload-detail/routes.tsx:12-20`）。
- 现有 Ingest builder 能安全构建 P03/P04，并支持 P04 的 `tab`、`objectId`、安全内部 `returnTo`；会过滤疑似敏感 query key（`frontend/src/features/ingest/routing.ts:8-27,34-74`）。
- 当前 P03/P04 UploadSession 模型没有 `task_code`、`data_package_id`、`collection_session_id`，所以不能仅靠追加 query 就假设页面会过滤或展示这些关联。
- P20 和 Raw 诊断的正式路径/builder 尚不存在。本文只定义上下文，不发明可实现 URL。

### 6.2 深链矩阵

参数名是语义候选，最终以路由 Owner 和正式合同为准；稳定身份优先，`task_code` 永不单独作为授权或唯一定位。

| 来源 → 目标 | 用户意图 | 必需稳定身份/上下文 | 建议视图状态 | 当前支持 | 阻塞项 |
|---|---|---|---|---|---|
| P01 → P20 列表 | 查看进行中/异常采集任务 | project、region 已由 Shell scope 固定 | `status`、`risk`、`assignee=self`、时间窗 | P20 无路由 | P20 route/query codec/API |
| P01 → P20 详情 | 处理某个采集任务 | `collection_task_id`；不得只传 task code | `tab=overview` 或 `tab=packages` | 无 | 稳定任务身份和详情合同 |
| P20 → P03 | 查看该任务关联上传记录 | `collection_task_id`；可选 `data_package_id` | 状态/时间筛选、`returnTo` | P03 builder 可带任意安全 query，但页面未消费这些筛选 | UploadSession 关联字段、P03 codec/合同 |
| P20/P01 → P04 | 查看明确上传会话 | `uploadId` path param | `tab` 取 `verification`、`events` 或 `manifest`，并带安全 `returnTo` | 现 builder 已支持 `uploadId/tab/objectId/returnTo` | 必须先由后端返回稳定 uploadId 关联 |
| P04 → P20 | 回到采集任务/核对进度 | `collection_task_id`；显示 task code 仅作辅助 | `tab=packages`，定位 `data_package_id` | 无 | P04 模型缺关联字段；P20 builder 不存在 |
| P20/P01 → Raw 诊断 | 定位 QC Risk/Reject 的原始证据 | `data_package_id` 或稳定 `rollout_id`、`qc_run_id` | `mode=raw-readonly`、camera/topic、`start_step/end_step`、异常锚点、`returnTo` | Raw workbench 路由未定义 | 共享工作台路由、身份映射、QC 合同 |
| Raw 诊断 → P20 | 回到任务与重采上下文 | `collection_task_id` + `data_package_id` | `tab=packages` | 无 | 同上 |
| P01 → P03 | 查看上传失败集合 | scope + 稳定筛选语义 | `status=failed`、时间窗 | 只能到 P03 基础页 | P01 pending target catalog、P03 筛选合同 |

深链统一规则：

- project/region 权限由后端和 Shell scope 校验，不能相信 query 参数；目标页若 scope 不匹配，显示 403/404 语义而非泄漏对象存在性。
- `returnTo` 仅允许安全站内路径；不得携带 token、签名、bucket、object key、credential。
- 时间区间统一 `from` inclusive / `to` exclusive；Step 区间统一 `[start_step, end_step)`。
- 过滤、Tab、排序、游标前的搜索条件应写 URL；临时抽屉开关可由实现评估，但复制链接必须回到有意义状态。
- 目标对象不存在、无权限、已归档时给明确状态，不静默回列表首页。

## 7. 指标定义候选表

这是产品决策输入，不是正式指标合同。所有计数都必须返回 scope、as-of、来源状态；“未知/无权限/未开放”不能显示为 0。

| 指标 | 推荐定义 | Scope / 时间 | 分子 / 分母 | 候选事实源 | 权限与刷新 | 特殊状态 | 待确认 |
|---|---|---|---|---|---|---|---|
| 我的可执行待办 | 当前用户有动作能力且未关闭的稳定 source item，按 source type + source id 去重 | 当前 project+region；点时快照 | 件数；无分母 | upload failure、QC Risk/Reject、annotation/review/publish 等真实模型 | 本人可执行集合；目标 60s，返回 as-of | 逾期、阻塞、未知来源分别标 | 来源目录、优先级、SLA、关闭条件 |
| 进行中采集任务 | P20 主状态为 ACTIVE，且在用户可见/分派范围 | 当前 scope；当前时点 | 任务数；无分母 | 新 P20 任务投影 | 读权限/分派范围；60s | PAUSED、EXPIRED 不混入 | 生命周期与有效期语义 |
| 目标数据包 | 采集任务当前生效 revision 的目标数量 | 单任务；点时 | 目标值；无分母 | 新 P20 任务 revision | 任务读权限；变更即时 | 未设置显示“未设置” | 目标能否修改、按何种数据单位 |
| PICO/机器人 SAVED | 机器人返回 SAVED 后，设备侧去重累计并成功同步到云端的记录数；必须保留来源/as-of | 单任务、分派设备集合；截至时间 | 去重 SAVED 记录；无分母 | 尚缺设备同步/接收事实 | 仅授权任务；近实时目标待定 | 离线、未同步、重复回执不重复计 | 同步协议、去重键、是否覆盖非 PICO 采集 |
| 云端 Received | 已原子提交且通过接收完整性门槛的 distinct `data_package_id` | 单任务或当前 scope；`received_at` 在窗口内/截至点需明确 | 唯一包数；无分母 | Ingest 扩展事实；现有 upload session/manifest 仅部分可用 | upload/任务读权限；60s | 接收中、失败、重复上传不重复计 | “Received”精确门槛及 rollout 映射 |
| SAVED↔Received 待核对 | 只对具备稳定映射、相同任务与截止点的 SAVED 集合减 Received 集合；显示集合差，不显示简单总数相减 | 单任务；同一 reconciliation snapshot | `SAVED IDs \ Received IDs`；分母可选已同步 SAVED | 新 reconciliation 投影 | 限任务运营者；新鲜度与延迟阈值 | 未同步/离线/映射缺失单列未知 | 是否作为 P01 主告警、允许延迟 |
| 上传失败 | 与任务关联、未恢复且达到失败定义的 upload session/data package 数 | 当前 scope/任务；点时与时间窗需分开 | 失败对象数；分母不默认显示 | upload sessions + workflow | upload.read；60s | 可重试、不可重试、已恢复 | session 还是 package 计数，terminal 定义 |
| QC Pass | 最新有效 QC run 为 PASS 的 Received 数据包/rollout 数 | 当前 scope；按 QC 完成时或 cohort 截止点 | PASS 数；分母为同 cohort 已出 QC 结论数（仅展示率时） | quality 表/报告 | QC 读权限；60s | 重跑时只算最新有效 run | 稳定 QC subject identity、重跑规则 |
| QC Risk | 最新有效 QC run 为 RISK，默认隔离且不进入训练 Manifest | 同上 | RISK 数；同上 | quality 表/报告 | QC 读权限；60s | 人工质检结论另列，不提供直接“强制 Pass” | 人工质检状态机、优先级 |
| QC Reject | 最新有效 QC run 为 REJECT，Raw 保留且不可进入 Lance | 同上 | REJECT 数；同上 | quality 表/报告 | QC 读权限；60s | 可发起重采；原始对象不删除 | 重采关系与关闭条件 |
| 等待 30 Hz / Lance | QC PASS 且尚未开始/完成对齐与 Lance 写入的 subject 数 | 当前 scope；点时 | wait / processing / failed / ready 分列 | quality + workflow + lance catalog | Lance/处理读取；60–300s | 转换失败独立可操作 | 作业状态、完成门槛、SLO |
| 标注与清洗工作量 | 按真实任务/草稿状态分别统计待领取、进行中、待审、需修改 | 当前 scope；点时 | 每状态件数；不合成转化率 | annotation 真实部分 + cleaning 待建事实 | 本人/全局由能力决定；60s | 无模型的类别显示未开放 | cleaning 正式模型与状态 |
| 待审核 | 已提交且需要 reviewer 动作的 annotation/cleaning/version revision | 当前 scope；点时 | 待审 revision 数；无分母 | annotation review + 待建 cleaning/version | review 能力；60s | 不得把审核通过当发布 | 审核来源目录、自审规则 |
| 待发布/已发布 | 审核已通过但未冻结的版本；已发布是窗口内成功冻结的不可变版本 | 当前 scope；点时/时间窗分开 | 待发布版本数 / 发布事件数 | publishing facts | publish/read 分离；300s | 发布失败/回滚另列 | 发布 readiness 和时间口径 |
| 最近活动 | 有权限对象的稳定业务事件流，不是上传吞吐 bucket | 当前 scope；事件时间窗 | 事件条目；无分母 | audit/outbox/领域事件（待设计） | 对每个对象再次 scope 过滤；60s | 事件源部分失败可局部提示 | 事件 catalog、保留期、延迟 |

统一口径要求：

- V1 推荐只支持单 project + 单 region，不跨区域聚合；DB 存 UTC，按项目 IANA timezone 展示和切桶。
- 时间窗推荐默认 24h，常用 7d/30d，`from` 含、`to` 不含；点时快照和窗口内新增必须在标题上区分。
- 各域响应应有 `as_of`/snapshot 身份和 per-section status；P01 同次刷新尽量共享一致读时点，pending 独立游标。
- 率必须同时展示分子/分母；没有批准 denominator 时只显示计数，不画百分比、漏斗或 gauge。
- 数据范围受限时明确“仅我的分派/仅授权对象”，不能让受限数看起来像项目总数。

## 8. 效果图 / Mockup 清单与验收

效果图数字必须标“示意数据”；聚合未开放图不得出现模拟业务数字。至少制作以下 9 张：

| 编号 | 画布 | 场景 | 必须验证 |
|---|---|---|---|
| M01 | P01 1440×900 | 内部运营者，正常/全读取 | 非六等权 KPI、信号轨道、待办优先、所有主链阶段、一个主动作 |
| M02 | P01 1280×800 | 采集人员，范围受限/只读混合 | 同页不换 IA；“仅我的任务”；无整页横滚；无权模块诚实降级 |
| M03 | P01 1440×900 | Dashboard 聚合能力未开放 | 0 个 dashboard 请求的诚实不可用态；无 fixture/占位数字 |
| M04 | P01 1440×900 | 部分失败 + 陈旧数据 | 接收正常、QC 失败、旧值保留、as-of/request id/局部重试 |
| M05 | P01 1440×900 | QC Risk/Reject 高峰 | Risk/Reject 分离；Raw 只读诊断深链；无人工覆盖通过动作 |
| M06 | P20 1440×900 | 任务列表 | 搜索/筛选/排序 URL 状态、任务码说明、多维进度、分派风险 |
| M07 | P20 1440×900 | 新建/编辑任务 | 单页分段、校验摘要、任务码生成说明、有效期时区、并发/未保存状态 |
| M08 | P20 1440×900 | 分派弹层/页 | 人/PICO/机器人三类、不可选原因、移除影响、只读降级 |
| M09 | P20 1440×900 | 任务详情 + 审计 | SAVED/Received/QC 分开、差异可信度、数据包深链、状态历史与审计脱敏 |

每张效果图验收：

- 读取 Master 的颜色、字体、圆角、间距、Ant Design/Lucide 和低动效规则，不创建局部第二套设计系统。
- 1440 为主评审，1280 不出现整页横向滚动；不以缩小到 9–10px 解决密度。
- 加载、空、错误、部分失败、无权限、只读、未开放、未知枚举至少在跨图集中覆盖。
- 可交互元素有 hover/focus/disabled/pending；复制、保存、暂停、移除分派有明确反馈。
- 不使用装饰性渐变卡片墙、六等分 KPI、无分母 gauge、非真实 funnel 或多处重复“信号轨道”。
- 自我批评检查：若去掉业务文案后像任意 SaaS dashboard，则需强化任务码、数据包身份、QC 门禁和阶段血缘，而不是增加装饰。

## 9. Web 设计规范审计发现

本次按 2026-08-17 获取的最新 Web Interface Guidelines 审核当前 P01。以下为应在后续实现计划中修复的 `file:line` 发现：

| 严重度 | 发现 | 证据与修复方向 |
|---|---|---|
| 高 | 待办直接显示内部 `wireType`，不是用户可理解文案 | `frontend/src/pages/p01-dashboard/components/DashboardPendingList.tsx:11-16`；建立已批准的类型标签映射，未知值显式降级 |
| 高 | 状态与优先级直接显示内部值 | `frontend/src/pages/p01-dashboard/components/DashboardPendingList.tsx:30-49`；提供本地化标签、语义色与可读说明，颜色不作为唯一信号 |
| 中 | 时间值直接输出 ISO 字符串 | `frontend/src/pages/p01-dashboard/components/DashboardPendingList.tsx:52-56`；使用项目时区的 `Intl.DateTimeFormat`，保留 `<time dateTime>` |
| 中 | 抽屉分页标签直接显示快照原值 | `frontend/src/pages/p01-dashboard/components/DashboardPendingDrawer.tsx:41-46`；格式化时间/快照摘要，完整值按需展开 |
| 高 | “可点击”目标实际渲染为“暂不可用”，没有可执行导航 | `frontend/src/pages/p01-dashboard/components/DashboardPendingList.tsx:59-67`；有稳定 target 才使用 Link/Button，无 target 显示阻塞原因且不可点击 |
| 中 | 覆盖表展示原始 ID 和手写百分比 | `frontend/src/pages/p01-dashboard/components/DashboardCoverageTable.tsx:8-27`；业务名称为主、ID 次级；百分比用 `Intl.NumberFormat`，先批准 denominator |
| 高 | 图标题固定为 24h，与 URL 的 7d/30d 选择冲突 | `frontend/src/features/dashboard/dashboard-charts.tsx:264-287`；标题由已解析时间窗生成，避免错误内容承诺 |
| 中 | 多处图表文字仅 9–10px，1280 下可读性风险 | `frontend/src/features/dashboard/dashboard-charts.tsx:142-170,227-242`；优先降信息密度/换行，不继续缩小字号 |

后续效果图/实现还必须遵守：

- 图标按钮有可访问名称；表单使用可见 `<label>`，错误靠近字段且首个错误可聚焦。
- 使用语义 button/link，不用可点击 div；导航/筛选/Tab 的可分享状态写入 URL。
- 所有动画尊重 `prefers-reduced-motion`；不为装饰添加持续运动。
- 图表提供可读标题、精确数值和文本/表格替代；不得只靠颜色、面积或 hover tooltip。
- 50 条以上表格使用游标分页；是否虚拟化由测量决定，虚拟化后仍需可访问行语义和键盘行为。

## 10. React 19 + Vite 实现约束

只应用 React/浏览器/Vite 相关规则，不套用 Next.js Server Component、Server Action、`next/dynamic` 等规则。

| 约束 | 当前证据/目标 |
|---|---|
| 保留路由懒加载 | P01 已在 `routes.tsx:4-9` 使用 lazy；P20 和 Raw 重型工作台也应按路由拆包 |
| 独立请求并发 | P01 的 activity/snapshot/pending 本身由独立 React Query 并发启动，应保留；刷新用 `Promise.all`，不能串行等待 |
| 并行加载 ECharts 模块 | 当前 `dashboard-charts.tsx:112-116` 串行动态 import；改为并行 Promise，不制造 waterfall |
| 按域拆分图表和 Effect | 当前一个 Effect 依赖 activity + snapshot 并创建三图（`:101-260`）；拆成稳定组件，让 activity 刷新不重建存储图 |
| 窄化依赖 | Effect/selector 依赖稳定 primitive 或必要字段；派生状态直接 render 计算，不用同步 Effect |
| 不在组件内定义组件类型 | 避免每次 render 产生新类型、丢焦点/滚动/内部状态 |
| 安全处理 BigInt | 不把未缩放容量/计数直接 `Number()`；显示与图表共用安全归一化结果 |
| 保留精确替代 | ECharts canvas 之外保留 `<details>`/数据表；数值用一致的 `Intl` 格式化 |
| 大列表先测量 | P20 列表默认游标分页；真实行数/列复杂度导致瓶颈时再虚拟化，或对离屏卡片使用 `content-visibility: auto` 并设 intrinsic size |
| 局部刷新与缓存 | Query key 必须含 scope、range、timezone、permission fingerprint/等效权限版本；不同域独立 stale/error，不整页抖动 |
| 高频进度隔离 | 若未来有 SSE/轮询，订阅集中到 store/query cache，行组件只订阅所需字段；避免每个数据包各建连接 |
| 用 Profiler 验证 | 以 P01 部分刷新、P20 50/500 行、抽屉开合、信号轨道更新为基准；没有测量不宣称优化完成 |

## 11. 产品决策：最优先 Top 5

每个问题都给出推荐、替代和阻塞任务。未决定前，效果图可以用明确注释表达，但不得进入业务编码。

| 优先级 | 需要用户确认的问题 | 推荐选择 | 可选方案 | 不决定会阻塞 |
|---:|---|---|---|---|
| 1 | P01/P20 的默认 scope、时间窗、时区和跨区域规则是什么？ | V1 单 project + 单 region；DB UTC；项目 IANA timezone；默认 24h，可选 7d/30d；`from` 含、`to` 不含；不跨区域聚合 | 固定 Asia/Shanghai；或用户本地时区；或组织跨区域汇总 | P01 所有指标、P20 有效期、URL codec、缓存键、权限与视觉文案 |
| 2 | P20 稳定任务身份、8 位 task code 的生成时机、失效与复用规则是什么？ | 独立不可变 `collection_task_id`；首次保存生成 task code；启用后 code 不变；失效仅停止新采集，历史继续可追溯；V1 不复用到另一任务 | 启用时才生成；结束后回收复用；按 revision 换码 | P20 模型/CRUD、PICO 在线确认、深链、审计、幂等与冲突 UX |
| 3 | P20 生命周期、到期、达到目标、暂停/关闭的准确规则是什么？ | DRAFT→SCHEDULED→ACTIVE↔PAUSED→CLOSED/CANCELLED；到期是派生风险，需人工关闭；达到目标只提示，不自动关闭 | 到期/达标自动关闭；直接复用底层 collection_job 枚举 | P20 列表/详情/动作权限、通知、审计、服务端状态机和验收测试 |
| 4 | SAVED、Received、QC 的事实源、同步延迟和核对规则是什么？ | SAVED 与 Received 分开；Received 按 distinct `data_package_id`；只对有稳定映射且同一 snapshot 的集合算差异；未知单列；QC P/R/X 分开 | 简单用 `SAVED - Received`；只展示 Received；以 upload session 数替代数据包 | P01 信号轨道、P20 进度、告警、reconciliation 投影、数据模型与测试 |
| 5 | “我的待办”纳入哪些来源，如何定优先级/SLA/去重/关闭与深链？ | V1 只纳入有真实模型和稳定 target 的来源；以 severity→截止时间→更新时间排序；source type + id 去重；每类独立关闭条件 | 直接采用 Mock 六枚举；或所有 workflow failure 全量混排 | P01 核心模块、pending API、权限 union、P03/P04/Raw/P20 深链、通知和 E2E |

### 11.1 次级但仍需确认的决策

| 问题 | 推荐选择 | 可选方案 | 不决定会阻塞 |
|---|---|---|---|
| P20 任务定义启用后如何修改？ | 不可变 revision + 生效时间；已产生数据继续引用原 revision | 原地覆盖；每次修改复制新任务 | 编辑 UX、Manifest 关联、审计、历史解释 |
| 人员/PICO/机器人分派是否必须一一绑定？ | 三个集合独立分派；数据包以实际 manifest 身份记录组合 | 预先固定 person×PICO×robot 三元组 | 分派模型、冲突校验、列表摘要、隐私边界 |
| task code 在线存在性/有效性由谁校验？ | PICO 离线只验 Verhoeff；上传/联网时由云端以 scope+稳定任务身份确认，失败隔离并允许人工归类修正 | PICO 强制联网查询；格式通过即视为存在 | 离线采集、异常归类、上传校验、Raw 保留规则 |
| QC RISK 的人工质检结果如何影响后续？ | 人工结论可请求重跑/重采/维持隔离；进入 Lance 必须有新的可审计 PASS 结果，不能直接覆盖 | 人工按钮把 RISK 改 PASS；所有 RISK 永久拒绝 | Raw 诊断动作、QC 状态机、Lance 门禁、审计 |
| 数据新鲜度和局部失败阈值？ | 每域返回 as-of；默认超过 5 分钟标陈旧（设备离线源另设阈值）；保留旧值并局部报错 | 任一域失败整页失败；无限期静默缓存 | P01/P20 状态、SLO、缓存、告警与视觉回归 |

## 12. 未核验项、交接与停止条件

### 12.1 已核验

- 已读取四份方向文档、改版计划、主设计系统、P01 开发计划、真实 API 覆盖审计和产品决策清单。
- 已静态检查 P01 route/page/query/client/schema/adapter/fixtures/handlers/scenario、能力解析、MSW 启动条件、主要组件与现有测试。
- 已静态检查 P03/P04 route/query/builder/model，确认现有深链只稳定支持 uploadId/tab/objectId/returnTo，尚无采集任务/数据包关联。
- 已静态检查 Ingest migration/model/OpenAPI，确认存在底层 collection job/manifest 事实，但没有 P20 CRUD/list/assignment/task-code 合同。
- 已按主设计系统做 `frontend-design` 自检；`ui-ux-pro-max` 仅做非持久化检索，没有覆盖 Master；已应用最新 Web Interface Guidelines 与 React/Vite 相关性能规则。

### 12.2 未核验 / 后续必须完成

- 未运行新的浏览器、Playwright、视觉回归或网络抓包；Browser/off 运行结果只引用既有审计，本次结论以静态证据为主。
- 未确认生产环境真实 capability payload、项目 timezone 来源或跨区域政策。
- 未找到并验证 P20、Raw 诊断工作台的最终 route、query codec、OpenAPI、数据表或权限字符串，因为它们当前尚未定义。
- 未确认 `collection_task_id`、`data_package_id`、`rollout_id`、`collection_job_id` 的正式映射关系。
- 未确认 SAVED 同步协议、Received 门槛、QC 重跑规则、任务码复用策略、P20 状态机与分派约束。
- 未产出视觉图像；本文是效果图输入规格，需由效果图终端/设计评审按 M01–M09 制作和确认。

### 12.3 后续顺序

```text
用户确认 Top 5 产品问题
→ 按 M01–M09 产出并评审效果图
→ 冻结字段、状态、口径、深链和权限语义
→ 由合同 Owner 设计正式 OpenAPI/模型/权限
→ 重写真实 API 实施计划与测试矩阵
→ 才允许进入业务编码
```

本文到此停止，不包含任何业务实现。

## 13. 文档自验命令

```bash
test -f plan/frontend-ux-parallel/T03-P01-P20-SPEC.md
! rg -n '[[:blank:]]+$' plan/frontend-ux-parallel/T03-P01-P20-SPEC.md
awk '/^```/{n++} END{exit n%2}' plan/frontend-ux-parallel/T03-P01-P20-SPEC.md
rg -n '^## ' plan/frontend-ux-parallel/T03-P01-P20-SPEC.md
rg -n 'P01|P20|task_code|data_package_id|SAVED|Received|PASS|RISK|REJECT|30 Hz|Lance|Raw' plan/frontend-ux-parallel/T03-P01-P20-SPEC.md
git status --short -- plan/frontend-ux-parallel/T03-P01-P20-SPEC.md
```
