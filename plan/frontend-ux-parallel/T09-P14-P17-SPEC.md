# T09｜P14–P17 机器人资产与数据契约 UX 规格

> 文档状态：效果图输入与产品评审稿，不是 API、数据库或实现合同
> 适用页面：P14 机器人型号资产、P15 机器人实例与组件、P16 标定管理、P17 Channel / Manifest / Topic / Schema Registry
> 编制日期：2026-08-17
> 设计基线：<code>design-system/hc-data-platform/MASTER.md</code>（只读，本文没有覆盖）
> 技术基线：React 19.1.1 + Vite 7；当前工作树只读审计
> 停止线：本文只定义 UX、状态与合同候选，不授权业务编码。

## 0. 先读结论

P14–P17 应组成一套“机器人资产护照与数据契约”管理面，而不是四个互不相关的设置页：

1. P14 登记 RobotDescription、不可变型号版本、URDF/Mesh 内容哈希和能力声明；
2. P15 把真实机器人实例、组件拓扑、Gateway、Adapter、控制部署模式和“最近一次上报”的在线摘要绑定到已发布型号版本；
3. P16 给实例/组件/坐标系绑定版本化标定，明确适用对象、有效期、验证结果、冲突和回退关系；
4. P17 管理 Channel、Manifest Profile、Topic Binding 与 Schema 的版本和兼容性，业务摘要默认可见，技术 JSON 按需展开。

这四页是**目录、配置、血缘与兼容性管理面**。PICO 统一发送约 100 Hz XR 输入但不计算关节；机器人端 Gateway / Adapter 负责坐标变换、标定、IK/重定向、限位、碰撞与安全；云平台不进入实时控制闭环。因此 P15 不得出现“连接机器人、移动、回零、急停、开始遥操作”等假云控按钮，在线信息也必须写明来源和观测时间。

当前不能据现有 UI 进入真实业务编码：P14–P17 的前端合同和写动作是草案，Browser Mock 只提供四个列表 GET；正式后端 OpenAPI、领域表、Repository/Service、Worker/Workflow 均没有对应闭环。效果图中的新增字段和状态必须按“方向 / UX 要求 / 待确认”标注，不能伪装成已支持事实。

## 1. 范围、证据等级与仓库保护

### 1.1 本文交付与不交付

本文交付：

- 当前页面、路由、类型、手写 API、Mock、测试、正式 OpenAPI、DB 与 Worker 的证据表；
- P14–P17 对象关系、页面边界、信息架构、详情层级、动作和状态；
- 每页 1440 与 1280 ASCII 线框，覆盖长哈希、JSON、枚举与内层横滚；
- 权限、空、离线、不兼容、过期、冲突、能力缺失、后端未开放等状态；
- 4 张主效果图和关键详情/错误态效果图清单；
- 最新 Web Interface Guidelines 的 <code>file:line</code> 发现与 React/Vite 性能约束；
- “当前支持 / 方向事实 / UX 候选 / 待产品或技术确认”的字段和动作矩阵；
- 与 T03 的 P20 采集任务，以及 T04 范围内 P02 数据源/P03 上传记录的交接边界。

本文不交付：

- 不修改前端、后端、OpenAPI、DB、Worker、部署、README、Master 或其他计划；
- 不生成、上传、发布真实 URDF/Mesh、标定或 Schema；
- 不把 Browser Mock、手写 Zod schema、设计方向稿或效果图字段提升为正式合同；
- 不设计任何云端实时控制、IK、急停或安全回路；
- 不为缺失能力填假值，不用随机哈希、硬编码矩阵或同一哈希自比来制造“已验证”；
- 不提交、不推送、不进入业务编码。

### 1.2 证据等级

| 标记 | 含义 | 可以如何使用 |
|---|---|---|
| F｜代码事实 | 当前工作树中可定位的前端/后端/OpenAPI/DB/Worker/测试 | 只陈述当前存在什么，不推断产品承诺 |
| M｜Mock 草案 | 仅 Browser Mock、fixture、手写前端合同支持 | 可用于审阅当前原型，不得标为后端已支持 |
| D｜方向事实 | 四份机器人/数据平台方向文档明确的架构边界 | 作为不得违背的设计方向；具体字段仍需合同化 |
| U｜UX 要求 | 为形成完整体验提出的页面、字段、状态和文案要求 | 可进入效果图；编码前需产品与合同 Owner 确认 |
| C｜待确认候选 | 本文推荐的默认方案或命名 | 必须经产品/技术决策后才能成为合同 |

方向文档是设计输入，不等于 API/DB/Worker 事实；这一点与改版计划 <code>plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:7,32-50</code> 一致。

### 1.3 开始工作时的工作树基线

以下是本终端开始时记录的 <code>git status --short</code>。它早于其他并行终端随后新增的规格文件；这些修改均视为用户/其他终端资产，本文没有修改或回退：

~~~text
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
~~~

## 2. 不可违背的系统边界

### 2.1 控制面、机器人端与云平台

~~~text
PICO / XR 设备
  统一 TeleopFrame / InputState，约 100 Hz；不算关节角，不保存机器人采集数据
                  │
                  ▼
机器人端 Gateway / Coordinator
  会话、超时、状态、录制协调、失败安全；部署位置可因机器人而异
                  │
                  ▼
Robot Adapter + RobotDescription + Calibration
  坐标变换、缩放、IK/重定向、限位、碰撞、能力映射、设备协议
                  │
                  ▼
机器人控制器 / Recorder ──产生数据包与实际 Manifest──► P02/P03 接入与上传

云平台 P14–P17：目录、版本、引用、兼容性、验证证据、最近上报摘要
云平台不在上面 100 Hz 实时控制链上，不替代机器人端安全系统。
~~~

依据：

- PICO 发送统一约 100 Hz 输入，PICO 不保存机器人数据，机器人差异在 Gateway/Adapter 侧吸收（<code>PICO 跨本体遥操作与机器人端录制方案.md:7-30,85-105</code>）。
- Gateway 负责会话、设备发现/状态和录制协调；Adapter 负责变换、标定、IK/重定向、限位、碰撞与 fail-safe（同文档 <code>:67-79,106-130,466-473</code>）。
- Unity/PICO 发送原始控制输入，机器人协调器完成变换、滤波、IK、限位与碰撞；软件“急停”不能替代硬件安全（<code>VR 全身遥操作初版方案（固定颈部）.md:31-41,45-51</code>）。
- 标准对象包括 RobotDescription、RobotState、MotionCommand、TeleopFrame、EpisodeManifest；新本体原则上通过模型/标定配置与 Adapter 接入（<code>统一机械臂规控、遥操作与数据采集平台建设及调试上线方案.md:34-51</code>）。

### 2.2 对象关系与版本血缘

~~~text
P14 Robot Model ──1:N──► immutable Model Version
                          ├─ RobotDescription hash
                          ├─ URDF / Mesh asset refs + SHA-256
                          ├─ joint / frame declaration
                          └─ declared capability profile
                                      │ publishes / compatible with
                                      ▼
P15 Robot Instance ──effective binding──► Model Version
       ├─ 1:N Component mount tree
       ├─ reports Gateway + Adapter versions / deployment mode
       ├─ reports observed capability + health summary
       └─ exposes frames/channels required by calibration and contracts
                  │
                  ├──────────────► P16 Calibration Target
                  │                 └─ immutable Calibration Version
                  │                    ├─ applicable instances/components
                  │                    ├─ validity interval
                  │                    ├─ verification evidence
                  │                    └─ supersedes / rollback-from relation
                  │
                  └──────────────► P17 Registry
                                    ├─ Channel Definition
                                    ├─ Topic Binding
                                    ├─ Schema + immutable versions
                                    └─ Manifest Profile + referenced versions

P20 Collection Task ─assigns──► people / PICO / P15 robot instances
机器人 Recorder ─produces──► actual package Manifest ─P02/P03 ingest──► data_package
actual Manifest pins model / adapter / calibration / schema references when contract permits.
~~~

“发布”意味着形成可引用的不可变版本；不得原地改写已被实例、标定、Manifest 或数据集引用的版本。是否使用 SemVer、如何弃用、如何跨项目复用仍是产品决策，不由本文假定。

### 2.3 页面所有权与禁止越界

| 页面 | 所有对象与主动作 | 可读取的相邻摘要 | 明确不拥有 |
|---|---|---|---|
| P02 数据源 | 上传来源、连接器、凭据引用、接入方式 | 可提示目标项目/区域与接入健康 | 机器人型号、组件、标定、采集任务、一次上传执行 |
| P03 上传记录 | upload session / data package 接收执行、状态、校验、Manifest 实例入口 | 机器人/型号/标定/Schema 的只读引用摘要 | P14 资产源、P20 任务定义、Registry 版本编辑 |
| P14 型号资产 | RobotDescription、型号版本、URDF/Mesh 引用与哈希、能力声明、兼容验证 | 被哪些实例/Manifest 引用 | P02/P03 的源配置与上传会话；通用业务文件上传 |
| P15 机器人与组件 | 实例、组件拓扑、型号绑定、Gateway/Adapter/部署方式、上报摘要 | 当前有效标定、契约兼容、P20 分派情况 | 实时遥操作、关节控制、急停、采集任务定义 |
| P16 标定 | 标定目标、版本、适用范围、有效性、验证和回退血缘 | 实例/组件、相关数据包引用 | 机器人实时求解、覆盖历史数据、P15 组件编辑 |
| P17 Registry | Channel/Topic/Schema/Manifest Profile 的定义、版本、兼容性 | 引用者与最近兼容检查 | 每个数据包的实际 Manifest 编辑、Topic 实时流量控制、P03 上传 |
| P20 采集任务 | 任务定义、8 位任务码、人员/PICO/机器人分派、有效期与多阶段进度 | P15 机器人是否可选/为何不可选 | P15 机器人目录编辑、P03 上传会话、100 Hz 控制 |

与 T03 已落盘规格的具体一致性：

- P20 以稳定 <code>collection_task_id</code> 定位，<code>task_code</code> 仅归类；P15 只返回可选择的机器人身份和原因，不重新定义任务码或分派状态（<code>plan/frontend-ux-parallel/T03-P01-P20-SPEC.md:132-145,328-336</code>）。
- P20 的人、PICO、机器人集合分别分派；P15 只提供机器人目录、区域、停用/维护、能力、标定/契约风险等摘要（同文档 <code>:413-436</code>）。
- P20 分别展示 SAVED、Received 和 QC；P15 的 online/last seen 不能替代这些数据包事实（同文档 <code>:451-481</code>）。
- P20→P03/P04 的关联以稳定任务/数据包/uploadId 为核心；P14–P17 的版本只作为 Manifest 引用，不接管上传执行（同文档 <code>:529-560</code>）。

## 3. 当前实现证据与漂移

### 3.1 总体支持结论

| 层 | 当前证据 | 结论 |
|---|---|---|
| 路由与导航 | P14–P17 均有设置页导航和 read capability gate（<code>frontend/src/app/shell/navigation-manifest.ts:80-83</code>，<code>frontend/src/app/router/index.tsx:111-114</code>）；各页 route 使用懒加载 | F：页面壳存在 |
| 前端领域类型 | 有 robot model/version、robot/component、calibration、data schema 类型，但字段只覆盖原型子集（<code>frontend/src/entities/robot-model.ts:1-50</code>、<code>robot.ts:1-31</code>、<code>component.ts:1-24</code>、<code>calibration.ts:1-35</code>、<code>data-schema.ts:1-37</code>） | F：草案形状存在，不是正式合同 |
| 前端 API | 四域均以手写 strict Zod schema 与请求函数实现；生成脚本把 robotics 指向外部 draft，而不是本仓正式后端（<code>frontend/scripts/generate-api-client.mjs:8-23</code>） | M：不可声称正式 API |
| Browser Mock | robotics handler 只有四个列表 GET（<code>frontend/src/mocks/handlers/robotics.handlers.ts:19-24</code>）；各只有一条 fixture（<code>frontend/src/mocks/fixtures/management/index.ts:43-97</code>） | M：详情、组件、写动作与错误矩阵没有 Mock 闭环 |
| Mock 场景 | management 只注册 happy/forbidden/contract-mismatch；全局要求的 loading/empty/offline 等没有本域响应实现（<code>frontend/src/mocks/scenarios/management.ts:6-30</code> 与 required scenario 目录） | M：状态展示主要由页面本地降级拼装 |
| 当前测试 | 审计时前端共有 10 个 test/spec 文件，P14–P17 专属测试为 0；<code>frontend/docs/frontend-scaffold-notes.md:99-101</code> 也说明历史测试已归档 | F：不得引用历史“114 tests/Playwright”当当前证据 |
| 状态文档漂移 | T7 曾记录 P14–P17 contract/E2E 已完成，并记录 23 files/114 tests、38 Playwright（<code>frontend/docs/status/T7.md:3-16,28-35</code>） | 历史记录；本次实际 Vitest 为 10 files/32 tests，P14–P17 专属为 0，当前也不能以文档证明 E2E |
| 正式 OpenAPI | <code>backend/openapi.generated.yaml</code> 有 44 个 path；没有 robot model/instance/component/calibration/registry/Gateway/Adapter 路径 | F：正式合同缺失 |
| DB | Ingest 仅把 <code>robot_id</code> 作为 collection job/rollout 的不透明字段（<code>backend/migrations/ingest/001_ingest.sql:3-35</code>）；Lance <code>schema_snapshots</code> 是数据集快照，不是 P17 Registry（<code>backend/migrations/lance_catalog/0001_lance_catalog.sql:2-13</code>） | F：无 P14–P17 领域表/FK/版本血缘 |
| Worker/Workflow | 当前 activities 只覆盖校验、QC、对齐、提交、预览、发布、导出、对账（<code>backend/src/hc_data_platform/workflow/activities.py:485-495</code>）；workflow 列表也没有四域任务（<code>backend/src/hc_data_platform/workflow/temporal_workflows.py:547-555</code>） | F：不存在异步验证/发布闭环 |
| Runtime wiring | RuntimeComponents 只装配 ingest/annotation/catalog/verification/quality/alignment/preview/publish/export（<code>backend/src/hc_data_platform/runtime.py:173-184,315-326</code>） | F：没有 P14–P17 service/repository |
| 既有审计 | 真实 API 覆盖审计把 P14–P17 评为 E、Mock-only、无 backend/DB/worker（<code>plan/P01-P19-REAL-API-COVERAGE-AUDIT.md:50-53,162-164</code>） | 与本次静态复核一致 |

<code>backend/src/hc_data_platform/verification/engine.py:218,587</code> 的 <code>data_schema_ids</code> 是 MCAP 内部 schema 标识集合，不能据名字推断为 P17 的版本化 Schema Registry。

### 3.2 P14 当前代码/Mock/API/测试与文档漂移

| 方面 | 当前证据 | 漂移与 UX 判定 |
|---|---|---|
| 页面 | 列表、详情 Tab、版本表、文件选择、哈希预计算、发布预检和 3D 区存在（<code>frontend/src/pages/p14-robot-models/page.tsx:100-213,236-258,387-500</code>） | 页面把自己写成“一次性上传 URDF/Mesh”入口（<code>:236-238</code>），越过 P02/P03 数据源/上传边界；应改成资产登记与版本护照 |
| 类型 | ModelVersion 有 semanticVersion、hash、readiness、allowedActions（<code>frontend/src/entities/robot-model.ts:38-50</code>） | 没有 RobotDescription hash、资产清单/媒体类型/大小、能力声明、兼容矩阵、引用者 |
| API | list/detail、preflight、publish、upload session 函数均为手写草案（<code>frontend/src/features/robot-models/api/index.ts:79-189</code>） | Mock 只实现列表 GET；当前按钮不代表后端可用 |
| 哈希 | 浏览器对整个文件 <code>arrayBuffer()</code> 后算 SHA-256（<code>page.tsx:197-213</code>） | 大 Mesh 会占用主线程/内存；效果图必须先显示大小限制与异步计算，不承诺当前实现可扩展 |
| 3D | 页面渲染 RobotSceneCore 但 jointMapping 传空（<code>page.tsx:387-408</code>）；实际 lazy loader 只在 P08 配置，P14 未配置 | 当前 P14 无可验证的真实 URDF/Mesh runtime；效果图不得用成功 3D 预览证明 loader 已工作 |
| 长标识 | ID/hash 使用普通 <code>code</code>（<code>page.tsx:410-437</code>） | 已有共享 CopyableId 却未使用；缺复制、<code>translate=no</code> 与完整值展开 |
| 计划 | 页面计划明确 API 与上传/预检/发布均是 prototype draft（<code>plan/frontend/frontend-page-14-robot-model-assets-development-plan.md:3-5,23-36</code>） | 计划描述不能当现行服务事实 |
| 测试 | 计划列了期望 component/route/MSW/E2E（同文档 <code>:38-44</code>） | 当前未找到 P14 测试；“应有”与“已有”必须分开 |

### 3.3 P15 当前代码/Mock/API/测试与文档漂移

| 方面 | 当前证据 | 漂移与 UX 判定 |
|---|---|---|
| 页面 | 左列表、中组件树、右详情三栏和跨 P16/P17 链接存在（<code>frontend/src/pages/p15-robots/page.tsx:158-213,303-507</code>） | 结构方向可复用，但没有 Gateway、Adapter、部署模式、声明/观测能力和上报新鲜度 |
| 类型 | Robot 只有 model binding、lifecycle、connectivity、region、serial（<code>frontend/src/entities/robot.ts:11-31</code>） | <code>ONLINE/OFFLINE</code> 没有 observed_at/source/TTL，容易被误读为实时控制连接 |
| 组件 | Component/Relation/Tree 与拓扑约束存在（<code>frontend/src/entities/component.ts:1-24</code>，<code>frontend/src/features/robots/constraints.ts:1-128</code>） | 约束无当前专属测试；组件读取 Mock 未实现 |
| API | list/bootstrap/components/frames/channels 均有手写函数（<code>frontend/src/features/robots/api/index.ts:103-173</code>） | robotics handler 仅实现 robot 列表；详情三栏正常态大多无法由 Mock 证实 |
| 默认选择 | 页面可显示首项为 selected，但组件 query 依赖 URL robotId | 首次进入可能“已选中”却不加载组件；效果图要求 URL 与视觉选择一致 |
| 动作 | 编辑按钮禁用；没有控制按钮 | “无云控按钮”是正确边界；未来也只加目录/配置动作 |
| 计划 | P15 计划明确读 API Mock-backed draft、写能力 unavailable（<code>plan/frontend/frontend-page-15-robots-components-development-plan.md:3-5,25-36</code>） | Gateway/Adapter 等字段应先进入合同决策 |
| 测试 | 计划列期望测试，当前 P15 专属测试为 0 | 不能把树能渲染当拓扑/冲突/权限已覆盖 |

### 3.4 P16 当前代码/Mock/API/测试与文档漂移

| 方面 | 当前证据 | 漂移与 UX 判定 |
|---|---|---|
| 页面 | 列表、变换/协方差/适用对象、预检/发布 UI 存在（<code>frontend/src/pages/p16-calibrations/page.tsx:160-195,289-319,399-516</code>） | 缺目标类型、适用实例集合、valid_from/to、验证证据身份、回退关系和冲突解释 |
| 假精度 | translation、quaternion 与 36 项 covariance 是页面硬编码（<code>page.tsx:67-103,126-132</code>） | 必须删除“像真实测量值”的假数据；合同缺失时显示字段未提供 |
| 假绑定 | 选中行拼出 <code>component-unbound</code>，详情拼 <code>world→base_link→component</code>（<code>page.tsx:164,419</code>） | 未有事实源，不能在效果图正常态中冒充后端关系 |
| 类型/API | CalibrationSet 有 transform、covariance、status、availability、hash、validation；list/detail/preflight/publish 为手写草案（<code>frontend/src/entities/calibration.ts:1-35</code>，<code>frontend/src/features/calibrations/api/index.ts:1-137</code>） | 无 validity/rollback，Mock 只实现列表 |
| Tab 命名 | tabs 把 intrinsics 标成坐标变换、time 标成协方差、joint 标成适用对象（<code>page.tsx:34-40</code>） | 语义与对象类型混淆；应改为概览/参数/适用范围/验证/血缘 |
| 计划/测试 | 计划写明 Mock read、candidate writes unavailable（<code>plan/frontend/frontend-page-16-calibration-management-development-plan.md:25-36</code>）；当前专属测试为 0 | 不得声称发布状态机已验证 |

### 3.5 P17 当前代码/Mock/API/测试与文档漂移

| 方面 | 当前证据 | 漂移与 UX 判定 |
|---|---|---|
| 页面对象 | 只有 Schema 列表/版本/兼容/JSON；顶部分类为本地常量（<code>frontend/src/pages/p17-data-schemas/page.tsx:42-59,407-418,482-559</code>） | Channel、Manifest、Topic Registry 均未实现；分类点击也没有实际过滤数据 |
| 兼容证据 | baselineHash 被设置为 targetHash，ruleset 使用 canonicalization version（<code>page.tsx:135-145</code>） | 同值自比不能证明兼容；无证据就必须显示“未检查/证据不可用” |
| 发布证据 | preflight 将 schema/version ID 硬拼为 validation/compatibility evidence ID（<code>page.tsx:232-233</code>） | 伪造证据身份；应由后端返回不可变 evidence ref |
| 动作文案 | “新建 Schema”按钮实际调用现有版本发布预检（<code>page.tsx:297-304</code>） | 高严重度动作/文案错配；拆成“新建定义/导入草稿”和“验证并发布版本” |
| JSON | 直接 <code>JSON.stringify</code> 到 <code>pre</code>（<code>page.tsx:556-559</code>） | 无折叠、搜索、节点复制、大小上限和延迟加载；大 JSON 可能阻塞 |
| Fixture | compatibility_result 写成 <code>COMPATIBLE</code>（<code>frontend/src/mocks/fixtures/management/index.ts:82-97</code>），adapter 只接受 BACKWARD/FORWARD/FULL/NONE | 当前 happy fixture 会降级为 UNKNOWN；Mock 与手写合同已漂移 |
| 类型/API | data-schema 类型和 list/detail/route/preflight/publish 存在（<code>frontend/src/entities/data-schema.ts:1-37</code>，<code>frontend/src/features/data-schemas/api/index.ts:1-160</code>） | 只是 Schema 子域草案，不能代表完整 Registry |
| 计划/测试 | 计划明确 read Mock、preflight/publish candidate unavailable（<code>plan/frontend/frontend-page-17-data-schema-development-plan.md:25-36</code>）；当前专属测试为 0 | 兼容矩阵、冲突和 JSON 体验仍需正式测试设计 |

## 4. 跨页信息架构与渐进披露

### 4.1 共同页面骨架

四页使用相同顺序，不按角色复制页面：

1. 标题、对象解释、scope 与单一主动作；
2. 业务摘要：数量、可用性、风险和最近更新时间，未知不显示为 0；
3. URL 可复现的搜索/过滤/排序；
4. 目录列表；
5. 选中对象详情；
6. 技术信息按需展开；
7. 引用者、版本血缘与审计。

桌面端 1440 可使用列表 4/12 + 详情 8/12；1280 改为列表 5/12 + 摘要 7/12，技术检查器进入抽屉或下方全宽。页面本体不得横向滚动；只有表格/代码区可以在自身容器内滚动，并保留固定的“名称/ID”与“动作”列。

Master 的“信号轨道”只用于 P01/P03/P04/P06/P07/P20 的真实链路（<code>design-system/hc-data-platform/MASTER.md:16-44</code>）。P14–P17 不复制第二条装饰轨道；本组的识别特征是紧凑的“对象护照”：名称、不可变版本、完整可复制身份、引用与证据始终在同一详情头。

### 4.2 四级渐进披露

| 层级 | 默认状态 | 内容 | 设计要求 |
|---|---|---|---|
| L0 目录摘要 | 默认可见 | 业务名称、状态、当前版本、适用范围、风险、最后更新 | 不出现整块 JSON；内部枚举先本地化 |
| L1 对象详情 | 选中后可见 | 字段分组、关系、允许动作、引用者 | 业务标签为主，技术 ID 次级 |
| L2 技术详情 | 用户主动展开 | 完整 ID/hash、原始 enum、Adapter/Gateway/Schema 版本、验证规则 | 使用 monospace、<code>translate=no</code>、逐项复制 |
| L3 原始证据 | 默认折叠 | JSON、校验报告、差异 patch、资产 manifest | 延迟加载；可搜索/折叠/复制节点与原文；大对象显示大小和截断策略 |

“业务摘要”不能只是把技术字段换中文：它要回答“这是什么、现在能不能用、为什么、谁在引用、下一步是什么”。“技术详情”不能靠 hover 才能读取。

### 4.3 长 ID、哈希、枚举与 JSON

- 名称永远是主标识；ID/hash 使用 12–16 字符首尾摘要，例如 <code>sha256:3a74c9…9e52</code>。
- 摘要旁提供显式“复制完整值”图标按钮，按钮有 aria-label，成功反馈进入 <code>aria-live</code>；完整值可在展开区自动换行。
- 完整 ID/hash 使用 monospace 与 <code>translate="no"</code>；复制值不得包含视觉省略号。
- 表格中长 token 单行省略，但焦点/点击展开后用 <code>overflow-wrap:anywhere</code>；不得以 <code>overflow:hidden</code> 永久剪掉唯一标识。
- 枚举默认显示“已发布”等业务文案；技术层并列原值 <code>PUBLISHED</code>。未知枚举显示“未知状态”，同时保留可复制原值，不映射成成功。
- JSON 默认树视图，显示节点数/字节数/Schema 版本；提供“展开到 2 层、全部折叠、搜索、复制节点路径、复制值、复制原始 JSON”。
- 超过合同阈值的 JSON 不自动 stringify/全量渲染；服务端分页/切片或 Worker parse 的阈值由技术确认。加载失败保留业务摘要，不让整个详情失败。

## 5. P14 机器人型号资产

### 5.1 用户任务与信息架构

用户进入 P14 要在 10 秒内回答：

- 这个型号当前推荐哪个不可变版本？
- RobotDescription、URDF/Mesh 与能力声明是否齐全、哈希是否匹配？
- 哪些机器人实例和 Manifest 正在引用它？
- 新版本相对基线有什么变化，能否发布？

信息架构：

| 区域 | 默认信息 | 展开信息/动作 |
|---|---|---|
| 列表 | 型号名、厂商/型号代码、当前发布版本、readiness、实例数、风险 | 搜索厂商/名称/代码；状态/能力过滤；URL 持久化 |
| 护照头 | model ID、version、状态、RobotDescription hash、更新时间 | 复制完整值、查看引用、弃用候选 |
| 资产清单 | URDF、Mesh bundle、配置 manifest 的名称/媒体类型/大小/hash/状态 | 查看元数据与校验报告；不在 P14 充当通用源上传 |
| 能力声明 | embodiment、可控部位、DOF/关节、录制/视频/标定、所需 Adapter | 展开原始 capability JSON |
| 结构预览 | 帧/关节树摘要；兼容且 loader 可用时才出现 3D | 3D 是辅助，不是发布证据；失败时仍可查看清单 |
| 版本与兼容 | 基线、目标、变更摘要、验证状态、引用影响 | 验证并发布、查看不可变 evidence |

P14 的“登记新版本”使用独立的机器人资产登记/受控资产服务；是否在版本表单中选择已有 managed object，或允许直接附加 URDF/Mesh，属于待确认项。它不得复用 P02 连接器或 P03 数据包上传记录来假装资产接入已经解决。无论选择哪种方案，P14 都不能被描述为业务数据上传来源，且发布前必须以服务端内容哈希为准。

### 5.2 1440 线框

~~~text
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ 机器人型号资产  RobotDescription 与不可变资产版本                         [登记新版本] │
│ 当前项目 / 华东-1 · 目录截至 10:32 · 此处不是数据上传入口                              │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ [搜索名称/厂商/代码…] [状态⌄] [能力⌄] [就绪性⌄] [重置]                                 │
├──────────────────────────────┬───────────────────────────────────────────────────────────┤
│ 型号 4/12                    │ Atlas-DualArm / v2.4.1 [已发布]                 8/12      │
│ ● Atlas-DualArm              │ model …a813 [复制]  version …91fe [复制]  引用实例 12    │
│   ACME · AD-20 · v2.4.1      │ RobotDescription sha256:3a74c9…9e52 [复制完整值]         │
│   就绪 · 12 个实例           ├───────────────────────────────────────────────────────────┤
│ ○ Flex-7                     │ [概览] [资产] [能力] [结构] [版本/兼容] [引用]           │
│   待补 Mesh · 3 个实例       │ 业务摘要：双臂固定基座 · 14 DOF · 需要 adapter ≥2.3      │
│                              │ 声明：遥操作✓ 录制✓ 视频2路 标定必需✓  最近验证 10:18   │
│                              ├───────────────────────────────────────────────────────────┤
│                              │ 资产清单                                                   │
│                              │ 类型  文件/引用           大小   哈希摘要          状态    │
│                              │ URDF  robot.urdf          84KB  8c10…60bd [复制]  已验证  │
│                              │ Mesh  visual.bundle      42MB  f320…9ad1 [复制]  已验证  │
│                              │              [表格内部横滚：固定类型/状态与行尾详情]      │
│                              ├───────────────────────────────────────────────────────────┤
│                              │ 技术详情 ▸  完整描述 JSON / joint tree / 校验报告          │
└──────────────────────────────┴───────────────────────────────────────────────────────────┘
~~~

### 5.3 1280 线框

~~~text
┌──────────────────────────────────────────────────────────────────────────────┐
│ 机器人型号资产                         [搜索型号…] [筛选⌄] [登记新版本]      │
├───────────────────────────────┬──────────────────────────────────────────────┤
│ 型号列表 5/12                 │ Atlas-DualArm / v2.4.1 [已发布]      7/12   │
│ 名称 · 当前版 · 风险          │ 14 DOF · 12实例 · 就绪                      │
│ Atlas-DualArm v2.4.1 就绪     │ Description 3a74…9e52 [复制]                │
│ Flex-7 v1.8 缺Mesh            │ [概览] [资产] [能力] [更多⌄]                │
├───────────────────────────────┴──────────────────────────────────────────────┤
│ 资产表（自身横滚；名称/状态固定）                                            │
│ URDF robot.urdf 84KB 8c10…60bd 已验证 │ Mesh visual.bundle 42MB …          │
├──────────────────────────────────────────────────────────────────────────────┤
│ 业务影响：12 实例引用 · 3 Manifest Profile 允许此版本   [查看引用]           │
│ [打开技术抽屉：完整 hash / 原始 enum / JSON / 结构预览]                      │
└──────────────────────────────────────────────────────────────────────────────┘
~~~

1280 时不保留常驻 3D 右栏；“结构”进入全宽区域/抽屉。长哈希不撑宽页面，资产表只在自身横滚。3D/技术 JSON 和列表不能同时抢占主线程。

### 5.4 P14 字段与动作证据状态

| 字段/动作 | 状态 | 说明 |
|---|---|---|
| model ID、display name、manufacturer、model code、current version | F/M | 当前实体与列表 fixture 有 |
| version ID、semantic version、description hash、readiness、allowed actions | F/M | 前端草案有，正式后端无 |
| RobotDescription hash、资产列表/大小/media type、能力声明、引用计数 | D/U | 方向要求；字段合同未定 |
| Adapter 最低版本、控制部署兼容、frame/joint declaration | D/U | 方向明确对象关系，精确字段需技术确认 |
| 3D 预览 | F（壳）/未核验 runtime | 不能作为当前成功能力 |
| 登记/验证/发布/弃用 | U/C | capability 名称存在，但正式写 API、状态机和 evidence 缺失 |
| 管理型资产登记 | C | 独立于 P02/P03 数据包 ingest；选择 managed asset ref 或本页受控附加文件仍待产品/存储合同确认 |
| 下载资产/校验报告 | reserved capability 草案 | <code>robot_model.asset.download</code> 与 validation report 在 reserved 列表，不是当前可授权 capability（<code>frontend/src/entities/capability.ts:85-103</code>） |

## 6. P15 机器人实例、组件、Gateway 与 Adapter

### 6.1 用户任务与信息架构

P15 回答“这台真实机器人是什么、装了什么、运行哪套适配、最后报告了什么、为什么能/不能被 P20 选用”。它不回答“现在如何操控它”。

| 区域 | 默认信息 | 展开信息/动作 |
|---|---|---|
| 实例列表 | 名称/序列、区域、型号版本、生命周期、最近上报摘要、风险数 | 名称/序列/ID 搜索，型号/状态/部署方式/能力过滤 |
| 护照头 | robot ID、effective model version、Gateway/Adapter、部署模式、last observed | 复制 ID、查看审计、目录编辑 |
| 组件树 | base、arm、camera、gripper 等 mount 关系和状态 | frame/channel、mount 历史；语义 tree keyboard |
| 能力 | declared、reported、required 三列差异 | 缺失原因、来源、观测时间；不提供控制 |
| 标定与契约 | 当前有效标定、P17 兼容摘要、可被 P20 选择的原因 | 深链 P16/P17；P20 分派只读引用 |
| 运营状态 | online/offline/unknown、Gateway heartbeat age、Adapter report | 只作为陈旧度摘要；不能承诺实时连接 |

必须区分：

- Declared：P14 型号版本声明“理论支持”；
- Configured：P15 实例绑定了哪些 Gateway/Adapter/组件；
- Observed：Gateway 最近一次实际上报了什么，带 <code>observed_at</code> 与 source；
- Required：P20 场景或 P17 契约要求什么；
- Eligible：综合生命周期、区域、能力、标定与契约后的“可选性”结论，必须返回原因列表。

### 6.2 1440 线框

~~~text
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ 机器人与组件  实例目录、拓扑与上报摘要                                     [登记机器人] │
│ 不是实时控制台；状态最后上报 10:31:42（34 秒前）                                       │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ [搜索名称/序列/ID…] [型号⌄] [生命周期⌄] [部署方式⌄] [能力⌄] [重置]                    │
├──────────────────────┬───────────────────────────┬───────────────────────────────────────┤
│ 实例 3/12            │ 组件树 4/12               │ Robot-06 [在用] [最近上报在线] 5/12 │
│ ● Robot-06           │ ▾ base                     │ robot …e84a [复制] · 华东-1           │
│ Atlas v2.4.1         │   ▾ left_arm [正常]        │ 型号 Atlas v2.4.1 [查看 P14]          │
│ 34秒前 · 1项风险     │     camera_left [正常]     │ Gateway gw-robot 1.9.0                │
│ ○ Robot-03           │     gripper_l [标定过期]   │ Adapter atlas-dual 2.3.4              │
│ 离线 · 2天前         │   ▾ right_arm              │ 部署：机器人边缘工控机                 │
│                      │     camera_right            │                                        │
│                      │                             │ 能力差异                               │
│                      │                             │ 声明 双臂/2视频/录制                   │
│                      │                             │ 上报 双臂/1视频/录制  !缺 camera_right │
│                      │                             │ 观测时间 10:31:42 · source gateway     │
│                      │                             ├───────────────────────────────────────┤
│                      │                             │ 标定：1 项过期 [去 P16]                │
│                      │                             │ 契约：Manifest teleop-v3 不兼容 [P17]  │
│                      │                             │ P20 可选性：不可选 · 2 个明确原因       │
└──────────────────────┴───────────────────────────┴───────────────────────────────────────┘
~~~

### 6.3 1280 线框

~~~text
┌──────────────────────────────────────────────────────────────────────────────┐
│ 机器人与组件                 [搜索机器人…] [筛选⌄] [登记机器人]             │
├───────────────────────────┬──────────────────────────────────────────────────┤
│ 实例列表 5/12             │ Robot-06 · Atlas v2.4.1 · 在用          7/12    │
│ Robot-06 34秒前 1风险     │ Gateway 1.9.0 · Adapter 2.3.4 · 边缘部署         │
│ Robot-03 离线2天 2风险    │ 最近上报 10:31:42（source: gateway）             │
│                           │ [概览] [组件] [能力] [标定/契约] [审计]           │
├───────────────────────────┴──────────────────────────────────────────────────┤
│ 组件树（全宽，选择后在右侧抽屉显示 frame/channel/完整 ID）                   │
│ ▾ base  ├─ left_arm ─ camera_left  └─ right_arm ─ camera_right [缺失上报]   │
├──────────────────────────────────────────────────────────────────────────────┤
│ 可选性：不可用于“全身遥操作 v3”                                              │
│ ① camera_right 能力缺失  ② hand-eye 标定已过期        [P16] [P17]           │
└──────────────────────────────────────────────────────────────────────────────┘
~~~

三栏布局在 1280 shell 内容宽度不足时必须降为两栏+全宽组件区，不能依靠固定 min-width 把页面裁掉。

### 6.4 P15 字段与动作证据状态

| 字段/动作 | 状态 | 说明 |
|---|---|---|
| robot ID/name/serial/region/lifecycle/connectivity/model binding | F/M | 当前前端实体与列表 fixture |
| component tree、relation、frame/channel ref | F/M（API 草案） | Mock 未实现详情；正式后端无 |
| Gateway 名称/版本、Adapter 名称/版本 | D/U | 方向文档明确，字段/上报接口待定 |
| <code>control_deployment_mode</code> | D/U | 方向建议写入质量/Manifest，枚举和展示文案待确认 |
| declared/configured/observed/required capability | U | 推荐的数据层次；不能压成一个布尔值 |
| observed_at/source/TTL/last heartbeat | U/C | 必须避免“在线=实时可控”；TTL 由设备/技术确认 |
| P20 eligible + reason codes | U/C | T03 分派所需投影；由后端计算，前端不自行拼规则 |
| 登记/更新/停用实例、组件挂载变更 | capability 草案 + U | 当前 canonical capability 有 robot.* 和 robot_component.*（<code>frontend/src/entities/capability.ts:52-59</code>），正式合同缺失 |
| 云端连接/移动/回零/开始遥操作/急停 | 禁止 | 不属于 P15；硬件安全不能被云按钮替代 |

## 7. P16 标定管理

### 7.1 对象模型与页面结构

P16 管理的是“一个标定目标的不可变版本及其适用性”，不是一张静态 4×4 矩阵。

推荐对象层次（C，需合同确认）：

~~~text
Calibration Definition
  target_type: frame_transform | camera_intrinsics | hand_eye | time_sync | joint_offset | ...
  source_ref / target_ref / method
          │
          └─ Calibration Version（不可变）
               parameters + units + covariance/error summary + content hash
               applicable_to: explicit instance/component refs 或批准的 selector
               valid_from / valid_to
               verification evidence + result + measured_at
               supersedes / rollback_from / revoked_by
~~~

列表默认按“标定目标”而非每个矩阵行展示；详情按概览、参数、适用范围、验证、血缘/审计分 Tab。参数结构根据 target_type 变化，禁止用 intrinsics/time/joint 名字错配一个固定 transform 面板。

### 7.2 1440 线框

~~~text
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ 标定管理  版本、适用对象、有效期与验证证据                               [新建标定草稿] │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ [搜索名称/实例/组件…] [类型⌄] [有效性⌄] [验证结果⌄] [到期≤30天□] [重置]               │
├──────────────────────────────┬───────────────────────────────────────────────────────────┤
│ 标定目标 4/12                │ Robot-06 / camera_left hand-eye · v7 [有效]       8/12   │
│ ● camera_left → left_tool    │ calibration …a18c [复制] · version …7e21 [复制]           │
│   Robot-06 · 有效 · 18天后到期│ 适用：Robot-06 / camera_left；2026-06-01—09-04 CST       │
│ ○ base → world               │ 验证：通过 · 误差 0.84 mm · evidence …19bd [复制]         │
│   3实例 · 已过期             ├───────────────────────────────────────────────────────────┤
│ ○ camera_right intrinsics    │ [概览] [参数] [适用范围] [验证] [血缘/审计]              │
│   验证失败                   │ 业务摘要：当前被 14 个数据包引用；2 个未来任务将跨到期日  │
│                              │                                                           │
│                              │ 参数摘要（单位不可省略）                                  │
│                              │ translation  x 12.4 mm · y −2.1 mm · z 33.0 mm            │
│                              │ rotation     quaternion · normalized                      │
│                              │ 不确定度     0.84 mm / 0.12°                              │
│                              │ [展开完整矩阵与原始参数 JSON]                              │
│                              ├───────────────────────────────────────────────────────────┤
│                              │ 血缘：v7 supersedes v6；“回退到 v6”将创建新版本 v8（候选）│
│                              │ [重新验证] [创建后继版本] [查看引用]                       │
└──────────────────────────────┴───────────────────────────────────────────────────────────┘
~~~

效果图中的数值必须标“示意数据”，真实页面只显示后端返回值。缺参数时写“该版本未提供 translation”，绝不回退硬编码矩阵。

### 7.3 1280 线框

~~~text
┌──────────────────────────────────────────────────────────────────────────────┐
│ 标定管理                       [搜索标定…] [筛选⌄] [新建标定草稿]            │
├───────────────────────────────┬──────────────────────────────────────────────┤
│ 标定目标 5/12                 │ camera_left hand-eye · v7 [有效]      7/12  │
│ Robot-06/camera_left 18天到期 │ Robot-06 · 2026-06-01—09-04 CST              │
│ base→world 已过期             │ 验证通过 · 0.84mm · evidence …19bd [复制]    │
│ camera_right 验证失败         │ [概览] [参数] [范围] [验证] [更多⌄]          │
├───────────────────────────────┴──────────────────────────────────────────────┤
│ 适用与影响：1 实例 · 1 组件 · 14 数据包引用 · 2 个任务跨到期日             │
│ 参数摘要；完整矩阵/JSON 进入技术抽屉，不压缩到 10px                         │
├──────────────────────────────────────────────────────────────────────────────┤
│ ! 若新版本与同一目标/实例的生效区间重叠，显示冲突对象和区间，不只写“失败”    │
└──────────────────────────────────────────────────────────────────────────────┘
~~~

### 7.4 生命周期、冲突与回退

当前类型只有 DRAFT/READY 与 availability 的 available/unavailable/pending 等草案值，不能直接支撑完整产品生命周期。效果图采用以下候选语义，不要求后端照抄枚举：

~~~text
[DRAFT] --提交验证--> [VALIDATING] --通过--> [VERIFIED] --激活/到生效时间--> [ACTIVE]
   │                       │失败                  │                         │
   └────────修改───────────┘                    └─被新版本取代──► [SUPERSEDED]
                                                                     │
ACTIVE --到 valid_to--> [EXPIRED]          ACTIVE/VERIFIED --撤销--> [REVOKED]

回退不是原地把旧版本改回 ACTIVE：
选择历史版本 + 填原因 + 重验影响 → 创建新的不可变后继版本，并记录 rollback_from。
~~~

冲突至少分开：

- applicability conflict：两个候选版本覆盖同一实例/组件/目标；
- validity overlap：生效时间区间重叠；
- reference missing：frame/component/model version 不存在或已停用；
- evidence stale：验证证据早于资产/模型/Adapter 变更；
- capability missing：目标实例没有标定所需传感器/帧；
- contract mismatch：参数结构或单位不符合 P17 Schema。

精确状态机、重叠是否允许、选择优先级、撤销对历史数据的影响，需要产品决策；前端不得用“最后发布者获胜”自行解决。

### 7.5 P16 字段与动作证据状态

| 字段/动作 | 状态 | 说明 |
|---|---|---|
| calibration/version/robot/component ID、transform、covariance、hash、validation | F/M | 当前实体有，fixture 仅一条 |
| target_type、source/target frame、units/method | D/U | 方向需要模型与标定配置，合同未定 |
| applicable instances/components/selectors | U/C | 必需；推荐显式 ref 优先，selector 规则要可审计 |
| valid_from/to、measured_at、timezone | U/C | 必需；存 UTC、展示项目 IANA timezone 的政策需确认 |
| evidence ID、result、metrics、ruleset/version | U | 必须服务端返回，不得前端拼接 |
| supersedes/rollback_from/revoked_by | U/C | 推荐不可变后继关系 |
| 新建草稿/验证/发布 | capability 草案 + U | canonical 有 calibration create/read/validate/publish（<code>frontend/src/entities/capability.ts:20-23</code>），正式 API 缺失 |
| availability 管理、源/报告下载 | reserved 草案 | <code>frontend/src/entities/capability.ts:89-91</code>，当前不能按有效 capability 宣称 |

## 8. P17 Channel / Manifest / Topic / Schema Registry

### 8.1 四类对象不能混成“Schema 分类”

| 对象 | 业务问题 | 核心关系 | 不应包含 |
|---|---|---|---|
| Channel Definition | “这路业务信号是什么？” | 语义名、direction、rate expectation、unit/frame、Schema ref | broker 凭据、实时消息、数据包实例 |
| Topic Binding | “在某协议/Adapter 上如何承载这路 Channel？” | protocol、topic/path、encoding、QoS 候选、channel/schema ref、适用 Adapter | 实时订阅控制、secret、生产 endpoint |
| Schema | “消息结构与兼容规则是什么？” | immutable versions、definition/hash、compatibility policy/evidence | 运行数据样本、前端自算假兼容 |
| Manifest Profile | “一个合规数据包/采集形态必须包含什么？” | required/optional channels/topics、pinned schema ranges/versions、校验规则 | P03 每个 data_package 的实际 Manifest 原文编辑 |

实际数据包 Manifest 由机器人 Recorder/上传链路产生并由 P03/P04 检查；P17 存的是可复用的定义/模板/版本及兼容策略。这一命名和范围必须由产品确认，避免把 <code>EpisodeManifest</code>、上传 Manifest 实例与 Registry Profile 混为一个对象。

方向依据：

- 数据包 Manifest 应记录任务、机器人、型号/Adapter/PICO/session/request、文件与哈希等引用（<code>PICO 跨本体遥操作与机器人端录制方案.md:391-410</code>）。
- 上传需以 Manifest、CRC/SHA 和 final marker 等校验完整性，required Topic 缺失会进入质量门禁（<code>数据平台 评测.md:121-168,171-224</code>）。
- 发布训练数据需要冻结的 Manifest 与不可变血缘（同文档 <code>:726-739</code>）。

### 8.2 页面信息架构

顶层使用对象类型切换，不把它伪装为数据过滤：

~~~text
[Channel] [Manifest Profile] [Topic Binding] [Schema]
     └─ 各自目录 → 定义详情 → 版本列表 → 兼容性/引用 → 原始技术内容
~~~

业务摘要默认回答：

- 谁在用、最新发布版是什么；
- 是 backward/forward/full/none 中哪种兼容政策，还是尚未检查；
- 新版本会影响哪些 P14/P15/P16、P20/上传 Manifest 或数据集；
- 哪些 required channel/topic/schema 缺失或冲突。

技术 JSON 只有在用户展开时加载。JSON 正常渲染不是发布证据；发布必须引用服务端的校验与兼容 evidence。

### 8.3 1440 线框

~~~text
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ 数据契约 Registry  Channel、Manifest、Topic 与 Schema 的版本/兼容                 [新建] │
│ [Channel 18] [Manifest 6] [Topic 24] [Schema 31] · 目录截至 10:32                       │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ [搜索名称/namespace/ID…] [状态⌄] [兼容策略⌄] [引用域⌄] [重置]                         │
├──────────────────────────────┬───────────────────────────────────────────────────────────┤
│ Schema 目录 4/12             │ robot.teleop.frame / v3.2.0 [已发布]             8/12   │
│ ● robot.teleop.frame         │ schema …b18c [复制] · version …d923 [复制]                │
│   v3.2.0 · BACKWARD          │ 内容 sha256:70c8…f193 [复制] · policy BACKWARD             │
│   12 引用 · 0 冲突           ├───────────────────────────────────────────────────────────┤
│ ○ camera.frame              │ [业务摘要] [字段] [版本] [兼容性] [引用] [技术 JSON]     │
│   v2.0.0 · 未检查            │ 业务摘要：遥操作统一输入；PICO producer / Gateway consumer │
│ ○ package.manifest          │ 变化：新增 optional field /trigger_pressure                │
│   v5 · 2 冲突               │ 影响：4 Channel · 3 Topic · 2 Manifest Profile             │
│                              │                                                           │
│                              │ 兼容证据：通过 · ruleset compat-2026.08 · evidence …90ab  │
│                              │ 基线 v3.1.2  → 目标 v3.2.0           [查看差异] [复制证据] │
│                              ├───────────────────────────────────────────────────────────┤
│                              │ 技术 JSON ▸  18.4 KB · 142 nodes · 默认折叠至 2 层        │
│                              │ 展开后：[搜索路径…] [复制节点] [复制原文] [换行⌄]         │
└──────────────────────────────┴───────────────────────────────────────────────────────────┘
~~~

### 8.4 1280 线框

~~~text
┌──────────────────────────────────────────────────────────────────────────────┐
│ 数据契约 Registry               [Channel] [Manifest] [Topic] [Schema] [新建] │
│ [搜索名称/ID…] [状态⌄] [兼容⌄]                                             │
├───────────────────────────────┬──────────────────────────────────────────────┤
│ 目录 5/12                     │ robot.teleop.frame v3.2.0 [已发布]     7/12 │
│ teleop.frame BACKWARD 0冲突   │ 12引用 · sha256:70c8…f193 [复制]             │
│ camera.frame 未检查           │ [摘要] [版本] [兼容] [引用] [JSON]           │
│ package.manifest 2冲突        │ 变化：新增 optional field /trigger_pressure  │
├───────────────────────────────┴──────────────────────────────────────────────┤
│ 兼容：基线 v3.1.2 → 目标 v3.2.0 · evidence …90ab [复制] [查看差异]          │
├──────────────────────────────────────────────────────────────────────────────┤
│ 技术 JSON（按需加载的全宽区域）                                              │
│ 路径/值长表只在自身横滚；原始 JSON 可换行，完整 token 用 anywhere            │
└──────────────────────────────────────────────────────────────────────────────┘
~~~

### 8.5 兼容性、冲突与发布

兼容结果要分清“政策”和“某次检查结果”：

| 项 | 示例 | 要求 |
|---|---|---|
| compatibility policy | BACKWARD | 定义批准的兼容目标，不等于已检查 |
| baseline / target | v3.1.2 → v3.2.0 | 必须是两个真实不可变版本，不能同哈希自比 |
| ruleset + version | compat-2026.08 | 可追溯 |
| result | COMPATIBLE / INCOMPATIBLE / UNKNOWN | UNKNOWN 不能显示成功色 |
| findings | removed required field、unit changed | 业务摘要 + JSON Pointer/路径 |
| evidence ID/hash | immutable ref | 后端生成、可复制、可审计 |
| affected refs | channels/topics/manifests/instances | 显示阻塞或警告规则 |

Schema conflict 包括同 identity/version 不同 hash、命名空间冲突、required 字段破坏、类型/单位/语义变化、循环引用或 evidence 过期。Topic conflict 还包括同适用范围内 topic/path 重复、direction/encoding 不匹配；Manifest conflict 包括 required 依赖缺失和版本范围无解。具体规则必须由 Registry 合同 Owner 确认。

推荐发布流程（U/C）：

~~~text
保存草稿 → 服务端 canonicalize/hash → 验证结构 → 选择真实基线
→ 运行兼容检查 → 展示影响与 evidence → 二次确认 → 发布不可变版本
~~~

禁止：

- “新建 Schema”按钮调用发布现有版本；
- 前端把 targetHash 同时当 baselineHash；
- 用 schema/version ID 拼一个看似 evidence 的 ID；
- 缺后端时显示 fixture JSON 和“兼容通过”；
- 在 Registry 保存 broker secret、签名 URL 或生产连接凭据。

### 8.6 P17 字段与动作证据状态

| 字段/动作 | 状态 | 说明 |
|---|---|---|
| Schema id/name/kind/status/version/hash/definition | F/M | 当前前端草案与单条 fixture |
| Schema list/detail/route/preflight/publish | M | 手写 API；Mock 仅 list，正式后端无 |
| Channel/Topic/Manifest Profile 对象 | D/U | 方向文档支持业务需要，具体模型/命名未批准 |
| compatibility policy/baseline/target/result | F/M + 漂移 | 当前部分字段存在但页面证据构造不可信 |
| ruleset/evidence/findings/affected refs | U | 必须正式服务端生成 |
| SemVer 与兼容策略 | C | 产品决策 PD22 尚未冻结；Manifest/Profile 是否适用 SemVer 也待定 |
| 新建/导入/验证/发布/弃用 | capability 草案 + U | canonical 有 data_schema create/import/read/validate/publish（<code>frontend/src/entities/capability.ts:29-33</code>）；deprecate 仍是 reserved（<code>:94</code>） |
| 查看实时 Topic 消息/控制订阅 | 禁止于 P17 | 属于诊断/运行工具，且需独立权限与脱敏设计 |

## 9. 权限、状态与异常矩阵

### 9.1 权限原则

导航 read gate 只决定是否显示入口，不是安全边界。正式 API 必须再次校验 organization/project/region/object scope。四页保持同一 IA，通过能力降级：

| 能力 | 有能力 | 无动作能力但可读 | 无读取能力 |
|---|---|---|---|
| read | 完整业务摘要与获准技术字段 | 同左 | 403/无权限状态，不泄漏对象数量、名称、ID/hash |
| create/update | 显示清晰动词与影响 | 不显示或明确只读；不能放一个无解释的灰按钮 | 不适用 |
| validate | 可发起并跟踪异步证据 | 可看已有 evidence，不可发起 | 不泄漏报告内容 |
| publish/deprecate/revoke | 在验证通过且 allowed_actions 返回时显示 | 可查看状态/影响 | 不适用 |
| download/raw JSON | 单独 capability + 审计 | 只显示业务摘要 | 不通过 DOM 或错误消息泄漏 |

前端 capability 目录本身仍是草案；尤其 reserved capabilities 不会被当前 <code>isCapability</code> 当 canonical 能力解析（<code>frontend/src/entities/capability.ts:85-112</code>）。效果图可以用语义动作，不得据此声明权限后端已就绪。

### 9.2 状态矩阵

| 状态 | 页面级行为 | 关键文案/动作 |
|---|---|---|
| 首次加载 | 保持列表/详情骨架，无假数字/hash/矩阵 | “正在读取目录…”；不抢焦点 |
| 空目录 | 说明是“当前筛选无结果”还是“尚未登记” | 有 create 能力才显示主动作；提供重置筛选 |
| 无权限 | 保留安全页面壳，不泄漏统计和对象存在性 | “你没有读取当前项目机器人资产的权限” |
| 只读 | 所有事实可读，写动作不显示；不是满页 disabled | “当前权限为只读” |
| 正式后端未开放 | 不发不存在请求，不回退 fixture | “该能力尚未接入正式服务”；无业务数字 |
| 局部后端失败 | 已成功区域保留；失败区显示 request id、last successful、局部重试 | 不把 error 显示为空 |
| offline/unknown | P15 显示 last observed/source/age；超过 TTL 标陈旧 | “最近上报离线/状态未知”，不是“已断开实时控制” |
| capability missing | 显示 declared/configured/observed/required 差异和 reason code | 深链 P14/P15；不提供“忽略并控制” |
| model/schema incompatible | 展示基线/目标、ruleset、findings、受影响引用 | 无 evidence 时显示“未检查”，禁止绿色 |
| calibration expired | 显示 valid_to、受影响实例/未来任务与替代版本 | “创建后继版本/重新验证”，不原地延长历史版本 |
| calibration overlap/conflict | 标出对象、target、相交区间和冲突版本 | 保存草稿可继续；激活/发布被阻塞 |
| schema identity/hash conflict | 显示双方来源、完整 hash 与 namespace | 要求改变版本/身份或由 Owner 解决，不静默覆盖 |
| unknown enum | 局部业务文案“未知状态”，技术层保留原值 | 页面其余部分继续 |
| version deleted/missing ref | 不能静默跳当前版 | 显示引用断裂、原 ID 与恢复/迁移责任人 |
| concurrent edit | 显示版本/etag 冲突与差异 | 刷新比较或复制为新草稿；不覆盖 |
| async validation pending | 保留输入与 job/evidence placeholder，允许离开后恢复 | URL/通知可回到同对象；不轮询全页 |

### 9.3 状态优先级

同一对象有多个问题时：

1. 无权限/对象不可见；
2. 引用断裂或后端不可用；
3. 发布/激活阻塞：不兼容、冲突、验证失败；
4. 运营风险：离线、过期、即将过期、能力缺失；
5. 信息性：草稿、已弃用、上报陈旧。

颜色不是唯一信号；每个阻塞都有文本原因、受影响对象和下一步。避免把整页涂红。

## 10. 效果图清单与验收

### 10.1 四张主效果图

| 编号/建议文件名 | 画布 | 场景 | 必须验证 |
|---|---|---|---|
| T09-M01-P14-model-passport.png | 1440×900 | P14 已发布型号版本 | 对象护照、资产哈希、能力声明、引用、非上传源文案、3D 非主证据 |
| T09-M02-P15-instance-topology.png | 1440×900 | P15 实例/组件/Gateway/Adapter | 三栏、declared/configured/observed/required、last observed、无云控按钮 |
| T09-M03-P16-calibration-validity.png | 1440×900 | P16 有效标定与即将到期影响 | 目标/版本/范围/有效期/验证/血缘、无硬编码假矩阵 |
| T09-M04-P17-registry-compatibility.png | 1440×900 | P17 Schema 兼容检查 | 四对象顶层 IA、业务摘要默认、真实 baseline/target/evidence、JSON 折叠 |

四张主图使用结构示意数据时必须显著标注“示意数据”；不能让示意 evidence/hash 看起来像当前系统返回。

### 10.2 响应式与关键详情/错误态图

| 编号/建议文件名 | 画布 | 场景 |
|---|---|---|
| T09-R01-P14-1280.png | 1280×800 | 列表+摘要、资产表内部横滚、3D/技术抽屉 |
| T09-R02-P15-1280.png | 1280×800 | 两栏+全宽组件树、无整页横滚 |
| T09-R03-P16-1280.png | 1280×800 | 参数下沉、适用影响与冲突可读 |
| T09-R04-P17-1280.png | 1280×800 | JSON 全宽按需区、长 token 处理 |
| T09-D01-P14-hash-mismatch.png | 1440×900 | URDF/Mesh 服务端 hash 不匹配、3D 不加载、查看校验报告 |
| T09-D02-P14-viewer-unavailable.png | 1440×900 | loader/资产不兼容但业务元数据仍可用 |
| T09-D03-P15-offline-capability-missing.png | 1440×900 | 状态陈旧 + required capability 缺失 + P20 不可选原因 |
| T09-D04-P15-permission-readonly.png | 1440×900 | 同 IA 只读，无组件编辑/目录写动作 |
| T09-D05-P16-expired-overlap.png | 1440×900 | 已过期与有效区间冲突并列，展示影响实例和任务 |
| T09-D06-P16-verification-failed.png | 1440×900 | 真实 evidence 摘要不可用/失败；无假矩阵 |
| T09-D07-P17-schema-conflict.png | 1440×900 | identity/version 同名不同 hash、发布被阻塞 |
| T09-D08-P17-json-large.png | 1440×900 | 大 JSON 延迟加载/搜索/复制/截断说明 |
| T09-D09-backend-unavailable.png | 1440×900 | 正式服务未开放；0 fixture 数字，四页统一诚实状态 |
| T09-D10-unknown-enum.png | 1280×800 | 未知枚举局部降级，保留原值且页面继续工作 |

### 10.3 效果图统一验收

- 严格复用 Master 的色彩、字体、间距、圆角、Ant Design 与 Lucide；不引入营销 Hero、渐变卡片墙、霓虹驾驶舱或第二套设计系统。
- P14–P17 不复制 P01/P03/P04/P06/P07/P20 的信号轨道作为装饰。
- 1440 为主评审；1280 无整页横滚，表格/JSON/长 token 只在自身区域滚动或换行。
- 正文字号不以 9–10px 换密度；技术信息也保持可读。
- 每个图标按钮有可见 tooltip 和可访问名称；hover、focus-visible、disabled、pending、success 反馈齐全。
- 加载、空、错误、无权限、只读、正式后端未开放、未知枚举在图集中全部覆盖。
- 正常图中不出现当前合同没有的假精度、假 evidence、假在线或假兼容；示意值必须标注。
- 视觉自检：去掉业务文案后若像任意设置页，应强化版本、引用、证据、适用范围与能力差异，而不是增加装饰。

## 11. Web Interface Guidelines 审计发现

本次获取并完整应用了 2026-08-17 时最新的 Web Interface Guidelines <code>command.md</code>。以下是当前 P14–P17 实现的 <code>file:line</code> 发现，供后续实现计划修复；本文没有修改代码。

### 11.1 frontend/src/pages/p14-robot-models/page.tsx

- <code>:236-258</code> — 页面把 P14 描述/实现为文件上传入口，动作语义与资产 Registry 边界不清；改为明确的资产登记/选择已接入引用，上传来源归 P02/P03 或批准的专用流程。
- <code>:263-270</code> — Summary 的 Lucide 装饰图标没有显式 <code>aria-hidden</code>；装饰 SVG 不应重复朗读。
- <code>:298</code> — 搜索 placeholder 未以省略号“…”结尾；应使用“搜索型号、厂商或代码…”。
- <code>:410-437</code> — 完整 ID/hash 仅为普通 code；添加 <code>translate=no</code>、显式复制和可聚焦的完整值披露。
- <code>:450</code> + <code>frontend/src/shared/ui/DetailTabs.tsx:38-52</code> — Tab 声明 <code>aria-controls=tabpanel-*</code>，实际 panel 缺相应 id/aria-labelledby；修复关联。

### 11.2 frontend/src/pages/p15-robots/page.tsx

- <code>:127-151</code> — <code>role=treeitem</code> 在容器，实际焦点/点击在嵌套 Button；ARIA 状态和键盘焦点对象不一致。使用语义 tree keyboard pattern，避免交互元素嵌套。
- <code>:245-260</code> — 装饰性 Summary 图标未显式隐藏于可访问树。
- <code>:288</code> — 搜索 placeholder 缺“…”。
- <code>:418-445</code> — 机器人/组件/型号 ID 没有复制、<code>translate=no</code> 与完整值披露。
- <code>:455</code> + <code>DetailTabs.tsx:38-52</code> — panel 缺与 Tab 匹配的 id/aria-labelledby。

### 11.3 frontend/src/pages/p16-calibrations/page.tsx

- <code>:67-103,126-132</code> — 硬编码技术数值会形成错误内容承诺；无合同值时应明确缺失，不显示示例成真实。
- <code>:289-299</code> — 一个按钮文案“新建/发布”混合两种动作；使用单一、具体动词并区分流程阶段。
- <code>:305-319</code> — 装饰性 Summary 图标未显式 <code>aria-hidden</code>。
- <code>:399-411</code> + <code>DetailTabs.tsx:38-52</code> — panel 与 tab 的 aria-controls 目标不匹配。
- <code>:486-516</code> — ID/hash/参数缺复制与不可翻译标记；长值需可展开。

### 11.4 frontend/src/pages/p17-data-schemas/page.tsx

- <code>:135-145,232-233</code> — 页面构造假兼容/证据，不符合“内容必须诚实具体”；缺事实时显示未检查。
- <code>:297-304</code> — “新建 Schema”实际启动发布预检，按钮标签与动作不一致，属于高风险误导。
- <code>:311-333</code> — 装饰性 Summary 图标未显式隐藏。
- <code>:378</code> — 搜索 placeholder 缺“…”。
- <code>:407-418</code> — 对象分类按钮只靠视觉 class 表达选中，缺 <code>aria-pressed</code> 或等效 tab/current 语义。
- <code>:482-500</code> — 标识/hash 缺复制、<code>translate=no</code> 和完整值披露。
- <code>:556-559</code> — JSON 全量 stringify 到 pre，无渐进披露/大小保护；按需加载、可折叠并保留可访问文本。

### 11.5 frontend/src/pages/ui-011e/workspace.module.css

- <code>:26-31</code> — 主内容 <code>overflow:hidden</code> 可能剪掉重要内容/焦点环；仅在明确视觉容器使用，滚动责任需显式。
- <code>:142-157</code> — 摘要值省略但没有完整值披露；长 token 不得永久不可达。
- <code>:168-178,632-653</code> — two/three/schemaPane 固定最小列宽，响应断点又偏晚；1280 shell 内宽可能裁剪或整页横滚，应在内容宽度不足时降栏。
- <code>:254-262</code> — 行内 recordButton 使用 auto/0 最小高度，可能低于 24×24 指针目标；保留紧凑但满足命中区。
- <code>:495-505</code> — 技术 code 区字号 10px；不以极小字号解决密度。
- <code>:45-67,115-125</code> — 多处 raw hex/背景色绕过语义 token，暗色/高对比适配难；复用 Master tokens。
- <code>:668-673</code> — 已正确尊重 <code>prefers-reduced-motion</code>，后续保留。

## 12. React 19 + Vite 性能约束

只应用 React/浏览器/Vite 相关规则，不套用 Next.js Server Component、Server Action 或 <code>next/dynamic</code>。当前 React 是 19.1.1，所以不把 React 19.2 Activity 当可用方案。

| 场景 | 当前证据 | 后续约束与验收 |
|---|---|---|
| 路由拆包 | P14–P17 routes 均 <code>lazy: () =&gt; import('./page')</code> | 保留；不要把四个 Registry 页面合并进常驻 shell chunk |
| Three/URDF chunk | Vite 把 three/urdf-loader 放 viewer chunk（<code>frontend/vite.config.ts:23-34</code>）；loader 使用动态 import（<code>frontend/src/features/viewer/lazy-three-loader.ts:19-33</code>） | 只有用户打开“结构”且兼容预检通过后加载；列表/业务摘要不依赖 3D |
| 兼容与清理 | RobotSceneCore 先 compatibility，再创建 runtime，并有 abort/dispose/context-loss 清理（<code>frontend/src/features/viewer/RobotSceneCore.tsx:58-63,72-137</code>） | 保留 abort/dispose；asset resolve 也要可取消；失败隔离在 viewer 区 |
| P14 loader 事实 | 配置 lazy loader 的当前调用只在 P08；P14 未配置 | 在实现前补正式 asset resolver 合同与测试，不能用效果图假定成功 |
| 浏览器哈希 | P14 当前 whole-file arrayBuffer（<code>page.tsx:197-213</code>） | 先限制文件大小/类型；优先服务端可信 hash；若需客户端预检，采用 Worker/流式方案并测内存，避免主线程冻结 |
| JSON viewer | P17 当前 render 时 stringify（<code>page.tsx:556-559</code>） | 折叠时不加载 viewer；展开后动态 import；大 JSON 在 Worker parse/索引，树节点虚拟化需测量 |
| 搜索 | 列表/JSON 搜索可能触发重渲染 | URL 搜索输入可即时，昂贵过滤使用 <code>useDeferredValue</code>；不让 input 等待 |
| 长列表 | query limit 可到 100；DataTable 用内部 <code>scroll x=max-content</code>（<code>frontend/src/shared/ui/data/DataTable.tsx:196</code>） | 默认游标分页；50+复杂行评估 virtualization 或 <code>content-visibility:auto</code>，用 Profiler 决定 |
| 稳定身份 | 组件/资产/Schema 树和表 | key 必须用稳定 version/component/schema ID，不用 index；展开状态按稳定 ID |
| memo | 复杂树/JSON/差异 | 先拆分订阅与测量，再 memo；不要为简单字段堆 useMemo/useCallback |
| 请求与刷新 | 详情包含多域引用/证据 | 独立 Query 并发、局部 error/stale；不要串行 waterfall，也不要每行建立轮询 |
| 导入边界 | P14 从 viewer barrel import | 对重型 viewer/JSON diff 优先直接动态 import 具体模块，检查 bundle analyzer 后再决定；不凭规则猜优化 |

性能验收基准：

- P14：42 MB Mesh/大 URDF 的登记预检不冻结输入；3D 打开/关闭后资源释放；
- P15：50/500 实例、200 组件树节点的搜索/展开/切换；
- P16：1000 适用实例/引用项的分页与冲突展示；
- P17：1 MB/10k 节点 JSON 的首次展开、搜索、折叠和复制；
- 1280 下切 Tab/开抽屉不产生 layout shift 或整页横滚；
- 使用 React Profiler、Performance、heap snapshot 和 Vite bundle report 给出证据，未测量不宣称优化完成。

## 13. 待产品/技术确认与阻塞

### 13.1 优先决策

| 优先级 | 问题 | 推荐候选 | 替代 | 不决定会阻塞 |
|---:|---|---|---|---|
| 1 | P14 型号、逻辑版本与资产内容的稳定身份/不可变规则？ | model ID 稳定；version ID 不可变；服务端 canonical hash；发布后只能新版本 | 原地覆盖版本；仅以文件名识别 | 全部引用、Manifest pin、发布/弃用、缓存与审计 |
| 2 | P14 资产如何进入 Registry？ | 设计独立的机器人资产登记/存储合同；P14 选择 managed asset ref 或在版本表单中受控附加，且明确不同于 P02 连接器/P03 数据包 ingest | 只保存外部 URI；或 P14 内置管理型直传 | 页面边界、权限、hash、失败恢复与资产生命周期 |
| 3 | P15 Gateway/Adapter/部署模式/在线摘要的事实源和 TTL？ | Gateway 上报 versioned snapshot：source、observed_at、expires_at；过期显示 unknown | 云轮询机器人；人工填写在线 | P20 可选性、在线文案、能力差异、缓存/轮询 |
| 4 | P16 适用范围、有效区间重叠和回退规则？ | 显式实例/组件 ref 优先；半开时间区间；重叠阻塞激活；回退创建不可变后继 | selector 自动匹配；最后发布者覆盖；旧版重新激活 | 标定状态机、冲突 UX、Manifest 引用与历史解释 |
| 5 | P17 四对象的正式命名与边界，尤其 Manifest Profile vs 实例？ | Registry 只存 profile；实际 package Manifest 归 P03/P04，只 pin 版本引用 | P17 同时编辑实例 Manifest | 路由、类型、权限、数据库、上传校验和审计 |
| 6 | Schema 版本/兼容政策如何定义？ | identity + immutable version + content hash；政策与检查 evidence 分离；Schema 优先 SemVer | 自增整数；hash-only；前端判断兼容 | PD22、发布门禁、兼容矩阵、数据集快照 |

这些问题对应既有产品决策清单的 PD20–PD22：机器人模型/版本身份、标定冲突/回退、Schema 版本与兼容仍未冻结（<code>plan/PRODUCT-DESIGN-DECISIONS-REQUIRED.md:34-36</code>）。

### 13.2 技术合同待确认

- 四域正式 OpenAPI path、分页/排序/filter、error envelope、etag/idempotency 与 async job/evidence；
- organization/project/region scope 和跨项目复用策略；
- RobotDescription canonicalization、URDF/Mesh media/size/hash 与签名 URL/下载权限；
- Gateway/Adapter identity、版本规范、部署模式枚举、上报签名/新鲜度；
- capability vocabulary、required/declared/observed 的 reason codes；
- Calibration target type、units、selector、时间语义、verification ruleset；
- Channel/Topic/Schema/Manifest Profile 的引用方向与循环依赖规则；
- compatibility engine、baseline 选择、ruleset version、finding path 和 evidence retention；
- actual package Manifest 如何 pin <code>robot_model_version</code>、<code>adapter_version</code>、<code>calibration_version</code>、Schema refs；
- Lance dataset <code>schema_snapshots</code> 与 P17 Registry version 的映射，但不得把两者合并；
- 审计事件、敏感字段/下载/原始 JSON 的独立 capability 与保留期。

### 13.3 可直接进入后续效果图、不能直接进入业务编码

可直接用于效果图：

- 本文四页 IA、对象护照、渐进披露、长 ID/JSON 模式；
- 1440/1280 布局、状态矩阵、效果图清单；
- “无云控按钮”“后端未开放不回退 Mock”“没有 evidence 不显示通过”；
- T03/T04 的页面所有权边界。

不能直接编码：

- 新增字段、枚举、状态机、URL 参数、接口和 capability；
- 管理型资产接入、Gateway 上报、P20 eligible、标定重叠、兼容引擎；
- 任何发布/撤销/回退/下载动作；
- 示例 hash、矩阵、在线状态或 compatibility evidence。

## 14. T03 / T04 交接清单

### 14.1 给 T03（P01/P20）

T09 提供给 P20 的只读选择投影应至少包含以下语义，字段名待合同：

| 语义 | P20 用途 | T09 Owner |
|---|---|---|
| stable robot ID + display name + serial + region | 分派与审计 | P15 |
| lifecycle / directory enabled | 是否允许新分派 | P15 |
| effective model version | 任务要求与 Manifest 引用 | P14/P15 |
| required capability match + reason codes | 可选/不可选说明 | P14/P15/P17 |
| calibration validity through task end | 跨有效期风险 | P16 |
| manifest/schema compatibility summary | 契约风险 | P17 |
| last observed/source/age | 运营提示，不能做授权 | P15 |

P20 仍拥有 task_code、人员/PICO/机器人分派集合、有效期和 SAVED/Received/QC 进度。T09 不创建分派、不统计数据包进度、不把 online 当可采集事实。深链必须使用稳定 robot/calibration/schema/version ID，不能只传 display name 或 task code。

### 14.2 与 T04（P02/P03/P04）已对齐

已完整复核 <code>plan/frontend-ux-parallel/T04-P02-P04-SPEC.md</code>。T04 的结论是：P02 只管理上传来源/连接器，P03 每行是一份数据包的一次上传执行，P04 展示本次数据包的实际 Manifest、MCAP/Topic 与 QC 事实；机器人资产归 P14/P15（<code>T04-P02-P04-SPEC.md:9-17,109-145,148-193,195-251</code>）。本节据此冻结效果图边界：

| 来源 → 目标 | 传递什么 | 谁显示/谁修改 |
|---|---|---|
| P02 → P15 | 稳定 <code>robotId</code> 与显示摘要 | P02 展示“绑定机器人/查看实例”；P15 管名称、序列、生命周期、组件和机器人 connectivity |
| P15 → P02 | 稳定 source ref（正式映射后）与返回上下文 | P15 只读展示上传来源摘要；P02 管连接器 connectivity；两个“离线”不能互相覆盖 |
| P03/P04 → P14–P17 | actual Manifest 中的稳定 model/adapter/calibration/schema refs，以及本次实际 Topic/Schema finding | P03/P04 只读显示摘要/深链；P14–P17 修改目录的新版本，不覆盖本次历史 |
| P14–P17 → P03/P04 | 人类可读名称、不可变 version/hash、兼容/有效性摘要 | P03/P04 用于解释本次校验，不在上传详情编辑 Registry |
| P17 → Ingest | Manifest Profile、required Topic/Channel、Schema ref 和验证规则 | Ingest 按 pinned version 验证；P17 不编辑 actual package Manifest |
| P04 → P17 | 某数据包的 actual MCAP/Topic inventory 与 conflict finding | P17 展示引用/影响；P04 仍拥有本次运行报告，P17 不重新生成/篡改它 |

P02 是上传来源/连接器，P03 是上传记录/执行，P04 是数据包摄取详情，P14 是 RobotDescription/资产目录；导航文案必须避免都叫“上传资产”。P14 的 URDF/Mesh 登记不经由 P03 数据包记录。当前 P03/P04 缺 robot model/calibration/schema version 的正式关联，不能只追加 query 就宣称深链已工作。T04 也明确要求 P04 的实际 MCAP/Topic inventory 与 P17 的期望 Registry 对比但不互相编辑，并区分 P15 机器人 connectivity 与 P02 连接器 connectivity（<code>T04-P02-P04-SPEC.md:648-654</code>）。

## 15. 已核验、未核验与停止条件

### 15.1 已核验

- 完整读取改版计划、Master、P14–P17 页面计划和四份方向文档；
- 完整读取并应用 frontend-design、ui-ux-pro-max、最新 Web Interface Guidelines/command.md、React best practices；ui-ux-pro-max 的营销 Hero/外部字体候选因违背 Master 被明确舍弃，只保留密集桌面、低动效、渐进披露和长 token 规则；
- 静态审计 P14–P17 route/page/query codec/entity/API/adapter/rules/Mock fixture/handler/scenario、shared DataTable/CopyableId/DetailTabs、viewer/Vite 配置；
- 静态审计正式 OpenAPI、Ingest/Lance migration/model、runtime、activities/workflows，并确认无四域正式闭环；
- 运行当前前端 Vitest 全量基线：10 个 test file、32 个测试通过；其中 P14–P17 专属测试仍为 0，不能扩大为四域已覆盖；
- 完整读取并对齐已落盘的 T03 P01/P20 与 T04 P02/P03/P04 规格。

### 15.2 未核验

- 未运行浏览器、Playwright、视觉回归、真实 Gateway/机器人或网络抓包；
- 未确认外部 robotics draft OpenAPI 的来源、批准状态或与本仓后端的版本关系；
- 未验证 P14 成功加载真实 URDF/Mesh；当前只确认 P14 未配置现有 lazy loader；
- 未确认生产 capability payload、P14–P17 scope、权限和审计政策；
- 未确认 Gateway 上报协议、online TTL、Adapter/部署模式枚举与 P20 eligible 算法；
- 未确认标定 target 类型、有效区间/重叠/回退规则；
- 未确认 Channel/Topic/Manifest Profile 正式模型、Schema SemVer/兼容算法与 evidence；
- 未产出视觉图像；本文是效果图输入规格；

### 15.3 后续顺序

~~~text
产品确认第 13 节优先决策
→ 按第 10 节制作并评审效果图
→ T03/T04/T09 交叉复核页面所有权与稳定身份
→ 冻结字段、状态、权限、兼容/标定规则
→ 设计正式 OpenAPI / DB / Worker / evidence
→ 重写真实 API 实施与测试矩阵
→ 才允许业务编码
~~~

本文到此停止，不包含任何业务实现。

## 16. 文档自验命令

~~~bash
test -f plan/frontend-ux-parallel/T09-P14-P17-SPEC.md
git diff --check -- plan/frontend-ux-parallel/T09-P14-P17-SPEC.md
awk '/[[:blank:]]+$/{print NR ":" $0; bad=1} END{exit bad}' plan/frontend-ux-parallel/T09-P14-P17-SPEC.md
rg -n '^## ' plan/frontend-ux-parallel/T09-P14-P17-SPEC.md
rg -n 'P14|P15|P16|P17|RobotDescription|Gateway|Adapter|Calibration|Channel|Manifest|Topic|Schema' plan/frontend-ux-parallel/T09-P14-P17-SPEC.md
rg -n '1440|1280|权限|离线|不兼容|过期|冲突|能力缺失|后端未开放|JSON|hash' plan/frontend-ux-parallel/T09-P14-P17-SPEC.md
cd frontend && pnpm exec vitest run --reporter=dot
git status --short -- plan/frontend-ux-parallel/T09-P14-P17-SPEC.md
~~~
