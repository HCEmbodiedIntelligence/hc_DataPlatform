# T01｜全局骨架、认证、通用状态与响应式效果图规格

> 文档状态：效果图输入与 UX 规格；不是业务实现、API 合同或已上线能力说明  
> 审查日期：2026-08-17（Asia/Shanghai）  
> 唯一交付文件：`plan/frontend-ux-parallel/T01-SHELL-AUTH-SPEC.md`  
> 适用栈：React 19 + Vite + React Router 7 + Ant Design 6 + Lucide  
> 业务基线：公网部署、用户名公开注册、注册后等待管理员审批；审批后仅创建私有空项目并授予只读 viewer，真实项目另行授权；V1 无邮箱验证、无 MFA。

## 0. 结论先行

当前代码已经具备可复用的 Shell 外形、按 capability 过滤导航、路由读取权限保护、作用域切换时清理旧数据、桌面/紧凑/手机三档布局、可见焦点和部分通用状态组件；但它还不是可在公网真实使用的认证骨架。

阻断效果图进入实施版的事实是：当前没有登录、注册、审批等待、拒绝、停用、无项目、会话过期或服务不可用的独立路由；生产路由没有注入会话、项目列表和授权加载器；P01 对未登录者没有认证门禁；401 只被转换为普通领域错误；账户设置和退出菜单没有行为；项目上下文在 UI 中退化为 Region 选择器；导航和品牌跳转使用程序化 `navigate` 而不是链接；没有 skip link 和路由切换后的焦点恢复；P20 也不在路由、能力或导航清单中。

目标态应拆成三层：公开认证层、账户状态层、受保护的平台 Shell。Shell 内的页面继续按功能组织，权限只决定路由、数据范围与动作；不为“内部/外包/管理员”复制页面。P20“采集任务”放在“采集与接收”组首位，与“上传记录”和“数据源”并列，不能塞进上传页。

本文件只规定效果图和后续实现约束，不宣称任何认证、审批、项目或通知 API 已存在。

## 1. 审查边界、工作区记录与 Skill 使用

### 1.1 开始前工作区快照

开始审查前执行了 `git status --short`。以下改动均为已有改动，本终端未清理、覆盖或据此假定功能已经完成：

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

### 1.2 审查范围

- 完整阅读总计划、主设计系统、根 README。
- 完整阅读当前 router、navigation manifest、`PlatformShell`、主题、共享 layout/state/form 组件、作用域与权限状态、HTTP 错误链路及相关测试。
- 检查全局路由、导航、项目切换、搜索、通知、任务中心、账户菜单、退出、错误边界与深链状态。
- 为登录、注册、待审批、拒绝、停用、无项目、403、404、会话过期和服务不可用建立目标流程，但不把目标流程写成现有事实。
- 审查 1440×900、1280 桌面、平板审批和手机认证/待办的内容优先级。
- 不修改 `frontend/src`、后端、OpenAPI、迁移、Worker、Compose、README、MASTER 或已有计划；不提交或推送 Git。

### 1.3 四个 Skill 如何实际使用

| Skill | 本次使用 | 对本规格的具体影响 |
|---|---|---|
| `frontend-design` | 完整阅读；以“公网工业机器人数据平台”为具体对象，以“判断数据阶段、阻塞原因和下一步”为页面任务；先沿用 MASTER 的色彩、排版、布局与“信号轨道”，再做模板化自检 | 保留紫蓝工业基调；不采用奶油金、巨型 Hero、营销 CTA 或装饰性动效；把唯一记忆点限制为编码真实阶段/血缘的“信号轨道”；所有错误和空态文案说明下一步 |
| `ui-ux-pro-max` | 完整阅读；没有执行 `--persist`，没有生成或覆盖 MASTER；执行非持久化 `--domain ux` 与 `--stack react` 查询 | 以错误播报、标题层级、键盘顺序、skip link、无横向滚动、移动端表格降级、React 路由懒加载和“先测量再优化”为检查重点；明确忽略原生 App 的触觉、安全区和底部导航默认建议 |
| `web-design-guidelines` | 完整阅读本地 Skill；于 2026-08-17 获取最新版 [command.md](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md)，共 161 行；按其格式记录代码 `file:line` 发现 | 增加链接/按钮语义、skip link、焦点、表单 `name`/`autocomplete`、异步 `aria-live`、长文本、图片尺寸、URL 状态与 i18n 检查；发现见第 10 节 |
| `vercel-react-best-practices` | 完整阅读；仅采用 React/浏览器和 bundle/rerender 规则 | 后续使用静态可分析的 `React.lazy`/动态导入、并行独立请求、派生状态不复制进 Effect、版本化浏览器存储、按需预加载；不套用 RSC、Server Action、`next/dynamic`、Next 缓存或 hydration 专属做法 |

非持久化查询命令：

```bash
python3 /mnt/c/Users/28384/.agents/skills/ui-ux-pro-max/scripts/search.py \
  "desktop web public authentication approval navigation accessibility loading error responsive" \
  --domain ux -n 12
python3 /mnt/c/Users/28384/.agents/skills/ui-ux-pro-max/scripts/search.py \
  "route lazy loading state restoration navigation performance" \
  --stack react -n 12
```

## 2. 实际代码证据

“已证实”只表示当前仓库事实，不表示符合目标态。

| 审查项 | 代码证据 | 当前结论 |
|---|---|---|
| 技术栈 | `README.md:21-28`；`frontend/package.json:21-34` | React 19、Vite、React Router、Ant Design、Zustand；不是 Next.js |
| 启动链路 | `frontend/src/main.tsx:8-40` | Mock 按环境动态加载，router 也延迟导入；环境无效时安全停止 |
| Mock 身份注入 | `frontend/src/mocks/index.ts:4-14` | 裸 Mock URL 会注入 fixture principal/scope/capability；这不是生产认证 |
| 根路由结构 | `frontend/src/app/router/index.tsx:188-207` | 所有路由共用 `PlatformShell`；没有公开认证布局和受保护布局的分层 |
| 未登录 P01 | `frontend/src/app/router/index.tsx:97-117,150-155`；`frontend/src/app/router/RouteCapabilityGuard.tsx:10-31` | P01 的必需 capability 为空，当前 guard 不构成登录门禁 |
| P01 内部能力冲突 | `frontend/src/pages/p01-dashboard/page.tsx:158-210`；`frontend/src/entities/capability.ts:83-112` | 页面检查 `dashboard.read`，但它是 reserved 而非 canonical；真实模式诚实显示能力未开放且不请求聚合接口，不能用 Mock 填补 |
| 认证与账户状态页 | `frontend/src/app/router/index.tsx:188-207` | 未注册登录、注册、待审批、拒绝、停用、无项目、会话过期、503 独立路由 |
| 404 | `frontend/src/app/router/index.tsx:197-203` | 有最小 404 文案，但固定在 Shell 内、没有主/次动作和焦点恢复 |
| 页面聚合与懒加载 | `frontend/src/app/router/index.tsx:16-78,134-180` | P02–P04 运行时懒加载；多数页面 route module 提供 lazy；重型页有静态检查 |
| P20 | `frontend/src/app/router/index.tsx:97-117`；`frontend/src/app/shell/navigation-manifest.ts:30-88`；`frontend/src/shared/routing/route-registry.ts:12-34` | 路由、能力映射、导航和安全 returnTo 清单均无 P20 |
| 导航按 capability 过滤 | `frontend/src/app/shell/navigation-manifest.ts:90-103` | 已存在能力过滤，但仅使用单一 read capability，未表达后端能力未开放状态 |
| 路由权限 fail-closed | `frontend/src/app/router/RouteCapabilityGuard.tsx:14-31` | 授权失败会取消/清除当前 scope 查询并显示 403；是可保留基础 |
| 导航链接语义 | `frontend/src/app/shell/PlatformShell.tsx:143-220` | Ant Menu 的 item 通过 `onClick` + `navigate` 跳转，不是 `<Link>`，无法可靠支持新标签页/复制链接 |
| 品牌跳转语义 | `frontend/src/app/shell/PlatformShell.tsx:415-424` | “返回工作台”是 button + `navigate`，应为链接 |
| 当前导航分组 | `frontend/src/app/shell/navigation-manifest.ts:30-88` | 仍为“数据接入/数据资产/数据标注/手动清洗/存储管理/系统管理”，缺少目标 P20，且“系统管理”过宽 |
| P10/P11 编号冲突 | `frontend/src/pages/p10-cleaning-drafts/routes.tsx:11-15`；`frontend/src/pages/p11-manual-cleaning/routes.tsx:12-17`；`plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:153-157` | 当前 P10 是清洗草稿、P11 是清洗工作台；目标计划把 P10 定义为标注任务，必须由主终端统一编号与路由 |
| Scope 数据模型 | `frontend/src/entities/scope.ts:1-24`；`frontend/src/shared/scope/shell-store.ts:6-23` | 状态模型包含 organization/project/region，但 UI 未完整呈现项目上下文 |
| Scope UI | `frontend/src/app/shell/PlatformShell.tsx:240-291` | 仅展示 Region Select；没有组织/项目选择器、项目空态或项目授权撤销流程 |
| 生产接线 | `frontend/src/app/shell/PlatformShell.tsx:63-68,295-300`；`frontend/src/app/router/index.tsx:188-193` | Shell 支持 `scopeOptions`/`authorizationLoader` props，但 router 只传 `pageAvailability`，真实模式未接线 |
| Scope 切换事务 | `frontend/src/app/shell/scope-transaction.ts:25-61` | 已按“阻断写入→取消请求/SSE→释放资源→清状态→装载授权→合法路由”处理，值得保留 |
| Scope 切换落点 | `frontend/src/app/shell/PlatformShell.tsx:359-379` | 成功后总是取第一条可见导航，不保留原页面；失败只 Toast，没有专用恢复状态 |
| 数据集上下文 | `frontend/src/app/shell/dataset-context.ts:10-69`；`frontend/src/app/shell/dataset-context.test.ts:8-45` | 数据集选择可写入 path/query 并清相关参数；测试只覆盖此局部，不覆盖项目切换 |
| 全局搜索 | `frontend/src/app/shell/PlatformShell.tsx:438-455` | 控件像可用搜索且展示 `⌘ K`，实际只提示接口未开放，也没有快捷键监听 |
| 通知中心 | `frontend/src/app/shell/PlatformShell.tsx:500-524` | 明确显示“未提供通知查询接口”，没有伪造通知数据；但顶栏图标仍像已开放能力 |
| 账户与退出 | `frontend/src/app/shell/PlatformShell.tsx:525-543` | 菜单包含“账户设置/退出登录”，但没有 `onClick` 或路由，两个动作均不可用 |
| 会话状态 | `frontend/src/shared/scope/shell-store.ts:27-47` | principal/token 只在内存；`clearSensitiveState` 只清 authorization，不能作为完整登出 |
| 401 处理 | `frontend/src/shared/api/domain-error.ts:82-91`；`frontend/src/shared/api/http-client.ts:196-213` | 401 被映射为 `UNAUTHENTICATED` 后交给各调用方；没有全局单次会话过期流程 |
| 安全 returnTo | `frontend/src/shared/routing/route-registry.ts:65-77` | 已有同源、已注册路由白名单校验，可扩展用于重新登录返回；新增认证/P20 路由时必须同步注册 |
| 全局错误边界 | `frontend/src/app/providers/ErrorBoundary.tsx:11-36` | 能捕获渲染异常并刷新；没有错误重置键、返回安全页、请求 ID或路由级隔离 |
| 通用页面状态 | `frontend/src/shared/ui/state/contracts.ts:1-22`；`frontend/src/shared/ui/state/PageState.tsx:76-183` | 有 loading/refreshing/empty/error/403/404/offline/contract mismatch/feature unavailable；缺少 partial error、只读、无项目、会话过期和服务不可用的明确合同 |
| 状态播报 | `frontend/src/shared/ui/state/PageState.tsx:87-118,164-180`；`frontend/src/app/providers/ToastProvider.tsx:23-40` | loading/refreshing/错误和 Toast 有基础播报；需避免路由初始载入时重复 `alert` |
| 只读表达 | `frontend/src/shared/ui/state/PageState.tsx:123-135`；`design-system/hc-data-platform/MASTER.md:140-143` | 当前把 unknown/contract mismatch/feature unavailable 归为 read-only 并隐藏 action；目标合同还需要保留安全导航与说明，且“只读”不等于全部置灰 |
| 页面标题与面包屑 | `frontend/src/shared/ui/layout/PageHeader.tsx:32-72`；`frontend/src/shared/ui/BreadcrumbNavigation.test.tsx:16-60` | h1 与 `<Link>` 面包屑语义正确，有局部测试 |
| 表单标签/name | `frontend/src/shared/ui/forms/controlled-fields.tsx:40-66,75-101,109-136` | Input/Checkbox 有 label、id、name；Select 没有显式 name；通用 Input 不强制正确 `autocomplete`/`spellCheck`/`inputMode` |
| 异步提交基础 | `frontend/src/shared/ui/forms/controlled-fields.tsx:40-184`；`frontend/src/app/providers/ToastProvider.tsx:23-40` | 共享字段与 Toast 可复用；没有认证表单、错误摘要、首个错误聚焦和重复提交合同 |
| 响应式 Shell | `frontend/src/app/shell/PlatformShell.tsx:70-75,95-124`；`frontend/src/app/shell/PlatformShell.module.css:532-633` | 1200/768 两个主要断点；1280 仍为完整侧栏，手机仍可进入所有业务路由 |
| 移动导航焦点 | `frontend/src/app/shell/PlatformShell.tsx:344-357,605-643` | 打开后聚焦关闭按钮、关闭后回触发器，基础正确 |
| skip link/main | `frontend/index.html:9-12`；`frontend/src/app/shell/PlatformShell.tsx:591-602` | 没有 skip link；主内容是 Ant `Content`，未明确输出 `<main id="main-content">` |
| Focus/reduced motion | `frontend/src/app/theme/global.css:77-89,199-206`；`frontend/src/app/shell/PlatformShell.module.css:635-643` | 有 `:focus-visible` 与 reduced motion；但实际焦点色在白色上的计算对比度只有 2.66:1，需真实渲染验证/加深外圈 |
| Logo CLS | `frontend/src/app/shell/PlatformShell.tsx:87-93`；`frontend/src/app/shell/PlatformShell.module.css:51-65` | CSS 预留容器，但 `<img>` 没有 `width`/`height` 属性；源图为 106×62 |
| 触控尺寸 | `frontend/src/app/shell/PlatformShell.module.css:248-258,626-628`；`frontend/src/app/theme/tokens.ts:34-38` | 部分顶栏按钮 34–42 px；手机目标需统一至少 44×44 CSS px |
| 主题一致性 | `frontend/src/app/theme/tokens.ts:1-20`；`design-system/hc-data-platform/MASTER.md:73-81` | 当前 success token 是蓝色，MASTER 目标为绿色语义；不能在效果图中让“信息/通过”同色 |
| 共享组件重复 | `frontend/src/shared/ui/PageHeader.tsx:1-43` 与 `frontend/src/shared/ui/layout/PageHeader.tsx:1-74` | 两套同名组件会造成标题、状态和响应式合同漂移；实施前需选定迁移方向 |
| i18n 请求 | `frontend/src/shared/api/http-client.ts:95-100,171-179` | 已使用浏览器语言发送 `Accept-Language`；页面显示仍需统一 `Intl` 与显式时区 |
| 现有相关测试 | `frontend/src/app/shell/dataset-context.test.ts:8-45`；`frontend/src/shared/ui/BreadcrumbNavigation.test.tsx:16-60`；`frontend/src/shared/api/http-client.test.ts:19-72` | 仅覆盖数据集 URL、面包屑与 scope header；没有 Shell、认证、导航、401、退出、项目切换或路由焦点测试 |

### 2.1 对比度计算记录

使用 WCAG 相对亮度公式对当前 Token 做静态计算；它只能作为风险提示，最终仍需在实际组件、背景、缩放和高对比度模式中验证。

```text
#676b80 on #ffffff: 5.26:1
#676b80 on #f6f7fb: 4.91:1
#5965d8 on #ffffff: 4.91:1
#8b96ff on #ffffff: 2.66:1   ← 不足以单独承担 3:1 的焦点指示边界
#3377e7 on #ffffff: 4.26:1   ← 不应作为 12–14 px 普通文本的唯一颜色
#a86512 on #ffffff: 4.63:1
#b84646 on #ffffff: 5.25:1
```

效果图保留 `#8B96FF` 作为柔和光晕，但键盘焦点必须再有 `#5965D8` 或等效深色的 2 px 实线外圈，不能只使用半透明 shadow。

## 3. 当前不合理点

### 3.1 代码已证实

| 优先级 | 问题 | 影响 | 目标处理 |
|---|---|---|---|
| P0 | 未登录访问与业务 Shell 没有分层，P01 没有认证门禁 | 公网环境可能在没有真实会话时进入业务骨架并触发错误/降级请求 | 公开认证路由与受保护 Shell 分树；认证、账户状态、项目状态完成后才装载 Shell |
| P0 | 真实模式没有 session/project/authorization bootstrap 接线 | Mock 看起来可用，生产无法建立可靠 principal、项目列表和授权快照 | 建立单一 bootstrap 状态机；失败不得退到匿名 P01 |
| P0 | 401 没有全局会话过期处理，`clearSensitiveState` 也不是登出 | 多请求可重复报错，旧 token/principal/页面敏感状态可能残留 | 单次过期协调器清理会话、Query/SSE/媒体、跳转登录并保存安全 returnTo |
| P1 | 账户设置、退出是假动作；搜索显示未实现快捷键 | 产生错误可用性预期，尤其影响公网账户安全 | 未接线前移除或明确 disabled；退出必须始终真实可用；只在快捷键监听落地后显示提示 |
| P1 | 顶栏没有项目切换，只能选 Region | 与项目隔离、私有项目、真实项目另行授权的业务模型不符 | “当前项目”成为第一上下文；Region 是项目内次级条件，不替代项目 |
| P1 | 程序化导航不是 Link，且没有 skip link、main landmark 和路由焦点 | 键盘、读屏、新标签页、复制链接和浏览器预期受损 | 导航/品牌使用 `<Link>`；首个可聚焦元素为 skip link；路由变化聚焦 h1 |
| P1 | P20 不在路由/导航；目标 P10 与当前 P10/P11 编号冲突 | 各效果图可能产生不同入口、路由和页面归属 | 主终端先冻结 P10/P11/P20 路由与 owner，再合并各终端规格 |
| P1 | 通用状态缺少 partial error、read-only、no project、session expired、service unavailable | 页面会各自发明文案/动作/播报；错误和权限含义混淆 | 使用第 8 节统一合同；全页与局部错误分开 |
| P1 | 手机仍可进入所有业务路由，未限制复杂工作台 | “能打开”会被误认为“可完成任务”，形成横向滚动与误操作风险 | 手机只承接认证、待办、状态和明确的轻量审批；复杂工作台显示能力不可用说明 |
| P2 | 1280 仍使用完整侧栏和大搜索控件 | 内容密集页可用宽度被压缩，主动作可能移出视口 | 1280 使用 64 px 可展开功能轨道；搜索降为图标/命令面板入口 |
| P2 | 两套 PageHeader 与两套状态样式并存 | 焦点、标题、文案和响应式行为漂移 | 实施期建立一个迁移出口；本阶段只规定目标合同，不改代码 |
| P2 | Logo 缺少元素尺寸，手机图标按钮小于 44 px，焦点色单独对比不足 | CLS、触控和键盘可见性风险 | 后续补 intrinsic size；触控 44 px；深色实线焦点 + 浅色光晕 |

### 3.2 需要技术验证

以下事项在当前代码中没有足够证据，不能画成“已经成功”：

1. 身份服务的登录、用户名注册、查询账户状态、审批、拒绝、停用、恢复、登出和密码重置接口是否存在，以及真实错误枚举。
2. 会话采用 HttpOnly Cookie、内存 bearer token 还是其他机制；CSRF、SameSite、跨域和刷新策略必须由安全架构确认，效果图不展示 token。
3. `PENDING`、`REJECTED`、`DISABLED` 是否为身份服务稳定枚举；当前生成 access 类型只证明项目成员投影含 `ACTIVE | DISABLED | UNKNOWN`，不能推导注册状态合同。
4. 审批后“创建私有空项目 + viewer 授权”是否原子完成；若部分失败，用户应该看到“账户已启用，项目准备中”还是回滚为待审批。
5. 当前用户可访问项目/Region 列表、授权快照与默认项目的真实读取接口及缓存策略。
6. 401、403、账户停用、项目授权撤销能否从 HTTP/SSE 得到一致信号；多并发 401 必须只触发一次跳转。
7. 登出是否可由服务端撤销会话；仅清浏览器内存不算完成。
8. 通知、全局搜索和 P20 是否有真实合同；没有合同前不得显示“0 条通知”或可输入搜索结果页。
9. React Router 对浏览器前进/后退的滚动、焦点、筛选和 Drawer 状态恢复方案，以及部署网关对新增深链的 SPA fallback。
10. Ant Design Menu/Popover/Drawer 的实际 DOM、键盘顺序、Escape、焦点回归和读屏播报，需要用浏览器与辅助技术验证，不能只靠 JSX 推断。
11. 1440、1280、834、768、390、360 宽度的真实截图、200% 缩放、Windows 高对比度和 `prefers-reduced-motion` 仍需视觉回归。
12. 错误响应是否总能提供安全 request ID、发生时间和 retryable；技术详情不得暴露 URL、路径、token、签名、对象 key 或内部堆栈。

### 3.3 需要产品确认

产品问题与推荐选项集中在第 12 节；未确认前，效果图使用“推荐方案”并加注“待确认”，不把备选画成并存功能。

## 4. 全局信息架构与导航树

### 4.1 三层路由模型

```text
HC Data Platform
├─ [PUBLIC｜无会话]
│  ├─ 登录
│  └─ 创建账户
├─ [ACCOUNT｜已识别但不可进入业务]
│  ├─ 等待审批
│  ├─ 注册申请未通过
│  ├─ 账户已停用
│  ├─ 登录已过期
│  └─ 服务暂时不可用
└─ [PROTECTED｜ACTIVE 会话]
   ├─ 项目上下文加载
   │  ├─ 没有可访问的项目
   │  ├─ 私有空项目（只读 viewer）
   │  └─ 已授权真实项目
   └─ PlatformShell
      ├─ 工作台
      ├─ 采集与接收
      ├─ 数据资产
      ├─ 标注与清洗
      ├─ 容量与生命周期
      ├─ 机器人资产
      └─ 账户、安全与审计
```

建议目标路由名称仅用于效果图对齐，尚不是已实现合同：

| 层级 | 建议路由 | 说明 |
|---|---|---|
| PUBLIC | `/auth/login` | 登录；支持经过白名单校验的 `returnTo` |
| PUBLIC | `/auth/register` | 用户名公开注册 |
| ACCOUNT | `/account/pending` | 等待审批；不得装载业务 Shell |
| ACCOUNT | `/account/rejected` | 注册申请未通过；不得泄露内部审批备注 |
| ACCOUNT | `/account/disabled` | 账户停用；清除当前业务状态 |
| ACCOUNT | `/auth/session-expired` | 会话过期说明；主动作回登录 |
| PROTECTED/no scope | `/projects/unavailable` | 活跃账户无项目或项目准备失败 |
| SYSTEM | `/403` | 有会话但无路由/资源读取权限 |
| SYSTEM | `/404` | 路由不存在；不把无权限资源泄露为“存在” |
| SYSTEM | `/service-unavailable` | 身份/bootstrap/核心服务不可用 |

项目深链推荐使用 `/projects/:projectId/...` 作为最终规范；这是跨页面迁移决策，需主终端确认。若实施期暂时保留现有路径，至少必须使用稳定的 `project` 查询参数并做 canonical redirect，不能依赖“上次选择项目”解释同一个 URL。

### 4.2 认证后功能导航

```text
工作台
└─ P01 工作台

采集与接收
├─ P20 采集任务              ← 入口置顶；任务定义/任务码/分派/进度
├─ P03 上传记录              ← 执行记录，不是采集任务
│  └─ P04 上传与自动质检详情  ← 深层路由，不重复出现在主导航
└─ P02 数据源                ← Web/工作站/机器人自动上传/离线导入来源

数据资产
├─ P05 数据集
├─ P06 采集条目              ← 深层上下文
└─ P07 数据版本              ← 深层上下文

标注与清洗
├─ P10 标注任务              ← 目标计划；当前编号冲突待统一
├─ P08 数据标注              ← 队列/工作台，按能力进入模式
├─ P09 质量问题              ← Lance 后人工问题
└─ P11 数据清洗              ← 非破坏性清洗与提交审核

容量与生命周期
├─ P12 容量管理
└─ P13 生命周期

机器人资产
├─ P14 机器人模型
├─ P15 机器人与组件
├─ P16 标定管理
└─ P17 Schema 注册表

账户、安全与审计
├─ P18 账户与权限
└─ P19 审计日志
```

与并行产出的 T03 规格交叉核对后，双方对“P20 采集任务 / P03 上传记录 / P02 数据源”的顺序和边界一致（`plan/frontend-ux-parallel/T03-P01-P20-SPEC.md:325-338`）。T03 同时确认 P20 正式路径/builder 尚不存在（同文件 `:556-565`），因此本规格只冻结导航位置，不发明 P20 可执行 URL。

导航规则：

- 功能组和页面名称对所有人一致，不出现“外包版工作台”“管理员数据集”等重复页面。
- 没有页面 read capability：主导航不显示该页面；直接深链进入统一 403，不请求领域数据。
- 有 read capability、没有 action capability：页面结构保持一致，显示“只读”，隐藏不适用动作；不能把所有字段灰掉伪装成 disabled。
- 有 capability 但环境能力未开放：导航可保留，落地页显示“能力尚未开放”和真实依赖；不得显示假列表或假成功。
- 某组过滤后无页面：整组移除；不要留下空标题。
- P04/P06/P07/具体工作台对象为深层路由，通过列表、面包屑、通知或待办进入，不重复占用主导航。
- 账户菜单的“退出登录”与普通跳转空间分离并使用明确文本；不使用只有图标的危险动作。

### 4.3 顶栏优先级

从左到右：品牌/移动菜单 → 当前项目 → 可选 Region → 当前页面上下文（例如数据集）→ 全局搜索 → 运行任务 → 通知 → 账户。

1. “当前项目”在所有受保护页面可见；显示项目名、`只读`/权限摘要和切换入口。
2. Region 只有在当前项目存在多个可访问 Region 且页面需要 Region 时出现；不允许用 Region 代替项目。
3. 数据集等页面级上下文只在相关页面出现，不能挤掉项目。
4. 搜索接口未落地时不显示输入框外观和快捷键；可显示 disabled 图标并标注“尚未开放”，也可完全移除，取决于第 12 节确认。
5. “运行任务”只表示当前会话启动的异步作业；“待办”和“通知”是不同概念，不能用任务中心代替。
6. 通知无合同时不显示“0”徽标；0 是数据事实，不是“接口不存在”。
7. 账户菜单至少显示用户名、账户状态、当前项目权限摘要和真实可用的退出动作；账户设置仅在路由/能力落地后显示。

## 5. 桌面骨架线框

### 5.1 1440×900 主效果图

```text
┌────────────────────────────────────────── 1440 ──────────────────────────────────────────┐
│ [HC Logo 58×40] HC 数据平台 │ 当前项目：个人私有项目 [只读] ▾ │ Region ▾ │ 搜索 │任务│通知│账户│ 68
├──────────────────── 218 ────┬──────────────────────────────────────────────────────────────┤
│ 工作台                      │ [skip 目标] 工作台                              [页面主动作] │
│ ● 工作台                    │ 私有空项目 · 只读                                             │
│                             ├──────────────────────────────────────────────────────────────┤
│ 采集与接收                  │ 状态/权限说明：真实项目由管理员另行授权                       │
│   采集任务                  ├──────────────────────────────────────────────────────────────┤
│   上传记录                  │                                                              │
│   数据源                    │  ┌────────────────────────────────────────────────────────┐  │
│                             │  │ 此项目还没有业务数据。                                 │  │
│ 数据资产                    │  │ 这是审批后创建的私有空项目，你当前拥有只读权限。        │  │
│   数据集                    │  │ [切换项目]                                              │  │
│                             │  └────────────────────────────────────────────────────────┘  │
│ 标注与清洗                  │                                                              │
│ 容量与生命周期              │                                                              │
│ 机器人资产                  │                                                              │
│ 账户、安全与审计            │                                                              │
│                             │                                                              │
│ [折叠导航]                  │                                                              │
└─────────────────────────────┴──────────────────────────────────────────────────────────────┘
```

1440 规则：

- 完整侧栏 218 px；内容左右内边距 30 px；顶栏 68 px。
- 项目名先保证 200–260 px；长项目名两行不可取，单行省略并在可聚焦 Tooltip/详情中查看全文。
- 页面 h1 不采用营销 Hero 尺寸；建议 28–36 px，标题区不超过首屏高度的 18%。
- 页面最多一个主动作；只读项目没有创建类主动作。
- 信号轨道只在真实业务阶段/血缘区域出现，Shell 顶栏和认证页不用轨道作装饰。

### 5.2 1280 桌面效果图

```text
┌─────────────────────────────────────── 1280 ──────────────────────────────────────────────┐
│ [HC] │ 当前项目：项目名 [只读] ▾ │ Region ▾ │ [搜索图标] │ [任务] [通知] [账户]            │ 64
├── 64 ┼─────────────────────────────────────────────────────────────────────────────────────┤
│ 仪表 │ 工作台                                                        [唯一主动作/无则留空] │
│ 上传 │ 私有空项目 · 只读                                                                   │
│ 数据 ├─────────────────────────────────────────────────────────────────────────────────────┤
│ 标注 │ 状态与权限摘要                                                                     │
│ 清洗 ├─────────────────────────────────────────────────────────────────────────────────────┤
│ 容量 │                                                                                     │
│ 资产 │  ┌───────────────────────────────────────────────────────────────────────────────┐ │
│ 安全 │  │ 主内容；表格只在自身容器内横向滚动，主标识列与主动作列保持可见               │ │
│      │  └───────────────────────────────────────────────────────────────────────────────┘ │
│ [展] │                                                                                     │
└──────┴─────────────────────────────────────────────────────────────────────────────────────┘
```

1280 规则：

- 1024–1359 默认使用 64 px 功能轨道，用户可展开；每个图标都有文本可访问名称，展开层支持键盘并保持当前项。
- 搜索降为图标按钮，打开命令面板后再输入；接口未落地时不显示快捷键。
- 内容左右内边距 22 px；不能出现整页横向滚动。
- 表格允许自身滚动；首列和动作列固定。批量动作超过 2 个进入“更多操作”。
- 顶栏项目名优先于 Region、搜索和账户显示名；空间不足时先折叠搜索文字，再折叠 Region 文本，不能隐藏当前项目。

## 6. 平板与手机的支持边界

| 设备 | 支持的轻量任务 | 不承诺/明确降级 | 内容优先级 |
|---|---|---|---|
| 平板 768–1023 | 登录、注册、账户状态、待办列表、账号注册审批、简单审核、列表查看、详情摘要、项目切换 | 多相机工作台、密集曲线编辑、大批量授权、复杂生命周期配置 | 标题/状态 → 对象身份 → 风险/影响 → 主动作 → 技术详情 |
| 手机 360–767 | 登录、注册、待审批、被拒绝/停用、待办、状态查看、低风险且信息完整的简单审批 | 标注/清洗工作台、Raw 诊断、多列对比、项目授权、发布冻结、批量动作 | 账户/任务状态 → 截止时间 → 阻塞原因 → 单一主动作；次要信息折叠 |

手机进入不支持的功能时，保留 URL 和对象身份，显示：

- 标题：`请在桌面端完成此任务`
- 说明：`此页面包含多相机、曲线或批量编辑，手机端无法安全完成。你可以查看任务状态，编辑内容不会在手机端开放。`
- 主动作：`查看任务状态`
- 次动作：`返回待办`

禁止把复杂工作台缩小到横向拖动的“能打开”版本。手机表单输入字号至少 16 px，所有触控目标至少 44×44 CSS px，两个相邻危险动作间距至少 8 px。

## 7. 认证、账户、项目与会话状态机

### 7.1 认证状态机

```text
[匿名]
  ├─ 登录提交 ── loading ── 成功识别账户 ───────────────┐
  │                    ├─ 凭据错误 → 就地错误 → 登录   │
  │                    └─ 服务失败 → 保留用户名 → 重试 │
  └─ 注册提交 ── loading ── PENDING → 等待审批         │
                                                       ▼
                           ┌─ PENDING  → 等待审批
[已识别账户状态检查] ──────┼─ REJECTED → 申请未通过
                           ├─ DISABLED → 账户已停用并清会话
                           └─ ACTIVE   → 加载项目与授权
```

约束：

- 注册成功只表示申请已接收并进入待审批，不使用“注册成功，开始使用”或任何营销 CTA。
- 登录错误统一为“用户名或密码不正确”，不暴露用户名是否存在；账户状态只有在完成安全识别后展示。
- 注册页必须诚实展示：平台位于公网、V1 不验证邮箱且不支持 MFA、不要复用其他网站密码、审批前不能访问项目。
- 无邮箱验证意味着不能承诺邮件通知、邮件找回密码或邮件审批结果。
- 表单提交开始后按钮显示 spinner 并禁用重复提交；超时和失败都恢复可操作状态。
- 服务端字段错误显示在字段旁；多个错误在表单顶部提供可聚焦摘要并链接到字段。

### 7.2 账户状态

```text
PENDING ──管理员批准──> ACTIVE + 创建私有空项目 + viewer
   │                         │
   └─管理员拒绝──> REJECTED  ├─另行项目授权──> ACTIVE + 真实项目范围
                             └─管理员停用──> DISABLED

REJECTED ──重新申请策略待确认──> PENDING 或保持 REJECTED
DISABLED ──管理员恢复策略待确认──> ACTIVE
```

UI 不推断管理员身份类型；审批页是否可见由 capability 决定。状态变化时，前端必须以后端权威结果为准。审批后私有项目创建失败的中间态需要技术合同，效果图不得假装项目已成功创建。

### 7.3 项目选择与授权撤销

```text
ACTIVE 会话
  → 加载可访问项目
     ├─ loading：稳定骨架，不先显示上次项目数据
     ├─ 0 个：无项目/项目准备失败页
     ├─ 1 个：选择唯一项目，校验深链后进入
     └─ 多个：优先 URL 项目 → 最近有效项目 → 项目选择页

项目切换
  → 立即阻断新写入
  → 取消旧 scope Query/HTTP/SSE
  → 释放 signed URL、媒体、Worker、Object URL、WebGL
  → 清除选中对象、临时表单和任务中心
  → 加载新项目授权/导航
     ├─ 原路径在新项目仍合法：保留路径与非敏感筛选
     ├─ 原路径无读取权限：进入该项目首个合法功能并说明原因
     └─ 授权加载失败：失败关闭，不回显旧项目数据
```

项目切换器显示项目名称为主、权限摘要为辅；完整内部 ID 放在技术详情。项目授权在当前会话中被撤销时，立即停止领域请求并进入无权限/无项目状态，不静默切换到另一个项目掩盖撤销事实。

### 7.4 会话过期

```text
本地已知到期时间 或 任一权威请求返回 401
  → 单次协调器锁住新的写操作
  → 取消并清除 Query/HTTP/SSE/媒体/临时授权
  → 清 principal、token、authorization、scope 与敏感内存
  → 生成经过 safeReturnTo 白名单校验的当前 URL
  → /auth/session-expired
  → 用户选择“重新登录”
  → 登录 + 账户状态 + 项目授权重新验证
     ├─ returnTo 仍合法：返回原页面
     └─ 不合法：进入首个合法页面并说明“权限或项目已变化”
```

会话过期页必须说明未提交内容可能无法恢复，不能承诺自动保存。401 不显示“服务不可用”；403 不跳登录；网络错误不清会话。多并发 401 只显示一次状态页。

## 8. 通用状态组件合同

### 8.1 目标接口

这是设计合同，不是要求本阶段修改现有组件：

```ts
type GlobalStateKind =
  | 'loading'
  | 'empty'
  | 'filtered-empty'
  | 'partial-error'
  | 'full-error'
  | 'no-permission'
  | 'no-project'
  | 'read-only'
  | 'feature-unavailable'
  | 'session-expired'
  | 'service-unavailable';

interface GlobalStateContract {
  kind: GlobalStateKind;
  title: string;
  description: ReactNode;
  primaryAction?: { label: string; href?: string; onAction?: () => void; pending?: boolean };
  secondaryAction?: { label: string; href?: string; onAction?: () => void };
  technicalDetails?: {
    requestId?: string;
    occurredAt?: string;
    safeCode?: string;
    safeMessage?: string;
  };
  live?: 'off' | 'polite' | 'assertive';
}
```

合同规则：

- 标题说明“发生了什么”；说明文字包含“影响 + 下一步”，不能只写“出错了”。
- 主动作最多 1 个，直接解决当前问题；次动作最多 1 个，通常是返回/切换项目。
- 技术详情默认折叠，仅含安全 code、request ID、发生时间和可公开消息；不含 URL、token、Cookie、签名、对象 key、路径、堆栈或原始响应。
- `loading` 在稳定容器设置 `aria-busy=true`，首次进入以 `role=status`/`aria-live=polite` 播报一次；刷新保留旧数据并说明“显示上次成功数据”。
- `partial-error` 使用局部 `role=status` + `aria-live=polite`，保留仍可信的数据和独立重试，不把整页替换成 500。
- 提交失败、会话过期等需要即时关注的事件使用 `role=alert`；不要同时嵌套 `aria-live=assertive` 造成重复播报。
- 初始渲染的 403/404 标题由路由焦点管理传达，不需要不断 assertive 播报。
- 路由状态页进入后聚焦 h1（`tabIndex=-1`）；Toast 不抢焦点。
- read-only 是横幅/状态标签，不是完整错误页；保持可读数据、复制安全标识和导航动作。

### 8.2 默认中文文案

| 状态 | 标题 | 说明 | 主动作 | 次动作 | 播报 |
|---|---|---|---|---|---|
| loading | `正在加载{对象}` | `正在读取当前项目中的{对象}。` | 无 | 无 | polite，一次 |
| empty | `暂无{对象}` | `当前项目还没有{对象}。有创建权限时可从页面主动作开始。` | 按 capability 决定；无权限则不显示 | 无 | off |
| filtered-empty | `当前筛选没有结果` | `调整或清除筛选条件后重试。` | `清除筛选` | 无 | off |
| partial-error | `部分内容未能加载` | `已保留上次成功数据；失败区域可能不是最新结果。` | `重试失败区域` | `查看技术详情` | polite |
| full-error | `无法加载此页面` | `本页没有可安全显示的数据。请重试；若问题持续，请提供请求 ID。` | `重试` | `返回工作台` | alert（动态发生时） |
| no-permission | `无权访问此页面` | `当前项目授权不允许读取此内容。你可以切换项目或返回有权限的页面。` | `返回有权限页面` | `切换项目` | route focus |
| no-project | `没有可访问的项目` | `账户已通过审批，但当前没有可读取的项目。管理员授权后才能进入业务页面。` | `重新检查权限` | `退出登录` | route focus |
| read-only | `当前项目为只读` | `你可以查看此页面，但不能执行修改操作。真实项目权限由管理员另行授予。` | 无 | `查看权限说明` | polite（状态变化时） |
| feature-unavailable | `能力尚未开放` | `当前环境没有此功能的完整接口合同，因此不显示半成品操作入口。` | 无 | `返回上一页` | route focus/off |
| session-expired | `登录已过期` | `为保护公网账户，已清除页面中的敏感数据。重新登录后将尝试返回刚才的页面。` | `重新登录` | 无 | alert（发生时） |
| service-unavailable | `服务暂时不可用` | `当前无法确认账户或项目状态，页面没有把旧数据显示为最新结果。` | `重试` | `返回登录` | alert（动态发生时） |

## 9. 效果图清单与真实中文文案

所有数据型画面均标注“效果图示例，不代表真实 API 已完成”；不出现成功 Toast、成功上传、已审批成功等虚构结果。认证图不使用 Hero、营销口号、试用 CTA 或社交证明。

### A01 登录｜1440×900 + 390×844

- 标题：`登录 HC 数据平台`
- 说明：`使用已注册的用户名登录。平台部署在公网，请勿在共享设备上保存密码。`
- 字段：`用户名`，示例占位 `例如：wangxiaoming`
- 字段：`密码`，占位 `请输入密码`
- 主动作：`登录`
- 次动作：`创建账户`
- 安全提示：`V1 暂不支持 MFA。请使用不与其他网站重复的密码。`
- 错误：`用户名或密码不正确。请检查后重试。`
- 服务错误：`暂时无法登录。请稍后重试；若问题持续，请联系平台管理员。`

### A02 创建账户｜390×844

- 标题：`创建账户`
- 说明：`平台部署在公网。V1 不验证邮箱且不支持 MFA；注册后需管理员审批，审批前不能访问项目数据。`
- 字段：`用户名`，说明 `用于登录，提交后能否修改需产品确认。`
- 字段：`密码`，说明 `请勿使用与其他网站相同的密码。`
- 字段：`确认密码`
- 主动作：`提交注册申请`
- 次动作：`返回登录`
- 字段错误：`两次输入的密码不一致。`
- 提交错误：`注册申请未提交。请修正标出的字段后重试。`

### A03 等待审批｜390×844

- 标题：`账户正在等待审批`
- 说明：`注册申请已提交。管理员批准前，你不能进入业务页面或查看项目数据。`
- 状态标签：`等待审批`
- 补充说明：`V1 不发送邮件通知。请稍后重新登录查看账户状态。`
- 主动作：`返回登录`
- 不展示：虚构审批人、预计完成时间、成功进度条。

### A04 注册申请未通过｜390×844

- 标题：`注册申请未通过`
- 说明：`管理员未批准此账户。你不能进入平台或查看任何项目数据。`
- 原因：`未提供可公开的原因。`
- 主动作：`返回登录`
- 帮助文字：`如需了解处理方式，请联系平台管理员并提供用户名。`

### A05 账户已停用｜390×844

- 标题：`账户已停用`
- 说明：`此账户已被管理员停用，当前会话已结束。你不能访问原有项目。`
- 主动作：`返回登录`
- 帮助文字：`如需恢复，请联系平台管理员，并提供用户名和发生时间。`

### A06 没有可访问的项目｜1440×900 / 无 Shell

- 标题：`没有可访问的项目`
- 说明：`账户已通过审批，但当前没有可读取的项目。审批后应创建的私有项目可能仍在准备，或项目授权已被撤销。`
- 主动作：`重新检查权限`
- 次动作：`退出登录`
- 技术详情标题：`技术详情`
- 技术详情字段：`发生时间`、`请求 ID`、`状态代码`

### A07 登录已过期｜1440×900 / 无业务数据背景

- 标题：`登录已过期`
- 说明：`为保护公网账户，已清除页面中的敏感数据。重新登录后将尝试返回刚才的页面。未提交内容可能无法恢复。`
- 主动作：`重新登录`
- 不展示：旧页面模糊截图、旧项目名、资源 ID 或其他敏感上下文。

### A08 403｜1440×900 / 保留 Shell

- 标题：`无权访问此页面`
- 说明：`当前项目授权不允许读取此内容。页面没有发起后续领域请求。`
- 主动作：`返回有权限页面`
- 次动作：`切换项目`
- 状态标签：`无读取权限`

### A09 404｜1440×900

- 标题：`页面不存在`
- 说明：`链接可能已失效，或地址输入有误。请从当前可用导航继续。`
- 主动作：`返回工作台`
- 次动作：`返回上一页`
- 安全规则：资源无权限与不存在需要后端统一防枚举策略，前端不擅自暴露差异。

### A10 服务暂时不可用｜1440×900 + 390×844

- 标题：`服务暂时不可用`
- 说明：`当前无法确认账户或项目状态，页面没有把旧数据显示为最新结果。`
- 主动作：`重试`
- 次动作：`返回登录`
- 技术详情：`发生时间：2026-08-17 14:30（Asia/Shanghai）`；`请求 ID：仅在服务端返回安全值时显示`

### A11 全局 Shell｜1440×900

- 顶栏：`当前项目`、`个人私有项目`、`只读`、`Region`、`搜索`、`运行任务`、`通知`、`账户菜单`
- 页面标题：`工作台`
- 页面说明：`查看当前项目的数据阶段、阻塞原因和待处理事项。`
- 只读提示：`当前项目为只读。真实业务项目由管理员另行授权。`
- 空态标题：`此项目还没有业务数据`
- 空态说明：`这是审批后创建的私有空项目，你当前拥有只读权限。`
- 次动作：`切换项目`
- 导航必须可见 `采集任务`，但是否显示由真实 read capability 决定。

### A12 紧凑 Shell｜1280×900

- 与 A11 使用同一文案和信息架构。
- 侧栏为 64 px 功能轨道；展开按钮：`展开导航`。
- 搜索按钮可访问名称：`打开全局搜索`；没有真实接口时显示 Tooltip：`全局搜索尚未开放`，且不展示快捷键。
- 顶栏顺序仍以 `当前项目` 为第一业务上下文。

### A13 项目切换面板｜1440 与 1280

- 面板标题：`切换项目`
- 搜索标签：`搜索项目`
- 当前项标签：`当前项目`
- 私有项目描述：`私有空项目 · 只读`
- 真实项目权限示例只使用能力摘要：`可查看 · 可提交审核`，不显示角色名称作为页面分流依据。
- 加载：`正在加载可访问项目…`
- 空态：`没有匹配的项目。`
- 切换中：`正在切换项目并清理旧项目数据…`
- 失败：`项目切换失败。旧项目数据已清除，当前按无权限处理。`

### A14 搜索、通知与账户菜单能力状态｜1440

- 搜索未开放：`全局搜索尚未开放`；说明 `当前环境没有经过确认的搜索接口。`
- 通知未开放：`通知尚未开放`；说明 `当前环境未提供通知查询接口。`
- 运行任务空态：`暂无运行中的任务`
- 账户菜单项：`账户状态`、`当前项目权限`、`账户设置`（仅真实路由落地后）、`退出登录`
- 退出确认说明：`退出后将清除当前页面中的敏感数据和临时授权。`
- 退出主动作：`退出登录`
- 取消动作：`取消`

### A15 通用状态矩阵｜1440

同屏展示第 8.2 节的 loading、empty、filtered-empty、partial error、full error、no permission、read-only 和 feature unavailable；每张卡必须使用该表的完整标题、说明与动作，不使用 `Lorem ipsum`、`Something went wrong` 或虚构请求成功状态。

### A16 平板账户审批｜834×1194

- 标题：`审批注册申请`
- 对象：`用户名：wangxiaoming`（效果图示例）
- 状态：`等待审批`
- 影响说明：`批准后将创建私有空项目，并授予只读查看权限；不会自动授予真实业务项目。`
- 风险提示：`V1 不验证邮箱且不支持 MFA。请确认申请人身份已通过线下流程核验。`
- 主动作：`批准申请`
- 次动作：`拒绝申请`
- 取消：`稍后处理`
- 批准确认标题：`确认批准此注册申请？`
- 批准确认说明：`此操作会启用账户并触发私有空项目创建。真实项目仍需另行授权。`

### A17 手机待办与轻量审核｜390×844

- 页面标题：`我的待办`
- 筛选：`全部`、`即将到期`、`需要修改`
- 待办示例：`清洗草稿 CLN-20260817-0042 待审核`（效果图示例）
- 说明：`提交人已完成修改，等待审核决定。`
- 截止：`今天 18:00（Asia/Shanghai）`
- 详情标题：`审核清洗草稿`
- 影响说明：`通过后进入待发布状态，不会自动发布。`
- 主动作：`通过审核`
- 次动作：`要求修改`
- 桌面限定说明：`发布冻结、权限授权和批量操作需要在平板或桌面端完成。`

## 10. Web Interface Guidelines 的 file:line 发现

以下按 2026-08-17 获取的最新版规则核对；“通过”只代表被检查的具体规则已有代码证据。

### `frontend/src/app/shell/PlatformShell.tsx`

- `frontend/src/app/shell/PlatformShell.tsx:90` - Logo `<img>` 缺少显式 `width`/`height`；源图 106×62，需防 CLS。
- `frontend/src/app/shell/PlatformShell.tsx:205` - 主导航 item 不是 `<Link>`；`onClick` + `navigate` 不支持标准新标签页/复制链接语义。
- `frontend/src/app/shell/PlatformShell.tsx:415` - 品牌返回工作台使用 Button + `navigate`；导航应使用 `<Link>`。
- `frontend/src/app/shell/PlatformShell.tsx:438` - 搜索控件展示 `⌘ K`，但没有快捷键处理；未实现前不应显示可用暗示，且文案应兼容 Ctrl/⌘。
- `frontend/src/app/shell/PlatformShell.tsx:525` - 账户菜单的“账户设置/退出登录”没有动作处理；交互项不能看起来可用但无结果。
- `frontend/src/app/shell/PlatformShell.tsx:591` - 主内容没有明确 `<main id="main-content">`，页面也没有 skip link。
- `frontend/src/app/shell/PlatformShell.tsx:344` - ✓ 移动 Drawer 打开/关闭有焦点进入和回归。
- `frontend/src/app/shell/PlatformShell.tsx:489` - ✓ 顶栏图标按钮有 `aria-label`，装饰 Lucide 有 `aria-hidden`。

### `frontend/src/app/router/index.tsx`

- `frontend/src/app/router/index.tsx:188` - 没有公开认证路由与受保护路由边界；路由变化也没有标题/主标题焦点管理。
- `frontend/src/app/router/index.tsx:197` - 404 只有静态文本，没有返回链接；错误恢复路径不完整。

### `frontend/src/app/theme/global.css`

- `frontend/src/app/theme/global.css:77` - ✓ 使用 `:focus-visible`，没有无替代地移除 outline。
- `frontend/src/app/theme/global.css:87` - ✓ 交互元素设置 `touch-action: manipulation`。
- `frontend/src/app/theme/global.css:143` - ✓ transition 显式列属性，没有 `transition: all`。
- `frontend/src/app/theme/global.css:199` - ✓ 提供 `prefers-reduced-motion` 变体。
- `frontend/src/app/theme/global.css:77` - 焦点色 `#8B96FF` 在白色上静态对比度约 2.66:1；需增加深色实线外圈，不能只依赖浅色光晕。

### `frontend/src/app/shell/PlatformShell.module.css`

- `frontend/src/app/shell/PlatformShell.module.css:248` - 桌面图标动作 38×38 px；触控设备需要 44×44 px 命中区。
- `frontend/src/app/shell/PlatformShell.module.css:626` - 手机图标动作缩到 34 px，低于本规格触控基线。
- `frontend/src/app/shell/PlatformShell.module.css:423` - ✓ 内容容器 `min-width: 0`，为 Flex/Grid 长文本收缩提供基础。
- `frontend/src/app/shell/PlatformShell.module.css:635` - ✓ Shell 内 reduced-motion 会关闭非必要动画。

### `frontend/src/shared/ui/forms/controlled-fields.tsx`

- `frontend/src/shared/ui/forms/controlled-fields.tsx:47` - ✓ Input 使用可见 Form label、`htmlFor`、稳定 id 和 `name`。
- `frontend/src/shared/ui/forms/controlled-fields.tsx:53` - `autocomplete`、`spellCheck`、正确 type/inputMode 由调用方可选传入但未强制；认证表单必须逐字段明确。
- `frontend/src/shared/ui/forms/controlled-fields.tsx:89` - Ant Select 没有显式 `name` 传递；提交/自动化语义需在实现时验证。
- `frontend/src/shared/ui/forms/controlled-fields.tsx:47` - 字段错误显示在 Form.Item 内，但共享层没有多错误摘要和提交后首错聚焦合同。

### `frontend/src/shared/ui/state/PageState.tsx`

- `frontend/src/shared/ui/state/PageState.tsx:87` - ✓ loading 设置 `aria-busy` 并用隐藏 status 文案播报。
- `frontend/src/shared/ui/state/PageState.tsx:103` - ✓ refreshing 保留 children，并使用 `aria-live="polite"`。
- `frontend/src/shared/ui/state/PageState.tsx:127` - request ID 应标记 `translate="no"` 并支持长文本换行/安全复制。
- `frontend/src/shared/ui/state/PageState.tsx:164` - 除 unknown 外的大多数状态都用 `role="alert"`；初始 403/404/feature 状态可能产生过度播报，应按“动态错误/路由状态”区分。

### `frontend/src/shared/ui/layout/PageHeader.tsx`

- `frontend/src/shared/ui/layout/PageHeader.tsx:36` - ✓ 面包屑使用 `<nav>` + `<ol>`，父级使用 `<Link>`。
- `frontend/src/shared/ui/layout/PageHeader.tsx:55` - ✓ 每页输出一个 h1；后续需让路由切换聚焦该 h1。

### `frontend/src/shared/ui/layout/EntityDrawer.tsx`

- `frontend/src/shared/ui/layout/EntityDrawer.tsx:33` - 在 render 中读取 `document.activeElement` 并修改 ref；应移入 Drawer 生命周期/Effect，避免 render 副作用。
- `frontend/src/shared/ui/layout/EntityDrawer.tsx:38` - Drawer 需在实际 DOM 验证 `overscroll-behavior: contain`、焦点陷阱和背景 inert。

### `frontend/index.html`

- `frontend/index.html:5` - ✓ viewport 允许缩放，没有 `user-scalable=no`/`maximum-scale=1`。
- `frontend/index.html:6` - 缺少与浅色页面背景一致的 `meta name="theme-color"`，移动浏览器系统栏可能不协调。

## 11. React/Vite 后续实现约束

本节只写约束，不修改代码。

### 11.1 路由与懒加载

- 路由树分为轻量公开认证分支和受保护 Shell 分支；未认证时不装载完整 `PlatformShell` 与业务 page registry。
- 页面使用静态可分析的 `lazy: () => import('...')` 或 `React.lazy(() => import('...'))`；禁止把变量拼接到 import path。
- 登录/注册可以共享小型 AuthLayout；重型图表、Three.js、URDF、ECharts 和统一可视化工作台在进入对应路由/模式后再加载。
- 可在链接 hover/focus 时预加载高概率下一页，但不预取无权限页面或敏感数据。
- 每个路由分支提供 `errorElement`/路由错误边界；Shell 渲染异常不应强制整站刷新，认证错误不能落到业务 500。
- 独立的账户 bootstrap、项目列表和授权快照请求能并行时用 `Promise.all`；不要形成“会话→用户→项目→授权→导航”的不必要串行瀑布。
- 不使用 RSC、Server Action、`next/dynamic`、Next metadata 或 Next cache 规则。

### 11.2 状态恢复与浏览器存储

- URL 是项目、路由、Tab、筛选、排序、分页、展开对象和可分享工作台位置的权威状态；返回/刷新后必须可恢复。
- 临时 Popover 是否打开、提交 spinner 等瞬时状态留在组件，不必深链。
- `localStorage` 只存版本化、非敏感的 UI 偏好（如导航折叠）；读取失败时安全降级。key 必须带 schema version。
- 不把 bearer token、用户名单、项目授权、签名 URL、审批详情或未提交敏感表单放入 localStorage/sessionStorage。
- 项目切换前清除旧项目 Query、SSE、媒体和临时状态；不能先渲染上次项目内容再“纠正”。
- 路由返回恢复滚动和筛选；路由前进聚焦新页面 h1。Drawer 关闭回到触发器。
- 派生的“有无权限/是否只读/当前项目名”从单一快照计算，不再用 Effect 复制一份状态。

### 11.3 URL 深链

- 推荐项目作用域进入路径：`/projects/:projectId/...`。链接必须携带目标项目，不依赖浏览器上次选择。
- 若收到不存在或无权项目 ID，进入统一 no-project/403；不得静默在另一项目打开同路径。
- `returnTo` 必须通过现有 `safeReturnTo` 同源和 canonical route 白名单；不能接受 `//host`、反斜杠、控制字符或未注册路径。
- 新增认证/P20/项目前缀后同步更新 route registry 和测试，否则登录返回会被错误拒绝。
- 404、403、会话过期和服务不可用均有稳定 URL，方便刷新与自动化测试；技术错误内容不写入 URL。

### 11.4 图标与 Logo

- 继续只用 Lucide；不引入 Phosphor、Heroicons、emoji 或第二套图标。
- 建议尺寸 Token：inline 16、button/nav 18、large status 24、full-page state 48；同层级 stroke width 1.8，禁止页面随意使用 17/19/21 混搭。
- 图标可视尺寸不等于命中区：桌面控件至少 40×40，触控布局至少 44×44。
- icon-only button 必须有稳定 `aria-label`；装饰图标 `aria-hidden="true"`。
- Logo `<img width="106" height="62" alt="">` 保留源图比例；CSS 容器桌面 58×40、紧凑 48×32，使用 `object-fit: contain` 和 `height: auto`，不能拉伸。
- 品牌名是导航链接的可访问名称；Logo 作为同一链接内装饰图时使用空 alt，避免重复朗读。

### 11.5 错误边界与会话

- 全局渲染边界、路由边界、局部数据错误分层；局部失败不替换整页。
- 401 由单一协调器处理；403 由权限状态处理；404 由路由/资源处理；503/网络错误不自动清会话。
- 退出与过期使用同一“释放敏感资源”基础能力，但退出还必须调用真实服务端登出合同；未确认前不得只清前端就显示成功。
- 错误边界提供“重试当前区域/返回安全页面”；刷新整页只能是最后手段。

### 11.6 性能与长内容

- 项目列表、通知、搜索建议和导航请求去重；输入使用 `useDeferredValue` 或受控 debounce，确保每次按键成本低。
- 列表超过 50 项、可视化超过 1,000 点时先 Profile，再决定虚拟化、聚合或 Canvas；不能仅因规则存在就盲目引入依赖。
- 用户名、项目名、任务名、错误消息和 ID 验证短/平均/极长三档；Flex 子项 `min-width: 0`，显示省略时提供键盘可达全文。
- 日期/时间用 `Intl.DateTimeFormat` 并显示时区；数字/容量用 `Intl.NumberFormat`；不在 P12 引入任何非容量业务概念。
- ID、用户名、项目 ID、状态代码使用 `translate="no"`；技术数字使用 tabular nums。

## 12. 需要用户确认的问题

| # | 问题 | 推荐选择 | 备选 | 影响 |
|---|---|---|---|---|
| Q1 | 项目是否进入 URL | **推荐：** `/projects/:projectId/...`，全站项目深链明确 | 保留现有路径并统一 `?project=` | 前者改路由范围大但语义和安全边界清楚；后者迁移小但每个链接/codec 都要保留 query |
| Q2 | 待审批状态如何查询 | **推荐：** 用户重新登录时查询 + 待审批页手动“重新检查状态”，不自动轮询 | 定时轮询；仅返回登录 | 轮询增加公网接口负载与枚举防护要求；只有返回登录体验较差 |
| Q3 | 拒绝原因展示 | **推荐：** 仅展示预定义、安全的原因类别；无类别时显示“未提供可公开的原因” | 全部隐藏；展示管理员自由文本 | 自由文本可能泄露内部信息或出现不当内容；全隐藏会增加支持成本 |
| Q4 | 无邮箱下如何找回密码 | **推荐：** 管理员核验后签发一次性、短时重置流程；UI 只写“联系管理员”直到合同落地 | 不支持重置；安全问题自助重置 | 不支持会造成账户永久丢失；安全问题风险高，不建议公网使用 |
| Q5 | 会话超时与敏感操作再认证 | **推荐：** 服务端绝对/空闲超时为准；授权、停用、发布等高风险动作要求重新输入密码 | 只依赖当前会话；仅在快到期时弹窗 | 再认证需要身份接口；仅会话校验在无 MFA 的公网 V1 风险更高 |
| Q6 | 审批后私有项目名称 | **推荐：** 固定显示 `个人私有项目` + `只读`，内部 ID 不含公开用户名 | `{用户名} 的项目`；随机技术名 | 固定名最少暴露身份且易本地化；重名由内部 ID 处理 |
| Q7 | 项目切换后的落点 | **推荐：** 原路径在新项目合法时保留，否则进入同功能组首个合法页并说明 | 总是工作台；总是第一条导航 | 保留路径最符合用户意图；无条件跳首页会丢任务上下文 |
| Q8 | 未开放的搜索/通知是否留入口 | **推荐：** 效果图展示 disabled/“尚未开放”；生产在合同落地前隐藏输入外观和快捷键 | 完全移除；保留当前可点击 Toast | 完全移除减少误导但难预留布局；当前方案仍像可用功能 |
| Q9 | 手机允许哪些“简单审批” | **推荐：** 仅信息完整、可撤销/低风险的审核决定；账户批准、权限授权、发布冻结留在平板/桌面 | 手机也支持账户批准；手机只读待办 | 范围越大越需再认证、影响摘要和误触防护；只读则偏离既定轻量审批目标 |
| Q10 | ACTIVE 账户出现 0 项目时的含义 | **推荐：** 视为私有项目创建中/异常或授权撤销，显示独立状态并允许重试 | 自动回到待审批；自动创建本地占位项目 | 回待审批混淆账户/项目状态；本地占位会伪造项目成功 |
| Q11 | 用户名规范 | **推荐：** 大小写不敏感、服务端规范化，允许规则和保留字在提交前明确展示 | 大小写敏感；任意 Unicode | 影响登录一致性、同形字、防枚举、迁移和错误文案，必须由身份服务决定 |
| Q12 | 公网支持入口 | **推荐：** 配置化“联系平台管理员”，不在代码写个人邮箱/电话；错误页显示安全 request ID | 固定公共邮箱；不提供支持入口 | 配置化便于不同部署；无支持入口会让拒绝/停用/服务失败无法闭环 |

## 13. 后续验收清单

### 13.1 效果图验收

- [ ] A01–A17 均有对应画面，全部使用第 9 节真实中文文案。
- [ ] 认证页明确公网、无邮箱验证、无 MFA 和审批前不可进入，不出现营销 CTA。
- [ ] 1440 使用完整功能导航；1280 无整页横向滚动且主动作无需横向滚动才能看到。
- [ ] 平板只承接列表/审批/轻量处理；手机不伪装支持复杂工作台。
- [ ] P20 位于“采集与接收”组首位；上传记录与采集任务概念分离。
- [ ] 页面不按角色复制；只读、不可用、无权限保持同一信息架构。
- [ ] 搜索/通知没有合同时不出现假结果、假数量或假成功。
- [ ] 每张图覆盖键盘焦点、长项目名/用户名、中文错误和技术详情折叠状态。
- [ ] 状态不只靠颜色；对比度至少 WCAG AA；焦点边界达到 3:1。
- [ ] 只使用 Lucide；Logo 比例正确；没有 emoji 结构图标。
- [ ] 信号轨道只表达真实数据阶段/血缘，不用于认证页或 Shell 装饰。
- [ ] 没有暗示 Mock、身份接口、通知、搜索、P20 或项目创建 Worker 已完成。
- [ ] P12 只按容量与健康状态方向处理；本规格没有新增任何偏离该方向的组件、指标或 CTA。

### 13.2 后续实现验收（效果确认后）

- [ ] 未登录无法加载/请求业务页，P01 也经过认证 bootstrap。
- [ ] 登录、注册、账户状态、项目状态、403、404、会话过期、503 都有稳定路由和测试。
- [ ] 401 多并发只触发一次清理/跳转；403 不跳登录；退出调用真实服务端合同。
- [ ] 项目切换保留合法路径，失败时旧项目数据不可见；URL 明确项目。
- [ ] 导航与品牌使用 Link；支持 Cmd/Ctrl+Click、Middle Click、复制链接。
- [ ] skip link 是页面第一个可聚焦元素，目标为 `<main id="main-content">`；路由后聚焦 h1。
- [ ] 登录字段：username=`autocomplete="username"`、`spellCheck=false`；密码=`autocomplete="current-password"`。
- [ ] 注册密码与确认密码使用 `autocomplete="new-password"`；所有字段有可见 label、稳定 name 和就地错误。
- [ ] 异步按钮开始请求后才禁用并显示 spinner；错误摘要聚焦首错；Toast 不抢焦点。
- [ ] 1440、1280、834、768、390、360 和 200% 缩放无整页横向滚动。
- [ ] reduced motion、Windows 高对比度、键盘、NVDA/VoiceOver 基础路径完成验证。
- [ ] Logo 有 106×62 intrinsic attributes；触控目标至少 44×44；焦点不是只靠浅色 shadow。
- [ ] 新路由同步 `safeReturnTo` 白名单；外部/非法 returnTo 被拒绝。
- [ ] Shell、认证、导航、401、退出、项目切换、路由焦点和通用状态新增自动化测试。

## 14. 无法验证事项与主终端应优先处理的冲突

### 14.1 当前无法验证

- 身份服务及注册审批 API/状态枚举不存在于当前前端路由和生成合同中。
- 真实会话载体、服务端登出、密码重置、再认证、CSRF 与跨域策略未形成可验证合同。
- 私有空项目创建是否原子、失败时状态、项目列表和授权 bootstrap 未接线。
- 搜索、通知、P20 与账户状态查询是否有真实 API；本文件没有将其画成成功状态。
- 没有可用的既有 E2E/视觉回归证明 1280、平板、手机、键盘和辅助技术行为。

### 14.2 建议主终端优先处理的 5 个冲突

1. **认证边界冲突：** 当前根路由把未登录用户直接放入 Shell，P01 路由无 capability 门禁，页面内部又检查无法成为 canonical 授权的 reserved `dashboard.read`；必须先冻结公开/账户/受保护三层路由，并分开“已登录”与“P01 聚合能力已开放”。
2. **项目上下文冲突：** 业务要求项目隔离，MASTER 要求 URL 表达项目，但当前 UI 只有 Region 且 URL 不含项目；需决定 `/projects/:projectId` 还是统一 query。
3. **页面编号冲突：** 目标计划中的 P10“标注任务”与当前 P10“清洗草稿”/P11“清洗工作台”冲突；T06/T07/T01 合并前必须统一 owner、路由和导航标签。
4. **P20 合同冲突：** P20 必须独立于上传记录并位于“采集与接收”首位，但当前无路由、capability、returnTo 或真实 API；T03 与主终端需先冻结命名和权限边界。
5. **设计/代码 Token 与领域残留冲突：** 当前 success/focus Token 与 MASTER/对比度要求不一致，现有能力清单和 P12 文件仍有需移除的旧方向命名；T01 只记录，不修改 MASTER 或业务代码，主终端应协调 T08 和设计系统所有者统一收口。

## 15. 读取与验证命令

```bash
git status --short
sed -n '1,240p' /home/czy/.codex/attachments/5a6820f1-ac30-497e-a8cf-1b0c2ca98541/pasted-text-1.txt
nl -ba plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md
nl -ba design-system/hc-data-platform/MASTER.md
nl -ba README.md
nl -ba frontend/src/app/router/index.tsx
nl -ba frontend/src/app/shell/navigation-manifest.ts
nl -ba frontend/src/app/shell/PlatformShell.tsx
nl -ba frontend/src/app/shell/PlatformShell.module.css
nl -ba frontend/src/app/theme/tokens.ts
nl -ba frontend/src/app/theme/component-theme.ts
nl -ba frontend/src/app/theme/global.css
nl -ba frontend/src/shared/ui/layout/*.tsx
nl -ba frontend/src/shared/ui/state/*.tsx
nl -ba frontend/src/shared/ui/forms/*.tsx
pnpm --dir frontend typecheck
pnpm --dir frontend exec vitest run \
  src/app/shell/dataset-context.test.ts \
  src/shared/ui/BreadcrumbNavigation.test.tsx \
  src/shared/api/http-client.test.ts
git diff -- plan/frontend-ux-parallel/T01-SHELL-AUTH-SPEC.md
git status --short
```

本次实际结果：

- `pnpm --dir frontend typecheck`：通过。
- 相关 Vitest：3 个测试文件、9 个测试全部通过（dataset context 5、HTTP scope 2、breadcrumb 2）。
- 这些测试没有覆盖认证、Shell、401、退出、项目切换、响应式或辅助技术；不能用上述通过结果证明这些目标态已经实现。
