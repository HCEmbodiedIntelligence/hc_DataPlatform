# 前端 UI 组件化重构实施计划

> 状态：待执行  
> 日期：2026-08-11  
> 实施仓库：`/home/czy/hc_DataPlatform`  
> 只读规格源：`/home/czy/plan`  
> 范围：React 前端 P01–P19、公共 Shell、公共 UI 与视觉验收  
> 用户确认：继续使用 React；不要求与设计稿像素级一致，以功能完整、主要布局和颜色大体一致为准

## 1. 结论与实施原则

本次不是更换前端技术栈，也不重写业务合同。保留 React 19、现有路由、
API Client、运行时 Schema、Adapter、TanStack Query、权限、Query Codec、
Mock、Fixture 和领域状态机，在展示层引入成熟 React UI 组件库，逐步替换
原生控件、重复组件和分散 CSS。

采用以下验收原则：

1. 功能与合同正确性优先于视觉还原。
2. 设计稿用于确认信息架构、主要区域、操作层级、青绿色调和紧凑密度，
   不作为逐像素复刻目标。
3. 组件库默认外观不能直接成为产品设计；必须通过统一主题 Token 收敛。
4. 页面迁移只改变展示和交互承载，不改变 HTTP、DTO、权限、状态机和
   跨页业务边界。
5. 每次只迁移一个页面模式或一组页面，完成验证后再扩展，禁止一次性重写
   P01–P19。

## 2. 当前问题基线

当前前端已经具备 React、TypeScript、路由、查询、合同校验和 E2E 骨架，
但 UI 层仍属于功能原型：

- 没有完整 UI 组件库，按钮、输入、筛选、Dialog、Drawer 和表格大量由
  原生 HTML 与页面 CSS 实现。
- `shared/ui` 只覆盖少量基础组件，且各业务页面实际复用不统一。
- ingest、datasets、cleaning、management 页面形成多套颜色、间距和组件形态。
- P13–P18 存在相同 CSS 的多份复制；部分全局类名可能产生路由间样式覆盖。
- 当前视觉测试多数只能证明页面可截图，不能证明布局和视觉已经过设计验收。
- 合同、类型、单测、Lint 和 Build 通过不等于 UI 已达到可交付质量。

本计划不得以 UI 重构为由修改已有业务不变量，也不得把本来
`feature-unavailable` 的能力伪装为已完成。

## 3. 目标与非目标

### 3.1 目标

- 建立一套全站统一的 React UI 基础设施和主题 Token。
- 用成熟组件替换原生按钮、输入、选择器、菜单、Tabs、Modal、Drawer、
  Tooltip、Tag、Skeleton、Empty、Alert 等常用交互。
- 将公共 Shell、页面头、筛选栏、数据表、详情面板和危险确认收敛为唯一实现。
- 使 P01–P19 在功能、主要布局、颜色、密度、响应式和键盘交互上达到一致标准。
- 建立有效的视觉、无障碍和跨页面样式回归门禁。
- 保持真实 API 与 MSW 使用同一 DTO、Schema、Adapter、Hook 和页面组件。

### 3.2 非目标

- 不迁移到 Vue 或其他 UI 框架。
- 不要求与 19 张设计图逐像素一致。
- 不修改 OpenAPI、数据库模型、后端 operation 或领域状态机。
- 不借 UI 重构新增产品功能、角色、权限、自动清洗或其他 V1 外能力。
- 不解除当前尚未获批能力的 fail-closed 状态。
- 不重写 Three.js、Viewer、上传调度器或 OSS Multipart 内核。

## 4. 不得改变的合同边界

下列内容是本次重构的硬边界：

### 4.1 路由与跨页合同

- P01–P19 页面 ID、21 条活动路由及导航 Owner 保持不变。
- Path 使用稳定 ID，不使用名称或 `latest/current` 替代。
- Query 继续由目标页 Codec 解析、规范化和构造。
- `returnTo`、面包屑和跨页入口继续使用 typed route builder。
- 组件的 Tab、Drawer、Inspector 状态不得绕过既有 URL 恢复合同。

### 4.2 API 与数据合同

- 页面不得直接 `fetch` 或手拼 API URL。
- wire JSON 保持 `snake_case`，ViewModel 保持 `camelCase`。
- 所有响应继续先经过运行时 Schema 和 Adapter，再进入 Query Cache。
- int64、bytes、纳秒和大计数继续以十进制字符串传输和计算。
- `null`、缺失、空数组、零值和 UNKNOWN 枚举语义不得被 UI 默认值合并。

### 4.3 权限与危险操作

- 可见性和可操作性继续取 capability、服务端 `allowed_actions`、资源状态和
  本地有效性的交集。
- 组件库的 disabled、hidden 或 loading 只是表现，不是授权事实源。
- 删除、发布、权限变更、生命周期执行、恢复和大规模导出继续执行
  preflight、影响摘要、确认、幂等和并发检查。
- 危险确认不能简化为只有“确定/取消”的 Popconfirm。
- 未知 capability、未知枚举、合同不匹配和授权快照失败继续 fail closed。

### 4.4 分页、表单和上传

- 业务大表继续使用服务端筛选、稳定排序和游标分页；不得切换为浏览器
  offset 分页或任意跳页。
- 保留 React Hook Form 与 Zod 作为表单状态和校验事实源；组件库 Form 不得
  建立第二套字段状态、默认值或校验规则。
- 文件选择可以使用成熟组件外观，但真实上传继续由现有 Multipart Controller、
  授权 Vault 和恢复描述符负责。
- STS、签名 URL、Bucket、Object Key、Multipart UploadId 和 Secret 不得进入
  UI 组件缓存、通知、日志、URL、localStorage 或测试截图。

## 5. 目标 UI 技术方案

### 5.1 组件库选择

在现有 React 工程中引入 Ant Design 作为通用交互组件库。实施时选择与
React 19 兼容的稳定版本并锁定精确版本，不使用浮动版本范围。

保留：

- React 19、TypeScript strict、Vite。
- React Router 与 typed route builder/query codec。
- TanStack Query、Zustand、MSW、Vitest、Playwright。
- React Hook Form + Zod。
- ECharts、Three.js、URDFLoader。
- Lucide React 图标体系。

Ant Design 只承担 UI 表现和基础交互，不成为业务状态、权限、API、缓存或
上传调度的事实源。

### 5.2 表格策略

标准管理表格由项目级 `DataTable` 统一封装，默认使用成熟 Table 表现层，但
强制关闭组件库数字分页并接入现有游标分页合同。

第一步先记录 UI 基础设施 ADR，再决定现有 TanStack Table 的最终处理：

- 若保留，TanStack Table 只负责列、排序和行模型，视觉由统一组件层承载。
- 若替换，必须先证明所有服务端排序、筛选、游标、稳定行 ID、空态、加载态、
  行选择和无障碍测试等价，再删除依赖。
- 禁止长期同时维护两套标准表格公共 API。

### 5.3 表单策略

- React Hook Form 保存字段值、dirty 状态、提交和服务端错误映射。
- Zod 保存本地 Schema 与校验规则。
- Ant Design Input、Select、DatePicker、Checkbox 等通过受控适配器接入。
- `Form.Item` 只用于布局和错误展示，不拥有第二份校验事实。
- Drawer/Modal 关闭时继续执行脏表单确认和焦点恢复。

### 5.4 主题与样式策略

建立唯一主题入口，例如：

```text
src/app/theme/
  tokens.ts
  component-theme.ts
  global.css
```

主题至少统一：

- 青绿色主色、hover、active、focus。
- 页面背景、卡片背景、边框和分隔线。
- 正文、弱化文字、危险、警告、成功和信息色。
- 4/8/12/16/24 间距梯度。
- 8px 以内圆角。
- 紧凑表格、筛选器和页面工具栏高度。
- 字号、行高、阴影、z-index 和响应式断点。

全局 CSS 只保留 reset、主题、Shell 和确有必要的跨页规则。页面样式迁移为
CSS Modules 或明确页面前缀，禁止新增未限定作用域的 `.page-header`、
`.filter-bar`、`.metric-grid`、`.dialog-actions` 等通用类名。

## 6. 公共组件收敛清单

以下公共模块只保留一个实现：

| 模块 | 目标职责 |
|---|---|
| `PlatformShell` | 顶栏、唯一导航清单、作用域选择、任务、通知、账户菜单、响应式导航 |
| `StandardPageScaffold` | 页面头、摘要、筛选、主内容、状态、分页插槽 |
| `DetailPageScaffold` | 面包屑、不可变资源头、Tabs、内容和 Inspector |
| `WorkbenchScaffold` | 稳定左右栏、媒体区、编辑区和时间轴坞站 |
| `PageHeader` | 标题、描述、面包屑和主次操作 |
| `FilterToolbar` | 搜索、枚举筛选、日期、重置、应用和窄屏 Drawer |
| `DataTable` | 服务端排序、稳定行 ID、选择、空态、加载态和响应式列 |
| `CursorPager` | `after/before` 游标上一组/下一组，不显示虚假总页数 |
| `EntityDrawer` | 详情、焦点管理、Escape、返回触发器和窄屏全屏化 |
| `DangerConfirmModal` | 资源 ID、影响、阻断原因、输入确认、pending 和冲突恢复 |
| `StatusTag` | UNKNOWN 安全表现、文字/图标/颜色多重表达 |
| `PageState` | loading、refreshing、empty、error、403、404/410、429、offline、contract mismatch |
| `MetricCard` | 指标、单位、口径、as-of 和无权限/未知值表现 |
| `SecureUploadPicker` | 只负责本地文件选择，绝不自行发送网络请求或保存 Secret |

不为所有 Ant Design 组件再建立一层同名薄包装；只有承载平台合同、主题约束或
跨页面一致行为的组件才进入 `shared/ui`。

## 7. 设计稿使用与视觉验收

### 7.1 必须大体保持

- 页面主要区域和信息顺序。
- Shell、侧栏、页面头、筛选区、摘要区、主表/主内容和右侧详情的层级。
- 青绿色主操作、白/浅灰背景、细边框和紧凑信息密度。
- 主操作、次操作、危险操作的视觉优先级。
- 列表、详情、工作台和分析页之间的基本页面类型差异。
- 设计稿中已经明确且合同支持的字段和操作入口。

### 7.2 不要求一致

- 不要求逐像素尺寸、坐标、字重和间距一致。
- 不要求示例数据、行数、金额、日期和图表数值一致。
- 不要求完全复制图中图标，只需保持 Lucide 线性图标风格和语义一致。
- 响应式布局可以根据成熟组件行为调整，只要核心内容、操作和返回路径可用。
- 组件库为可访问性增加的提示、焦点轮廓和辅助文本可以与设计图不同。

### 7.3 截图门禁

- 正式设计图用于首次人工结构评审，不直接作为像素差异基线。
- 页面首次通过产品/UI 人工评审后，将实现截图锁定为自动回归基线。
- P01–P19 至少覆盖 1440、1024、768、390 四档截图。
- 1280 和 200% zoom 按工程基线进行人工/自动补充验证。
- 删除“只检查截图大于 1KB”的伪视觉断言。
- 基线更新必须附视觉差异说明，禁止为通过 CI 无审查批量重录。

## 8. 分阶段实施

### 阶段 0：基线、ADR 与问题分级

交付：

1. 记录当前 19 页桌面和窄屏截图、当前 Bundle 和自动化结果。
2. 新增 UI 组件库 ADR，写清 React 保留、组件库边界、表格决策和回退方式。
3. 建立 P01–P19 功能/布局/视觉问题矩阵。
4. 将问题分为：
   - P0：主流程不可用、权限错误、数据泄漏、合同绕过。
   - P1：主要布局错误、操作不可发现、响应式不可用、视觉体系分裂。
   - P2：间距、文案、图标和局部细节。
5. 将当前状态明确为“合同实现与 UI 验收分离”，不得再用测试全绿代表视觉完成。

完成标准：ADR 评审通过；19 页均有截图和问题清单；没有修改业务合同。

### 阶段 1：公共主题、Shell 与核心组件

交付：

1. 安装并锁定 UI 组件库。
2. 建立 `ConfigProvider`、中文 Locale、主题 Token 和全局样式层。
3. 重构 `PlatformShell`，保留唯一 `NavigationManifest` 和全部权限过滤逻辑。
4. 落地第 6 节公共组件。
5. 统一 Toast/Notification、错误态、Skeleton、Empty、Forbidden 和 Contract Mismatch。
6. 增加静态门禁，禁止页面新增原生 Dialog、未受控危险确认和无作用域全局 CSS。

完成标准：公共组件 Story/Harness、键盘测试、主题测试和合同测试通过；旧页面仍可运行。

### 阶段 2：三个代表页面试点

按顺序迁移：

1. P02 数据源：验证列表、筛选、行操作、详情 Drawer 和危险操作。
2. P05 数据集：验证高密表格、摘要、组合筛选、游标分页和跨页跳转。
3. P12 存储容量：验证指标、图表、Tabs、只读详情和异步加载。

试点阶段同时确定：

- 标准表格最终实现。
- 筛选栏在 1024/768/390 的折叠规则。
- Drawer、Modal、状态 Tag 和页面头的最终视觉规格。
- 人工设计评审和自动截图基线流程。

完成标准：三个页面功能与负向流程不回归；主要布局和颜色通过人工评审；四档截图锁定。

### 阶段 3：标准页面批量迁移

迁移：P01、P03、P04、P06、P07、P09、P10、P13、P18、P19。

执行规则：

- 按页面模式复用公共骨架，不按页面复制 CSS。
- 每页先迁移只读结构，再迁移写操作和危险确认。
- 每完成一页即删除该页已经无消费者的旧组件和样式。
- 不修改页面 API、Query Key、Route Codec 和 Fixture 语义。

完成标准：十页逐页通过合同、主流程、主要失败流程、响应式、键盘和截图验收。

### 阶段 4：系统管理复杂页迁移

迁移：P14、P15、P16、P17。

重点：

- 三栏/主从布局、树、Tabs、详情和历史记录。
- 文件选择、验证报告和发布/停用确认。
- Robot/Component/Frame/Calibration/Schema 固定引用关系。
- 3D 区域资源生命周期和按需加载。

成熟组件只能承载树、表格、表单和面板，不得改写 `RobotSceneCore` 或资产绑定合同。

完成标准：四页主要布局可用，资源从属关系和危险操作测试保持通过，3D/Bundle 无明显回归。

### 阶段 5：工作台迁移

迁移：P08 数据标注、P11 手动清洗。

重点：

- 保持 `WorkbenchScaffold` 稳定布局。
- 不让高频播放状态进入组件库 Form、全局 Store 或整页 React 重渲染。
- 保持 `EpisodeWorkbenchCore`、`PlaybackClock`、`RobotSceneCore` 和时间区间事实不变。
- 在窄屏下将次要面板抽屉化，不牺牲播放、保存、提交和错误恢复入口。

完成标准：播放/编辑/保存/预检/提交主流程、局部资源失败、性能和资源释放测试通过。

### 阶段 6：全站验收与清理

交付：

1. P01–P19 四档视觉回归。
2. 键盘、焦点、Escape、焦点返回、200% zoom 和自动无障碍检查。
3. 三角色、权限撤销、unknown/reserved capability 和 `allowed_actions` 回归。
4. CSS 重复、全局选择器、原生控件例外和未使用样式扫描。
5. Bundle、图表、3D 和路由 chunk 预算复验。
6. 删除旧公共组件、重复页面组件和确认无消费者的 CSS。
7. 更新实施状态，明确哪些页面只是 Mock-ready、哪些已真实联调、哪些功能仍 conditional。

完成标准：所有门禁通过，UI 人工验收完成，未关闭能力继续明确 fail closed。

## 9. 每页迁移固定步骤

每个页面按以下顺序执行：

1. 对照设计稿和逐页执行规格列出功能与主要区域。
2. 锁定现有 Route、Query、API、权限、状态机和 E2E 行为。
3. 将页面替换为公共 Scaffold 和成熟组件，不改变 Hook 与 ViewModel。
4. 迁移 loading、empty、partial error、fatal error、forbidden、conflict、offline、
   contract mismatch 和 unknown enum。
5. 迁移写操作、pending、preflight、确认、冲突和恢复。
6. 验证 1440、1024、768、390 和键盘操作。
7. 通过人工结构/颜色评审后建立实现截图基线。
8. 删除该页旧 CSS 和重复组件。
9. 运行页面定向测试，再运行前端全量门禁。

## 10. 自动化与质量门禁

最低命令保持：

```bash
cd /home/czy/hc_DataPlatform/frontend
pnpm typecheck
pnpm test
pnpm lint
pnpm build
pnpm e2e
```

新增门禁：

- P01–P19 主流程截图断言，不再只保存附件或检查字节数。
- 自动无障碍扫描不得存在 serious/critical 级问题。
- Dialog、Drawer、Menu、Tabs、Tree 和危险确认必须覆盖键盘与焦点测试。
- 页面不得新增直接 `fetch`、手写 API URL、角色名授权判断或数字 offset 分页。
- 页面不得使用未经适配的组件库 Upload 自动请求。
- 页面不得在普通通知、日志和截图中输出 Secret 或完整对象定位信息。
- 管理页首个路由 JS gzip 继续满足现有预算；图表和 3D 必须按需加载。

## 11. Definition of Done

一个页面只有同时满足以下条件才算完成 UI 重构：

- 功能点与逐页执行规格一致，正常和主要失败流程可用。
- Route、Query Codec、API、Schema、Adapter、Query Key 和权限合同无变化或有明确评审记录。
- 主要区域、操作层级、青绿色调和紧凑密度与设计方向大体一致。
- 不要求像素级复刻，但不能遗漏设计稿和合同共同要求的区域或操作。
- 使用公共主题和公共组件，不保留页面私有 Button/Badge/Dialog/Drawer 体系。
- 游标分页、表单校验、危险确认和上传安全边界保持不变。
- 1440、1024、768、390、键盘、焦点和 200% zoom 验收通过。
- happy、empty、error、forbidden、conflict、contract mismatch 和 unknown 状态可验证。
- 定向测试和前端全量门禁通过。
- 已删除确认无消费者的旧组件和样式。

## 12. 风险与控制

| 风险 | 控制措施 |
|---|---|
| 组件库默认主题偏离设计 | 先建 Token 和三个试点页，禁止页面单独覆写主题 |
| Table 默认数字分页破坏游标 | 公共 `DataTable + CursorPager` 强制关闭数字分页 |
| Form 建立第二套状态 | RHF/Zod 保持事实源，成熟组件只作受控输入和布局 |
| Upload 自动请求泄漏授权 | `SecureUploadPicker` 只选文件，传输继续走现有 Controller/Vault |
| Modal 简化危险操作 | 统一 `DangerConfirmModal` 保留 preflight、影响、阻断和并发前提 |
| CSS Reset 导致旧页变化 | 主题分层、路由分批迁移、每批执行跨页截图回归 |
| UI 迁移误改权限 | 保留现有 Hook 和 Gate，三角色与权限撤销测试作为硬门禁 |
| Bundle 增长 | 按需引入、路由切分、构建预算检查 |
| 批量重录掩盖回归 | 基线变更必须人工审核并记录差异原因 |

## 13. 交付物

- UI 基础设施 ADR。
- 统一主题 Token 与全站 Provider。
- 公共 Shell、Scaffold 和第 6 节公共组件。
- P01–P19 完成迁移的页面代码。
- 页面问题矩阵和迁移状态矩阵。
- 四档实现截图基线与评审记录。
- 自动无障碍、视觉、键盘、性能和敏感信息门禁。
- 旧组件/CSS 删除清单。
- 更新后的实施状态与仍未关闭的 conditional 能力清单。

## 14. 执行约束

- `/home/czy/plan` 继续作为只读规格源，不在 UI 实施过程中修改。
- 当前工作区已有未提交改动，执行者必须保留并审查，禁止 reset 或批量覆盖。
- 每个阶段以可运行、可回归的小批次落地。
- 任何业务合同变化必须独立提出，不得混在“换组件”提交中。
- 不执行未经确认的依赖大版本升级、Git 提交或推送。
