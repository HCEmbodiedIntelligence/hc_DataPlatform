# ADR 0001：React UI 组件系统与阶段 1 边界

- 状态：Proposed（待独立复检；通过后供阶段 1 任务使用）
- 决策日期：2026-08-12
- 元数据核对时间：2026-08-12 19:08 CST
- 范围：前端 UI 基础设施与 `FRONTEND-UI-REFACTOR-PLAN.md` 阶段 1
- 依据：`docs/FRONTEND-UI-REFACTOR-PLAN.md` §4–§6、§8、§10–§14，`docs/FRONTEND-UI-BASELINE.md`

## Context

当前应用已经以 React 19 实现 P01–P19、21 条活动路由以及完整的 Route、Query、
API、运行时 Schema、Adapter、TanStack Query、权限、Mock 和领域状态机合同。
这次工作要收敛表现层，而不是重写这些合同：继续使用 React，不迁移 Vue，
不修改业务合同，也不以设计稿做像素级复刻。

UI 仍是功能原型。阶段 0 基线记录了页面层至少 149 个原生 `button`、54 个
`input`、55 个 `select`、4 个 `dialog`、22 个 `table` 和 9 个 `textarea`；
13 份 CSS 全是全局 CSS。`shared/ui` 有 15 个组件，但 5 个没有消费者，页面仍
并存多套表格、筛选、分页、Drawer 和危险确认。

当前依赖与真实消费者如下（均为 2026-08-12 只读核对结果）：

| 能力               | 当前精确版本         | 使用事实与本 ADR 边界                                                                                 |
| ------------------ | -------------------- | ----------------------------------------------------------------------------------------------------- |
| React / React DOM  | `19.1.1`             | 保留；应用由 Vite `createRoot` 启动，当前没有 SSR 入口                                                |
| TanStack Query     | `5.85.5`             | 28 个源文件直接导入；继续作为服务端状态和 Query Cache 事实源                                          |
| TanStack Table     | `8.21.3`             | `StandardTable` 和 P13–P18 共 7 个源文件直接导入；现有实现已使用手动排序、筛选、分页和稳定 `getRowId` |
| React Hook Form    | `7.62.0`             | 当前集中在 `SchemaDrivenAnnotationForm.tsx`；继续拥有字段值、dirty、submit 和服务端字段错误           |
| Zod                | `4.1.5`              | 27 个源文件直接导入；继续拥有运行时数据和表单校验规则                                                 |
| Lucide React       | `0.542.0`            | 5 个源文件直接导入；继续作为产品图标体系                                                              |
| ECharts            | `6.0.0`              | Dashboard 与 Storage 图表按需加载；不由组件库替换                                                     |
| Three / URDFLoader | `0.180.0` / `0.12.6` | 只在 Viewer runtime 中加载；不改写 `RobotSceneCore` 或资产绑定合同                                    |

`src/app/providers/index.tsx` 当前唯一生产 Provider 顺序是
`ErrorBoundary → QueryClientProvider → ToastProvider → ScopeProvider → RouterProvider`；
`ProviderHarness` 复用 Query、Toast 和 Scope。UI Provider 必须插入同一入口并保留
Query、Toast、Scope 和 Router 的相对顺序，页面不得自行创建第二个全局主题。

## Decision

### 1. 技术栈与组件库

1. 保留 React 19、TypeScript strict、Vite、React Router、typed route builder、
   Query Codec、TanStack Query、Zustand、RHF/Zod、MSW、Vitest、Playwright、
   Lucide、ECharts、Three.js 和 URDFLoader。
2. 通用交互表现层采用 Ant Design，并在依赖批次把 `antd` **精确锁定为
   `6.6.0`**，不得使用 `^`、`~`、`latest` 或 workspace 浮动范围。
3. Ant Design 只提供表现、可访问性交互和主题能力，不拥有 Route、API、DTO、
   Query Cache、权限、表单状态、上传传输或业务状态机。
4. 不为每个 Ant Design 控件建立同名薄包装。只有承载平台合同或跨页一致行为的
   组件进入 `shared/ui`。
5. 产品图标继续直接使用 Lucide。`antd@6.6.0` 虽传递依赖
   `@ant-design/icons`，业务代码不得把它作为第二套产品图标 API。

#### 版本与兼容性证据

2026-08-12 19:08 CST 在 `frontend/` 执行：

```bash
pnpm view antd version dist-tags.latest peerDependencies peerDependenciesMeta engines --json
pnpm view antd@6.6.0 version peerDependencies dependencies engines license repository homepage --json
pnpm view antd@6.6.0 time --json
```

可复现结果：registry 的 `latest` 和精确版本均为 `6.6.0`；其 peer 声明是
`react >=18.0.0`、`react-dom >=18.0.0`，因此覆盖项目的 React/React DOM
`19.1.1`；版本发布时间为 `2026-08-10T09:10:01.335Z`，license 为 MIT。
候选 tarball integrity 为
`sha512-UDwWIbpmrCHB9ZQ+bPh4vQfB6DTI2ulIyoQ0Tc9xxalFblttiNGHl3ySBD9SyV/8+gUjFzfSx1+iU1Fog2i46w==`。
本 ADR 只读取元数据，没有安装依赖或修改 lockfile。若依赖批次无法解析这个精确
版本或完整性校验不一致，必须停止并回到 ADR 复审，不得静默换成新版本。

### 2. 唯一 Provider、Locale 与主题入口

唯一入口及预计文件边界为：

```text
src/app/providers/
  UiProvider.tsx             # ConfigProvider、Ant Design App、zh_CN
  index.tsx                  # 生产与测试 Harness 只在这里组合 Provider
src/app/theme/
  tokens.ts                  # 平台语义 Token，不含业务状态
  component-theme.ts         # Ant Design component token 映射
  global.css                 # 最小 reset、主题、Shell 级规则
src/main.tsx                 # 只导入唯一全局主题入口
```

生产顺序固定为：

```text
ErrorBoundary
└─ UiProvider(ConfigProvider + Ant Design App + zh_CN)
   └─ QueryClientProvider
      └─ ToastProvider
         └─ ScopeProvider
            └─ RouterProvider
```

`QueryClientProvider → ToastProvider → ScopeProvider → RouterProvider` 的相对顺序
不变。`ToastProvider` 保持应用唯一通知 API；若内部改用 Ant Design App context，
也不得允许页面直接调用静态 `message`/`notification` 形成第二条通知通道。

`UiProvider` 同时供 `AppProviders` 和 `ProviderHarness` 使用。组件 Harness、Vitest
和 Story/Harness 必须复用它，不得复制 Token。当前应用无 SSR，因此阶段 1 不新增
SSR 分支；将来引入 SSR 时必须另立 ADR 处理 CSS-in-JS 样式提取和 hydration，不能
在页面内临时补 Provider。

Locale 固定为 Ant Design `zh_CN`，只控制展示文案、日期控件和组件交互文本；
服务端时间、纳秒、时区、枚举和 wire 值仍由既有 Codec/Adapter 决定。页面不能
用 Locale 格式化覆盖十进制字符串、`UNKNOWN` 或缺失/空值语义。

主题以现有青绿色方向为起点，将主色、hover/active/focus、背景、边框、正文、
弱化文字、危险/警告/成功/信息色、4/8/12/16/24 间距、≤8px 圆角、紧凑控件高度、
字号/行高、阴影、z-index 和断点映射到 Ant Design Token。页面不得嵌套
`ConfigProvider` 改全局主题；唯一例外是组件测试在 `UiProvider` 上注入已声明的
测试配置，且不能进入生产页面。

不在首批全局导入 Ant Design reset。`global.css` 只承载最小 reset、主题和 Shell；
组件样式由 ConfigProvider 管理。页面样式迁移到 CSS Modules 或 `pXX-` 明确前缀，
禁止新增无作用域 `.page-header`、`.filter-bar`、`.metric-grid`、
`.dialog-actions` 等选择器。既有 `src/shared/ui/styles.css` 在迁移期仅作兼容层，
按消费者删除，不能继续新增公共规则。

### 3. 表格：保留 TanStack headless，Ant Design 仅作表现层

作出唯一决策：**保留 TanStack Table 作为标准表格的 headless 列、行模型和受控
状态层；Ant Design Table 作为 `DataTable` 的默认内部表现层。** 不替换
TanStack Table，也不向页面暴露 Ant Design 的第二套 Table API。

原因是现有 `StandardTable` 已用 `ColumnDef`、`useReactTable`、稳定 `getRowId`、
`manualSorting`、`manualFiltering` 和 `manualPagination`，且 P13–P18 已依赖这套
headless 语义。当前没有证据证明直接替换能等价覆盖服务端筛选、稳定排序、游标、
行选择、空态、加载态、响应式列和无障碍；在 UI 重构中承担该替换风险没有收益。

唯一公共 API 是项目级 `DataTable<T>`：

- 接受 TanStack `ColumnDef<T>`、稳定 `getRowId`、受控 server sorting/filtering、
  selection、loading/empty/error 和响应式列策略。
- 内部把 TanStack header/cell/row model 映射到 Ant Design Table；Ant Design
  `columns`、`TableProps`、数字分页、客户端排序/筛选状态不得透出 `shared/ui`。
- 强制 `pagination={false}`，不得显示页码、总页数、跳页器或把游标转换为 offset。
- `CursorPager` 只接收既有 `PageInfo` 的 `start_cursor`/`end_cursor` 和
  `has_previous_page`/`has_next_page`，只提供“上一组/下一组”；cursor 不进入 URL
  以外的新状态事实源。
- 稳定 row ID 缺失、未知排序字段或不完整 PageInfo 一律 fail closed，不以数组索引、
  名称或 `latest/current` 代替。
- `StandardTable` 只允许在逐页迁移期作为旧名称兼容出口，并在消费者归零后删除；
  新页面不得使用它。页面不得直接导入 `antd/es/table` 或创建第二个标准表格封装。

若 Ant Design Table 的内部 renderer 在试点中不能满足键盘、语义或 bundle 门禁，
回退的是 `DataTable` 内部 renderer（回到主题化 semantic table），不是 TanStack
headless 合同，也不是新增另一个公共表格 API。

### 4. 表单：RHF/Zod 是唯一事实源

`RHF + Zod` 的职责不变：

- RHF 拥有值、default values、dirty、touched、submit、reset 和服务端字段错误映射。
- Zod 拥有 schema 和本地校验；既有运行时 wire schema 不转移到 Ant Design。
- `RHFInput`、`RHFSelect`、`RHFDatePicker`、`RHFCheckbox` 等适配器使用 RHF
  `Controller`/`useController` 把值和事件接入 Ant Design 控件。
- `Form.Item` 只做 label、help、status 和布局，不声明 rules、不保存 initialValues、
  不调用第二次 validate，也不独立提交。
- server JSON Pointer 映射、unknown/unsupported 字段只读保留、dirty 关闭确认、
  Modal/Drawer 焦点恢复继续沿用现有合同。

表单适配器放在 `src/shared/ui/forms/`，但领域 schema 编译、默认值和错误映射仍留在
各 feature。禁止建立全站巨型动态表单 schema 或把业务字段定义写进 UI 层。

### 5. 上传：SecureUploadPicker 只能选文件

`SecureUploadPicker` 可使用 Ant Design Upload/Dragger 的外观，但只返回浏览器本地
`File`/`FileList`，不拥有网络和恢复状态：

- 必须使用受控 `fileList`，以 `beforeUpload={() => false}` 阻止自动请求；不得设置
  `action`，不得提供发请求的 `customRequest`，不得把组件状态当上传队列。
- 文件只在当前交互的内存中存活，取消、完成和卸载时清空；不写 Query Cache、URL、
  localStorage、遥测、通知或截图。
- STS、签名 URL、Bucket、Object Key、Multipart UploadId、Secret、完整对象定位和
  授权 Header 不得成为 props、错误文案、日志或可序列化状态。
- `accept`、数量、大小等前端检查只是本地有效性，不替代服务端授权和验证。

静态门禁必须拒绝 `SecureUploadPicker` 中的 `action`/网络 `customRequest`，并拒绝
页面直接使用未经适配的 Ant Design Upload 发请求。

### 6. 危险操作、权限与状态

`DangerConfirmModal` 是承载平台危险操作合同的唯一组件，不是简单 Popconfirm：

- 展示不可变资源 ID、动作、影响摘要、阻断原因、preflight 时间/版本以及需要时的
  输入确认；preflight 缺失、过期、scope 改变或存在 blocker 时禁止确认。
- 保留 pending、取消、Escape、焦点圈、关闭后焦点返回以及窄屏全屏行为。
- 组件只呈现调用者提供的 intent；幂等 key、If-Match/资源版本和 409/412 冲突恢复
  仍由既有 mutation/feature 负责，Modal 不自行请求、不缓存授权、不吞并错误。
- 删除、发布、权限变更、生命周期执行、恢复和大规模导出必须走这个合同；普通
  Ant Design `Popconfirm` 不得用于这些操作。

权限事实源仍是 capability、服务端 `allowed_actions`、资源状态与本地有效性的交集。
`disabled`、`hidden`、loading、Menu 或 Modal 只是表现。未知 capability、未知枚举、
合同不匹配和授权快照失败继续 fail closed；任何组件不得按角色名直接授权。

`PageState` 统一 loading、refreshing、empty、error、403、404/410、429、offline、
contract mismatch 和 unknown，但只呈现状态，不改写 DomainError 或重试策略。
`StatusTag` 必须用文字/图标/颜色多重表达，并为 `UNKNOWN` 提供安全只读表现。

### 7. 公共组件和文件所有权

阶段 1 只建立以下平台合同组件：

| 目录                     | 唯一职责                                                                                                         |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| `src/shared/ui/layout/`  | `StandardPageScaffold`、`DetailPageScaffold`、`WorkbenchScaffold`、`PageHeader`、`FilterToolbar`、`EntityDrawer` |
| `src/shared/ui/state/`   | `PageState`、`StatusTag`、`MetricCard` 与统一 Skeleton/Empty/Forbidden/Contract Mismatch 表现                    |
| `src/shared/ui/data/`    | `DataTable`、`CursorPager`；服务端排序/筛选/游标合同                                                             |
| `src/shared/ui/forms/`   | RHF 受控输入适配器和 `SecureUploadPicker`                                                                        |
| `src/shared/ui/actions/` | `DangerConfirmModal`                                                                                             |

页面不通过组件库重建 Route、Query、API、权限、Toast、表单、上传或数据状态。旧
`PageHeader`、`StandardTable`、`ConfirmDialog`、`SideDrawer` 等按消费者迁移，
不在公共组件创建批次中批量删除。

## Alternatives considered

### A. 迁移 Vue 或重写前端

拒绝。用户和计划已确认继续 React；重写会把 UI 收敛与 21 条路由及业务合同迁移
耦合，无法证明等价。

### B. 保持全部原生控件和页面 CSS

拒绝。阶段 0 已证明原生控件、重复组件、13 份全局 CSS 和视觉门禁的系统性缺口，
继续局部修补不能形成唯一 UI 系统。

### C. 页面直接使用 Ant Design，不建平台合同组件

拒绝。这样会让 Table 数字分页、Form rules、Upload 自动请求和简单 Popconfirm 绕过
既有业务边界，也会让主题和交互再次分叉。平台包装只用于确有合同价值的组件。

### D. 直接删除 TanStack Table，全面使用 Ant Design Table 状态

拒绝。当前 7 个源文件已经依赖 TanStack headless 语义，且没有等价证据覆盖手动
服务端排序/筛选、游标和稳定行 ID。保留 headless 层可把视觉迁移与数据合同解耦。

### E. 同时长期公开 TanStack 和 Ant Design 两套表格 API

拒绝。双 API 会产生两套列、排序、空态和分页行为。对页面只公开项目
`DataTable`；Ant Design Table 是可回退的内部 renderer。

### F. 采用 Ant Design Form 或 Upload 作为新业务事实源

拒绝。Form 会复制 RHF/Zod 状态，Upload 会绕过 Multipart Controller/Vault；二者
都违反既有合同与敏感信息边界。

## Consequences

正向结果：

- React 与现有业务合同不动，UI 可按页面模式小批量迁移。
- 主题、Locale、通知入口、表格、表单、上传和危险确认各有一个公共边界。
- TanStack headless 与 Ant Design renderer 解耦，游标分页和服务端状态可验证、可回退。
- ProviderHarness 与生产 Provider 同源，减少测试与生产主题/portal 行为差异。
- 可用静态扫描阻止 direct fetch、数字分页、Upload 自动请求、角色名授权和全局 CSS
  再次扩散。

代价与约束：

- `antd@6.6.0` 及传递依赖会增加安装和 bundle 体积，必须做真实构建预算检查和按需
  路由加载；不能因 tree-shaking 假设跳过测量。
- TanStack 到 Ant Design Table 的内部适配需要覆盖 header/cell、受控排序、selection、
  loading/empty 和键盘语义；适配代码必须留在 `DataTable` 内。
- 迁移期间旧 CSS 和旧组件会短期共存，但只能减少消费者，不能成为新功能入口。
- ConfigProvider、portal、Modal/Drawer 焦点和 Locale 使组件测试必须使用统一 Harness。
- UI 组件库默认外观不是产品验收；三个试点仍需人工结构/颜色评审与四档截图锁定。

## 十二项风险控制

|   # | 风险                                 | 强制控制与证明                                                                                                           |
| --: | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------ |
|   1 | 默认主题偏离设计方向                 | 先落唯一 Token/ConfigProvider，再做 P02/P05/P12；页面无嵌套全局主题，人工结构/颜色评审通过                               |
|   2 | Table 数字分页破坏游标               | `DataTable pagination=false` + `CursorPager`；测试断言无页码/跳页并只消费四个 PageInfo 字段                              |
|   3 | 两套表格 API 长期并存                | 页面只导入项目 `DataTable`；静态扫描拒绝页面直引 Ant Design Table，新消费者不得使用 `StandardTable`                      |
|   4 | Form 建立第二套值和校验              | RHF/Zod 唯一事实源；适配器测试覆盖 dirty、reset、server error、unknown 字段，拒绝 `Form.Item rules`/`initialValues`      |
|   5 | Upload 自动请求或泄漏授权            | `beforeUpload=false`、无 `action`/网络 `customRequest`；测试证明选择文件不发请求且敏感字段不进入状态/日志/截图           |
|   6 | 危险确认退化成确定/取消              | `DangerConfirmModal` 保留 preflight、影响、blocker、资源 ID、输入确认、pending、版本与冲突恢复；危险动作禁用 Popconfirm  |
|   7 | CSS reset/全局类破坏旧页             | 首批不全局导入 Ant reset；新增 CSS Module 或 `pXX-` 前缀，分批跑跨页截图，旧全局 CSS 只减不增                            |
|   8 | UI 迁移误改权限或 fail-open          | 保留 capability Hook、`allowed_actions` 和 Gate；三角色、撤权、unknown/reserved、scope 切换测试为硬门禁                  |
|   9 | UI 组件绕过 Route/API/数据合同       | 静态扫描 direct fetch/手写 URL/角色名/offset；合同测试锁定 typed route、Codec、Schema、Adapter、Query Key 与十进制字符串 |
|  10 | bundle、图表或 3D 回归               | 记录依赖前后构建产物；路由切分，ECharts/Three 继续 lazy；超预算或 viewer 变化即回退该批                                  |
|  11 | 批量重录截图掩盖回归                 | 禁止 `--update-snapshots` 作为验收；四档差异需人工审核、原因记录，negative state 建独立基线                              |
|  12 | Provider/Portal/焦点在测试与生产分叉 | App 与 Harness 共用 `UiProvider`；测试覆盖 zh_CN、portal、Tab/Escape、焦点圈与返回、390px 和 200% zoom                   |

## Rollout

阶段 1 拆成文件所有权互斥的小批次。每一批单独领取、验证和回退，不允许跨批“顺手”
迁移业务页面。依赖图：

```text
B1 精确依赖锁定
└─ B2 Provider + Locale + Theme
   ├─ B3 PlatformShell
   ├─ B4 Scaffold + 状态组件
   ├─ B5 DataTable + CursorPager
   └─ B6 RHF 适配 + SecureUploadPicker + DangerConfirmModal
      （B4/B5/B6 也依赖 B2，彼此文件互斥，可分别验收）
B3 + B4 + B5 + B6
└─ B7 公共出口 + 静态门禁 + 阶段 1 全量验证
```

### B1：依赖锁定

- 独占文件：`package.json`、`pnpm-lock.yaml`。
- 工作：重新核对 registry，精确加入 `antd: "6.6.0"`，执行 frozen install 复验；不改
  源码、CSS、测试或截图。
- 验证：lockfile diff、`pnpm install --frozen-lockfile`、license/peer 检查、依赖树。
- 回退点：只回退 B1 的两文件；任何解析/完整性/peer 异常都不得进入 B2。

### B2：Provider、Locale 与主题

- 独占文件：`src/app/providers/UiProvider.tsx`、`src/app/providers/index.tsx`、
  `src/app/providers/ToastProvider.tsx`、`src/app/theme/**`、`src/main.tsx`，以及只测该边界的
  `tests/contracts/ui-provider.spec.tsx`。
- 工作：挂唯一 ConfigProvider/Ant Design App/`zh_CN`、语义 Token 和兼容 CSS 层；
  App 与 Harness 共用入口，保留既有 Provider 相对顺序。
- 验证：Provider/Locale/Toast/portal/主题单测，typecheck、lint、unit、build，P01–P19
  关键路由 smoke 与四档跨页截图（不更新基线）。
- 回退点：恢复原 Provider 组合和 `shared/ui/styles.css` 单入口；不触碰 B1 lock。

### B3：PlatformShell

- 独占文件：`src/app/shell/**` 与 `tests/contracts/platform-shell-ui.spec.tsx`。
- 工作：保留唯一 `navigation-manifest.ts`、Scope 选择与权限过滤，只替换顶栏、导航、
  Drawer、Menu 和响应式表现。
- 验证：三角色/撤权/scope transaction、21 路由 Owner、键盘/焦点、1024/768/390、
  typecheck/lint/unit/build。
- 回退点：按 Shell 文件整体回退，不改 Provider、路由注册表或页面。

### B4：Scaffold 与状态组件

- 独占文件：`src/shared/ui/layout/**`、`src/shared/ui/state/**`、
  `tests/contracts/ui-layout-state.spec.tsx`；本批不改根 `src/shared/ui/index.ts`。
- 工作：实现三种 Scaffold、PageHeader、FilterToolbar、EntityDrawer、PageState、StatusTag、
  MetricCard Harness，不接业务 Hook、不删除旧组件。
- 验证：全部状态矩阵、UNKNOWN/fail-closed、键盘/焦点、四档 Harness 截图、a11y、
  typecheck/lint/unit/build。
- 回退点：删除本批新增目录即可，旧页面无行为变化。

### B5：DataTable 与 CursorPager

- 独占文件：`src/shared/ui/data/**`、`tests/contracts/ui-data-table.spec.tsx`；本批不改
  `StandardTable.tsx`、页面或根 index。
- 工作：实现唯一 DataTable API、TanStack headless/Ant Design renderer 和游标分页。
- 验证：server sorting/filtering、稳定 row ID、selection、empty/loading/error、PageInfo
  边界、无数字分页、键盘/a11y、10k 行输入不做客户端全量业务分页、bundle diff。
- 回退点：仅切换 DataTable 内部 renderer 或删除新增目录；TanStack 和旧表格不变。

### B6：表单、上传与危险确认

- 独占文件：`src/shared/ui/forms/**`、`src/shared/ui/actions/**`、
  `tests/contracts/ui-form-upload-danger.spec.tsx`；不改 feature、Controller、Vault 或页面。
- 工作：RHF 受控适配器、SecureUploadPicker、DangerConfirmModal Harness。
- 验证：dirty/reset/server error/unknown；文件选择零请求与卸载清理；preflight 缺失/过期/
  blocker/scope 变化；pending、409/412 回调、Tab/Escape/焦点返回；敏感信息扫描。
- 回退点：删除本批新增目录，业务表单、Multipart 和旧 ConfirmDialog 保持可运行。

### B7：公共出口与静态门禁

- 独占文件：`src/shared/ui/index.ts`、UI 架构门禁测试及必要的测试脚本；不改业务页面、
  `playwright.config.ts` 或截图。
- 工作：一次性发布 B4–B6 公共出口；禁止新原生 Dialog、页面直引 Ant Table/Upload
  请求、direct fetch、手写 API URL、角色名授权、数字 offset、无作用域全局 CSS 和危险
  Popconfirm。门禁只约束新增/迁移代码，既有债务以基线白名单逐项减少，不扩大白名单。
- 验证：typecheck、unit、lint、build、E2E `--list`、公共 Harness、Shell smoke、合同与
  静态门禁；记录 bundle 前后差异，不重录截图。
- 回退点：回退根出口和门禁文件；B2–B6 已验收模块保留但不供页面使用。

阶段 2 的 P02、P05、P12 只能在 B7 通过后串行开始。每个试点先用测试锁定 Route、
Query、API、Schema、Adapter、Query Key、权限和失败状态，再迁展示；试点不得反向修改
公共主题、公共组件或 Playwright 配置。P02 的现有截图变化、P05 的跨 P06/P07 CSS
消费者、P12 的现有页面/spec 修改必须先由各自 Owner 收敛。

## Rollback

1. 每批以文件所有权为最小回退单元；不得用全仓 reset、覆盖他人修改或同时回退已经
   通过的前置批次。
2. 依赖问题在 B1 停止；Provider/CSS 问题回退 B2；Shell、Scaffold、DataTable、表单/
   上传/危险确认分别回退 B3–B6，不改变业务 Hook 或合同。
3. 页面试点出现回归时，回退该页面到旧 renderer，同时保留公共基础设施继续修复；
   不恢复第二套公共 API。
4. DataTable 表现层失败时只回退内部 renderer，TanStack headless 与项目公共 props 不变。
5. 截图差异不允许用批量重录“回退”；应恢复造成差异的代码或经人工评审逐张批准。
6. 回退后必须重跑该批定向测试、typecheck、unit、lint、build 和受影响页面 E2E，并
   记录仍然存在的 conditional/fail-closed 能力。

## Validation

ADR 评审和后续批次使用以下门禁：

```bash
cd /home/czy/hc_DataPlatform/frontend
pnpm typecheck
pnpm test
pnpm lint
pnpm build
pnpm e2e --list
```

阶段 1 还必须证明：

- React/Router/API/Schema/Adapter/Query Key/权限合同测试无变化。
- `DataTable + CursorPager` 无数字分页，服务端排序和游标边界通过。
- RHF/Zod 单一状态、SecureUploadPicker 零网络请求、DangerConfirmModal preflight/影响/
  blocker/并发与焦点行为通过。
- Provider 与 Harness 的 Locale、Token、portal 和 Toast 行为一致。
- 静态门禁拒绝 direct fetch、手写 API URL、角色名授权、offset、未经适配 Upload、
  危险 Popconfirm 和无作用域全局 CSS。
- P01–P19 现有路由仍可运行；ECharts/Three 保持 lazy；构建记录 Ant Design 引入前后
  raw/gzip chunk 差异并满足预算。
- 1440/1024/768/390、键盘、焦点、Escape、焦点返回、200% zoom 和 serious/critical
  a11y 问题均有真实证据；截图不使用无审查批量更新。

本 ADR 的文档验证命令：

```bash
cd /home/czy/hc_DataPlatform
git diff --check -- frontend/docs/adr/0001-ui-component-system.md
git status --short -- frontend/package.json frontend/pnpm-lock.yaml frontend/src frontend/tests frontend/docs/adr
```

评审通过只表示架构决策和阶段 1 边界可执行，不表示 Ant Design 已安装、公共组件已
实现、P01–P19 已迁移或 UI 已通过产品验收。
