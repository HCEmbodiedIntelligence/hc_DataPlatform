# 前端 `shared` 目录说明

## 1. 文档目的

本文说明 `frontend/src/shared/` 及其所有子目录的职责、适用场景和代码边界，供开发、代码评审和目录维护时使用。

本文采用 ASCII 目录树表示层级关系。目录说明基于当前仓库代码，而不是预期中的未来结构。

## 2. 一句话定位

`shared` 是前端的跨页面公共基础层：它提供 HTTP、权限、运行时配置、异步任务、通用工具、路由、作用域、遥测和公共 UI，但不承载某个具体业务域独有的流程。

## 3. ASCII 目录总览

```text
frontend/src/shared/
+-- api/                  # 通用 HTTP、错误、校验、分页、缓存键和传输生命周期
|   `-- generated/        # 根据外部 OpenAPI 草案生成的 TypeScript 接口类型
+-- auth/                 # 页面级和资源级权限判断
+-- config/               # 应用启动期运行时配置
+-- jobs/                 # 跨页面异步任务跟踪和全局任务中心
+-- lib/                  # 不依赖 React 和业务状态的通用工具
+-- routing/              # 路由注册、安全回跳和 URL 查询参数编解码
+-- scope/                # 当前会话、组织/项目/区域作用域和授权快照
+-- telemetry/            # 前端遥测记录、敏感信息脱敏和输出适配
`-- ui/                   # 跨页面复用的表现层和交互合同
    +-- actions/          # 危险操作确认组件
    +-- data/             # 标准数据表格和游标分页
    +-- forms/            # React Hook Form、Zod 与 UI 控件适配
    +-- layout/           # 页面骨架、Header、筛选工具栏、抽屉和响应式布局
    `-- state/            # 页面状态、业务状态标签和指标卡片
```

## 4. 依赖方向

```text
app/  pages/  features/  mocks/
              |
              | consume
              v
           shared/
              |
              | may use stable domain types and constants
              v
           entities/

shared/  -X->  app/  pages/  features/  mocks/
```

含义如下：

- 页面、业务模块、应用外壳和 Mock 可以使用共享能力。
- `shared` 可以依赖稳定的实体类型或规范常量，例如 `Scope`、`Capability`。
- `shared` 不应反向依赖具体页面、业务 Feature、应用装配或 Mock 实现。
- 某个业务域独有的 endpoint、DTO 适配、状态机和表单 schema 应留在对应的 `features/` 中。

## 5. 各目录详细职责

### 5.1 `src/shared/`

职责：作为共享层根目录和公共命名空间入口。

当前内容：

- `index.ts` 按命名空间聚合 `api`、`auth`、`jobs`、`lib`、`routing`、`telemetry` 和 `ui`。
- `config` 与 `scope` 当前没有从根 `index.ts` 聚合导出，使用方仍通过明确路径导入。

适合放入这里的代码：

- 被多个页面或业务域实际复用的能力。
- 属于整个平台的稳定合同，而不是单页实现细节。
- 没有明确业务域所有权的基础设施或表现层能力。

不适合放入这里的代码：

- 仅服务一个页面的组件。
- 特定业务的 API 请求、DTO Adapter、业务校验或状态机。
- 因为“以后可能复用”而提前抽出的代码。

### 5.2 `src/shared/api/`

职责：为所有业务域提供统一的 JSON API 基础设施，不负责组合具体业务接口。

当前能力：

- `http-client.ts`：实现统一的 `request<T>()`，组装 URL、请求头、JSON Body、取消信号和错误转换。
- `domain-error.ts`：定义 `DomainError`、字段错误、操作错误、阻断原因、错误工厂和类型守卫。
- `pagination.ts`：定义严格的游标分页结构和 Zod Schema 工厂。
- `query-keys.ts`：生成包含当前 `scopeKey` 的 React Query 缓存键，并规范化筛选条件。
- `validate.ts`：使用 Zod 校验服务端响应，将合同不匹配统一转换为领域错误。
- `transport-lifecycle.ts`：登记和释放 HTTP、SSE、媒体、Worker、Object URL、WebGL 和敏感内存等资源。
- `index.ts`：聚合手写 API 基础能力，不聚合 `generated/`。

请求层会统一处理登录令牌、组织、项目、区域、客户端版本和语言等请求头。作用域切换时，它会阻止写请求，并拒绝已经跨作用域失效的响应。

边界：

- 具体业务 endpoint、Query Hook、Mutation、命令和 ViewModel Adapter 应放在 `features/<domain>/api/`。
- 页面和业务模块不应直接调用 `fetch()` 或手写平台 `/api` 地址。
- `request()` 面向 JSON API；二进制传输和对象存储上传应由专用端口或控制器处理。
- Query Key 不得包含令牌、签名 URL、函数、`Date`、循环对象等不安全内容。

### 5.3 `src/shared/api/generated/`

职责：保存由 `openapi-typescript` 根据外部 OpenAPI 草案生成的编译期类型。

```text
generated/
+-- access.ts             # 访问控制、成员、角色、授权和审计合同
+-- annotation.ts         # 标注任务、草稿、提交和标注集合同
+-- cleaning.ts           # 查看、人工问题、清洗草稿、EDL 和审核合同
+-- datasets.ts           # 数据集、版本、Episode、Schema、清单和导出合同
+-- ingest.ts             # 数据源、上传会话、分片、校验和事件合同
+-- platform.ts           # 平台通用异步任务合同
+-- robotics.ts           # 机器人模型、机器人、标定和数据 Schema 合同
`-- storage.ts            # 存储概览、对象、成本、生命周期和恢复合同
```

边界：

- 这里的文件带有 `AUTO-GENERATED - DO NOT EDIT` 标记，禁止直接修改。
- 需要修改类型时，应先修改外部 OpenAPI 源，再执行 `OPENAPI_ROOT=/path/to/openapi pnpm gen:api`。
- 这些文件只提供 TypeScript 类型，不是可直接调用的 API Client，也不是运行时校验器。
- 类型已经生成不代表后端接口已经实现、冻结或可用于生产。

### 5.4 `src/shared/auth/`

职责：向 React 页面和组件提供当前用户能否执行某项操作的只读判断。

当前能力：

- `useCapabilities()`：判断当前授权快照是否包含某个平台能力。
- `useAllowedActions()`：判断服务端随资源返回的允许操作，并提供阻断原因。
- `index.ts`：聚合权限 Hook。

边界：

- 授权快照过期、加载失败、能力未知或 `scopeKey` 不匹配时一律拒绝，即 fail closed。
- 前端权限只控制显示和交互，不能替代服务端鉴权。
- 不按角色名称直接授权；判断依据应是 capability 和资源的 `allowed_actions`。
- 本目录不负责请求或刷新授权快照，授权数据由 Shell/Provider 写入。

### 5.5 `src/shared/config/`

职责：保存经过校验、供共享模块读取的启动期运行时配置。

当前能力：

- `runtime.ts` 定义 API 地址、SSE 地址、构建版本和发布环境。
- `configureRuntime()` 写入并冻结配置。
- `getRuntimeConfig()` 在配置未初始化时立即报错。
- `resetRuntimeConfigForTests()` 只用于测试重置。

边界：

- 环境变量读取和合法性校验属于应用启动层，本目录只保存校验后的结果。
- API、任务和遥测模块开始工作前必须完成初始化。
- 不得在运行时配置中保存令牌、密码或其他秘密。

### 5.6 `src/shared/jobs/`

职责：统一观察跨业务域的异步任务，并提供全局任务中心。

当前能力：

- `types.ts`：定义 `AsyncJob` 和任务状态。
- `use-async-job.ts`：查询任务、校验响应、连接 SSE、断线重连、轮询降级、事件去重和版本合并。
- `job-center-store.ts`：保存需要在全局任务中心展示的任务 ID，并自动去重。
- `GlobalJobCenter.tsx`：展示任务类型、状态和连接降级提示。
- `index.ts`：聚合任务模块出口。

边界：

- 本目录负责观察任务，不负责创建、取消或重试某个具体业务任务。
- 只接受更高 `resourceVersion` 的快照，避免旧事件覆盖新状态。
- SSE 不可用或连续重连失败时使用轮询兜底；任务进入终态后停止持续跟踪。
- 任务中心 Store 只保存任务 ID，不持久化完整任务和敏感载荷。

### 5.7 `src/shared/lib/`

职责：保存不依赖 React、网络和业务状态的纯工具函数。

当前能力：

- `bigint-string.ts`：以品牌字符串安全表示 int64，并提供校验、转换、加减和比较。
- `time-range.ts`：创建和计算纳秒级半开区间 `[startNs, endNs)`。
- `case-convert.ts`：转换 camelCase/snake_case，并递归转换数组和普通对象的键。
- `unknown-enum.ts`：把未知枚举值安全投影为只读的 `UNKNOWN`。
- `index.ts`：聚合通用工具。

边界：

- int64、容量、计数和纳秒值不能先转成 JavaScript `number`，以免丢失精度。
- 工具应保持纯函数、可测试，并避免页面、网络和具体业务语义。
- 只被一个业务域使用的工具应优先放在对应 Feature，而不是放进 `shared/lib`。

### 5.8 `src/shared/routing/`

职责：提供跨页面统一的路由安全规则和 URL 查询参数状态管理。

当前能力：

- `registerPageRoutes()`：登记页面可识别的合法路由。
- `safeReturnTo()`：校验站内回跳地址，防止开放重定向。
- `defineQueryCodec()`：定义查询参数的解析、构建、默认值和规范化规则。
- `index.ts`：聚合路由工具。

边界：

- 本目录不创建 React Router 实例，也不直接执行页面导航。
- 新页面需要先注册路由，才能被 `safeReturnTo()` 接受。
- 业务字段的合法性仍由页面或 Feature 自己定义；这里只负责通用编解码和安全规则。
- 筛选条件改变时应清除旧游标，避免使用与新筛选条件不匹配的分页位置。

### 5.9 `src/shared/scope/`

职责：保存应用 Shell 当前会话、组织/项目/区域作用域和授权快照。

当前能力：

- `shell-store.ts` 使用 Zustand 保存用户、会话令牌、Scope、`scopeKey`、切换状态和授权状态。
- `useShellStore` 供 React 组件订阅状态。
- `getShellState()` 供 HTTP Client 等非 React 代码读取当前快照。
- `index.ts`：聚合作用域状态出口。

边界：

- Store 只保存状态；取消请求、清理缓存、释放资源和导航由 `app/shell/scope-transaction.ts` 组织。
- `sessionToken` 只能保存在内存中，不得写入日志、URL、遥测或持久化缓存。
- `setScope()` 会生成新的 `scopeKey` 并清除旧授权，防止授权快照跨作用域复用。
- `clearSensitiveState()` 当前只清除授权快照，不等同于完整登出。

### 5.10 `src/shared/telemetry/`

职责：统一构造前端遥测记录，并在交给输出端之前清理敏感数据。

当前能力：

- `track.ts` 定义遥测事件结构，补充构建版本和作用域哈希，并递归脱敏元数据。
- `configureTelemetrySink()` 可以替换默认输出端。
- `index.ts`：聚合遥测出口。

边界：

- 本目录不负责上传队列、重试或持久化，这些由配置的 Sink 负责。
- 作用域哈希只是关联标识，不是密码学匿名化。
- 自动脱敏只是防御措施；调用方仍不得主动传入令牌、凭证、签名 URL 或完整业务载荷。
- 调用 `track()` 前必须完成运行时配置初始化。

### 5.11 `src/shared/ui/`

职责：提供跨页面复用的表现层组件和交互合同。

当前结构处于迁移期双轨状态：

```text
ui/
+-- index.ts              # 页面使用的统一公共出口
+-- *.tsx                 # 旧版兼容组件和仍保留的通用小组件
+-- styles.css            # 旧全局样式兼容层，只允许随迁移收缩
+-- focus-trap.ts         # 旧 Dialog/Drawer 使用的焦点循环工具
+-- actions/              # 新危险操作合同
+-- data/                 # 新数据展示合同
+-- forms/                # 新表单控件适配
+-- layout/               # 新页面布局合同
`-- state/                # 新页面状态合同
```

`ui/index.ts` 同时导出旧组件和新分层组件。三组同名组件暂时使用兼容别名：

```text
layout/PageHeader  -> UiPageHeader
state/MetricCard   -> UiMetricCard
data/CursorPager   -> DataCursorPager
```

边界：

- 页面应统一从 `shared/ui` 公共出口导入，不直接依赖 `shared/ui/layout`、`state`、`data`、`forms` 或 `actions`。
- UI 只负责展示和交互，不成为 Route、Query、API、权限、Toast、上传传输或业务状态机的事实源。
- 根目录旧组件仍有兼容用途，不能看到同名新组件就直接删除。
- `styles.css` 是旧无作用域全局样式兼容层，只能减少规则和消费者；新样式应使用 CSS Modules。

### 5.12 `src/shared/ui/actions/`

职责：提供高风险操作的统一确认界面。

当前能力：

- `DangerConfirmModal` 展示不可变资源 ID、动作、影响、阻断原因和 preflight 证据。
- 支持确认文本、pending 状态、过期证据、Scope/版本变化和冲突信息展示。

边界：

- 组件只呈现调用者传入的 intent，不自行发请求、不缓存授权，也不决定用户是否真正有权限。
- 幂等键、`If-Match`、Mutation 和 `409/412` 冲突恢复由对应 Feature 负责。
- 删除、发布、权限变更、生命周期执行、恢复和大规模导出等危险操作应使用该合同。

### 5.13 `src/shared/ui/data/`

职责：提供服务端驱动的数据表格和游标分页合同。

当前能力：

- `DataTable`：支持稳定 Row ID、受控排序/筛选/选中，以及 loading、empty、error 状态。
- `CursorPager`：只提供基于 `after`/`before` 游标的上一组和下一组。
- `row-contract.ts`：检查 Row ID 是否非空且唯一，是目录内部工具。

边界：

- TanStack Table 负责 Headless 状态，Ant Design Table 只负责内部表现。
- 不向页面暴露 Ant Design `TableProps`、客户端分页或数字页码。
- 不得用数组下标代替稳定 Row ID，也不得把游标强行转换成 offset。

### 5.14 `src/shared/ui/forms/`

职责：连接 React Hook Form、Zod 和 Ant Design 表单控件。

当前能力：

- `controlled-fields.tsx`：提供 `RHFInput`、`RHFSelect`、`RHFCheckbox` 和 `RHFDatePicker`。
- `createZodResolver.ts`：把 Zod 校验结果安全映射为 React Hook Form 错误。
- `SecureUploadPicker.tsx`：只选择本地文件并进行数量、大小等前端预检。
- `index.ts`：聚合表单适配器。

边界：

- React Hook Form 拥有字段值、dirty、submit 和错误状态；Zod 拥有本地校验规则。
- 领域 schema、默认值和服务端错误映射应留在对应 Feature。
- `SecureUploadPicker` 明确阻止自动上传，不拥有网络请求、STS、签名、分片或断点续传状态。

### 5.15 `src/shared/ui/layout/`

职责：统一页面骨架、布局区域和响应式交互。

当前能力：

- `StandardPageScaffold`：标准列表页骨架。
- `DetailPageScaffold`：详情页骨架。
- `WorkbenchScaffold`：媒体、编辑器、检查器和时间轴组成的工作台骨架。
- `PageHeader`：标题、面包屑、说明、元数据和操作区。
- `FilterToolbar`：桌面内联筛选和窄屏 Drawer 筛选。
- `EntityDrawer`：实体详情抽屉、加载骨架和关闭后焦点恢复。
- `responsive.ts` 与 `layout.module.css`：紧凑布局判断和局部响应式样式。

边界：

- 布局组件定义区域和交互结构，不拥有页面路由、请求、权限或业务状态。
- 业务文案、业务字段和业务操作由页面通过 Props 传入。
- 新布局样式放在 CSS Modules 中，不向全局兼容样式追加无作用域选择器。

### 5.16 `src/shared/ui/state/`

职责：统一跨页面状态的视觉表现和安全降级方式。

当前能力：

- `PageState`：呈现 loading、refreshing、empty、403、404/410、429、offline、合同不匹配和 unknown 等状态。
- `StatusTag`：使用文字、图标和颜色共同表达状态，并安全显示未知值。
- `MetricCard`：呈现指标值、单位、口径、截至时间，以及 loading、unknown、forbidden、error 状态。
- `contracts.ts`：定义 `PageStateKind`、`StatusTone` 和 `MetricState`。
- `state.module.css`：状态组件的局部样式。

边界：

- 状态组件只负责呈现，不改写 `DomainError`、重试策略或权限判断。
- `UNKNOWN`、合同不匹配和权限未知必须安全降级，不能猜测业务含义。
- 具体业务状态到通用 Tone/Label 的映射应由 Feature 或页面完成。

## 6. 新代码应该放在哪里

```text
新增代码
|
+-- 只属于一个业务域？
|   `-- 是 -> features/<domain>/ 或对应 pages/<page>/
|
+-- 是纯领域实体、规范类型或常量？
|   `-- 是 -> entities/
|
+-- 是跨页面基础设施？
|   +-- 网络与合同处理       -> shared/api/
|   +-- 权限判断             -> shared/auth/
|   +-- 运行时配置           -> shared/config/
|   +-- 异步任务观察         -> shared/jobs/
|   +-- 纯工具               -> shared/lib/
|   +-- 路由安全与编解码     -> shared/routing/
|   +-- 会话和作用域状态     -> shared/scope/
|   `-- 遥测与脱敏           -> shared/telemetry/
|
`-- 是跨页面 UI 合同？
    +-- 危险操作             -> shared/ui/actions/
    +-- 表格与游标分页       -> shared/ui/data/
    +-- 表单控件适配         -> shared/ui/forms/
    +-- 页面骨架与响应式布局 -> shared/ui/layout/
    `-- 页面状态与状态标记   -> shared/ui/state/
```

如果不能明确回答“至少有哪些页面或业务域会复用”，优先把代码留在所属 Feature，等复用边界稳定后再抽取。

## 7. 公共出口与维护规则

1. 修改或新增公共能力时，同步维护相应目录的 `index.ts`。
2. 页面 UI 统一从 `shared/ui` 导入，避免页面绑定内部目录结构。
3. `api/generated/` 只能通过生成脚本更新，禁止手工修补生成文件。
4. 新业务请求统一经过 `shared/api/request`；业务路径、Query 和 Adapter 留在 Feature。
5. Query Key 必须绑定 Scope，未知权限、未知枚举和合同错误必须 fail closed。
6. 旧 `ui/*.tsx` 和 `ui/styles.css` 属于迁移兼容层，只减少消费者，不作为新代码模板。
7. 修改公共合同后，应同步补充或更新 `frontend/tests/contracts/shared-*.spec.ts(x)` 和相关 UI 架构测试。

## 8. 相关资料

- [前端脚手架交接说明](./frontend-scaffold-notes.md)
- [React UI 组件系统 ADR](./adr/0001-ui-component-system.md)
- [API 类型生成脚本](../scripts/generate-api-client.mjs)
- [共享层合同测试](../tests/contracts/)
