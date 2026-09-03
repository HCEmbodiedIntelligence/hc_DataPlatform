# T04｜P02–P04 数据来源、上传记录与数据包摄取/自动质检 UX 规格

> 交付类型：效果图与后续实现规格，不是生产合同，也不是实现结果
>
> 责任域：T04（P02、P03、P04）
>
> 唯一修改文件：`plan/frontend-ux-parallel/T04-P02-P04-SPEC.md`
>
> 基线日期：2026-08-17，Asia/Shanghai
>
> 结论强度：除标为“已有”的仓库事实外，本文所有页面字段、状态、动作和投影都只是候选合同；不得据此创建数据库关系或宣称真实 API 已接入。

## 0. 先给结论

1. P02 是“上传来源/连接器目录”，只管理怎样把数据送入平台；机器人模型资产归 P14，机器人实例与组件归 P15。P02 仅显示稳定引用与深链，不复制资产注册表、序列号档案或组件树。
2. P03 必须从“上传任务”改名为“上传记录”。一行代表一次上传执行/续传事实，不代表 P20 的采集任务。默认主标识是 `data_package_id`，上传执行 ID 降为技术值。
3. P04 的页面对象是“一个数据包的一次摄取详情”，须把上传、传输完整性、Manifest 最终提交、MCAP 结构/Topic、自动 QC、Lance 准入分层显示。`uploadId/session_id/rollout_id` 不得冒充 `data_package_id`。
4. `data_package_id` 的产品语义来自方向恢复稿：机器人接受开始请求时生成；相同 `recording_request_id` 返回原数据包；云端按 `data_package_id` 幂等。当前真实 ingest 合同仍以 `rollout_id/session_id` 为核心，映射关系尚不存在或无法确认。
5. 相同 SHA 的不同 `data_package_id` 必须保留为两条独立数据包，只能提示“内容摘要相同”，不能自动合并。
6. Raw MCAP 不可变。MCAP 验证或自动 QC 的 RISK/REJECT 均停留 Raw；界面不提供“人工改为通过”“释放后进入 Lance”。允许的动作只能是修复技术原因后由服务端重新验证，新的自动结果仍由规则产生。
7. 当前前端 P02–P04 是 Mock-backed draft，真实 ingest/verification/quality/Worker 只提供底层能力；同名 `upload-sessions` 不代表页面已经接入真实 API。

## 1. 范围、方法与证据强度

### 1.1 已使用的四个 Skill

- `frontend-design`：约束效果图必须有明确视觉主张、真实中文文案、可操作状态与非模板化信息层级。
- `ui-ux-pro-max`：运行了 design-system、UX 与 React 栈检索；结果只作为检查清单，没有 `--persist`，没有重写或重新持久化设计系统。自动候选的通用绿色 Dashboard/Fira 方向未采用，继续服从主设计系统“机器人遥测账本”。
- `web-design-guidelines`：审查前先从官方仓库重新拉取最新 `command.md`，再按 file:line 检查当前 P02–P04。
- `react-best-practices`：按本仓库 React 19 + Vite 使用；不套用 Next.js Server Component、Server Action 或 `next/dynamic`。

视觉基线完全继承 `design-system/hc-data-platform/MASTER.md:16-44,46-88,90-105,106-127,145-156,168-185`：紫蓝语义色、系统中文字体、Ant Design + Lucide、高密度低动效、技术值渐进展开，以及只在单个数据包链路使用“信号轨道”。本文不另建第二套设计系统。

### 1.2 证据状态定义

| 标记 | 含义 |
|---|---|
| 已有 | 当前真实后端模型/Router/OpenAPI/Worker 有直接证据；仍不等于页面已接入 |
| 草案 | 只存在于前端 wire/schema/Mock/页面或开发计划 |
| 不存在 | 在本轮检查的前后端、OpenAPI、Worker 中未找到对应字段或页面合同 |
| 无法确认 | 方向/产品要求存在，但映射、所有权、数据库关系或最终合同未批准 |

### 1.3 方向文件访问说明

`plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:32-37` 指定的四个无时间戳正式文件，在仓库和可访问用户目录中均未找到。可访问的是 Typora `draftsRecover` 同名恢复稿；本轮读取了下列最新恢复快照中的上传、幂等、Manifest、MCAP、校验/QC 章节，不能把它们当正式签发版本：

| 方向文件 | 实际读取快照 | 可用结论与限制 |
|---|---|---|
| PICO 跨本体遥操作与机器人端录制方案 | `/mnt/c/Users/28384/AppData/Roaming/Typora/draftsRecover/2026-8-14 PICO 跨本体遥操作与机器人端录制方案 094851.md:223-445` | 五标识见 `:307-315`；机器人生成/持久化 request→package 映射见 `:317-323`；不同数据包不合并见 `:339-341,430-434`；Manifest 与幂等/原子提交见 `:391-442` |
| VR 全身遥操作初版方案（固定颈部） | `/mnt/c/Users/28384/AppData/Roaming/Typora/draftsRecover/2026-8-14 VR 全身遥操作初版方案（固定颈部） 094733.md:1-90` | 已完整读取该恢复稿；未找到上传、`data_package_id`、幂等、Manifest、MCAP 或云端校验要求，不从名称相似处推断 |
| 统一机械臂规控、遥操作与数据采集平台建设及调试上线方案 | `/mnt/c/Users/28384/AppData/Roaming/Typora/draftsRecover/2026-8-17 统一机械臂规控、遥操作与数据采集平台建设及调试上线方案 154435.md:1-140,180-220,240-369` | 本地 SSD、断点续传、硬盘导入和哈希见 `:94-99`；机器人采 MCAP/上传链路见 `:240-253`；QC 维度与 Manifest/报告见 `:337-369` |
| 数据平台 评测 | `/mnt/c/Users/28384/AppData/Roaming/Typora/draftsRecover/2026-8-17 数据平台 评测 104459.md:1-260` | 三种上传见 `:46-119`；分片/CRC64/SHA/Manifest 最终提交见 `:142-167`；三层校验与 QC 见 `:171-248`。其中 `:233-241` 的“等待人工质检”不能覆盖本轮已确认的“不得人工放行进入 Lance”约束 |

冲突优先级：用户本轮明确约束与改版计划 `:39-50` 高于恢复稿旧表述；未知关系保持“无法确认”。

## 2. 当前实现、Mock、API 与测试证据表

### 2.1 P02–P04 前端证据

| 页 | 证据 | 当前事实 | 判断 |
|---|---|---|---|
| P02 | `frontend/src/app/router/index.tsx`；`frontend/src/features/ingest/routing.ts:34-39` | 路由为 `/ingest/sources` | 已有前端路由 |
| P02 | `frontend/src/pages/p02-data-sources/query-codec.ts:3-15,27-55,70-100` | URL 状态含搜索、四类状态、排序、游标、每页、选中来源；source type 仅 `ROBOT/EDGE_AGENT/OSS_IMPORT` | 草案；缺工作站/离线导入的清晰业务分类 |
| P02 | `frontend/src/pages/p02-data-sources/page.tsx:449-520` | 标题为“数据源”，描述为机器人/边缘代理/OSS；主区有总数、在线、验证字节、异常 | Mock 页面；“异常需要人工处理”不能被解释为人工改 QC |
| P02 | `frontend/src/pages/p02-data-sources/page.tsx:523-629` | 7 个筛选同时平铺 | 与“常用 3–5 项、更多筛选”基线不符 |
| P02 | `frontend/src/pages/p02-data-sources/page.tsx:651-731` | 抽屉显示 raw source type、绑定对象、配置/凭据、raw ISO 时间；机器人绑定只是文字 | 缺 P15 稳定跳转；未复制资产表，边界尚可 |
| P02 | `frontend/src/pages/p02-data-sources/components/DataSourceTable.tsx:28-112` | 列表列为来源、连接器、连接、凭据、最近上传、查看；没有固定列声明 | 页面内容偏连接器，符合改版起点；需业务化标签 |
| P02 | `frontend/src/features/ingest/api/client.ts:54-69,102-132` | 前端请求 `data-sources/page`、详情与写操作 | 仅前端草案；真实 `backend/openapi/ingest.yaml` 无 data-source 路径 |
| P02 | `frontend/src/mocks/handlers/ingest.handlers.ts:102-147` | MSW 提供列表、详情、创建、编辑、凭据、连接测试、启停 | Mock，不得称真实连接器 API |
| P02 | `frontend/src/mocks/handlers/ingest.handlers.test.ts:1-60` | 唯一 ingest 测试只测 P03 列表状态筛选 | 未找到 P02 页面/组件/契约自动化测试 |
| P03 | `frontend/src/app/router/index.tsx`；`frontend/src/features/ingest/routing.ts:40-45` | 路由 `/ingest/uploads` | 已有前端路由 |
| P03 | `frontend/src/pages/p03-upload-jobs/page.tsx:320-339` | 页面叫“上传任务”，主动作“新建上传” | 与目标“上传记录，不是采集任务”冲突 |
| P03 | `frontend/src/pages/p03-upload-jobs/page.tsx:313-318,340-366` | KPI 由当前游标窗口现场求和，却有“上传中/已完成/流量”外观 | 必须标“当前窗口”，不能冒充全局统计；当前部分文案已标窗口 |
| P03 | `frontend/src/mocks/handlers/ingest.handlers.ts:148-167,207-225` | MSW 有列表、creation-options、create、pause/cancel/retry | Mock 草案 |
| P03 | `frontend/src/mocks/handlers/ingest.handlers.test.ts:34-59` | 只覆盖 uploading/failed 两组生命周期筛选 | 未覆盖上传进度、断网、分片重试、幂等、Manifest 或恢复 |
| P04 | `frontend/src/app/router/index.tsx`；`frontend/src/features/ingest/routing.ts:48-72` | 路由 `/ingest/uploads/:uploadId`，稳定参数仅 `uploadId` | 页面入口以执行 ID 定位；不得将其显示成 data package ID |
| P04 | `frontend/src/features/ingest/validation-pipeline.ts:3-30` | 前端阶段只有 Manifest schema、对象大小/SHA、适配器、数据集语义、原子可用提交 | 草案缺 CRC64、MCAP 结构/索引/Topic 和真实 QC 报告 |
| P04 | `frontend/src/features/ingest/api/queries.ts:146-161,209-242` | bootstrap 活跃时 5 秒轮询；事件无条件 5 秒轮询 | 详情页多查询叠加风险；事件只应在需要时或按状态更新 |
| P04 | `frontend/src/mocks/handlers/ingest.handlers.ts:168-225` | MSW 提供 bootstrap、objects、verification-runs、events、retry | Mock，不是实际聚合详情 API |
| P04 | `frontend/src/mocks/handlers/ingest.handlers.test.ts:1-60` | 无 P04 测试 | 未找到标识、Manifest、MCAP、QC、审计、Raw 跳转或错误态测试 |
| 共用 | `frontend/src/shared/ui/data/DataTable.tsx:104-151,180-197` | AntD Table 已使用 `scroll={{x:'max-content'}}`，因此横向滚动在表格自身；转换层未传固定列属性 | 自滚已有；固定首列/动作列尚无公共能力，需与共享组件 owner 协调 |

### 2.2 真实后端/OpenAPI/Worker 只读证据

| 能力 | 真实证据 | 能说什么 | 不能说什么 |
|---|---|---|---|
| 上传会话与幂等 | `backend/src/hc_data_platform/ingest/service.py:56-122`；`backend/openapi/ingest.yaml:1-42` | `Idempotency-Key` 已有；相同 `rollout_id` + SHA 返回已有 session，不同 SHA 冲突 | 不能改写成按 `data_package_id` 已幂等；真实模型没有该字段 |
| Manifest/现有身份 | `backend/src/hc_data_platform/ingest/models.py:70-86,101-160`；`backend/openapi/ingest.yaml:221-317` | 已有 `task_id/collection_job_id/rollout_id/robot_id/session_id`、SHA、CRC64、Manifest fingerprint | 不能自行定义它们与五标识的数据库关系 |
| 分片/暂停/续传/取消 | `backend/src/hc_data_platform/ingest/router.py:124-214`；`backend/openapi/ingest.yaml:63-190,273-339` | 有 parts list、renew、complete、pause、resume、cancel | 没有 P03 页面列表投影、creation-options 或 P04 聚合 bootstrap |
| CRC64/SHA/最终提交 | `backend/src/hc_data_platform/ingest/service.py:341-425`；`backend/openapi/ingest.yaml:191-213` | 完成对象后流式重算大小、CRC64、SHA，再写不可变 Manifest 最终提交标记 | 跨对象存储和数据库的严格原子事务语义无法由这些代码确认；需技术设计 |
| 不可覆盖与提交冲突 | `backend/src/hc_data_platform/ingest/adapters.py:33-35,82-108,175-178,216-245`；`service.py:475-507` | Raw/Manifest 使用 forbid overwrite/If-None-Match；已有冲突对账 | 不能由 SHA 跨 package 合并 |
| 离线导入 | `backend/src/hc_data_platform/ingest/cli.py:67-107,149-224,266-301` | CLI 校验 `.mcap` + Manifest、CRC64/SHA，并复用分片/续传/最终提交协议 | 不是 P02/P03 工作站 UI 已接入证据 |
| MCAP 结构/Topic | `backend/src/hc_data_platform/verification/README.md:1-11,21-40`；`models.py:23-45,58-114`；`openapi/verification.yaml:1-16,35-170` | 已有 Header/Footer/Summary/索引/CRC32/Schema/Channel/Topic/解码验证与不可变报告 | 该模块明确不做传输 CRC64/SHA 或自动 QC（README `:3-11`） |
| 自动 QC | `backend/src/hc_data_platform/quality/README.md:1-26`；`models.py:54-82,324-400,532-654`；`openapi/quality.yaml:35-49,78-103,305-383` | 已有 PASS/RISK/REJECT、时序/图像/关节/动作/点云/模态等 finding 与不可变报告 | 当前报告仍以 `rollout_id/source_sha256` 关联；没有 `data_package_id` 页面投影 |
| QC 后 Lance 门禁 | `backend/src/hc_data_platform/workflow/temporal_workflows.py:215-318`；`workflows.py:27-61` | verification REJECT、QC RISK/REJECT 均 `raw_preserved=true`、`training_eligible=false`；仅 PASS 执行 alignment/Lance commit | 不存在人工改 QC PASS 的 Worker 动作 |
| Worker 注册 | `backend/src/hc_data_platform/workflow/names.py:3-19`；`worker.py:37-71`；`activities.py:271-333,336-386` | verify→quality→alignment→commit 的活动真实注册 | 前端草案 `verification-runs/events` 不是因此自动成立 |

仓库既有审计已给出相同结论：`plan/P01-P19-REAL-API-COVERAGE-AUDIT.md:38-40` 将 P02–P04 均评为 D，P03 明确“同名资源不兼容”；正式页面投影工作仍为未开始的 `plan/P01-P19-REAL-API-IMPLEMENTATION-PLAN.md:91-92`。

## 3. P02｜来源/连接器

### 3.1 页面任务与非目标

用户来到 P02 只回答三件事：数据从哪里来、连接是否可用、下一次上传将走哪种方式。

候选来源分类：

| 来源/方式 | P02 管理什么 | P02 不管理什么 | 进入上传记录 |
|---|---|---|---|
| 机器人自动上传 | 机器人引用、上传策略引用、最近握手/最后上传、连接状态；支持单次采集结束后异步上传与任务结束后批量上传 | 机器人序列号、型号版本、组件树、标定/Schema 资产 | 链到 P03，预置 `sourceId`；每次执行仍是一条上传记录 |
| 工作站/Web 上传 | 导入工作站/浏览器来源配置、允许格式与策略、最近操作者、连接状态 | 本地文件资产库、永久保存浏览器授权、采集任务管理 | “发起导入”进入 P03 创建流程；选文件后必须显式确认 |
| 离线硬盘导入 | 导入入口、工作站引用、介质批次的本次导入摘要与校验前置状态 | 物理硬盘资产注册表、把一个 Task 打成单一大包 | 逐个 `data_package_id` 产生记录；一个坏包不阻塞其他包 |

“来源类型”和“上传触发方式”必须是两个维度：机器人来源可以有 `单次完成后异步` 与 `任务完成后批量` 两种触发方式；离线硬盘是导入方式，不应被伪装成机器人连接器。具体枚举由产品/技术合同确认。

### 3.2 与 P14/P15 的边界与跳转

| 页面 | 资产所有权 | P02 可显示 | 稳定跳转候选 |
|---|---|---|---|
| P02 来源/连接器 | 上传端点、代理、工作站、导入方式、凭据引用、连接策略 | `source_id`、名称、方式、状态、`robot_id`/工作站引用、最近上传 | 机器人绑定存在时：`robots.build({robotId})` → `/settings/robots?robotId=…`，route builder 已存在于 `frontend/src/features/robots/routing.ts:1-12` |
| P15 机器人/实例/组件 | 机器人实例、序列号、生命周期、连接事实、模型绑定、组件拓扑 | P02 只显示一行“机器人：星舟 017 / robot_astro_017” | P15 返回 P02 时携带稳定 `sourceId`；不能靠显示名/IP 反查 |
| P14 机器人模型资产 | URDF/Mesh、不可变模型版本、关节映射、发布/绑定候选 | P02 不展示资产清单；只有响应明确给出 `robotModelVersionId` 时才可给“查看模型版本”链接 | `robotModels.build({modelId,versionId})` 已存在于 `frontend/src/features/robot-models/routing.ts:1-12`；不得从 `robot_id` 猜版本 |

P14/P15 自身仍是 Mock-backed draft，见其开发计划 `plan/frontend/frontend-page-14-robot-model-assets-development-plan.md:23-30`、`frontend-page-15-robots-components-development-plan.md:23-30`。深链是导航候选，不是数据关系证明。

### 3.3 信息架构与交互

- 页面标题：`上传来源`；说明：`管理机器人自动上传、工作站导入和离线硬盘导入的连接方式。机器人与组件资料在资产管理中维护。`
- 唯一主动作：`添加来源`。未授权时隐藏，不以 disabled 假装可用。
- 顶部摘要：来源总数、可用、需检查、最近 24 小时有上传。禁止在 P02 放全局上传字节漏斗或 QC 总量。
- 常用筛选：搜索、来源方式、连接状态、`更多筛选`。
- 高级筛选展开：启用状态、凭据/授权状态、上传触发方式、绑定机器人/工作站、最近上传时间、排序、每页。
- 表格固定左列“来源”，固定右列“查看”；自身横向滚动。中间列：来源方式、触发方式、绑定对象、连接状态、最后上传、需处理原因。
- 行主动作只有 `查看`。`测试连接`、`轮换凭据`、`停用` 放详情抽屉，且完全由 `allowedActions/blockedReasons` 驱动。
- 详情抽屉的“技术详情”折叠区才展示 source ID、策略版本、凭据版本、endpoint（脱敏）、requestId。默认展示业务名与相对时间。
- 机器人行显示 `查看机器人实例`；没有明确模型版本字段时不显示 P14 链接。
- 空态文案：`还没有上传来源。添加机器人上传、工作站导入或离线硬盘导入来源后，上传记录会出现在“上传记录”。`

## 4. P03｜上传记录列表

### 4.1 页面身份

- 标题：`上传记录`
- 说明：`查看每个数据包的上传、续传、校验与提交结果。采集任务定义、任务码和人员/机器人分派在“采集任务”中管理。`
- 唯一主动作：`发起导入`，仅对工作站/Web/离线导入能力开放；机器人自动上传不从页面伪造一次新采集。
- 次要跳转：`管理来源` → P02；任务名称/任务码 → P20（T03 owner）。
- 一行的身份顺序：`data_package_id`（业务唯一）→ 上传记录 ID（技术）→ P20 任务引用（归类）。禁止把 task code 或 SHA 当唯一数据标识。

### 4.2 列表列与行摘要

| 列 | 默认内容 | 展开/说明 |
|---|---|---|
| 数据包（固定左） | `dp_01K2X9…P7`；次行 `记录 up_8f31…` | 完整五标识进技术详情；复制有可访问反馈 |
| 来源 | `机器人自动上传 · 星舟 017` / `工作站导入 · 上海导入站 02` / `离线硬盘 · 批次 DISK-20260817-04` | 连接器链接 P02；机器人链接 P15 |
| 采集任务 | `透明物体抓取 / 48273164` | 明示 `任务码用于归类，不是数据唯一标识`；链接 P20 |
| 分片进度 | `183 / 256 片` + `71.5%`；状态 `等待网络/重试中/已对账` | 展开显示缺失片号、已重试数、下次重试时间；数字等宽 |
| 校验链 | `CRC64 通过 · SHA 待校验 · MCAP 未开始 · Manifest 未提交` | 使用文字+图标，不只用红绿；点击进入 P04 对应锚点 |
| 幂等结果 | `首次接收` / `续传同一数据包` / `数据包已存在` / `相同 SHA，独立数据包` / `ID 内容冲突` | 结果来自服务端候选字段；前端不得根据 SHA 自算合并结论 |
| 恢复 | `自动续传 2/5` / `需重新选择本地文件` / `不可重试` | 只有 server allowedAction 成立才出现动作 |
| 更新时间 | `今天 17:42` | 完整时间在 tooltip/详情，用 `Intl.DateTimeFormat` |
| 主动作（固定右） | `查看详情` | 失败时仍先进入 P04；行内不堆暂停/取消/释放 |

### 4.3 常用/高级筛选

常用筛选（始终可见，最多 5 项）：

1. 搜索：占位 `搜索数据包、上传记录或机器人…`。
2. 状态快捷项：全部、上传中、校验中、已接收、需处理。
3. 来源：机器人自动上传、工作站/Web、离线硬盘。
4. 时间：最近 24 小时、7 天、30 天、自定义。
5. `更多筛选（n）`。

高级筛选：P20 任务/任务码、来源连接器、`robot_id`、`pico_instance_id`、分片状态、失败阶段、CRC64/SHA、MCAP、Manifest、自动 QC、幂等结果、重试次数、创建人、排序、每页。筛选、游标和选中项都进入 URL；改变任一筛选时清空游标，不保存签名 URL、文件句柄或敏感授权。

### 4.4 进度、重试、失败恢复与幂等呈现

- 进度只展示服务端确认字节/分片，不把浏览器已发送字节称为“云端已接收”。两者同时存在时分别命名。
- SSE 连接正常时标 `实时更新`；降级轮询时标 `每 5 秒更新`；断网时标 `离线，显示 17:42 的最后状态`。连接状态不能只藏在开发日志。
- 自动重试显示 `第 2/5 次，将在 28 秒后继续`，并允许查看上次错误；不要制造每片 Toast。
- 本地工作站刷新页面后若失去文件句柄，显示 `需要重新选择同一文件以继续；已上传分片不会重传`，不得承诺浏览器能无条件恢复。
- 重复 `recording_request_id` 映射同一 `data_package_id` 时显示 `续传同一数据包` 或 `数据包已存在`，不是新成功记录。
- 相同 SHA、不同 `data_package_id` 时显示两行，徽标 `内容摘要相同，数据包独立保留`。
- 相同 `data_package_id`、不同内容/Manifest 时显示阻断冲突，禁止覆盖，动作仅 `查看冲突详情`/`复制诊断信息`。
- 批量动作只适用于同一 server action 均允许的当前选择；混合选择必须解释哪些行被排除。采集任务的批量管理不在此页。

## 5. P04｜数据包摄取详情与自动质检

### 5.1 页面骨架

页面头：

- 标题：`数据包摄取详情`
- 主标识：`dp_01K2X9W4Q8C6P7`
- 次要信息：`机器人自动上传 · 星舟 017 · 透明物体抓取`
- 状态句：`自动质检异常，Raw 已保留，未进入 Lance。`
- 唯一主动作按状态变化：通常是 `打开 Raw 诊断`；纯技术失败且 server 允许时为 `重试失败阶段`。绝不出现“改为通过”“释放进入 Lance”。

顶部信号轨道只显示真实顺序：

`接受录制请求 → 上传分片 → CRC64/SHA → Manifest 最终提交 → MCAP/Topic → 自动 QC → 30 Hz 对齐/Lance`

每节点有时间、文字状态和形状；后续未执行显示“被上游阻断”，不是失败。QC RISK/REJECT 后，Lance 节点显示 `未进入（自动质检异常）`。

### 5.2 标识关系

默认只展示业务摘要；“标识与来源”卡必须至少逐项列出：

| 标识 | 页面解释 | 来源标签 | 关系表达限制 |
|---|---|---|---|
| `pico_instance_id` | 产生控制信息的 PICO 安装实例 | 机器人 Manifest/方向候选 | 不画成机器人父节点 |
| `robot_id` | 保存该数据包的机器人 | Manifest/机器人目录引用 | 只按稳定 ID 跳 P15，不按名称/IP |
| `collection_session_id` | 一次连续采集工作段 | 机器人 Manifest 候选 | 不等于 P20 任务，也不等于上传 session |
| `recording_request_id` | 一次开始/停止命令的去重标识 | 机器人请求日志/Manifest 候选 | 相同 request 应返回同一 package；当前数据库映射无法确认 |
| `data_package_id` | 机器人保存的一份独立数据包 | 机器人在接受开始时生成 | 云端业务幂等主标识候选；不能由 SHA 代替 |

另外单独列“现有技术标识”：`uploadId/session_id`、`rollout_id`、`collection_job_id`、`task_id`、object key、workflow/job ID。用分组和“当前系统标识”标签展示，禁止擅自画出等号或外键线；待后端映射合同批准后再显示关联状态（已解析/缺失/冲突）。

### 5.3 页面分区

1. `概览`：身份摘要、信号轨道、当前阻断原因、下一步、不可变 Raw 提示。
2. `Manifest`：schema/version、自身 SHA、提交标记、生成者、时间范围、五标识、文件列表、文件大小/SHA、期望/实际 Topic、记录器/压缩版本；默认摘要，完整 JSON 只读展开。
3. `对象与分片`：对象大小、分片数、缺失片号、每片 ETag/CRC64、对象 CRC64/SHA、重试历史。表格自身滚动，固定对象路径和“详情”。
4. `MCAP 与 Topic`：Header/Footer/Summary/索引/CRC32、Schema/Channel/Topic inventory、required/optional/unknown、消息数、时间范围、解码探针；支持按 finding/Topic 筛选。
5. `自动质检`：profile/version/hash、engine version、PASS/RISK/REJECT、findings、observed/threshold、受影响时间段、Topic 指标。异常只有 `打开 Raw 诊断` 与 `复制诊断信息`。
6. `时间线与审计`：业务时间线显示上传/校验/重试；正式审计仅给 P19 深链，明确当前事件摘要不是审计全量。

技术详情按 Tab 内折叠，不把 hash、纳秒、object key、safe payload 默认倾倒在概览。完整 JSON/ID 区提供复制、换行/横向滚动与全文查看路径。

### 5.4 校验链与准入规则

| 顺序 | 检查 | 通过后 | 失败后 |
|---:|---|---|---|
| 1 | 分片对账：片号连续、大小/ETag/CRC64 | 允许完成 multipart | 标缺失/冲突片，只恢复对应分片 |
| 2 | 对象完整性：总大小、CRC64、云端 SHA-256 | 允许进入 Manifest 最终提交 | Raw 临时对象不提交；显示 expected/actual |
| 3 | Manifest：schema、自身 hash、对象集合、五标识完整性 | 写最终提交标记候选 | 保持未提交；不可把对象已存在称为已接收 |
| 4 | 不可变/原子提交 | Raw 进入 committed/received | 冲突时禁止覆盖并对账；跨存储原子语义仍待技术确认 |
| 5 | MCAP 结构 | 进入 Topic/解码检查 | Raw 保留；Lance 阻断 |
| 6 | Topic/Schema/Channel/解码 | 进入自动 QC | Raw 保留；Lance 阻断 |
| 7 | 自动 QC | PASS 才排队 30 Hz 对齐/Lance | RISK/REJECT 均停留 Raw，不人工放行 |
| 8 | 对齐/Lance commit | 显示 Lance 引用 | 技术失败可重试；不能反向修改 Raw 或 QC 结论 |

“重跑”必须产生新的验证/QC run 和不可变报告引用，旧报告保留。只有输入、profile 或引擎版本变化且服务端允许时才能重跑；重跑按钮不能暗示结果会变成 PASS。

### 5.5 Raw 诊断与审计跳转

- `打开 Raw 诊断` 进入 T02 统一工作台的 Raw 只读模式，候选参数只传稳定 `dataPackageId`、finding/topic/time range；最终 route builder 由 T02 定义。P04 不复制媒体/时间轴内核。
- 无 Raw 权限时不显示下载/对象 URL，提示 `你可以查看质检结论，但没有 Raw 诊断权限。`
- P19 深链候选按稳定资源引用过滤审计；若 P19 未提供 route/合同，则显示 `正式审计查询尚未开放`，不把安全事件时间线冒充正式审计。

## 6. 1440 / 1280 ASCII 线框

图中 `[固]` 表示固定列，`↔ 表内` 表示只有表格容器横向滚动；页面本身不得横向滚动。

### 6.1 P02｜1440 × 900

```text
┌─220 导航────────┬─1220 内容────────────────────────────────────────────────────┐
│ 采集与接收      │ 上传来源                         [管理上传记录] [添加来源·主] │
│  数据源（当前） │ 管理上传连接方式；机器人资产在 P14/P15                     │
│  上传记录       ├─────────────────────────────────────────────────────────────┤
│                 │ 来源 18 │ 可用 14 │ 需检查 3 │ 24h 有上传 11               │
│                 ├─────────────────────────────────────────────────────────────┤
│                 │ [搜索来源…] [来源方式⌄] [连接状态⌄] [更多筛选(2)] [应用]   │
│                 ├─────────────────────────────────────────────────────────────┤
│                 │┌─表格自身 ↔───────────────────────────────────────────────┐│
│                 ││[固]来源 │方式│触发│绑定对象│连接│最后上传│原因│[固]查看││
│                 ││上海机器人 017│机器人│异步│星舟017 ↗P15│在线│3分钟前│—│查看││
│                 ││导入站 02│工作站│手动│工作站02│降级│昨天│授权过期│查看 ││
│                 │└──────────────────────────────────────────────────────────┘│
│                 │                                                [上一页 1 下一页]│
└─────────────────┴─────────────────────────────────────────────────────────────┘
```

### 6.2 P02｜1280 × 800

```text
┌─64 图标导航─┬─1216 内容───────────────────────────────────────────────────────┐
│             │上传来源                                      [添加来源·主]     │
│             │[18 来源][14 可用][3 需检查][11 最近上传]                       │
│             │[搜索来源…][方式⌄][状态⌄][更多筛选]                              │
│             │┌─表格自身 ↔──────────────────────────────────────────────────┐│
│             ││[固]来源│方式/触发│绑定对象│状态│最后上传│需处理│[固]查看│    ││
│             │└─────────────────────────────────────────────────────────────┘│
│             │详情用右侧抽屉；技术详情默认折叠，不挤压表格                     │
└─────────────┴────────────────────────────────────────────────────────────────┘
```

### 6.3 P03｜1440 × 900

```text
┌─220 导航────────┬─1220 内容────────────────────────────────────────────────────┐
│ 采集与接收      │ 上传记录                       [管理来源] [发起导入·主]      │
│                 │ 每行是一份数据包的上传执行；任务管理请前往 P20               │
│                 ├─────────────────────────────────────────────────────────────┤
│                 │ [全部] [上传中 6] [校验中 3] [已接收 128] [需处理 9]         │
│                 │ [搜索数据包…][来源⌄][最近24h⌄][更多筛选(3)] [应用]           │
│                 ├─────────────────────────────────────────────────────────────┤
│                 │┌─表格自身 ↔───────────────────────────────────────────────┐│
│                 ││[固]数据包│来源│采集任务↗│分片进度│校验链│幂等结果│恢复│更新│[固]详情││
│                 ││dp_…P7    │机器人│透明抓取│183/256│CRC✓ SHA…│续传同包│2/5│17:42│详情││
│                 ││dp_…F1    │离线盘│夜间搬运│256/256│MCAP×   │首次接收│—  │17:35│详情││
│                 │└──────────────────────────────────────────────────────────┘│
│                 │ 状态栏：实时更新 / 最后事件 17:42:08               [上一页 下一页]│
└─────────────────┴─────────────────────────────────────────────────────────────┘
```

### 6.4 P03｜1280 × 800

```text
┌─64 图标导航─┬─1216 内容───────────────────────────────────────────────────────┐
│             │上传记录                                      [发起导入·主]     │
│             │[全部][上传中][校验中][已接收][需处理]                          │
│             │[搜索…][来源⌄][24h⌄][更多筛选]                                  │
│             │┌─表格自身 ↔──────────────────────────────────────────────────┐│
│             ││[固]数据包/记录│来源/任务│分片│CRC/SHA/MCAP/MF/QC│幂等│[固]详情││
│             │└─────────────────────────────────────────────────────────────┘│
│             │窄屏合并“来源/任务”和校验链摘要；不隐藏 data_package_id          │
└─────────────┴────────────────────────────────────────────────────────────────┘
```

### 6.5 P04｜1440 × 900

```text
┌─220 导航────────┬─1220 内容────────────────────────────────────────────────────┐
│                 │ 数据包摄取详情 / dp_01K2…P7          [复制诊断] [打开 Raw·主]│
│                 │ 自动质检异常，Raw 已保留，未进入 Lance                     │
│                 │ ●接受 ─ ●分片 ─ ●CRC/SHA ─ ●Manifest ─ ●MCAP ─ ▲QC ─ ○Lance│
│                 │ 17:20    17:31    17:35       17:36      17:39   异常   被阻断│
│                 ├─────────────────────────────────────────────────────────────┤
│                 │ [概览][Manifest][对象与分片][MCAP/Topic][自动质检][时间线]  │
│                 ├──────────────────────────────────────┬──────────────────────┤
│                 │ 自动质检异常                         │ 标识与来源            │
│                 │ /camera/front 覆盖率 91.4% < 98%    │ data_package_id       │
│                 │ 受影响 00:13.2–00:18.7 [定位 Raw]   │ robot_id ↗ P15       │
│                 │                                      │ request/session/pico… │
│                 ├──────────────────────────────────────┤ [技术标识详情⌄]       │
│                 │ 校验链 / Finding / Topic 表自身 ↔    ├──────────────────────┤
│                 │ [固]Topic│消息数│覆盖率│阈值│结论│详情│ 下一步：修复采集问题 │
│                 └──────────────────────────────────────┴──────────────────────┤
└─────────────────┴─────────────────────────────────────────────────────────────┘
```

### 6.6 P04｜1280 × 800

```text
┌─64 图标导航─┬─1216 内容───────────────────────────────────────────────────────┐
│             │数据包摄取详情 / dp_…P7                         [打开 Raw·主]   │
│             │●接受─●上传─●完整性─●Manifest─●MCAP─▲QC─○Lance                   │
│             │[概览][Manifest][对象/分片][MCAP][QC][时间线]  ← tabs 自身可滚动 │
│             ├────────────────────────────────────────────────────────────────┤
│             │当前状态 + 下一步                                                │
│             │自动质检异常：/camera/front 覆盖率不足；不会进入 Lance            │
│             ├────────────────────────────────────────────────────────────────┤
│             │标识摘要（两列） [查看全部技术标识⌄]                              │
│             ├────────────────────────────────────────────────────────────────┤
│             │Finding / Topic 表格自身 ↔；固定 Topic 与详情列                   │
└─────────────┴────────────────────────────────────────────────────────────────┘
```

## 7. 状态与错误矩阵

| 场景 | 列表文案 | P04 解释 | 可用动作 | 不允许 |
|---|---|---|---|---|
| 断网 | `网络已断开，已确认 183/256 片；显示 17:42 的状态` | 分清客户端离线、来源离线、服务端不可达；显示最后成功时间 | 自动续传倒计时；手动重连；工作站必要时重选同一文件 | 把本地已发送称云端已接收；静默回退 Mock |
| 重复 recording request | `续传同一数据包` | 相同 request 映射同一 package 的候选结论及服务端证据 | 继续缺失分片/查看既有数据包 | 新建第二个 package |
| 数据包已提交后重传 | `数据包已存在，未重复写入` | 显示原提交时间和当前 requestId | 打开既有详情 | 覆盖或计为第二份成功 |
| 相同 SHA、不同 package | 两条记录均显示 `内容摘要相同，数据包独立保留` | 明示 SHA 只作完整性证据 | 分别查看 | 自动合并、去重删除 |
| 分片缺失 | `缺失 4 片 · 可续传` | 列出 `184、190–192`、授权到期与已确认片 | 续传缺失分片/刷新授权（server 允许） | 重传全部、完成 multipart |
| CRC64 不符 | `传输完整性失败` | expected/actual、对象与分片层级；未提交 | 重传失败对象/片（server 允许） | 写 Manifest 完成标记 |
| SHA-256 不符 | `内容摘要不一致` | 云端重算值、Manifest 声明值；Raw 未提交或验证失败 | 重新上传正确对象/更正未提交 Manifest（规则待定） | 用 SHA 相似推断可合并、覆盖已提交 Raw |
| MCAP 结构失败 | `MCAP 结构未通过` | 例：`MCAP 缺少 Footer；Raw 已保留，未进入 Lance`，显示 offset/code | 打开 Raw 诊断、复制 finding；技术重验需 server 允许 | 人工改为通过 |
| Topic/解码失败 | `必需 Topic 缺失` | required/actual、schema/decoder finding | Raw 诊断、按 Topic 定位 | 跳过必需 Topic 后进入 QC/Lance |
| Manifest 失败 | `Manifest 未提交` | schema/hash/object set/标识缺失；对象上传≠云端接收完成 | 修复未提交清单、重提（server 允许） | 把 multipart complete 显示为 committed |
| 提交冲突 | `data_package_id 内容冲突` | 相同 ID 对应不同内容/Manifest，阻止覆盖，附 requestId | 查看现有记录、复制诊断、联系管理员 | “仍然提交”、新建同 ID |
| 跨存储提交部分失败 | `提交状态待对账` | 标记 object/Manifest/DB 各自已知状态；不伪造原子成功 | 自动对账/查看运维状态 | 用户手动拼接最终状态 |
| 自动 QC RISK | `自动质检异常 · Raw` | warning findings、profile/version；`training_eligible=false` | Raw 诊断、复制诊断；新输入/新 profile 后自动重跑候选 | 人工放行进入 Lance |
| 自动 QC REJECT | `自动质检拒绝 · Raw` | error findings 与受影响区间；Raw 不变 | Raw 诊断、重新采集 | 人工改 PASS、修改 Raw |
| QC 技术失败 | `质检执行失败` | 与业务 RISK/REJECT 分开；显示 job/retry | server 允许时重试执行 | 把技术失败算 REJECT/PASS |
| 功能未开放 | `此功能尚未开放` | 说明未发送请求及缺失合同/权限 | 返回、复制需求信息 | disabled 无解释、假成功、调用类似名称接口 |

状态文案分层：上传状态、Manifest 状态、MCAP 验证、自动 QC、Lance 准入分别显示，不合成一个含糊的“已完成”。只有 Manifest 最终提交完成才叫“云端已接收”；只有自动 QC PASS 后才允许显示“等待对齐/进入 Lance”。

## 8. 效果图清单与真实中文文案

### 8.1 三张主效果图

1. `T04-01-P02-上传来源-1440.png`

   默认列表：机器人自动上传、工作站/Web、离线硬盘三类来源；P15 深链；更多筛选收起；主动作“添加来源”。重点证明“不复制机器人注册表”。

2. `T04-02-P03-上传记录-1440.png`

   混合窗口：上传中、续传同一数据包、相同 SHA 的独立数据包、MCAP 失败；展示表内滚动、固定数据包/详情列、实时更新降级文案。

3. `T04-03-P04-自动质检异常-1440.png`

   `dp_01K2X9W4Q8C6P7` 的完整信号轨道；`/camera/front 覆盖率 91.4%，阈值 ≥98%`；Raw 保留、Lance 被阻断；主动作“打开 Raw 诊断”。

### 8.2 关键详情/错误态补充图

| 编号 | 画面 | 必须出现的中文文案 |
|---|---|---|
| T04-D01 | P02 来源详情抽屉 | `绑定机器人：星舟 017`、`查看机器人实例`、`机器人模型与组件不在此页维护` |
| T04-D02 | P03 离线/续传 | `网络已断开，已完成 183/256 片；将在 28 秒后继续`、`需要重新选择同一文件时，已确认分片不会重传` |
| T04-D03 | P03 幂等 | `同一 recording request 已映射到数据包 dp_…P7`、`数据包已存在，未重复写入` |
| T04-D04 | P03 同 SHA 独立包 | `SHA-256 相同，data_package_id 不同；两份数据独立保留` |
| T04-D05 | P04 Manifest/对象 | `对象上传完成不等于数据包已接收；等待 Manifest 最终提交`、`缺失分片：184、190–192` |
| T04-D06 | P04 CRC/SHA 错误 | `CRC64 与对象存储结果不一致；该对象未提交`、`SHA-256 不一致；不会写入 Manifest 提交标记` |
| T04-D07 | P04 MCAP 失败 | `MCAP 缺少 Footer；Raw 已保留，未进入 Lance`、`错误代码：MCAP_FOOTER_MISSING` |
| T04-D08 | P04 提交冲突 | `data_package_id 已存在且内容不同；已阻止覆盖`、`复制请求 ID` |
| T04-D09 | P04 功能未开放 | `正式审计查询尚未开放；当前仅显示安全资源事件摘要，没有发起 P19 审计请求` |
| T04-D10 | P04 无 Raw 权限 | `你可以查看质检结论，但没有 Raw 诊断权限。` |

效果图使用主设计系统浅色语义 Token；成功/风险/失败必须同时有图标和文字。不要用大面积红色、漏斗、Gauge 堆叠、营销 Hero 或装饰性信号线。

## 9. web-design-guidelines file:line 审查

以下是对当前源码的发现，不代表本轮已修复：

| 严重度 | file:line | 发现 | 后续要求 |
|---|---|---|---|
| 中 | `frontend/src/pages/p02-data-sources/page.tsx:540-628`；`p03-upload-jobs/page.tsx:404-468` | 表单控件 JSX 没有稳定 `name`/autocomplete；AntD Select 嵌套 label 的最终可访问名称需用生成 DOM 验证 | 显式 label/id/name；搜索用合适 autocomplete，技术 ID 可 `off`；就地错误与帮助文本关联 |
| 中 | `frontend/src/pages/p02-data-sources/page.tsx:544`；`p03-upload-jobs/page.tsx:408,422` | 输入 placeholder 未以省略号结束，且 P03 仍写“任务 ID” | 改为 `搜索来源名称或 ID…`、`搜索数据包、上传记录或机器人…` |
| 中 | `frontend/src/pages/p02-data-sources/components/DataSourceTable.tsx:73-84`；`p02-data-sources/page.tsx:710-725` | 多处直接展示 ISO 字符串 | 用 `Intl.DateTimeFormat` 显示业务时间，保留 `<time dateTime>`；完整 ISO 放技术详情 |
| 中 | `frontend/src/shared/ui/data/DataTable.tsx:119-151,186-197` | 有表内横向滚动，但 column adapter 不支持 fixed；P02/P03/P04 的关键首尾列会滚走 | 扩展共享 meta/adapter 后固定主标识与主动作；需共享组件 owner 审核 |

满足的现有点：交互主要使用 Button/链接语义；装饰图标普遍 `aria-hidden`；DataTable 有 caption/aria-busy/可访问选择标签与表内横向滚动，见 `frontend/src/shared/ui/data/DataTable.tsx:154-198`。列表窗口最大 50，当前无需为了 20/50 行强行虚拟化。

## 10. React 19 + Vite 后续实现风险

### 10.1 上传进度更新

- 文本进度 4–10 Hz 足够；传输层事件可更高频但不进入 React 树。进度 row/Cell 用稳定 props 与 memo，事件 Map/Set 放 ref；不要为每片创建 Toast 或 Query invalidation。
- 同一行必须区分 `clientSentBytes` 与 `serverConfirmedBytes`；只以后者推进“云端接收”。大整数保持 decimal string/BigInt，格式化边界再转，禁止超出安全整数。

### 10.2 轮询与 SSE

- SSE 必须保留 `event_id/resource_version` 去重与乱序拒绝；scope 切换清缓存；后端确认 Last-Event-ID/鉴权/代理超时合同后才启用。当前 3 次失败后永久停在 polling（`:93-100`），需要页面可见的降级状态和受控重连，而不是无限指数重试。
- P04 当前 bootstrap 活跃 5 秒轮询 + events 5 秒轮询，未来还会加入 verification/QC。只为可见 Tab/活跃阶段订阅；终态停止高频；页面隐藏时降频；一次刷新并行发请求，避免串行瀑布。
- SSE 不是成功依据；断连、重连或缺事件时以版本化快照对账，进度不允许回退。对账差异显示“状态已校正”，不可静默跳变。

### 10.3 长列表

- P02 query codec 限制 10/20/50（`query-codec.ts:3-24`），保持服务端游标窗口时先不虚拟化，避免破坏表格语义和焦点。
- 若以后允许 >50 条、展开分片上千行或 Topic inventory 很大，再对表体虚拟化；固定头/列、行高、键盘焦点、滚动恢复与 aria 行语义要一起验证。
- 筛选/排序变更复位游标并写 URL；数据源和任务选项应有服务端搜索，不把完整机器人/任务目录塞进首包。

### 10.4 重型详情分包

- Vite 方案：`React.lazy(() => import('./tabs/McapTopicTab'))` 等静态可分析动态导入；概览/身份留首包，Manifest JSON viewer、MCAP、QC 图表、审计详情按 Tab 分包，在 hover/focus 或空闲时预取。每个 Tab 有 ErrorBoundary、固定骨架尺寸和重试。
- 不引入 `next/dynamic`。避免把整个 shared UI/barrel 或 ECharts 带入概览；只有真正使用图表的 QC 明细包才载入图表库。并行拉 bootstrap/可见分区数据，避免 import 完成后再串行请求。

## 11. 前端所需字段：候选合同

此表描述页面 projection/ViewModel 所需字段，不提议数据库表、外键或具体新 endpoint。`已有` 仅说明底层有直接证据；页面聚合仍可能不存在。

| 候选字段 | 用途 | 状态 | 证据/缺口 |
|---|---|---|---|
| `project_id`, `region_code` | scope | 已有 | ingest OpenAPI 路径与 session 已有；页面鉴权/投影仍待接入 |
| `source_id`, `source_kind`, `trigger_mode`, `source_display_name` | P02/P03 来源 | 草案/部分不存在 | 前端有 data source draft；真实 backend 无 connector；`trigger_mode` 不存在 |
| `pico_instance_id` | 五标识 | 不存在 | 只见 PICO 恢复稿；当前 ingest/frontend upload model 均无 |
| `robot_id` | 五标识/P15 深链 | 已有（底层） | `RolloutManifestV1` 已有；前端上传 session 没有该字段 |
| `collection_session_id` | 五标识 | 不存在 | 方向恢复稿有，当前合同无 |
| `recording_request_id` | request 去重解释 | 不存在 | 方向恢复稿有，当前合同无 request→package 读取投影 |
| `data_package_id` | 业务幂等/页面主标识 | 不存在 | 本轮强制候选；当前真实模型以 `rollout_id`，不得直接改名 |
| `upload_record_id` | 一次上传执行 | 草案 | 前端 `upload_id`；真实是 `session_id`；二者映射无法确认 |
| `rollout_id`, `collection_job_id`, `task_id` | 现有底层血缘 | 已有 | backend manifest/model 已有；与 P20/五标识映射无法确认 |
| `collection_task_id`, `task_code`, `task_display_name` | P03→P20 跳转/归类 | 草案/不存在 | `collection_task_id` 是 T03 的 UX 候选名，不是既有合同；任务码不是唯一 ID（`T03-P01-P20-SPEC.md:136,571-574`） |
| `manifest_id`, `manifest_schema_version`, `manifest_sha256`, `manifest_status`, `committed_at` | 最终提交摘要 | 草案 + 已有底层概念 | 前端 summary 为 draft；真实 Manifest 没有 manifest_id/status 页面投影 |
| `manifest_identity_claims` | Manifest 报告的五标识 | 不存在 | 当前真实 Manifest 只有 task/collection_job/rollout/robot |
| `object_count`, `expected_bytes`, `confirmed_bytes` | 进度 | 草案/已有底层部分事实 | 页面 projection 不存在；需定义口径 |
| `completed_parts`, `total_parts`, `missing_part_numbers`, `part_retry_count` | 分片恢复 | 部分已有 | 后端可列 parts/CRC64；聚合总数、缺失与重试次数未确认 |
| `expected_crc64`, `actual_crc64`, `expected_sha256`, `actual_sha256` | 完整性 | 已有（底层） | backend upload object 已有；页面 draft 只显 SHA/ETag |
| `idempotency_outcome` (`CREATED/RESUMED/ALREADY_COMMITTED/CONTENT_CONFLICT`) | P03/P04 幂等结果 | 不存在 | 有 Idempotency-Key 行为，无稳定响应 outcome；枚举只是候选 |
| `same_content_other_package_count` | 提示相同 SHA 的独立包 | 无法确认 | 不能客户端全局查 SHA；权限、范围、性能与隐私待设计 |
| `mcap_report_id/status/findings/schemas/channels/topics/content_sha256` | MCAP 详情 | 已有（底层） | verification report 已有，仍按 rollout；缺 P04 聚合 |
| `qc_report_id/status/profile_id/profile_version/profile_sha256/engine_version/findings` | 自动 QC | 已有（底层） | quality report 有等价事实；缺 report id、package 映射与页面聚合 |
| `training_eligible`, `lance_gate_reason`, `lance_job_id` | Lance 准入 | 部分已有 Worker 结果 | Worker result 有 training_eligible；稳定读取投影/Job 深链待设计 |
| `timeline_events[]` | 操作时间线 | 草案 | 前端 Mock 有安全事件；真实统一事件投影不存在 |
| `audit_resource_ref`, `audit_deep_link` | P19 正式审计 | 不存在/无法确认 | P04 明确当前 events 不是 P19；由 T10/P19 定义 |
| `allowed_actions`, `blocked_reasons`, `etag/resource_version` | 安全动作/并发 | 草案 + 真实基础能力 | 前端 draft 已有，真实页面资源投影未有；动作名必须按正式状态机 |
| `raw_diagnostic_ref` | T02 Raw 工作台入口 | 不存在/无法确认 | T02 route/adapter 未交付；不能传签名 URL或 object key 充当业务 ref |

任何候选响应都应带 `scope`、`snapshot_at/as_of`、`request_id`、`contract_version`；错误使用稳定 code、retryable、field/operation errors。Signed URL、secret、文件句柄不得进入 Query cache、URL、日志或持久化状态。

## 12. 产品问题、技术设计项与可直接实现项

### 12.1 必须由产品确认

1. P02 三类来源的正式命名与组合：工作站/Web 是一个来源还是两个；离线硬盘是来源、导入方式还是一次导入批次。
2. 机器人自动上传的“单次结束后异步”和“Task 完成后批量”是来源配置还是每次执行策略；是否允许同一来源并存。
3. `data_package_id` 与现有 `rollout_id` 是否一一映射、共存还是迁移；`collection_session_id/recording_request_id/collection_job_id/task_id/P20 task` 的产品关系。本文不作数据库结论。
4. P20 任务深链和任务显示口径；任务码的可见性、过期/撤销状态和是否允许按任务码搜索。
5. P03 是否允许浏览器直接发起上传，哪些角色可发起离线导入；机器人上传是否只能观察/恢复。
6. 暂停、取消、替换、重传的状态规则；已提交 Raw 的“取消”只取消后续处理还是完全不可用。
7. 自动 QC RISK 与 REJECT 的中文产品文案和后续业务：两者都不能进入 Lance；是否允许“重新采集”任务入口。
8. 相同 SHA 的独立数据包提示范围与可见性；不得成为自动合并功能。
9. Manifest “最终提交/原子提交”的用户承诺边界；跨对象存储、数据库、事件的对账中状态如何命名。
10. P04 时间线保留期、事件可见范围与 P19 正式审计的职责分界。

既有 `plan/PRODUCT-DESIGN-DECISIONS-REQUIRED.md:25-26,34-36` 的 PD-11/12/20/22 仍未确认，不能被效果图默认为已解决。

### 12.2 必须先做技术设计

1. 建立 P02 connector 模型/secret reference/egress allowlist 与审计；当前 backend 无 data-source API。
2. 为 P03/P04 建真实列表/detail projection，聚合 ingest、verification、quality、workflow、source、P20、P15，但保留各域 owner 与版本；不得让前端拼接“最终事实”。
3. 设计旧 `rollout_id/session_id` 与新五标识的兼容/迁移合同、唯一性和冲突策略；同 SHA 不去重。
4. 定义 `idempotency_outcome`、Manifest 状态、分片失败与可重试动作的稳定错误码；补并发、重复 request、冲突、Worker replay 测试。
5. 定义对象存储 Manifest marker 与数据库/Outbox 的原子性或可恢复 saga/对账状态；UI 不先承诺严格原子事务。
6. 设计 SSE scope、鉴权、Last-Event-ID、事件版本、背压、降级轮询与终态停止；做 50 行×高频进度的性能剖析。
7. P04 MCAP/QC 报告按 package 的授权投影、finding 分页、Topic 高基数处理与 Raw 诊断令牌；Raw URL 不直出。
8. 与共享 DataTable owner 设计 fixed column meta；与 T02 定义 Raw workbench route；与 T03/T09/T10 定义 P20/P15/P19 route builders。
9. 按 Vite 做详情 Tab 分包、ErrorBoundary、按 Tab 请求和 bundle 分析；不引入 Next 专属模式。

### 12.3 效果确认后可直接实现的前端项

这些只需要现有前端/Mock 即可表现，但仍属于下一实现阶段，本轮未编码：

- P03 文案从“上传任务/任务 ID/新建上传”改为“上传记录/上传记录 ID/发起导入”，并加 P20 边界说明。
- P02/P03 将筛选重排为常用 + 更多筛选，保留现有 URL codec 行为。
- P04 移除所有“申请释放隔离”文案与动作；技术重试只叫“重试失败阶段/重新运行自动校验”，且说明不会人工改变 QC 结果。
- 用 `Intl` 格式化日期/字节/数字；完整 ISO/ID/hash 放技术详情；补 placeholder 省略号。
- 修复 P04 Tab 键盘、progressbar ARIA、异步状态 `aria-live`，为长文本提供查看全文。
- P02 用现有 P15 route builder 增加“查看机器人实例”，只在明确 `robotId` 存在时出现。
- 所有当前 Mock-only 动作用 `功能未开放` 明示，不把类似后端路径当接入完成。

## 13. 验收清单

- [ ] P02 只展示上传来源/连接器，不出现 P14 的 URDF/Mesh 注册表或 P15 组件树。
- [ ] 三种上传方式都能从来源到记录再到详情讲清楚，且 Task 批量上传仍逐个数据包记录。
- [ ] P03 全页不把上传记录称采集任务；任务操作明确去 P20。
- [ ] 五标识逐项存在，`data_package_id` 为业务主标识；没有自创数据库连线。
- [ ] 重复 recording request 同 package；相同 SHA 不同 package 两条独立记录。
- [ ] 分片、CRC64、SHA、Manifest 最终提交、MCAP/Topic、自动 QC 分层可读。
- [ ] QC RISK/REJECT 停 Raw；任何画面都没有“人工通过/释放进入 Lance”。
- [ ] 1440/1280 无整页横向滚动；表格自身滚动并固定主标识/主动作；每页最多一个主 CTA。
- [ ] 常用筛选不超过 5 个，高级筛选可折叠且 URL 可分享。
- [ ] 断网、重复、分片缺失、CRC/SHA、MCAP、Manifest、提交冲突、QC、未开放全有真实中文状态。
- [ ] Mock/草案/已有/不存在/无法确认标签在效果图注释和评审稿中保持一致。

## 14. 初始工作树记录、执行命令与未验证项

### 14.1 开始时 `git status --short`

```text
 M README.md
 M backend/Dockerfile
 M compose.dev.yaml
 M deploy/compose/gateway.conf
 M frontend/docs/status/T2.md
 M frontend/src/app/theme/component-theme.ts
 M frontend/src/app/theme/global.css
 M frontend/src/app/theme/tokens.ts
 M frontend/src/features/storage-overview/metrics-contract.ts
 M frontend/src/mocks/fixtures/cleaning/index.ts
 M frontend/src/pages/p01-dashboard/components/DashboardPendingList.tsx
 D frontend/src/pages/p01-dashboard/components/DataLifecycleRail.module.css
 D frontend/src/pages/p01-dashboard/components/DataLifecycleRail.tsx
 M frontend/src/pages/p01-dashboard/page.tsx
 M frontend/src/pages/p01-dashboard/styles.module.css
 M frontend/src/pages/p02-data-sources/components/DataSourceTable.tsx
 M frontend/src/pages/p02-data-sources/styles.module.css
 M frontend/src/pages/p03-upload-jobs/styles.module.css
 M frontend/src/pages/p05-datasets/components/DatasetTable.tsx
 M frontend/src/pages/p05-datasets/styles.module.css
 M frontend/src/pages/p12-storage-overview/components/StorageObjectDrawer.tsx
 M frontend/src/pages/p12-storage-overview/components/StorageOverviewPanel.tsx
 M frontend/src/pages/p12-storage-overview/components/StorageSummaryStrip.tsx
 M frontend/src/pages/p12-storage-overview/components/StorageVisualCharts.tsx
 M frontend/src/pages/p12-storage-overview/page.tsx
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

这些均为进入任务前已有改动；T04 未触碰或回退。P02/P03 当前文件本身已有他人未提交修改，所以本规格只读取其当前工作树状态，不把它们归为 T04 产出。

### 14.2 关键命令

```text
git status --short
rg --files / nl -ba / sed -n / wc -l / rg -n
curl -fsSL https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md
python3 /mnt/c/Users/28384/.agents/skills/ui-ux-pro-max/scripts/search.py "robotics data ingestion operations console dense desktop enterprise" --design-system --density 8 -p "HC Data Platform"
python3 /mnt/c/Users/28384/.agents/skills/ui-ux-pro-max/scripts/search.py "table progress error recovery accessibility" --domain ux
python3 /mnt/c/Users/28384/.agents/skills/ui-ux-pro-max/scripts/search.py "upload progress polling SSE long list Vite" --stack react
cd frontend && pnpm exec vitest run src/mocks/handlers/ingest.handlers.test.ts
```

定向现状测试结果：`1` 个 test file、`2` 个测试均通过；它们只覆盖 Mock 上传列表的两组生命周期筛选，不扩大证据范围。未执行 commit/push，未修改生产代码、后端、OpenAPI、迁移、Worker、部署或其他计划。

### 14.3 未验证项

- 四份正式方向文件不可访问；只读了明确列出的恢复快照，无法确认它们是否为最终版本。
- 未确认 `data_package_id` 与现有 `rollout_id/session_id/collection_job_id` 的关系、迁移和唯一约束。
- 未确认 P02 正式 connector 类型、凭据 owner、离线硬盘/工作站的模型和真实 API。
- 未确认 P20 的 route builder、任务 ID/任务码字段与 P03 projection。
- 未确认 P04/P19 正式审计深链与 T02 Raw 工作台深链。
- 未确认跨对象存储、数据库和事件的“原子提交”技术保证；当前代码能证明不可变 marker 与对账，不能证明跨系统 ACID。
- 未运行端到端/视觉回归；本文是效果图规格。只运行了现有 ingest 定向单测，范围仍只到两个 Mock 列表筛选用例。

## 15. 与 T03 / T09 的边界冲突与对齐请求

### 15.1 T03（P01 + P20）

- 所有采集任务定义、任务码、目标数量、人员/机器人分派、PICO SAVED/云端 Received/QC 计数归 T03/P20。P03 只展示任务引用和跳转，不编辑任务。
- 已对齐：T03 明确 P20 不是 P03 改名版，`collection_task_id` 是稳定任务身份候选、`data_package_id` 是跨 P20/P03/P04/Raw 的关联键，P20→P03 候选筛选为 `collection_task_id` + 可选 `data_package_id`（`T03-P01-P20-SPEC.md:13,136-143,560-576`）。本规格采用同一候选名，不把它写成现有接口字段。
- 已对齐：T03 将 Received 候选口径定义为“完成接收门槛的 distinct `data_package_id`”，重复上传不重复计（`:503,597`）；本规格把 Manifest 最终提交/接收门槛展示在 P03/P04。跨对象存储、数据库和事件是否严格原子仍是共同未决技术项，不能由两个页面文案先行承诺 ACID。
- 仍需 T03/路由 Owner 给出正式 `collectionTask` route builder、无权/不存在/已过期行为；现有 P03 codec 并不消费上述候选筛选（`:561-574`），故效果图只标“候选深链”。
- 冲突警戒：T03 使用“RISK 等待人工质检/人工结论”措辞（`:116,128,601,703`）。只有当人工结果仅能请求自动重跑、重采或维持隔离，且进入 Lance 必须产生新的可审计自动 PASS 时，才与本规格一致；任何人工结果直接改变 QC gate 均冲突并应删除。
- 冲突警戒：P20 若把“上传中/上传失败”做成可执行上传列表，会与 P03 重叠；P20 只显示聚合与深链，实际重试/恢复在 P03/P04。

### 15.2 T09（P14–P17）

- P14 拥有机器人模型资产/版本，P15 拥有机器人实例/组件，P16 标定，P17 Channel/Manifest/Topic Schema 注册表。P02 只持引用并跳转。
- 已对齐：T09 的页面所有权表把 P02 定义为来源/连接器、P03 定义为 upload session/data package 接收执行，明确排除 P14 资产源、P20 任务定义和 Registry 编辑（`T09-P14-P17-SPEC.md:183-193`）；与本规格一致。
- 已对齐：actual package Manifest 与本次 MCAP/Topic finding 归 P03/P04 只读展示；P17 只拥有可复用 Manifest Profile、Channel/Topic/Schema 版本与兼容规则，不能编辑历史包 Manifest（`:601-614,947-959`）。修复 Registry 必须产生新版本或重新上传，不能覆盖 Raw/历史报告。
- 仍需 T09/路由 Owner 给出 P15 `robotId`、P14 `modelId/versionId`、P16/P17 version ref 的稳定路由及 not-found/gone 语义；已有前端 builder 只是草案，当前 P03/P04 也缺正式版本关联（`:954-959`）。
- 冲突警戒：T09 把 P14 资产接入推荐为经 P02/P03 或专用流程产生 `asset ref`（`:333,393,891,953`）。若选择专用管理型流程，它也不能在 P02 伪装成机器人数据源，不能让 P14/P02 同时拥有同一 connector；合同 Owner 必须明确 asset ref 与 data package 的差别。
- 冲突警戒：P02 若保存机器人 display name、serial、组件树、模型版本快照，会复制 T09 registry；只消费稳定引用和响应提供的显示摘要。P15 的 connectivity 是实例上报事实，P02 的 connectivity 是连接器探测事实；即使都叫“离线”，也要分别带 `observed_at/source`，不能互相覆盖。

责任划分依据为 `plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:263-282`，并已与本轮可见的 T03/T09 文档静态交叉复核。两份文档都没有把候选路由/字段提升为正式合同，因此效果图中的跨页入口继续标“候选深链”，不阻塞本页信息架构，也不虚构接口。
