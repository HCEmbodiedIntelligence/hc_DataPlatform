# 前端 UI 阶段 0 基线

> 调查任务：TASK-028
>
> 调查时间：2026-08-11 20:01–20:10 CST
>
> 执行终端：`worker-wakeup-ui-01`
>
> 依据：`docs/FRONTEND-UI-REFACTOR-PLAN.md` §2、§7、§8 阶段 0、§10–§14
>
> 边界：本文件只记录现状；没有修改前端实现、依赖、lockfile 或截图，也不表示阶段 1–6 已完成。

## 1. 结论

- P01–P19 的页面 ID 集合精确为 19 项；路由注册表实际有 21 条活动路由，额外两条来自 P06 的 Episode Viewer 和 P08 的任务工作台。
- 当前 UI 是可运行的功能原型，不是已完成人工 UI 验收的产品界面。最高风险为 P01：真实 API 模式下聚合合同尚未定义，页面按既有安全边界显式不可用；UI 重构不得用模拟指标掩盖此 P0。
- 页面层直接包含 149 个原生 `button`、54 个 `input`、55 个 `select`、4 个原生 `dialog`、22 个 `table` 和 9 个 `textarea`。`shared/ui` 已有 15 个组件文件，但其中 5 个没有真实消费者，Shell、Scaffold、筛选、表格、Drawer 和危险确认尚未收敛成唯一实现。
- `src` 下共有 13 个 CSS 文件、1,383 行、68,512 bytes，且没有 CSS Module。P13–P18 的六份 `page.css` 内容与 SHA-256 完全相同，重复 504 行/25,662 bytes；全局 `.page-header`、`.filter-bar`、`.metric-grid`、`.detail-panel` 等命名存在跨路由覆盖风险。
- 39 个逻辑 E2E 用例在两个 Playwright project 下收集为 78 项。只有 P02/P03/P04 使用 `toHaveScreenshot` 做真实像素比较；P05/P06/P07/P08 只产附件，P01/P08/P12/P19 只检查截图字节数，P09/P10/P11/P13–P18 没有截图路径。所有截图路径都只覆盖 happy/main 状态，没有 negative-state 视觉基线。
- 当前构建通过，但生成 `analytics` 1,085.39 kB（gzip 358.69 kB）并触发大 chunk 警告，同时生成空 `viewer` chunk。P02/P03/P04 页面还是 route eager，其他页面使用 route lazy；ADR 需要明确 bundle 预算和回退方式。

风险级别取每页发现的最高级：P0 为主流程不可用/权限或合同安全问题，P1 为主要布局、响应式、可发现性或视觉体系问题，P2 为局部文案、间距和图标问题。当前矩阵未把单纯样式瑕疵升级为 P0，也没有把测试全绿解释为 UI 已验收。

## 2. 截图状态图例

- `V(C+M)`：四档均由 `toHaveScreenshot` 做像素比较，当前工作区同时有 chromium/mobile 基线文件。
- `A`：四档截图只作为 Playwright 附件输出，没有像素比较。
- `B`：四档截图只检查 `byteLength > 1000`，没有持久化比较基线。
- `A+B`：既产附件又只检查字节数。
- `—`：该断点没有截图路径。

P05/P06/P07 虽各有 4 张历史 chromium PNG，但当前测试调用的是 `testInfo.attach`，不会读取或比较这些 PNG，因此矩阵仍标为 `A`。

## 3. P01–P19 页面问题矩阵

| ID | Route 与入口 | 模式与主要布局区 | 已有关键交互 | 明显问题与最高风险 | 现有定向测试 | 1440 | 1024 | 768 | 390 |
|---|---|---|---|---|---|---|---|---|---|
| P01 | `/dashboard`；`src/pages/p01-dashboard/page.tsx` | 分析/工作台；标题与时间范围、指标网格、图表/覆盖率、待办活动、右侧待办面板 | 时间筛选、按需加载覆盖率、打开/关闭待办、局部重试 | **P0**：真实 API 聚合合同未定义时主内容明确不可用，这是合同前置而非可由 UI 伪造的数据；另有大量 inline style、原生面板且视觉仅验字节 | `p01-dashboard.spec.ts` 3 项：happy、真实 API fail-closed、合同不匹配 | B | B | B | B |
| P02 | `/ingest/sources`；`src/pages/p02-data-sources/page.tsx` | 列表/管理；页头、指标、筛选、表格、详情 Inspector、多类编辑/危险 Dialog | 新建、查看、编辑、轮换凭据、连接测试、启停、删除 | **P1**：页面私有表格和 4 个公开 Dialog 组件，19 个输入/选择控件集中在编辑器；独立全局 CSS；当前截图基线处于他人未提交变化中 | `p02-data-sources.spec.ts` 2 项：happy + 合同泄漏阻断 | V(C+M) | V(C+M) | V(C+M) | V(C+M) |
| P03 | `/ingest/uploads`；`src/pages/p03-upload-jobs/page.tsx` | 列表/管理；页头、摘要、状态 Tabs、筛选、批量栏、上传表、游标分页、创建/取消 Dialog | 新建、批量选择/暂停/取消、详情跳转、SSE/轮询状态 | **P1**：私有表格与创建 Dialog，仅取消复用共享确认；依赖 ingest 全局 CSS；route eager；截图基线处于他人未提交变化中 | `p03-upload-jobs.spec.ts` 2 项：happy + unknown enum | V(C+M) | V(C+M) | V(C+M) | V(C+M) |
| P04 | `/ingest/uploads/:uploadId`；`src/pages/p04-upload-detail/page.tsx` | 详情；面包屑/资源头、概要、对象表、校验流水线、隔离、重试历史、审计摘要 | 对象分页、资源级重试、隔离复验与危险确认 | **P1**：页面私有对象表与原生危险 Dialog，页面主体压缩成超长 JSX；两层全局 CSS；route eager；截图基线处于他人未提交变化中 | `p04-upload-detail.spec.ts` 3 项：happy、合同泄漏、拒绝 latest/current | V(C+M) | V(C+M) | V(C+M) | V(C+M) |
| P05 | `/datasets`；`src/pages/p05-datasets/page.tsx` | 列表；页头、组合筛选、摘要条、数据表/游标、创建反馈与创建 Dialog | 创建、筛选、排序、游标翻页、详情/Episode 跳转 | **P1**：筛选、表格、创建 Dialog 都是页面私有组件；四档测试仅产附件，仓库存量 PNG 不参与断言 | `p05-datasets.spec.ts` 2 项：happy + contract mismatch | A | A | A | A |
| P06 | `/datasets/:datasetId` 与 `/datasets/:datasetId/versions/:versionId/episodes/:episodeId/view`；`page.tsx`/`ViewerShell.tsx` | 详情/只读 Viewer；资源头、Tabs、概要、Ready 版本、Versions、Episodes+Inspector、Schema、来源证据、容量 | 多区域筛选/分页、Episode Inspector、固定版本 Viewer 跳转 | **P1**：主页面 834 行、3 张直接表格与多套筛选/分页；使用 datasets 私有 `CursorPager`/`ConfirmDialog` 而非 shared；四档仅附件 | `p06-dataset-detail.spec.ts` 2 项：happy + gone/not-found | A | A | A | A |
| P07 | `/datasets/:datasetId/versions/:versionId`；`src/pages/p07-version-detail/page.tsx` | 详情/复核；版本头与 Tabs、概要、Episodes/Revisions+Inspector、Review/Findings、Manifest、Diff、Schema | Revision 选择、结构化 Finding、Approve/Return 预检确认、冲突恢复、返工跳转 | **P1**：单文件 1,295 行，直接含 6 张表与大量原生控件，使用 datasets 私有确认组件；四档仅附件 | `p07-version-detail.spec.ts` 4 项：happy、412、Return、blocker | A | A | A | A |
| P08 | `/annotations` 与 `/annotations/tasks/:taskId`；`AnnotationQueuePage.tsx`/`AnnotationTaskPage.tsx` | 列表+工作台；队列筛选/表格/分页；任务头、工具栏、Viewer、Schema 表单、Inspector、多危险确认 | 领取、保存、提交/复核、STALE rebase、时间轴键盘、报告问题 | **P1**：一个页面 ID 承载两种复杂模式；私有 CSS/Badge/状态/危险 Dialog，工作台响应式依赖页面规则；截图只附件+字节 | `p08-data-annotation.spec.ts` 4 项：两条 happy、STALE、权限撤销 | A+B | A+B | A+B | A+B |
| P09 | `/manual/issues`；`src/pages/p09-manual-issues/page.tsx` | 列表/管理；公共页头、摘要、筛选、直接表格、详情 Inspector、命令 Dialog | 分诊、解决、Issue→Draft、候选草稿选择、回 Viewer | **P1**：表格、Inspector、两个确认表单和候选 Dialog 均页面自建；无四档响应式/视觉证据 | `p09-manual-issues.spec.ts` 2 项：交接 + empty/forbidden | — | — | — | — |
| P10 | `/manual/drafts`；`src/pages/p10-cleaning-drafts/page.tsx` | 列表/详情；页头、范围 Tabs、摘要、筛选、表格/分页、右侧 Inspector | 状态筛选、游标、打开 Inspector、进入后继草稿/P11 | **P1**：自建筛选、表格、分页与非模态 role=dialog Inspector；无截图，RETURNED 之外的 negative 视觉状态未覆盖 | `p10-cleaning-drafts.spec.ts` 2 项：交接 + RETURNED successor | — | — | — | — |
| P11 | `/manual/drafts/:draftId`；`src/pages/p11-manual-cleaning/page.tsx` | 工作台；页头/来源条/操作栏、Viewer 对照、EDL 编辑器、只读复核反馈、提交确认 | 播放对照、保存、Preview、Commit、Finding 定位、危险确认 | **P1**：工作台布局完全依赖 cleaning 全局 CSS，确认组件来自 datasets 私有实现；没有任何断点截图 | `p11-manual-cleaning.spec.ts` 2 项：提交 + 只读 Finding | — | — | — | — |
| P12 | `/storage/overview`；`src/pages/p12-storage-overview/page.tsx` | 分析/详情；页头与 Tabs、容量指标/图表、Inventory 表、对象右侧面板、Multipart 表、费用 | Tab 切换、对象选择/关闭、只读诊断与费用查看 | **P1**：大量 inline style、两张直接表格和自建 role=dialog；四档只查字节，不会发现布局像素回归 | `p12-storage-overview.spec.ts` 2 项：happy + 非法 bytes | B | B | B | B |
| P13 | `/storage/lifecycle`；`src/pages/p13-storage-lifecycle/page.tsx` | 管理/分析；页头、局部 Tabs、指标、策略表、Simulation 影响面板、确认 | 策略选择、模拟、预检/执行确认、分页状态 | **P1**：与 P14–P18 复制同一 4,277-byte CSS；仅一条浅 happy E2E，无响应式或 negative 证据 | `p13-storage-lifecycle.spec.ts` 1 项 happy | — | — | — | — |
| P14 | `/settings/robot-models`；`src/pages/p14-robot-models/page.tsx` | 管理；页头+文件入口、模型表/版本、详情、3D/Joint Mapping、发布确认 | 文件选择、直传、固定版本选择、预览、发布 | **P1**：复制管理页 CSS；页面仍有原生 file input 与页面确认流程；仅一条浅 happy E2E，无视觉/上传 negative 证据 | `p14-robot-models.spec.ts` 1 项 happy | — | — | — | — |
| P15 | `/settings/robots`；`src/pages/p15-robots/page.tsx` | 管理；三栏机器人表、组件树、详情/时间化关系 | 选择机器人/组件、树键盘导航、查看绑定关系 | **P1**：三栏布局使用复制 CSS，在 768/390 完全无证据；仅一条浅 happy E2E | `p15-robots.spec.ts` 1 项 happy | — | — | — | — |
| P16 | `/settings/calibrations`；`src/pages/p16-calibrations/page.tsx` | 管理；三栏标定表、Frame Graph、详情 Tabs、发布确认 | 固定关系解析、详情 Tab、预检/发布 | **P1**：三栏和 Frame Graph 使用复制 CSS；高精度输入仍为原生控件；仅一条浅 happy E2E，无关系错误/响应式视觉证据 | `p16-calibrations.spec.ts` 1 项 happy | — | — | — | — |
| P17 | `/settings/data-schemas`；`src/pages/p17-data-schemas/page.tsx` | 管理；页头、区域 Tabs、Registry 表、固定版本详情 Tabs、发布确认 | 搜索/选择版本、只读定义、兼容性/引用、预检发布 | **P1**：复制管理页 CSS，Registry/详情在窄屏无证据；仅一条浅 happy E2E，无合同/unknown 视觉基线 | `p17-data-schemas.spec.ts` 1 项 happy | — | — | — | — |
| P18 | `/settings/access`；`src/pages/p18-access/page.tsx` | 管理；不可用提示、指标、成员/角色/策略 Tabs、成员表+详情、Capability 矩阵 | 成员选择、角色矩阵筛选、ScopeGrant 本地预校验（只读） | **P1**：复制管理页 CSS；大矩阵和两栏布局无窄屏证据；写入能力仍按产品边界关闭，UI 不得伪装；仅一条浅 happy E2E | `p18-access.spec.ts` 1 项 happy | — | — | — | — |
| P19 | `/settings/audit`；`src/pages/p19-audit/page.tsx` | 列表/管理；页头、指标、密集筛选、审计表/游标、事件右侧面板、导出不可用区 | 时间/事件/Actor/风险筛选、键盘打开详情、关闭、能力投影 | **P1**：大量 inline style、直接表格和自建 role=dialog；四档只查字节；筛选器在窄屏无结构断言 | `p19-audit.spec.ts` 3 项：happy、敏感字段阻断、三种能力投影 | B | B | B | B |

集合校验：`P01 … P19` 共 19 个唯一 ID，无遗漏、无重复。P01 的 P0 和其余页面的 P1 均为当前最高风险；文案、间距、英文/中文混用、局部图标等 P2 项被更高风险覆盖，后续不应先于 P0/P1 边界处理。

## 4. 公共实现与真实消费者

### 4.1 Shell 与 Scaffold

- `src/app/shell/PlatformShell.tsx` 是唯一 Shell，负责顶栏、作用域三级 `select`、桌面/折叠/移动导航、任务、通知、账户菜单和页面 Outlet；它本身仍直接使用 6 个 `button`、3 个 `select`，移动导航用 `div role="dialog"` 自建焦点圈。
- `src/app/shell/navigation-manifest.ts` 是唯一导航清单，P01–P19 都从这里获得导航 Owner；该边界必须保留。
- 当前没有 `StandardPageScaffold`、`DetailPageScaffold` 或 `WorkbenchScaffold` 实现。`PageHeader` 只被 P09/P10/P11/P13–P18 共 9 页消费，P01–P08/P12/P19 各自实现页头。

### 4.2 `shared/ui` 盘点

`src/shared/ui` 有 15 个 TSX 组件：

| 组件 | 真实消费者/结论 |
|---|---|
| `PageHeader` | P09、P10、P11、P13–P18（9 页） |
| `StandardTable` | P13–P18（6 页）；其余页面继续使用直接 table 或页面私有表格 |
| `StatusBadge` | P09、P10、P11、P13–P18（9 页） |
| `EmptyState` | cleaning 状态层及 P09/P10/P13–P18 |
| `ErrorPanel` | cleaning 状态层及 P13–P18 |
| `SkeletonBlock` | Route Guard、cleaning 状态层、P13–P18 |
| `ForbiddenPanel` | Route Guard、cleaning 状态层 |
| `MetricCard` | P13、P18 |
| `DetailTabs` | P16、P17 |
| `ConfirmDialog` | P03、P13、P14、P16、P17 |
| `CopyableId`、`CursorPager`、`FilterBar`、`RelativeTime`、`SideDrawer` | **零真实消费者**；同名行为由页面/feature 私有实现承担 |

重复实现的直接证据：

- 表格：页面层 22 个直接 `table`，另有 P02/P03/P05 的页面私有 Table 组件；`StandardTable` 只覆盖 P13–P18。
- 筛选：共享 `FilterBar` 零消费者；P02、P03、P05、P06、P07、P08、P09、P10、P12、P19 均自建筛选表单/区域。
- 分页：共享 `CursorPager` 零消费者；datasets feature 另有同名 `CursorPager`，P03/P04/P08/P09/P10/P19 再各自写上一组/下一组。
- 确认/Modal：共享 `ConfirmDialog` 与 `features/datasets/components/ConfirmDialog.tsx` 并存；P02 的 `SourceActionDialogs`/`SourceEditorDialog`、P03 的 `CreateUploadDialog`、P04 的 `DangerousUploadActionDialog`、P09 的命令 Dialog 又各自实现。
- Drawer/Inspector：共享 `SideDrawer` 零消费者；P01/P02/P09/P10/P12/P19 及 Shell 用 `aside`、`section` 或 `div role="dialog"` 自建。
- 页面私有组件：`src/pages/*/components` 共 15 个 TSX 文件，全部集中于 P02–P05。

### 4.3 原生控件与 className 数量

| 区域 | button | input | select | dialog | table | textarea | className |
|---|---:|---:|---:|---:|---:|---:|---:|
| `src/pages` | 149 | 54 | 55 | 4 | 22 | 9 | 384 |
| `src/shared/ui` | 9 | 0 | 0 | 0 | 1 | 0 | 31 |
| `src/app` | 7 | 0 | 3 | 0 | 0 | 0 | 22 |

计数命令只匹配 TSX 中的直接 JSX 标签；通过组件间接产生的 DOM 不重复计数，因此可复核但属于保守下限。

### 4.4 CSS 集中点

- 13 个 CSS 文件全部是全局 CSS；`*.module.css` 数量为 0。
- 最大文件为 `src/features/datasets/components/datasets.css`（610 行/12,473 bytes），其次是 `src/shared/ui/styles.css`（119 行/9,560 bytes）、`p08.css`（85 行/6,937 bytes）、`cleaning.css`（61 行/6,817 bytes）。
- `src/pages/p13-storage-lifecycle/page.css` 至 `p18-access/page.css` 六份均为 84 行/4,277 bytes，SHA-256 均为 `2a8c03b33171a08d03886342a7645f6083a377c588e2f9ee1fa8fe2d1eb380bc`。
- 重复/全局选择器的主要集中点包括 `.management-page`、`.metric-grid`、`.workspace-grid`、`.three-pane`、`.detail-panel`、`.tree-panel`、`.local-tabs`、`.page-header`、`.filter-bar`、`.dialog-actions`。共享 CSS 从 `src/main.tsx` 全局导入，feature CSS 又由多个 route 页面导入；后加载样式可影响先前路由。

## 5. 依赖、路由与构建事实

`frontend/package.json` 当前精确版本：

- React/React DOM `19.1.1`，React Router DOM `7.8.2`，Vite `7.1.3`，TypeScript `5.9.2`。
- TanStack Query `5.85.5`、TanStack Table `8.21.3`。
- React Hook Form `7.62.0`、Zod `4.1.5`。
- ECharts `6.0.0`；Three `0.180.0`、URDFLoader `0.12.6`；Lucide React `0.542.0`；Zustand `5.0.8`。
- 没有 Ant Design 或其他成熟通用 UI 组件库；本任务未安装依赖、未修改 `package.json`/`pnpm-lock.yaml`。

代码使用事实：TanStack Query 被 28 个源文件直接导入，Zod 被 27 个源文件直接导入，TanStack Table 被 7 个源文件直接导入；RHF 当前集中在 `SchemaDrivenAnnotationForm.tsx`；ECharts 集中于 dashboard/storage 两个 chart 模块；Three/URDFLoader 位于 Viewer runtime/lazy loader。

路由事实：P02/P03/P04 的 route module 静态导入页面并提供 `element`，属于 route eager；其余页面使用 `lazy`。`vite.config.ts` 把 ECharts 固定到 `analytics`、Three/URDFLoader 固定到 `viewer`。本次 build 变换 2,886 modules，最大产物如下：

- `analytics-*.js`：1,085.39 kB，gzip 358.69 kB，触发 Vite 500 kB warning。
- `browser-*.js`：383.73 kB，gzip 117.88 kB。
- 主 `index-*.js`：355.47 kB，gzip 110.94 kB；另一入口 149.22 kB，gzip 42.74 kB。
- `StandardTable-*.js`：50.55 kB，gzip 13.75 kB。
- `AnnotationTaskPage-*.js`：45.04 kB，gzip 17.02 kB。
- `viewer-*.js`：0.05 kB，构建明确提示 empty chunk。

## 6. 自动化与视觉断言审计

### 6.1 真实像素比较

- P02/P03/P04：各 4 个宽度，`toHaveScreenshot`；当前工作区有 chromium/mobile 各 4 张，共 24 张基线文件（其中 mobile 文件尚未被 Git 跟踪）。
- 这 24 张图在本任务领取前已经处于 Git 变化状态：12 张已跟踪图为 `M`、12 张 mobile 图为 `A`；文件 mtime 为 11:34–12:43 CST，早于本任务 20:01 CST 开始时间。本任务未生成、删除、覆盖或重录它们，不能把变化归因 TASK-028。

### 6.2 伪视觉或仅附件

- P05/P06/P07：四档通过 `page.screenshot` 后 `testInfo.attach`，不执行像素断言。仓库各有 4 张历史 chromium PNG，但当前测试不引用。
- P08：四档与额外 200% zoom 只检查字节数并附加到报告。
- P01/P12/P19：四档只检查 `byteLength > 1000`，既不比较也不保留仓库基线。
- P09/P10/P11/P13/P14/P15/P16/P17/P18：无 screenshot 调用。
- 所有截图循环只运行 happy/main 场景；合同不匹配、forbidden、empty、STALE、412、unknown enum、offline 等 negative state 没有视觉快照。

### 6.3 本次真实验证

| 命令 | 结果 |
|---|---|
| `pnpm typecheck` | 通过，`tsc -b --pretty false`，exit 0 |
| `pnpm test` | 通过，23 files、122 tests passed |
| `pnpm lint` | 通过，exit 0 |
| `pnpm build` | 通过；2,886 modules；保留 analytics 大 chunk warning 与 empty viewer chunk 事实 |
| `pnpm e2e -- --list` | **失败**，exit 1；额外 `--` 被 Playwright 当成文件过滤条件，报 `No tests found` |
| `pnpm e2e --list` | 通过；19 files、39 logical tests × 2 projects = 78 tests |
| `pnpm e2e tests/e2e/p02-data-sources.spec.ts tests/e2e/p05-datasets.spec.ts tests/e2e/p12-storage-overview.spec.ts --project=chromium --project=mobile --workers=1` | 通过，12 passed（1.3m）；没有使用 `--update-snapshots` |
| `rg` 提取矩阵 ID 后与 `seq -w 1 19` 比较 | 通过，19 rows、19 unique IDs、exact set |
| `git diff --check -- docs/FRONTEND-UI-BASELINE.md` 与对未跟踪文件有效的 `git diff --no-index --check /dev/null docs/FRONTEND-UI-BASELINE.md` | 通过 |
| `find frontend/src frontend/tests frontend/package.json frontend/pnpm-lock.yaml -type f -newermt '2026-08-11 20:01:00'` 及截图子集检查 | 输出为空；任务开始后没有前端实现、测试或截图文件被写入 |

格式验证失败历史：第一次对未跟踪文档执行 `git diff --no-index --check` 时，文档头 4 行的 Markdown 硬换行被报告为 trailing whitespace；移除这 4 处尾空格后，以上两种 diff check 均通过。该失败只影响新建文档格式，不涉及业务代码或截图。

## 7. 下一批最小任务输入

### 7.1 UI 基础设施 ADR 必须裁定

1. Ant Design 与 React 19 的精确版本、按需引入策略、卸载/回退条件；不得顺带改变 React Router、Query、RHF/Zod、权限或上传合同。
2. 标准表格最终保留还是替换 TanStack Table；无论选择哪种，必须保持服务端排序、游标、稳定 row ID、空/加载/错误态，不允许数字 offset 分页。
3. `ConfirmDialog` 与 datasets/page 私有 Dialog 的收敛路径；危险确认必须保留 stable resource ID、影响、blocked reasons、preflight、pending、焦点恢复和冲突恢复。
4. CSS 迁移策略：全局层允许的 selector 白名单、CSS Modules/页面前缀规则、P13–P18 六份重复 CSS 的删除时机，以及旧路由共存期间避免 reset 污染的办法。
5. 视觉基线流程：设计人工结构评审完成后才建立基线；P05–P08/P01/P12/P19 从附件/字节检查迁移为真实比较；P09–P11/P13–P18 补四档和关键 negative state；禁止无说明批量重录。
6. Bundle 预算：`analytics` warning、空 `viewer` chunk、P02–P04 route eager 的处理和失败阈值。

### 7.2 主题与 Provider 文件边界

建议下一任务只允许新建/修改以下基础设施边界，不触碰业务页：

- `frontend/src/app/providers/index.tsx`：只挂载 UI Provider/locale，不改变现有 Query、Scope、Toast 和 ErrorBoundary 顺序语义。
- 新目录 `frontend/src/app/theme/`：`tokens.ts`、`component-theme.ts`、`global.css`；全局 CSS 只承载 reset、主题和 Shell 级规则。
- `frontend/src/main.tsx` 与 `frontend/src/shared/ui/styles.css`：仅为主题入口迁移所需；必须先记录旧全局样式兼容策略。
- `frontend/package.json` 与 `frontend/pnpm-lock.yaml`：只能由依赖安装任务独占修改，必须与并行前端任务隔离。

明确冲突：当前 `frontend/src/app/env.ts`、`playwright.config.ts`、P01/P12 页面及多个 E2E/截图已有他人修改；主题任务不要把这些文件纳入首批范围。

### 7.3 公共组件批次边界

先落地并单测不带业务 Hook 的 `PageState`、`PageHeader`、`FilterToolbar`、`CursorPager`、`StatusTag`、`EntityDrawer`、`DangerConfirmModal`；再落地只接受服务端分页/排序状态的 `DataTable`。不要为每个组件库控件建立无合同价值的薄包装，也不要在这一批删除页面消费者。

### 7.4 P02/P05/P12 试点前置与文件冲突

- 共同前置：ADR、主题 Provider、核心公共组件、四档真实截图流程通过；现有 Route/Query/API/Schema/Adapter/Query Key/权限合同先由测试锁定。
- P02：涉及 `src/pages/p02-data-sources/**`、`src/features/ingest/styles.css` 和 P02 E2E/24 张 P02 中的 8 张基线。当前 P02 spec 和 8 张截图已有他人未提交变化，必须先由 Owner 收敛；不得与 P03/P04 同时改共享 ingest CSS。
- P05：涉及 `src/pages/p05-datasets/**`、`src/features/datasets/components/datasets.css`、datasets 私有分页/确认组件和 P05 E2E。该 CSS 同时被 P06/P07/ViewerShell 消费，迁移时必须跑 P05–P07 跨页视觉回归；现有 4 张 PNG 不是有效断言，先裁定保留或替换。
- P12：涉及 `src/pages/p12-storage-overview/page.tsx`、storage chart 与 P12 E2E。当前页面和 spec 已有他人未提交修改，必须先合并归属；不得在试点中改变费用/bytes 字符串、只读 Multipart 或图表懒加载合同。
- 三个试点必须分任务串行处理与验收；不要让 P02/P05/P12 同时修改公共主题、公共组件或 Playwright config。

## 8. 阶段 0 边界声明

本基线证明当前合同、类型、unit、lint、build 和指定 E2E 可运行，同时证明视觉断言与组件复用仍有系统性缺口。它不证明 UI 已通过设计评审，也不授权实现 Ant Design、修改业务合同、解除 fail-closed 能力或重录截图。后续只能按 ADR、主题/公共组件、三个试点的小批量顺序继续。
