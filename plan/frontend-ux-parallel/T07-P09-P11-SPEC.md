# T07 · P09 人工质量问题与 P11 非破坏性清洗 UX 规格

> 状态：效果图与产品确认输入，不是生产实现合同
>
> 编制：T07
>
> 日期：2026-08-17
>
> 唯一修改范围：本文件；未修改前端、后端、OpenAPI、迁移、Worker、部署或其他计划

## 0. 结论先行

1. **P09 只治理已进入 Lance 后由人发现的质量问题。** Raw 自动质检的 `RISK/REJECT` 仍停留在 Raw，由 P01/P04 的 Raw 诊断模式解释；P09 不提供人工改写自动质检结论、绕过 30 Hz 对齐或直接生成 Lance 的动作。
2. **P11 是统一数据可视化工作台的清洗模式，不是独立播放器。** 清洗人员基于不可变的 Lance 基线，追加半开步骤区间 `[start_step, end_step)`、规则和原因，保存不可变草稿修订，生成隔离预览并提交审核。
3. **审核者仍进入同一个工作台。** Review 模式提供“原始 / 编辑后 / 对比”；这里的“原始”明确指清洗前的**基线 Lance**，不代表 Raw MCAP，也不授予 Raw 下载权限。审核者可批准、拒绝或要求修改。
4. **清洗、审核、发布是分段权限。** 清洗人员只有保存草稿、生成预览、提交审核；P11 的审核者只决定精确清洗修订。该修订交给 T05 的 P07 候选版本组成/版本审核，最终由发布者在 P07 冻结不可变版本；任一审核通过都不等于正式发布。
5. **当前 P09/P11 只能作为 Browser Mock 支撑的交互草稿。** 仓库审计明确指出真实 API、持久化模型、Worker、集成测试和 E2E 尚未形成闭环，不得由现有页面、生成类型或 fixture 推断生产能力已经存在。

本规格遵循 `design-system/hc-data-platform/MASTER.md`，不修改该主文件。主视觉继续使用现有紫蓝数据平台语言、系统中文字体、Ant Design 和 Lucide 图标。P09/P11 **不新增页面私有或装饰性的“信号轨道”**；P11 必须复用 T02 已定义的 `StickySignalTimeline`，将相机可用性、动作/状态、区间修订、评论和审核检查项放进同一坐标系（`plan/frontend-ux-parallel/T02-DATA-VISUALIZATION-WORKBENCH-SPEC.md:67-137`）。P09 的辨识度来自“问题—证据—处置”信息链，P11 的辨识度来自“基线—修订—影响”三联对比。

## 1. 输入、证据等级与技能落地

### 1.1 证据等级

| 等级 | 可用于什么 | 本规格中的来源 |
|---|---|---|
| A：已实现且有后端/测试证据 | 陈述现有事实 | 质量引擎、标注修订/审核、发布冻结模块 |
| B：现有前端实现 | 识别可复用结构和当前问题 | P09/P11 页面、实体、适配器、查询、命令 |
| C：Mock 或外部草案 | 只用于枚举状态和交互降级，不是生产合同 | MSW fixture/handler/scenario、生成类型 |
| D：目标设计 | 效果图与产品/技术评审输入 | 本规格的 IA、状态机、工作台模式、任务拆解 |

证据冲突时优先级为：已确认业务方向与 MASTER → 真实后端代码/测试 → 当前前端 → Mock/生成类型 → 本规格建议。总计划也明确声明它只定义前端信息架构，不代表 API、数据库或 Worker 已完成（`plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:7-7`），并要求先确认效果图再编码（同文件 `:15-20`、`:247-257`）。

### 1.2 四项技能怎样影响本规格

| 技能 | 采用的约束 | 在本规格中的结果 |
|---|---|---|
| `frontend-design` | 先明确产品、用户和单一任务；结构必须表达业务真相；避免泛化管理后台和无意义装饰 | P09 以证据链为主轴，P11 以基线/修订/影响为主轴；不复制角色页面，不新增页面私有轨道 |
| `ui-ux-pro-max` | 可访问性优先，其次响应式、交互、性能；44px 触控目标；桌面密集工具也需清楚层级 | 1280 收起低频面板、关键动作始终可见、危险动作不用颜色作为唯一提示 |
| `web-design-guidelines` | 使用 2026-08-17 获取的最新上游规则逐文件审查 | 第 9 节列出 `file:line` 发现；重点处理焦点、表单、溢出、长列表、URL 状态和未保存离开 |
| `react-best-practices` | 只采用 React 19 + Vite 适用规则，不采用 Next.js/RSC/服务端组件规则 | 第 10 节规定高频时间轴隔离、长列表窗口化、对比视图按需加载、查询并行与稳定依赖 |

`ui-ux-pro-max` 的本地搜索脚本不在当前仓库/技能安装中，因此本轮依据完整技能正文的内置优先级表，不把缺失搜索结果伪装成已执行的设计系统生成。`web-design-guidelines` 的审查基线来自最新上游 `vercel-labs/web-interface-guidelines/command.md`；获取日期和未验证项见第 14 节。

### 1.3 MASTER 中直接适用的约束

- 产品对象、用户与单一任务见 `design-system/hc-data-platform/MASTER.md:9-14`。
- 颜色、字体、Ant Design/Lucide 延续约束见同文件 `:16-44`、`:46-105`。
- 工作台列结构、1280 无整页横向滚动、超过 50 项评估虚拟化、大序列聚合/降采样见同文件 `:106-143`。
- 状态不能只靠颜色、控件可访问命名、危险动作二次确认见同文件 `:145-157`。
- 面向用户使用业务语言、技术细节渐进披露见同文件 `:168-175`。
- React/Vite 与加载、空、错、无权限、只读、能力不可用状态合同见同文件 `:176-201`。

### 1.4 并行规格与方向原文的最终对齐

- T02 文件在本轮终检时出现，已完整读取 648 行。本规格接受其唯一组件树、固定 identity、adapter/capability 投影、单一 selection、同构 source/edit/compare、容器响应、局部错误和性能预算（`plan/frontend-ux-parallel/T02-DATA-VISUALIZATION-WORKBENCH-SPEC.md:67-164`、`:336-437`、`:551-596`）。T07 只窄化 Cleaning/Review 的工具和用户文案。
- T05 要求 P07 接收固定清洗规则修订的 revision ID、content hash、operation count、base compatibility、提交者和提交审核时间，并单独承担发布冻结（`plan/frontend-ux-parallel/T05-P05-P07-SPEC.md:795-800`）。第 8、12、14 节按此交接。
- T06 已把面向用户的动作词统一为“保存草稿 / 提交审核 / 审核通过 / 要求修改”，并接受 T02 的 1280 rail/drawer（`plan/frontend-ux-parallel/T06-P08-P10-SPEC.md:89-95`、`:243-267`）。T07 使用相同词族；T02 内部的 review intent 名不直接显示给用户。
- 四份方向原文已从 `/mnt/d/桌面` 补读：PICO 不保存机器人采集数据并以 `data_package_id` 保证上传唯一/幂等（`/mnt/d/桌面/PICO 跨本体遥操作与机器人端录制方案.md:7-18`、`:299-323`、`:413-435`）；VR 文档只约束实时控制/显示，未定义 P09/P11 合同（`/mnt/d/桌面/VR 全身遥操作初版方案（固定颈部）.md:29-51`）；统一平台方案把文件、时序、控制和代表性质量分开，`RISK` 默认不进训练集（`/mnt/d/桌面/统一机械臂规控、遥操作与数据采集平台建设及调试上线方案.md:185-220`）；数据平台评测明确非破坏性区间、三种对比视图、版本历史和审核结果（`/mnt/d/桌面/数据平台 评测.md:455-664`、`:668-700`）。
- 数据平台评测较早段落仍写 `RISK` 可生成隔离预览并等待人工质检（同文件 `:224-242`），与本轮已锁定的“非 PASS 不进入 Lance、P09 不人工覆盖 Raw QC”冲突。这里以更新的总计划、T02 规格和真实 workflow 证据为准；旧段落只记为文档漂移，不能成为 P09 入口。

## 2. 当前实现证据与缺口

### 2.1 P09 当前证据

| 层 | 当前事实 | 证据 | 结论 |
|---|---|---|---|
| 页面 | 列表、状态标签、筛选、详情侧栏、分诊、解决、问题转草稿已存在 | `frontend/src/pages/p09-manual-issues/page.tsx:130-193`、`:196-289`、`:291-405` | 可复用查询状态和动作门控；信息架构需重排 |
| 页面 | 默认展示内部 Issue/type、英文枚举、Raw 纳秒范围和内部草稿标识 | 同文件 `:94-127`、`:196-256` | 业务默认层暴露实现细节，需格式化与渐进披露 |
| 页面 | 桌面检查器自行监听 Escape，模型式语义和焦点归还不完整 | 同文件 `:166-177`、`:339-360` | 需明确“非模态检查器”或实现完整模态焦点契约 |
| 页面 | “需要选择候选草稿”弹层只列候选，没有可执行的选择动作 | 同文件 `:390-405` | 当前存在死路，不可作为验收完成态 |
| 实体/状态 | `ManualIssue` 明确与自动 QC 分离；状态只有 `OPEN / IN_PROGRESS / RESOLVED` | `frontend/src/entities/manual-issue.ts:1-14`、`frontend/src/features/cleaning/issue-state-machine.ts:1-17` | 方向正确；关闭/重开仍是产品决策 |
| 命令 | 分诊与解决使用强 ETag 和幂等键；问题转草稿由服务端派生并可返回候选 | `frontend/src/features/cleaning/api/manual-issues.commands.ts:151-255` | 可保留交互意图；不等于真实端点已上线 |
| Mock | fixture 有待处理、处理中、已解决和问题转草稿分支 | `frontend/src/mocks/fixtures/cleaning/index.ts:41-149` | 仅用于效果图/降级场景 |
| 测试 | 清洗域专项测试只验证四个裸 GET 的状态码和 scope | `frontend/src/mocks/handlers/cleaning.handlers.test.ts:23-44` | 无 P09 页面、状态迁移、越权、冲突或 E2E 证明 |

现有 P09 行动作区最小宽度为 `19rem`（`frontend/src/pages/p09-manual-issues/styles.module.css:21-23`），共享表格又固定使用容器内横向滚动（`frontend/src/shared/ui/data/DataTable.tsx:180-197`）。总计划已记录 P09 在 1280 下可能出现整页横向问题（`plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:83-87`），因此不能只继续增加列。

### 2.2 P11 当前证据

| 层 | 当前事实 | 证据 | 结论 |
|---|---|---|---|
| 页面 | 已有草稿本地编辑、保存、预览、三栏工作台和底部时间轴 | `frontend/src/pages/p11-manual-cleaning/page.tsx:193-317`、`:368-624` | 可复用工作台骨架和局部状态处理 |
| 页面 | 默认暴露 EDL、规则代码、Raw 纳秒、字节与内部终结动作 | 同文件 `:121-139`、`:368-412`、`:450-503`、`:626-756` | 与业务语言和权限方向不一致 |
| 页面 | 审核反馈仅只读展示，没有 Review 模式的批准、拒绝、要求修改 | 同文件 `:680-715` | 目标工作台模式尚未存在 |
| 查询状态 | 草稿、来源、编辑/对比模式和选区进入 URL | `frontend/src/pages/p11-manual-cleaning/query-codec.ts:1-15` | 深链接方向正确，应扩展而不是移回纯本地状态 |
| 实体 | 当前草稿状态仍是“编辑中 / 旧终态”二态，预览/终结属于草稿模型 | `frontend/src/entities/cleaning-draft.ts:1-78` | 必须由正式清洗域合同替换为业务审核状态 |
| Mock | scenario 覆盖 loading/empty/error/conflict/offline/dirty/preview 等，但仍含旧终结状态 | `frontend/src/mocks/scenarios/cleaning.ts:6-12`、`frontend/src/mocks/fixtures/cleaning/index.ts:201-250`、`:384-459` | 适合视觉状态枚举，不是后端事实 |
| 测试 | 没有 P11 页面、范围编辑、预览过期、审核或冲突恢复测试 | `frontend/src/mocks/handlers/cleaning.handlers.test.ts:23-44` | 编码前必须补测试矩阵 |

当前 CSS 在根容器用 `overflow: clip`（`frontend/src/pages/p11-manual-cleaning/workbench.module.css:1-9`），工具按钮约 32px（同文件 `:47-60`），媒体网格还被 `display: none` 隐藏（同文件 `:211-213`）。1280 仍强制三列固定侧栏（同文件 `:390-413`），会压缩中间证据区；这些都不能直接继承为目标布局。

### 2.3 真实 API 与测试覆盖审计

- 当前浏览器路径为 Mock-only；生成类型来自外部草案，并非运行时客户端的已接通合同（`plan/P01-P19-REAL-API-COVERAGE-AUDIT.md:9-17`）。
- P09 没有真实后端、问题/证据/指派/历史持久化和集成/E2E（同文件 `:45-45`）。
- P11 页面 API、非破坏性规则域、执行 Worker 与专项测试均未落地（同文件 `:47-47`）。
- 实施计划仍将人工问题模型、清洗域/重放/乐观锁和执行工作流列为未开始项（`plan/P01-P19-REAL-API-IMPLEMENTATION-PLAN.md:79-79`、`:96-98`、`:113-113`）。
- 现有生成类型中虽出现 P09/P11 草案端点（`frontend/src/shared/api/generated/cleaning.ts:125-353`、`:490-818`），仍只能按 C 级证据处理。

因此，效果图必须给真实模式预留以下降级态：能力未上线、无权限、无分配数据、已离线、资源不存在、资源过期、合同不兼容、并发冲突、预览生成中/失败/过期。**不得静默回退到 fixture，也不得让不可用按钮看起来像可完成。**

### 2.4 后端只读证据：能证明什么、不能证明什么

| 模块 | 已证明 | 证据 | 不能外推 |
|---|---|---|---|
| 自动质量 | 不解析/插值/写 Lance；配置不可变；任一错误为 `REJECT`、任一警告为 `RISK`、无发现为 `PASS`；报告持久化失败不发假完成事件 | `backend/src/hc_data_platform/quality/README.md:3-26`；`backend/src/hc_data_platform/quality/engine.py:51-102`、`:680-686` | 不能证明 P09 人工问题域存在，也不能提供人工覆盖 QC 的入口 |
| 自动质量路由 | 当前只提供质量配置与报告读写，没有 P09 人工改判接口 | `backend/src/hc_data_platform/quality/router.py:48-104` | P09 不应自行增加自动 QC 状态变更 |
| 标注修订 | 已实现不可变修订、追加操作、半开步骤区间、强 ETag、幂等 mutation、412 冲突、角色分离、禁止自审 | `backend/src/hc_data_platform/annotation/README.md:3-21`；`backend/src/hc_data_platform/annotation/models.py:19-35`、`:61-182` | 这是 P11 可借鉴的技术模式，不是清洗后端已经复用它的证明 |
| 标注测试 | 验证过期修订不覆盖、同 mutation 幂等、精确修订审核、批准后再编辑保留历史 | `backend/tests/annotation/test_annotation_service.py:114-238` | 不能替代清洗域自己的单元/集成/E2E |
| 发布 | 只冻结满足条件的精确批准快照；版本和 Manifest 不可变；发布读取批准记录而非可变草稿 | `backend/src/hc_data_platform/publishing/README.md:3-21`；`backend/src/hc_data_platform/publishing/service.py:200-374` | 不能让 P11 清洗按钮暗示已冻结或已发布 |
| 发布权限 | 发布路由单独要求发布权限 | `backend/src/hc_data_platform/publishing/router.py:54-69` | P11 的编辑/审核能力不能推导发布能力 |

## 3. 业务边界与跨页主链

```text
Raw MCAP
   |
   +-- 自动 QC = RISK / REJECT --> P01/P04 · Raw 只读诊断
   |                                └─ 修复采集/上传/格式问题；无人工改判通道
   |
   `-- 自动 QC = PASS --> 30 Hz 对齐 --> 不可变 Lance 基线
                                              |
                             人工在浏览/标注/审核中发现问题
                                              v
                                     P09 · 人工质量问题
                                              |
                                      定位、分派、关联证据
                                              v
                                 P11 · Cleaning 模式
                                 保存草稿 / 隔离预览 / 提交审核
                                              |
                                              v
                                 P11 · Review 模式
                              批准 / 拒绝 / 要求修改
                                              |
                                   精确清洗修订审核通过
                                              v
                         P07 · 候选版本组成 / 版本审核（T05 所有）
                                              |
                                   发布者冻结正式版本
```

上述边界来自总计划：自动质检通过后才生成 Lance，异常停在 Raw 且不得人工覆盖（`plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:41-49`）；P09 聚焦 Lance 后人工发现问题（同文件 `:94-97`、`:154-156`）；审核与发布分离（同文件 `:47-50`）。P11 的清洗修订审核与 P07 的候选版本审核是两个可追踪事实；它们都可复用 T02 Review 模式，但上下文、状态和决定 ID 不得混成同一个对象。

### 3.1 P09 允许与禁止

允许：记录人发现的错位、抖动、内容不一致、标注/元数据疑点；定位到采集条目和 Lance 时间段；分派；补证据/评论；创建或关联清洗草稿；在满足解决证据后标记已解决；查看审计轨迹。

禁止：修改 Raw；把自动 QC 异常改成通过；触发 Raw→Lance；伪造质量报告；无权限下载 Raw；在问题页批准审核；冻结正式版本；用内部 ID 代替问题标题。

### 3.2 P11 允许与禁止

允许：读取分配范围内的 Lance 基线和授权预览；增加、修改、停用非破坏性规则；填写原因和评论；保存草稿修订；生成隔离预览；查看影响摘要；提交审核。

禁止：覆盖 Lance 或 Raw；删除历史规则；自行批准；自行拒绝/要求修改自己的提交；冻结正式版本；把预览当成派生成功；把“原始”对比误解为 Raw 文件访问。

## 4. P09 信息架构与交互规格

### 4.1 产品、用户、单一任务

- 产品对象：Lance 后人工发现且可追踪的问题事实。
- 主要用户：质量处理人、被分派的清洗人员、审核者；管理员只扩大数据范围，不获得另一套页面。
- 单一任务：**从一条问题迅速确认“哪里不对、证据是什么、谁处理、下一步去哪”。**
- 视觉签名：每行和详情均按“问题 → 证据 → 处置”阅读顺序；严重程度服务于排序，不用大面积红色制造告警墙。

### 4.2 入口、出口与 URL

入口：P06 Lance 浏览中的“报告人工问题”、P08 标注工作台评论、P11/P07 审核退回、P01 待办；所有入口携带项目、采集条目、Lance 版本、步骤范围和安全的 `returnTo`。

出口：在统一工作台定位；创建/继续清洗草稿；返回来源上下文；跳转关联审核记录。筛选、排序、游标、打开的问题、来源返回地址均写入 URL，刷新和分享后可恢复；敏感令牌、自由文本草稿和未保存评论不得进入 URL。

### 4.3 列表字段优先级

| 优先级 | 1440 默认列 | 1280 策略 | 展示规则 |
|---|---|---|---|
| P0 | 问题摘要 | 保留 | 1–2 行，类型为次级标签；不显示内部问题 ID |
| P0 | 数据位置 | 保留 | 数据集 / 采集条目业务名，可悬停或展开看完整标识 |
| P0 | 时间段 | 保留 | `00:01.800–00:02.200 · 12 steps`；内部纳秒仅技术详情 |
| P0 | 严重程度 + 状态 | 合并为一列 | 中文标签 + 图标/文本，不只靠颜色 |
| P0 | 主动作 | 保留 | 单一“定位处理”；更多动作进入省略菜单 |
| P1 | 处理人 | 有空间时保留 | 头像/姓名/“未分派”，不用主体 ID |
| P1 | 最近更新 | 移入详情 | 相对时间 + 可访问的绝对时间 |
| P2 | 来源、创建者、关联修订数 | 移入详情 | 作为证据上下文，不挤占列表 |

列表使用服务端游标和排序。50 行以内可用共享表格；大于 50 行必须验证虚拟化或 `content-visibility`，不能一次渲染无上限事件/问题。表格自身可以横向滚动，但“问题摘要、状态、定位处理”必须无需横向滚动即可看到。

### 4.4 筛选、批量与保存视图

首层只保留：状态、严重程度、处理人、搜索。高级筛选包含问题类型、数据集、采集条目、来源页面、创建时间、更新时间和“只看我负责”。默认视图为“我的待处理”，但不是硬编码角色页。

- 搜索匹配业务标题、数据集/采集条目名称和安全可见的标识；明确按钮文案为“搜索”，不只放放大镜。
- 筛选变化重置游标；未知枚举显示“新状态（只读）”，不崩溃也不开放动作。
- V1 不默认提供批量解决。批量分派需产品确认，并必须逐项返回成功/失败结果。
- 保存视图属于共享列表模板依赖；T07 只声明字段，不修改共享组件。

### 4.5 详情检查器

1440 可使用同页非模态检查器；1280 采用右侧覆盖抽屉。统一顺序：

1. 摘要：标题、状态、严重程度、处理人、更新时间、主动作。
2. 证据：业务时间段、关键帧/曲线缩略图、发现来源、问题描述。
3. 数据位置：数据集、采集条目、Lance 基线、相机/通道；点击“在工作台定位”。
4. 处置：分派、开始处理、创建/继续草稿、填写解决说明。
5. 活动：创建、分派、状态迁移、评论、关联草稿/审核记录；append-only。
6. 技术详情（默认折叠）：完整 ID、内部枚举、纳秒、ETag、请求 ID；复制按钮需有可访问名称和成功反馈。

非模态检查器用 `<aside aria-label="问题详情">`，不伪装成对话框。若效果图改为模态抽屉，则必须具备初始焦点、焦点圈、Escape、关闭按钮和关闭后焦点归还。

### 4.6 P09 状态与动作

```text
待处理 OPEN --分派/开始--> 处理中 IN_PROGRESS --附解决证据--> 已解决 RESOLVED
     ^                           |                                  |
     |                           `--取消处理?------------------------'
     |                                                              |
     `------------------------- 重新打开? <---- 已关闭 CLOSED? <-----'
```

`OPEN → IN_PROGRESS → RESOLVED` 是当前前端草案已存在的状态；`CLOSED`、取消处理、重开由 `PD-16` 决策，不能先写入正式合同（`plan/PRODUCT-DESIGN-DECISIONS-REQUIRED.md:30-31`）。效果图可用虚线/“待确认”标记展示候选路径。

| 动作 | 前置 | 成功结果 | 失败保留 |
|---|---|---|---|
| 分派/改派 | 有问题处置能力；选择可见成员；填写原因 | 状态进入处理中或保持处理中；活动追加 | 选择和原因保留，显示字段级/全局错误 |
| 开始处理 | 当前用户可领取；未被他人锁定 | 记录处理人和时间 | 若冲突，刷新处理人，不覆盖他人状态 |
| 创建清洗草稿 | 有清洗创建能力；问题位于 Lance；服务端能确定基线 | 创建后进入 P11 | 多候选时用户必须可选择；重复意图返回同一结果 |
| 标记已解决 | 关联经审核的结果或允许的解决证据；填写说明 | 进入已解决，记录精确修订/版本 | 不允许只填任意版本 ID 就解决 |
| 关闭/重开 | 产品确认状态语义、角色和 SLA | 追加迁移记录 | 不能覆盖历史 |

## 5. P11 Cleaning/Review 工作台规格

### 5.1 产品、用户、单一任务

- 产品对象：基于不可变 Lance 的版本化、非破坏性清洗修订。
- Cleaning 用户：被分派的清洗人员。
- Review 用户：具有审核能力且不是本次提交作者的审核者。
- 单一任务：**Cleaning 模式判断要排除/修订什么并证明影响；Review 模式判断精确提交的修订是否可接受。**
- 视觉签名：顶部一直显示“基线 / 当前草稿修订 / 预览状态”三元上下文；中心证据永远比工具面板更宽。

### 5.2 统一工作台模式合同

| 模式 | 中心视图 | 左侧 | 右侧 | 底部 | 主要动作 |
|---|---|---|---|---|---|
| Cleaning | 基线、编辑后、对比 | 分配队列/关联问题 | 区间修订、规则、评论、影响 | 时间轴、区间、评论锚点 | 保存草稿、生成预览、提交审核 |
| Review | 基线、编辑后、对比 | 待审队列/提交摘要 | 检查项、评论、影响、修订历史 | 同一时间轴，锁定提交修订 | 批准、拒绝、要求修改 |
| Read-only | 同上 | 关联对象 | 决策/审计 | 同上 | 返回、复制安全链接 |

该合同已与 T02 的概念规格对齐，但 T02 仍是效果图合同，不是已实现组件/API。T07 不创建共享组件或正式接口，只向其 adapter/slot 提供以下模式输入：

| T02 概念字段/槽位 | Cleaning | Review |
|---|---|---|
| `mode` | `cleaning` | `review` |
| fixed identity | dataset/version/episode/base revision + cleaning draft revision，禁止 latest/current | 同一固定基线 + 精确提交修订 + review context |
| timebase/selection | `aligned-step`；playhead、`StepRange`、stream、focused rule/comment | 复用同一 selection，focused finding/check item |
| projections | source + edit；预览缺失时保留 edit slot 的诚实状态 | source + locked edit；compare 同 slot、同轴、同游标 |
| tracks | cleaning rule、comment | cleaning rule、comment、review finding |
| left/right slots | 清洗草稿/退回项；区间/规则/评论/影响 | 待审核项；检查项/评论/影响/历史 |
| capabilities | edit/save/preview/submit-review；P11 中 publish 永远 false | review decision；publish 永远不属于工作台动作组 |

对应的共享树、模式矩阵和固定 identity 规则见 `plan/frontend-ux-parallel/T02-DATA-VISUALIZATION-WORKBENCH-SPEC.md:67-164`。面向用户的主动作按本任务和 T06 的统一词汇显示为“提交审核”，内部 intent 可以保留 `canSubmitForReview`。

`original` 视图的 UI 文案固定为“基线”，`edited` 固定为“编辑后”，`compare` 固定为“对比”。对比方式包括并排、透明叠加/差异层和同一时间游标；不支持的模态显示“当前数据没有可用预览”，不得伪造画面。

### 5.3 顶部上下文与动作条

左侧：面包屑、采集条目业务名、关联问题、只读/编辑状态。中部：基线 Lance 版本、草稿修订号、预览“未生成/生成中/最新/已过期/失败”。右侧动作按顺序：`保存草稿`、`生成预览`、`提交审核`；只显示当前模式允许的动作。

- 未保存：标题旁显示“有未保存更改”，离开/切 scope/关闭标签需确认。
- 预览过期：规则变化后立即标记，旧预览仍可看但加明显水印，不能用于提交前检查。
- 提交前置：无未保存更改、校验通过、预览对应当前修订、必填原因完成、服务端允许动作、在线。
- 异步动作使用 `aria-live="polite"` 状态区；按钮 pending 时禁止重复意图，但保留取消导航的明确规则。

### 5.4 区间与规则工具

所有作用范围以 `[start_step, end_step)` 为规范坐标。默认显示“开始时间、结束时间、持续时间、step 数”，技术详情才显示纳秒。结束必须严格大于开始；拖拽与输入框双向联动，键盘可每次移动 1 step，Shift 加速 10 steps。

| 内部候选类型 | 用户文案 | 最少输入 | 预览效果 | 产品状态 |
|---|---|---|---|---|
| `EXCLUDE_RANGE` | 排除此区间 | 起止、原因 | 灰化被排除段，显示保留比例 | 直接进入技术设计 |
| `TRIM` | 限定有效范围 | 保留起止、原因 | 范围外灰化 | 需确认是否与排除规则重复 |
| `SPLIT` | 拆分采集片段 | 分割点、原因 | 时间轴分段 | 需产品确认下游语义 |
| `TIME_OFFSET` | 校正时间偏移 | 通道、偏移、原因 | 曲线/帧对齐前后 | 需确认允许通道与阈值 |
| `DISABLE_CHANNEL` | 停用数据通道 | 通道、原因 | 通道禁用徽标 | 需确认发布资格影响 |
| `SET_METADATA` | 修订元数据 | 字段、值、原因 | 前后字段 diff | 需白名单与 Schema 校验 |
| `INVALIDATE_EPISODE` | 标记整条采集不可用 | 原因、严重说明 | 全轴灰化 | 高风险；需角色与二次确认 |
| `INVALID_MASK` | 标记无效区间 | 通道、区间、原因 | 局部遮罩 | 需确认与排除的差异 |

效果图至少完整画出“排除此区间”；其余规则在产品确认前显示为能力受限或隐藏，不因 Mock 枚举存在就承诺上线。规则列表按顺序号展示业务摘要、范围、原因和启用状态；技术类型、hash、内部 ID 位于折叠详情。删除规则的业务行为是“停用并追加新修订”，不删除历史操作。

### 5.5 评论与协作

- “原因”属于规则本身，保存后进入不可变修订；“评论”属于讨论线程，可锚定时间点、区间、规则或审核检查项。
- 评论包含作者、绝对时间、可见范围、已解决状态和审计事件；编辑/删除策略需产品确认，默认建议更正追加而非覆盖。
- Review 的“要求修改”必须至少创建一个未解决检查项；提交后清洗人员在同一锚点看到上下文。
- presence/lease 只显示“谁正在编辑”和过期时间，不当作安全锁；服务端 ETag 才决定是否可写。
- 没有评论 API 时显示“协作评论能力尚未上线”，不落入仅本地保存的假评论。

### 5.6 影响预览

预览是隔离、可过期、可失败的派生视图，不是正式版本。摘要默认展示：

- 保留/排除 step 数与比例、有效时长变化；
- 受影响的相机/通道/片段；
- 规则冲突、空结果、全量失效、越界、重叠等阻断项；
- 基线与编辑后的关键帧、曲线和元数据差异；
- 生成时间、基于的精确草稿修订、是否过期。

字节数、对象键、内部 job ID 和 Worker 详情默认折叠。若真实后端只能返回部分影响，UI 必须标“估算/未提供”，不能在前端用抽样数据推导确定性结论。

### 5.7 Review 模式

审核者打开的是一个**锁定到精确提交修订**的工作台。默认进入“对比”，可切基线/编辑后；评论、检查项、规则和时间轴双向定位。动作：

| 动作 | 必填 | 结果 | 约束 |
|---|---|---|---|
| 批准 | 可选总结；高风险规则可要求检查清单 | 该精确修订进入审核通过 | 不冻结正式版本；提交作者不能自审 |
| 要求修改 | 至少一个锚定检查项和说明 | 状态进入需要修改 | 清洗人员继续产生新修订，历史提交保留 |
| 拒绝 | 拒绝原因 | 当前提交进入已拒绝 | 是终止整个草稿还是只终止该提交，待产品确认 |

审核期间发现提交修订已被替代时，页面保持只读并提示“你正在审核历史提交”；不能悄悄切到最新草稿。批准请求必须携带精确修订、ETag 和稳定幂等键。

## 6. 状态机、并发与幂等

### 6.1 清洗业务状态候选

```text
                    保存新修订
                  +-------------+
                  |             v
              [编辑中] ----提交审核----> [待审核]
                 ^  ^                       |  |  \
                 |  |                       |  |   \ 拒绝
                 |  `---要求修改--- [需要修改]  |    v
                 |                           |  [已拒绝]
                 |                           |
                 |                        批准精确修订
                 |                           v
                 `--批准后再编辑?------ [清洗审核通过] --交接 P07 组成/版本审核--> [可冻结]
                                                                                  |
                                                                        P07 发布者 v
                                                                              [已冻结]
```

实线主链可进入技术设计；“批准后再编辑”“拒绝是草稿终态还是提交终态”须产品确认。无论选择哪种模型，任何编辑都产生新修订，不覆盖已审核快照；发布只能读取精确批准快照。这与已实现标注模块的安全模式一致（`backend/src/hc_data_platform/annotation/README.md:13-21`），但清洗域仍需自己的合同与测试。

### 6.2 写命令通用协议

| 要素 | 客户端行为 | 服务端要求 | 冲突 UX |
|---|---|---|---|
| 精确修订 | 保存时发送 `expected_revision` | 校验当前基线和操作日志版本 | 显示“服务器修订 / 我的更改”摘要 |
| 强 ETag | 从最后一次成功响应读取，发送 `If-Match` | 原子 compare-and-swap；过期返回 412 | 不丢输入；允许刷新对比或另存为新草稿 |
| 幂等键 | 每个用户意图生成一次，网络重试复用同一键 | 同键同正文返回原结果；同键异正文返回 409 | 提示重复意图冲突，禁止静默生成新键重放 |
| mutation ID | 保存修订内持久化稳定 client mutation ID | 参与去重和审计 | 显示安全请求 ID，不显示敏感正文 |
| pending | 同一意图按钮禁用，其他只读浏览可继续 | 返回确定状态或可查询 job | 刷新后从服务端恢复，不假设成功 |
| lease/presence | 只作提示，过期转只读并允许重新申请 | 不能替代 ETag/权限 | 显示编辑者和过期时间，不自动抢占 |

当前 P09 分诊/解决草案已经发送 ETag 与幂等键（`frontend/src/features/cleaning/api/manual-issues.commands.ts:151-203`）；当前 P11 保存草案带 expected revision 和 client mutation，但前端协议仍需与正式后端统一（`frontend/src/features/cleaning/api/workbench.mutations.ts:24-88`）。工程总决策也要求所有命令执行状态机校验、幂等和乐观并发（`plan/PRODUCT-DESIGN-DECISIONS-REQUIRED.md:50-63`）。

### 6.3 错误与恢复

| 场景 | 页面行为 | 禁止行为 |
|---|---|---|
| 409 同幂等键异正文 | 停止自动重试，显示意图冲突和请求 ID | 生成新键后偷偷重发 |
| 412 修订/ETag 过期 | 保留本地规则，拉取服务端最新，提供并排 diff、重新应用或另存 | 覆盖服务器修订、自动合并重叠范围 |
| 422 校验失败 | 定位到具体规则/区间，保留编辑 | 只显示顶部“失败” |
| 403/动作被撤销 | 立即转只读，保留未保存内容供复制安全摘要 | 继续显示可写成功态 |
| 404/410 | 显示不存在/已过期及返回队列 | 回退到 fixture 或空白画布 |
| 离线 | 本地标记未同步；禁止提交审核；恢复后先比较 ETag | 将本地草稿宣称已保存 |
| 预览失败/过期 | 仍可编辑，提交审核保持阻断；显示可重试与原因 | 沿用旧预览作为最新证据 |
| scope 切换 | 取消旧查询，清空旧 scope 数据，处理未保存确认 | 短暂展示上一项目内容 |

## 7. 1440 / 1280 响应式 ASCII 规格

### 7.1 P09 · 1440×900

```text
+--220 shell--+------------------------- 1220 content --------------------------+
| 导航        | 人工质量问题                         [我的待处理] [更多筛选]       |
|             | [状态] [严重程度] [处理人] [搜索................] [搜索]         |
|             +--------------------------------------+---------------------------+
|             | 问题列表 ~760                        | 非模态详情 ~420            |
|             | 摘要/位置/时间/状态/处理人/定位       | 摘要 + 证据                |
|             | ------------------------------------ | 数据位置 [在工作台定位]    |
|             | ...                                  | 处置                        |
|             | [游标上一页] [下一页]                | 活动 / 技术详情             |
+-------------+--------------------------------------+---------------------------+
```

验收：核心五列和“定位处理”无需表格横向滚动；详情不是模态时不抢焦点；列表与详情分别滚动，页面根不横滚。

### 7.2 P09 · 1280×800

```text
+--220 shell--+------------------------- 1060 content --------------------------+
| 导航        | 人工质量问题             [筛选] [搜索..............] [搜索]      |
|             +-----------------------------------------------------------------+
|             | 摘要/位置/时间/严重+状态/定位处理                               |
|             | ----------------------------------------------------------------|
|             | 列表占满；处理人、更新时间进入行次级信息或详情                  |
|             |                                               [详情抽屉覆盖 >] |
|             | [上一页] [下一页]                                              |
+-------------+-----------------------------------------------------------------+
```

验收：详情为 420–480px 覆盖抽屉，不与列表并排挤压；打开/关闭后焦点可预期；主动作始终可见。

### 7.3 P11 · 1440×900

```text
+--220 shell--+-------------------- ~1180 workbench after main padding ----------------+
| 导航        | 采集条目 / 基线 r12 / 草稿 r4 / 预览最新    [保存][预览][提交审核]|
|             +----------+----------------------------------+--------------------+
|             | 队列 228 | 中心证据 min 640                 | 工具 300           |
|             | 问题/任务 | [基线] [编辑后] [对比]           | 区间/规则/评论/影响 |
|             |          | 相机 / 曲线 / 元数据差异          | 表单与检查项        |
|             +----------+----------------------------------+--------------------+
|             | 时间轴：播放、step/time、区间、规则、评论锚点（整行）           |
+-------------+------------------------------------------------------------------+
```

验收：中心证据不小于 640px；左右面板可收起，长队列窗口化并遵循 T02 单一主纵向滚动；顶部动作、共享时间轴和错误状态固定可见；P11 不在 T02 `StickySignalTimeline` 之外再画第二条轨道。

### 7.4 P11 · 1280×800

```text
+--220 shell--+-------------------------- 1020 workbench ------------------------+
| 导航        | 条目/基线/草稿/预览     [保存][预览][提交审核][检查器]           |
|             +----+-------------------------------------------------------------+
|             |44px| [基线] [编辑后] [对比]                                     |
|             |rail| 相同 camera slots / 曲线 / 元数据差异；单卡 >= 280px        |
|             | Q  |                                                             |
|             | S  |                         [需要时右侧 overlay drawer >]        |
|             +----+-------------------------------------------------------------+
|             | 播放栏 + 共享时间轴：区间修订 / 评论 / Finding                  |
+-------------+------------------------------------------------------------------+
```

验收：工作台容器 `<1120px` 时左侧自动收为 44px rail，右工具默认关闭并以不改变中心宽度的 overlay drawer 打开；drawer 接管焦点，Escape 关闭并归还触发点。布局按工作台**容器宽度**响应，不只按 viewport；规则见 `plan/frontend-ux-parallel/T02-DATA-VISUALIZATION-WORKBENCH-SPEC.md:365-393`。手机不承诺完整清洗，只提供状态、评论和轻量审核入口。

## 8. 角色、数据范围与动作矩阵

同一 P11 路由根据数据范围、能力和服务端 `allowed_actions` 切换模式，不创建清洗人员版/审核者版/管理员版页面。前端门控只改善 UX，后端仍是安全边界（`plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:63-73`）。

| 动作 | 清洗人员 | 审核者 | 发布者（P07） | 无对应能力 |
|---|---:|---:|---:|---:|
| 查看分配范围的 P09/P11 | ✓ | ✓（待审范围） | 只读追踪 | 隐藏路由或 403 |
| 在 P09 分派/领取 | 按独立问题能力 | 按独立问题能力 | — | — |
| 编辑区间/规则/原因 | ✓，仅编辑中/需要修改 | — | — | 只读 |
| 保存草稿 | ✓ | — | — | 隐藏 |
| 生成隔离预览 | ✓ | ✓，只读查看/刷新按能力 | 只读查看 | 隐藏或不可用说明 |
| 提交审核 | ✓，前置全部满足 | — | — | 隐藏 |
| 批准 | — | ✓，禁止自审 | — | 隐藏 |
| 要求修改 | — | ✓ | — | 隐藏 |
| 拒绝 | — | ✓ | — | 隐藏 |
| 冻结正式版本 | — | — | ✓，仅 P07 | P11 永不出现 |
| 下载 Raw | 默认无 | 仅独立 Raw 权限 | 仅独立 Raw 权限 | 不渲染入口 |

动作判定顺序：路由能力 → project/region/assignment 数据范围 → 资源状态 → 服务端动作列表 → 本地前置（dirty/preview/validation）。“管理员”身份不绕过以上判定；未知状态一律只读。

当前 P07 代码把批准与发布能力共同用于一个动作，并在文案中暗示批准后启动发布（`frontend/src/pages/p07-version-detail/page.tsx:297-314`、`:724-740`、`:1035-1044`）。这是 T05 需要对齐的现状，不在 T07 修改范围；目标仍是审核动作与发布冻结分开授权。

## 9. Web Interface Guidelines 逐文件审查

审查基线：`https://github.com/vercel-labs/web-interface-guidelines` 最新 `command.md`，获取于 2026-08-17。以下是规格阶段发现，不代表已经修复。

| 严重度 | `file:line` | 发现 | 目标处理 |
|---|---|---|---|
| P0 | `frontend/src/pages/p11-manual-cleaning/workbench.module.css:1-9` | 根容器 `overflow: clip` 会掩盖真实溢出 | 修复列宽/最小宽度，根不以裁剪代替响应式 |
| P0 | `frontend/src/pages/p11-manual-cleaning/workbench.module.css:390-413` | 1280 仍保留固定三列，中心证据被压缩 | 使用容器查询；1280 将队列移入抽屉，工具可折叠 |
| P0 | `frontend/src/pages/p11-manual-cleaning/page.tsx:201-218` | 已跟踪 dirty，但无离页、切 scope 或关闭标签保护 | 增加路由 blocker + `beforeunload` 兜底，保存成功后解除 |
| P0 | `frontend/src/pages/p09-manual-issues/page.tsx:390-405` | 多候选状态只展示文本，没有可选择的下一步 | 候选必须是可选择列表；显示基线、来源和创建时间 |
| P1 | `frontend/src/pages/p09-manual-issues/page.tsx:166-177`、`:339-360` | Escape 存在，但检查器语义/焦点归还不完整 | 非模态用 aside；模态则实现完整焦点圈和归还 |
| P1 | `frontend/src/pages/p09-manual-issues/page.tsx:363-383` | 处理人是自由输入内部 ID，缺少人员选择和稳定字段语义 | 使用限定 scope 的可搜索人员选择；可见姓名，ID 折叠 |
| P1 | `frontend/src/pages/p09-manual-issues/styles.module.css:21-23` | 行动作区固定 `19rem`，与多列表格争宽 | 单主动作 + 省略菜单；低优先列响应式隐藏 |
| P1 | `frontend/src/pages/p11-manual-cleaning/workbench.module.css:47-60` | 高频工具按钮约 32px，对触控/低精度指针过小 | 目标 44×44px；纯桌面密集模式也不低于共享 38px |
| P1 | `frontend/src/pages/p11-manual-cleaning/workbench.module.css:211-213` | 媒体网格被全局隐藏，无法完成视觉对比 | 无媒体时展示明确空态；有媒体时恢复自适应网格 |
| P1 | `frontend/src/pages/p11-manual-cleaning/page.tsx:461-503` | 规则列表无上限直接 map | >50 条窗口化或 `content-visibility: auto`，保留键盘定位 |
| P1 | `frontend/src/pages/p11-manual-cleaning/page.tsx:121-139`、`:450-503` | 默认文案暴露内部规则类型和纳秒 | 默认业务名、timecode/step；技术值折叠并可复制 |
| P1 | `frontend/src/pages/p11-manual-cleaning/page.tsx:626-675` | 默认检查器暴露 bytes、内部状态和命令原因 | 业务影响优先；请求/字节/hash 移入技术详情 |
| P1 | `frontend/src/pages/p11-manual-cleaning/page.tsx:392-412` | 异步保存/预览/提交状态缺少统一 live region | 增加命名状态区；错误聚焦到摘要或首个字段 |
| P2 | `frontend/src/pages/p11-manual-cleaning/query-codec.ts:1-15` | 选区和对比模式已进入 URL | 保留该优点；剔除令牌、自由文本和未保存操作 |
| P2 | `frontend/src/shared/ui/styles.css:16-21` | 已有全局 `:focus-visible` | 保留；新抽屉、时间轴和自定义画布不得覆盖为无 outline |
| P2 | `frontend/src/shared/ui/data/DataTable.tsx:180-197` | 表格横向滚动被限制在容器内 | 可继续作为兜底，但关键列/动作仍需响应式优先级 |

额外验收：所有图标按钮有中文 `aria-label`；所有输入有可见 label、`name`，人员搜索按隐私规则决定 autocomplete；状态时间用 `Intl.DateTimeFormat`，数字用 `Intl.NumberFormat`/`font-variant-numeric: tabular-nums`；截断文本保留可访问完整值；动画遵循 `prefers-reduced-motion`；颜色不作为唯一状态信号。

## 10. React 19 + Vite 性能约束

仓库实际为 React `19.1.1`、Vite `7.1.3`、TanStack Query/Table（`frontend/package.json:21-34`、`:48-58`）。下列约束只面向客户端 React/Vite；不采用 Next.js 动态组件、RSC、Server Actions 或服务端缓存规则。

| 场景 | 目标实现 | 禁止/门禁 |
|---|---|---|
| 30 Hz 播放游标 | 高频 playhead 放共享 clock/external store 或 ref，由画布/时间轴局部订阅；DOM 写入对齐 `requestAnimationFrame` | 不让 P11 页面、规则列表和侧栏每个 tick 全树重渲染；Profiler 验证非播放区不随 tick 重绘 |
| 拖拽选区 | pointer move 使用 ref 保存瞬态值；按帧更新可视 selection；pointer up 才形成业务操作 | 不在每个像素移动时重建整个操作日志或发预览请求 |
| 长问题/规则/评论 | 服务端游标；>50 行窗口化或 `content-visibility`; 稳定 row key | 不一次渲染无界数据；不使用数组索引作 key |
| 草稿操作日志 | reducer/不可变操作；按 operation ID 建 `Map`；派生摘要用稳定 primitive 依赖；保存发送点击时的精确 snapshot | 不用 effect 同步可计算状态；不在失败时清空本地输入 |
| 过滤与规则搜索 | 输入保持 urgent；昂贵过滤/图表派生使用 `useDeferredValue` + memo；模式切换可用 transition | 不为简单表达式滥用 memo；不让 deferred 值参与写命令 |
| 基线/编辑后/对比 | Vite `import()` + `React.lazy` 按需载入重型对比/3D/大图组件；仅渲染当前模式必要 pane；昂贵 pane 独立 memo | 不把三个完整视图常驻 DOM；不照搬 Next.js 专用 API |
| 数据读取 | bootstrap、预览摘要、评论等独立请求并行；TanStack Query 负责去重、scope key、取消旧请求 | 不形成可并行请求瀑布；scope key 不完整时不启用查询 |
| 大序列 | 按 viewport/range 拉取；>1k 点先聚合/降采样，缓存键含基线版本、草稿修订、范围、通道 | 不把完整 MCAP/Lance 序列塞入 React state；不在 render 中多次线性扫描 |
| Bundle | 直接导入组件/图标；重型可视化路由/模式分块；预加载以用户意图为依据 | 不从大型 barrel 无差别导入；首屏不加载 Review 专属重型视图 |

验收方法：React Profiler 录制 30 秒播放、拖拽与模式切换；浏览器 Performance 检查长任务；构建报告检查工作台 chunk；Playwright 在 1440/1280 验证无根横滚；测试 100 问题、200 规则/评论和 10k 降采样点的交互。采用 T02 的待校准目标：主线程帧 p95 ≤16ms、5 分钟视觉掉帧 <1%、workbench shell ≤10 次渲染/s、seek 可见 p95 ≤100ms、缓存投影切换 p95 ≤150ms、稳定内存增长 ≤20MB（`plan/frontend-ux-parallel/T02-DATA-VISUALIZATION-WORKBENCH-SPEC.md:551-587`）；本轮未测量，不能声称通过。

## 11. 效果图与视觉验收清单

效果图先于编码。每个主要画面提供 1440×900，另附 1280 检查图；使用真实长度中文、长业务名和未知枚举，不能只画理想短文本。

| 图号 | 画面 | 必须呈现 | 关键验收 |
|---|---|---|---|
| T07-V01 | P09 我的待处理 | 四个首层筛选、优先列、游标、空/错入口 | 无根横滚；主动作首屏可见；技术 ID 不抢视觉 |
| T07-V02 | P09 详情与定位 | 证据、业务时间、处理人、活动、关联清洗草稿 | 详情焦点正确；Raw 自动异常明确回 P01/P04 |
| T07-V03 | P11 Cleaning 正常编辑 | 基线/编辑后/对比、范围工具、规则原因、dirty、影响摘要 | 保存/预览/提交审核层级清楚；中心证据最大 |
| T07-V04 | P11 Review 对比 | 锁定修订、同步游标、检查项、批准/拒绝/要求修改 | 不出现编辑工具；批准不暗示冻结发布 |
| T07-V05 | 要求修改后续 | 锚定反馈、需要修改状态、新草稿修订 | 旧提交只读，历史和新修订不混淆 |
| T07-V06 | 冲突与降级 | 412 diff、离线、预览过期、403、能力未上线 | 输入不丢失；无 fixture 回退；恢复路径可执行 |

建议视觉密度：P09 使用 8px 间距网格、44px 行高和中性表面；P11 中心画布深色或低亮中性底，工具面板保持 MASTER 浅色表面，差异使用紫/青主色，排除区间使用带纹理的警示遮罩。严重/危险状态使用图标 + 文案 + 颜色，禁止仅红绿区分。图表仅用于曲线和影响趋势，不把问题计数做成装饰性仪表盘。

## 12. 产品确认、技术设计与可执行任务

### 12.1 必须产品确认

| ID | 决策 | 推荐默认 | 未确认时的 UI |
|---|---|---|---|
| P09-D1 | `RESOLVED` 后是否需要 `CLOSED`，谁能关闭/重开 | 解决与关闭分开；质量负责人关闭；允许带原因重开 | 隐藏关闭/重开，只保留三态 |
| P09-D2 | 问题类型、严重程度、SLA、重复问题合并规则 | 受控类型 + 可扩展“其他”；严重程度与 SLA 分离 | 不显示虚假 SLA 和自动去重 |
| P09-D3 | 谁可创建、分派、解决；解决证据门槛 | 发现者可创建；处理负责人分派；经审核修订或明确豁免才解决 | 解决保持能力受限 |
| P09-D4 | 问题可否关联多个采集条目/多个区间 | V1 一问题一采集条目、多个区间 | 效果图按推荐默认 |
| P11-D1 | 正式规则集合及每类下游语义 | V1 先上线排除区间，再逐类启用 | 未确认规则隐藏/能力不可用 |
| P11-D2 | 拒绝与要求修改的差异、终态范围 | 要求修改可继续；拒绝终止该提交但保留历史 | 按候选状态画虚线 |
| P11-D3 | 批准后再编辑是新草稿还是同草稿新修订 | 新修订并撤销当前批准指针 | 审核通过只读 |
| P11-D4 | 自审、同人审核后发布、双人规则 | 禁止自审；审核与发布可否同人另行决定 | 自审按钮隐藏并解释 |
| P11-D5 | 评论编辑/删除、通知、外包可见范围 | append-only 更正；按项目/任务范围可见 | 只画不可编辑评论 |
| P11-D6 | 影响预览必须有哪些确定指标 | step/时长/通道/阻断项为 P0 | 缺失值明确“未提供” |

这些问题覆盖并细化仓库 `PD-16`/`PD-17`（`plan/PRODUCT-DESIGN-DECISIONS-REQUIRED.md:30-31`）。产品结论需按该文件模板记录 scope、例外、日期、决策人、验收样例及对合同/数据/测试的影响（同文件 `:40-48`）。

### 12.2 必须技术设计

1. **T02 共享工作台合同**：mode、数据 adapter、clock、range、camera/channel、comment anchor、loading/error/readonly、容器响应式与性能预算。
2. **人工问题域**：Issue/Evidence/Assignment/Activity/Relation 持久化；创建、分诊、解决、关闭/重开状态机；游标、scope、审计和幂等。
3. **清洗修订域**：immutable base、append-only operation log、normalized effective ranges、revision/ETag、preview snapshot、submission/review decision。
4. **坐标映射**：Lance step 为规范坐标；timecode/ns 仅显示映射；通道偏移、边界舍入和时区/帧率规则。
5. **预览执行**：隔离命名空间、job 生命周期、过期、取消、缓存键、影响摘要、失败重试；不得修改基线。
6. **权限与范围**：route capability、assignment/scope、resource action、禁止自审、Raw 下载独立能力、发布独立能力。
7. **协作**：评论/检查项锚点、presence/lease、离线恢复、冲突 diff、通知和审计保留。
8. **正式 OpenAPI/数据库/Worker**：运行时 OpenAPI 为唯一生成源；Problem Details；真实模式无 Mock 回退；专项测试和可观测性。

### 12.3 产品确认与效果图通过后可直接执行

| Task | 所有者/依赖 | 工作 | 交付与验收 |
|---|---|---|---|
| T07-01 | T07；无代码 | 绘制 V01–V06，标注 1440/1280、状态、能力和技术详情层 | 产品对第 12.1 节逐项签结论 |
| T07-02 | T07 + T02 | 把第 5.2 节映射到共享 adapter/mode，不改 T02 文件 | 输出字段/事件映射；无复制 viewer |
| T07-03 | Backend/API owner | 定义 P09 正式合同、迁移、状态机、审计、幂等 | Router/Service/Repository 测试，越权/冲突用例通过 |
| T07-04 | Backend/API owner | 定义 P11 修订、预览、提交、审核合同与 Worker | 不可变/重放/精确审核/隔离预览测试通过 |
| T07-05 | T07 frontend；依赖 T02/T07-03 | 重排 P09 列表/详情/定位/状态与格式化 | 页面测试覆盖 ready/empty/error/403/unknown/conflict/1280 |
| T07-06 | T07 frontend；依赖 T02/T07-04 | 接入 Cleaning 模式工具与状态机 | 区间键盘操作、dirty guard、save/preview/submit 测试通过 |
| T07-07 | T07 frontend；依赖 T02/T07-04 | 接入 Review 模式 | 精确修订、三视图、三决策、自审禁止、历史提交测试通过 |
| T07-08 | T07 + QA | 视觉/无障碍/性能/E2E 门禁 | axe/键盘、1440/1280 截图、Profiler、真实 API E2E 通过 |
| T07-09 | T05 | 修正 P07 审核与发布冻结动作边界 | P11 交接精确清洗修订；P07 完成候选版本组成/版本审核，发布者再单独冻结 |
| T07-10 | T06 | 对齐 P08/P10 的人工问题入口与位置深链接 | P08 创建问题后可回原时间点；P10 分配不复制工作台 |

不得并行跳过的门禁：`产品决策 → 效果图确认 → T02 共享合同 → 正式后端/API → 前端编码 → 真实 E2E/视觉回归`。在正式合同前，可直接执行的只有效果图、字段映射和测试样例设计；不能把 Mock 类型升级为生产事实。

## 13. 验收清单

### 13.1 P09

- [ ] 所有问题都明确为 Lance 后人工发现；Raw QC 异常只有 P01/P04 去向。
- [ ] 1440/1280 下摘要、位置、时间、状态和主动作无需整页横滚。
- [ ] 默认不出现纳秒、内部枚举、超长 ID；技术详情可查看/复制。
- [ ] 分派使用成员选择而非自由输入 ID；多候选草稿可真正选择。
- [ ] 详情包含证据、处置、活动和工作台定位；URL 可恢复筛选与打开项。
- [ ] 未知状态只读；403/404/410/离线/冲突不回退 fixture。
- [ ] 状态迁移遵循正式合同，解决必须有证据，历史 append-only。

### 13.2 P11

- [ ] Cleaning/Review 复用 T02 同一内核；布局不按角色复制。
- [ ] 基线指 Lance，明确不代表 Raw；无 Raw 权限不出现下载入口。
- [ ] 区间统一 `[start_step, end_step)`；timecode/step 默认，ns 折叠。
- [ ] 保存草稿、预览、提交审核三层语义清楚；清洗人员看不到审核/发布动作。
- [ ] Review 锁定精确提交修订，支持基线/编辑后/对比及批准/拒绝/要求修改。
- [ ] P11 清洗修订审核通过只产生交接事实；P07 的候选版本审核与发布者冻结仍是两个独立动作。
- [ ] dirty 离页保护、409/412 diff、离线恢复、预览过期和 scope race 可恢复。
- [ ] 1280 无根横滚，中心证据不被固定三栏压垮，工具/队列可收起。
- [ ] 30 Hz 游标不使整页重渲染；长列表、大曲线、重型对比通过性能门禁。

### 13.3 测试矩阵最低要求

| 层 | P09 | P11 |
|---|---|---|
| schema/adapter | unknown enum、长 ID、业务时间格式、候选草稿 | 每类 operation、半开边界、未知规则、预览过期 |
| state machine | 正常/非法迁移、解决证据、重开候选 | 编辑→待审→三类决定→再修订；自审禁止 |
| command | ETag、幂等重复/复用冲突、scope | 保存/预览/提交/审核的 409/412/422/403 |
| component | 筛选 URL、详情焦点、成员选择、候选选择 | 键盘区间、dirty guard、三视图同步、live region |
| integration | 真 API 列表/详情/活动/关系 | 真 API 修订/预览 job/审核历史；无 Mock fallback |
| E2E | 来源工作台→P09→P11 | 清洗提交→审核要求修改→再提交→批准→P07 候选版本组成/审核→发布者冻结 |
| visual/perf | 1440/1280/长文本/100 行 | 1440/1280/200 规则/30 Hz/10k 降采样点 |

## 14. 协作边界、命令记录与未验证项

### 14.1 T02 / T05 / T06 边界

- **T02**：唯一拥有共享 viewer/workbench 布局、模式、clock、`StickySignalTimeline`、adapter、selection、容器响应和通用性能基线。其 648 行效果规格已完整读取；T07 只提供 Cleaning/Review 的输入、规则工具和动作需求，不代写/修改 T02，也不把概念类型当成已实现导出。
- **T05**：拥有 P05–P07 数据集/采集条目/候选版本与发布冻结。T07 向 P07 交接审核通过的精确 cleaning rules revision：revision ID、content hash、operation count、base Lance/annotation compatibility、提交者、提交审核时间、审核事实和 deep link；T07 不修改 P07，也不决定发布 Manifest/版本名。
- **T06**：拥有 P08/P10 标注工作台与任务。T07 接收 P08 人工问题入口的固定 Lance identity、stream、半开 step 区间和 return URL；不修改标注工具/队列。P08/P11 共同使用 T02 Review adapter，但各自拥有领域工具和状态，不能共享可变草稿 DTO。

### 14.2 本轮只读/验证命令摘要

```bash
git status --short
sed -n '1,260p' <任务附件>
sed -n '1,330p' plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md
sed -n '1,240p' design-system/hc-data-platform/MASTER.md
sed -n '1,700p' plan/frontend-ux-parallel/T02-DATA-VISUALIZATION-WORKBENCH-SPEC.md
sed -n '1,900p' plan/frontend-ux-parallel/T05-P05-P07-SPEC.md
sed -n '1,760p' plan/frontend-ux-parallel/T06-P08-P10-SPEC.md
sed -n '1,700p' "/mnt/d/桌面/PICO 跨本体遥操作与机器人端录制方案.md"
sed -n '1,180p' "/mnt/d/桌面/VR 全身遥操作初版方案（固定颈部）.md"
sed -n '1,320p' "/mnt/d/桌面/统一机械臂规控、遥操作与数据采集平台建设及调试上线方案.md"
sed -n '1,900p' "/mnt/d/桌面/数据平台 评测.md"
rg --files frontend/src/pages frontend/src/features/cleaning frontend/src/mocks backend plan
rg -n '<关键状态、动作、权限、并发与测试模式>' frontend backend plan
curl -fsSL <ui-ux-pro-max SKILL.md 上游原文>
curl -fsSL <web-design-guidelines SKILL.md 与最新 command.md>
git diff --no-index --check /dev/null plan/frontend-ux-parallel/T07-P09-P11-SPEC.md
```

本轮是规格编制，不运行前端 typecheck/build/test 或后端测试；没有生产代码变化，运行这些门禁不能验证目标交互。最终只对本文执行 Markdown/差异/禁用措辞与唯一文件范围检查。

### 14.3 初始工作树记录

开始工作前已记录 `git status --short`。以下变更均先于本规格且属于用户/其他终端，T07 未清理、覆盖或归并：

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

### 14.4 尚未验证 / 不得宣称完成

1. T02 工作台效果规格已经读取并完成概念对齐，但它仍不是已实现的 TypeScript 导出或生产 API；最终 route、adapter 字段、slot 签名、camera 排序和容器查询尚未验证。
2. 四份方向原文已从 `/mnt/d/桌面` 完整读取。`/mnt/d/桌面/数据平台 评测.md:224-242` 的旧 `RISK` 隔离预览/人工质检表述与本轮硬约束冲突，已按总计划、T02 和真实 workflow 判为文档漂移；仍需产品 Owner 在正式需求中清理歧义。
3. P09/P11 正式 OpenAPI、数据库表、清洗执行 Worker、评论/检查项 API、真实 scope/capability 和端到端链路均未验证为存在。
4. 生成类型来自外部草案且未接入运行时；MSW fixture/scenario 不能作为生产能力证据。
5. 尚未生成 V01–V06 效果图，也未在真实浏览器执行 1440/1280、键盘、读屏、视觉回归和性能录制。
6. P09 关闭/重开、SLA/去重、P11 正式规则、拒绝语义、自审/双人规则、评论保留和影响指标仍待产品确认。
7. `ui-ux-pro-max` 本地搜索 corpus/脚本不可用；已使用完整技能正文的内置规范，但未生成其自动 design-system 输出。

---

本规格的实施门槛不是“页面能显示 Mock 数据”，而是：产品决策已记录、效果图通过、T02 合同对齐、真实后端/API 有证据、角色边界可测试、冲突可恢复，并在 1280 下完成核心任务。
