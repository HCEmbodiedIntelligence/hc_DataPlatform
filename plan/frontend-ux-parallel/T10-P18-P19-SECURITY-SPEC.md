# T10：P18 账户与权限、P19 审计及公网安全规格

> 日期：2026-08-17
>
> 阶段：效果图、安全评审与正式合同输入；不是已实现声明
>
> 责任域：P18、P19、认证状态与全局公网安全检查
>
> 实现边界：React 19 + Vite；不采用 Next.js 专属方案

## 0. 结论先行

当前 P18/P19 只能作为 Browser Mock 原型，不能作为公网可用的账户、授权或审计系统：

- P18 的页面、接口和 capability 目录仍以三种固定角色为中心；P18 只有两个 Mock GET，真实成员、注册审批、项目授权与撤销 API 不存在。
- P19 的只读列表和详情是 Mock-backed draft；后端只有审计写入基础表和事务缓冲能力，没有生产业务事件发射、读取 Router、服务端脱敏、保留、导出或不可抵赖证明。
- 真实模式没有 session/scope/capability bootstrap，P02–P19 会先被权限 Guard 阻断。
- 当前页面上的“日志不可篡改”不能被代码证明，必须在效果图和实现中改为“完整性未验证”，直到服务端返回可验证证明。
- 公网注册、不要求邮箱验证、不要求 MFA 是已确定业务约束，不是低风险选择。V1 必须用密码策略、注册/登录限流、反自动化、短会话、即时撤权、最小数据范围、审计与运营复核补偿；仍有剩余风险。
- 旧草案的“外部 IdP + 邀请 + 三固定角色”不再是本规格的业务前提。平台采用公开用户名注册、管理员审批、权限模板和项目授权；不以“内部/外包”账户类型决定权限。

本规格不修改代码、OpenAPI、迁移、Mock、测试、网关或共享设计系统，只给出后续 Owner 可执行的设计与验收合同。

## 1. 口径、证据与参考

### 1.1 证据等级

| 标记 | 含义 | 使用规则 |
|---|---|---|
| 代码已证实 | 当前工作树中的可定位实现或明确缺失 | 可作为现状结论，不等于已通过生产验证 |
| 文档已记录 | 仓库计划、README 或历史验收记录 | 若与当前代码冲突，以当前代码为准 |
| 目标规格 | 本文建议的正式产品、API、数据或交互合同 | 未经 Owner 实现和测试，不得描述为已上线 |
| 项目级待验证 | 需要部署环境、业务负责人、安全/合规或外部依赖才能确认 | 必须保留为发布门禁 |

### 1.2 已读取的主依据

- 已确定的公网账户与权限规则：plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:52-73。
- P18/P19 修改方向：plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:163-178。
- T10 责任域和并行约束：plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:276-284。
- 共享设计系统：design-system/hc-data-platform/MASTER.md:9-18,90-156,176-215。
- 当前真实 API 覆盖结论：plan/P01-P19-REAL-API-COVERAGE-AUDIT.md:7-17,54-77,151-164。
- 后续实现依赖：plan/P01-P19-REAL-API-IMPLEMENTATION-PLAN.md:58-83,89-102,121-148。
- 生产阻断项：plan/BACKEND-PRODUCTION-READINESS-GAPS.md:53-76,88-103。
- P18/P19 页面计划均明确是前端草案：plan/frontend/frontend-page-18-access-control-development-plan.md:3-5,23-34；plan/frontend/frontend-page-19-audit-log-development-plan.md:3-5,23-34。

### 1.3 Skill 与网页规则使用说明

用户点名的 frontend-design、ui-ux-pro-max、web-design-guidelines、vercel-react-best-practices 未出现在本会话可用 Skill 清单中，因此无法按 SKILL.md 执行，也不能伪称已运行。本文采用以下可验证替代：

- 只读使用共享 MASTER；其中已记录 frontend-design 与 ui-ux-pro-max 的既有取舍，见 design-system/hc-data-platform/MASTER.md:35-44。
- 2026-08-17 获取并审阅最新 Web Interface Guidelines command.md：
  https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md
- React 约束仅使用 React 19 + Vite 的仓库事实和 MASTER 规则；不套用 Server Component、Server Action 或 next/dynamic，见 frontend/package.json:21-58 与 design-system/hc-data-platform/MASTER.md:176-185。
- 安全建议参考当前 NIST SP 800-63B 与 OWASP 官方 Cheat Sheet：
  [NIST SP 800-63B](https://pages.nist.gov/800-63-4/sp800-63b.html)、
  [OWASP Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)、
  [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)、
  [OWASP CSRF](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)、
  [OWASP Logging](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)、
  [OWASP File Upload](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)。

## 2. 当前实现事实与主要错位

| 领域 | 当前可保留基础 | 已证实的缺口或错位 | 证据 |
|---|---|---|---|
| P19 页面 | 有 URL 筛选、稳定游标、列表/详情、未知枚举和多种失败态 | 原始 ISO/内部码直出；九项筛选过密；“不可篡改”无依据；导出只有禁用按钮 | frontend/src/pages/p19-audit/page.tsx:138-210,230-295,331-400 |
| P19 API/Mock | 有 scope 响应校验、严格 schema、Mock 负向场景 | 仅 Mock GET；字段可见性仍由浏览器 capability 推导；详情缓存 5 分钟 | frontend/src/features/audit/api/client.ts:9-84；frontend/src/features/audit/field-visibility.ts:11-31；frontend/src/features/audit/api/queries.ts:7-20 |
| 真实鉴权 | 后端校验 JWT 签名、issuer、audience、有效期和角色 allowlist | 前端无真实 session/bootstrap；后端角色是 uploader/annotator/reviewer/publisher/admin，与前端 capability 不同 | backend/src/hc_data_platform/security/auth.py:13-38,181-285；frontend/src/app/router/index.tsx:188-207 |
| Scope | 后端有 project/region ScopeGuard，数据库有 FORCE RLS helper | 人类 admin 在代码中默认绕过 project/region；JWT 直接携带 roles/project_ids，缺即时授权 revision | backend/src/hc_data_platform/security/scope.py:30-69；backend/src/hc_data_platform/security/auth.py:253-285 |
| 会话清理 | scope 切换会取消查询、传输并释放 signed URL/media/worker/object URL/WebGL | clearSensitiveState 只清 authorization；账户菜单“退出登录”没有行为 | frontend/src/app/shell/scope-transaction.ts:25-61；frontend/src/shared/scope/shell-store.ts:27-46；frontend/src/app/shell/PlatformShell.tsx:525-543 |
| 审计写基础 | 有 audit_events 表、RLS、事务内 audit/outbox 同提交 | project_id 强制非空，无法自然承载注册/失败登录；无 append-only DB 约束；details 任意 JSON；生产源码没有业务 AuditRecord 发射 | backend/migrations/security/001_core.sql:24-44,66-163；backend/src/hc_data_platform/security/postgres.py:352-381 |
| 审计读取 | 前端草案定义了 rich projection 和 server-redacted 目标 | 当前运行时 OpenAPI 没有 audit read 路径；外部生成草案不是运行时事实源 | backend/openapi/security.yaml:58-75；README.md:183-192,214-219 |
| 对象授权 | upload 分片、preview、published export 使用短期授权；部分响应 no-store | Raw 下载页面端点不存在；已签 URL 是 bearer，授权撤销不能自动让已签 URL失效 | backend/src/hc_data_platform/ingest/service.py:142-195；backend/src/hc_data_platform/preview/service.py:246-272；backend/src/hc_data_platform/publishing/s3.py:59-90 |
| 公网入口 | 前端容器已有 nosniff、X-Frame、Referrer、Permissions Policy | compose gateway 无 TLS、CSP、HSTS、限流、body/timeout；Helm ingress 注解为空且 NetworkPolicy 默认关闭 | frontend/container/nginx.conf:1-36；deploy/compose/gateway.conf:1-46；deploy/helm/hc-data-platform/values.yaml:49-64 |

补充事实：

- 当前后端返回扁平 RFC 9457，前端却解析嵌套 error envelope，真实错误码和 request id 会丢失：frontend/src/shared/api/http-client.ts:47-56,102-134；backend/src/hc_data_platform/core/app.py:43-51。
- Browser Mock 会注入 fixture token/scope/capabilities，掩盖真实 bootstrap 缺失：README.md:125-146。
- P18/P19 当前没有自身的 test/spec 文件；现有审计也明确没有页面真实集成/E2E：plan/P01-P19-REAL-API-COVERAGE-AUDIT.md:54-55,178-189。
- generated/access.ts 的邀请、组织成员和三固定角色定义来自仓库外草案，只能作为历史输入，不能升级为正式业务合同：frontend/src/shared/api/generated/access.ts:82-121,156-287,942-1095。

## 3. 账户、注册、登录与审批规格

### 3.1 账户不是权限角色

V1 账户只表达身份和生命周期，不表达“内部人员”或“外包人员”。同一个账户是否能看到数据、能做什么，完全由项目 grant、权限模板、数据范围和资源状态共同决定。

正式账户状态：

| 状态 | 能否登录业务系统 | 可访问界面 | 允许的管理转换 |
|---|---|---|---|
| PENDING | 否 | 受限的“等待审批”状态页和退出 | 管理员 APPROVE 或 REJECT |
| ACTIVE | 是 | 仅限授权页面、数据和动作 | DISABLE；项目 grant 可单独增删 |
| REJECTED | 否 | 受限的“申请未通过”状态页和退出 | 是否允许重新提交需产品确认 |
| DISABLED | 否 | 受限的“账户已停用”状态页和退出 | 有权限管理员 ENABLE |

不提供物理删除账户的日常 UI。用户名、审批、grant、会话和审计历史必须保留可追溯关系。

### 3.2 公开注册

未编号路由建议：

- /register：公开注册。
- /account/pending：等待审批。
- /account/rejected：申请未通过。
- /account/disabled：账户停用。
- /login：登录。
- /session-expired：会话过期。
- /no-project：没有可进入的真实业务项目。

注册表单：

- 用户名：V1 建议使用 4–32 位小写 ASCII 字母、数字、点、下划线、连字符；服务端做唯一规范化并保留原始显示值。若产品要求中文用户名，必须先完成 Unicode 同形异义、规范化和申诉规则评审。
- 密码：单因素登录建议至少 15 个字符、最多不少于 64 个字符；允许粘贴、密码管理器和显示/隐藏；检查常见/已泄漏密码 blocklist；不强制特殊字符组合，不做无事故的周期轮换。
- 确认密码；隐私与使用条款链接。不要采集当前业务不需要的邮箱、电话、公司或供应商字段。
- 输入必须有稳定 name、autocomplete=username/new-password、正确 inputmode 和就地错误；错误摘要需可聚焦。
- 提交成功后创建 PENDING 账户，并建立只允许读取自身账户状态的受限 HttpOnly session；receipt、密码、token 或审批信息不得放入 URL、localStorage 或 sessionStorage。

公网防滥用：

- 注册按 IP 前缀、设备信号、规范化用户名和全局流量分别限速；不能只做单 IP 或单用户名桶。
- 达到风险阈值后启用渐进等待、验证码/挑战或人工队列；429 返回 Retry-After。
- 用户名已存在、账户被拒绝、账户停用等响应在 HTTP、正文和时间上避免可批量枚举；确需提示用户名冲突时必须有强限流和监控。
- 限制单来源待审批数量，过期 PENDING 记录进入归档而非无界增长。
- 注册、挑战、限流、提交和失败均生成平台级安全审计事件，但不得记录密码或完整客户端指纹。

### 3.3 登录

登录只有用户名和密码。V1 明确没有邮箱验证码和 MFA。

- 未知用户名、错误密码、账户停用使用通用失败文案；只有在凭据已验证后才可显示 PENDING、REJECTED 或 DISABLED 的具体状态页。
- 成功认证后必须轮换 session id，读取账户状态和最新 authorization_revision，再签发会话。
- PENDING/REJECTED/DISABLED 只能获得受限状态 session，不能获得业务 API token。
- 登录按账户、IP/网络、设备风险和全局流量分层限速；检测 password spraying、credential stuffing 和大量不存在用户名。
- 连续失败不采用永久锁死，避免攻击者锁定合法用户；采用指数冷却、挑战和安全告警。
- 成功、失败、限流、状态拒绝、会话创建和可疑来源均进入平台级审计。

### 3.4 审批与私有空项目

审批必须是单个数据库事务或可证明的原子工作流：

1. 重新读取 registration request、账户 ETag 和当前管理员权限。
2. 展示预检：用户名、提交时间、风险信号、将发生的账户状态变化。
3. 管理员填写审批原因并确认。
4. PENDING → ACTIVE。
5. 创建唯一 home_project；项目初始为空且 private。
6. 为本人创建 Home Viewer 模板的 project grant；只能只读该空项目。
7. 写审计和 Outbox。
8. 提交后提升 authorization_revision；旧受限 session 不自动变成业务 session，用户需重新登录或安全刷新。

任一步失败必须全部回滚，不能出现“已激活但没有私有项目”或“项目存在但无 owner/viewer grant”。

真实业务项目必须由管理员另行授权。审批动作不得顺带把用户加入任何真实项目。

拒绝：

- 预检展示影响，必须填写公开安全原因码和内部备注。
- 对申请人只显示安全、可操作的公开说明；内部风控细节不回显。
- REJECTED 用户不能进入业务系统。重新申请、申诉和用户名释放规则尚未确认，是上线前产品/运营门禁。

### 3.5 会话与退出登录

目标会话模式：

- 首选同源 BFF/服务端 session，cookie 名使用 __Host- 前缀，Secure、HttpOnly、SameSite=Strict、Path=/，不设置 Domain。
- 若必须延续 SPA bearer：access token 仅在内存且短时有效，refresh token 使用上述 HttpOnly cookie 并轮换；任何 token 不进入 Web Storage、URL、日志或错误。
- JWT 不应继续把长期 project_ids/roles 当作唯一实时授权事实；至少携带 sid 与 authorization_revision，并由服务端撤销表/短缓存复核。
- 建议的空闲和绝对会话时长需由安全 Owner 确认；高风险动作使用近期密码再认证。V1 无 MFA 时，不得把“step-up”假装为第二因素。

退出登录是必须实现的真实动作，不是菜单文案：

1. POST 幂等 logout，撤销当前 session/refresh family。
2. 无论网络结果如何，前端立即取消查询和传输。
3. 清空 QueryClient 中所有账户、授权、P18/P19 和业务数据。
4. 清空 principal、sessionToken、scope、authorization、job center、页面选择。
5. 销毁 upload authorization vault、本地 File 引用、SSE、signed URL、media、Object URL、Worker、WebGL。
6. 通过 BroadcastChannel 通知同源其他 Tab。
7. replace 导航到 /login，不能由浏览器后退恢复敏感页面。
8. 服务端响应清除 cookie，并在适用时使用 Clear-Site-Data。

当前 clearSensitiveState 只清 authorization，且 Dropdown 没有 onClick；这两个点都必须由共享 Shell Owner 修正，而不是由 P18 页面私自补丁。

## 4. 权限模板、项目授权与外包数据范围

### 4.1 计算模型

最终允许 = ACTIVE 账户
∩ 当前有效 project grant
∩ grant 引用的版本化权限模板 capability
∩ data_scope 资源谓词
∩ 当前资源 allowed_actions/状态机
∩ 职责分离约束
− 显式 DENY/撤销。

规则：

- 服务端每个 Router/Service/Repository 都重新授权；前端隐藏按钮只是体验优化。
- 未知模板、capability、scope、状态、过期 snapshot 或授权服务失败一律 deny。
- V1 不提供项目自定义 capability 或自由表达式策略；模板由平台版本化发布，项目管理员只做模板和数据范围绑定。
- 同一账户可在不同项目拥有不同模板，也可在同一项目叠加多个模板；不存在固定账户类型。
- 项目授权 valid_to 固定为空，在显式撤销前持续有效。UI 必须展示“长期有效，直到撤销”和最近复核时间，不能暗示自动到期。
- 不创建供应商组织。外包边界完全依赖项目 grant、任务分配、所有权和资源级检查。
- 移除当前人类 admin 的隐式跨项目绕过。平台级 break-glass 必须是独立、短时、带原因、完整审计的运维流程；不能来自普通 admin 字符串。

### 4.2 建议权限模板

这些是模板，不是账户分类；最终 capability key 需由正式 OpenAPI 冻结。

| 模板 | 默认数据范围 | 允许 | 明确禁止 |
|---|---|---|---|
| Home Viewer | 本人 home_project | 查看项目壳和自身授权 | 任何真实业务数据、上传、Raw、审核、发布 |
| Collector | ASSIGNED_TASKS + OWN_SUBMISSIONS | 查看分配采集任务；发起本人 Web/工作站上传；查看本人上传；查看与分配任务关联的机器人自动上传汇总 | 他人任务/上传、对象路径、Raw 下载、清洗、审核、发布 |
| Cleaner | ASSIGNED_TASKS | 查看分配清洗任务和服务端生成预览；编辑非破坏性清洗草稿；提交审核 | Raw 默认不可见/不可下载；批准、发布、项目管理 |
| Reviewer | ASSIGNED_REVIEWS 或显式资源集 | 查看审阅所需对比；批准、拒绝、要求修改 | 修改原始数据、发布冻结、授权管理 |
| Publisher | APPROVED_VERSIONS | 查看已批准版本；执行冻结发布 | 审核自己的提交、绕过检查、修改已发布版本 |
| Project Access Manager | 当前项目 | 读成员/grant；预检并提交授权变更；撤销 | 通过管理权限自动读取项目业务数据；移除最后管理者 |
| Platform Account Approver | PLATFORM | 查看注册队列；审批/拒绝/停用账户 | 自动读取任意项目数据 |
| Security Auditor | PLATFORM 或显式项目 | 读取脱敏审计投影；按单独能力发起导出 | 修改账户/grant；读取 secret、Raw 或未脱敏 payload |

审核和发布是两个 capability 与两个状态转换。目标规则要求批准人与发布人不是同一 principal；若产品不接受四眼原则，必须作为显式风险例外批准，不能由前端模板叠加默默绕过。

### 4.3 Data scope 结构

grant 的 data_scope 只能使用服务端结构化谓词：

- PROJECT：整个项目；只授予确需全项目范围的模板。
- ASSIGNED_TASKS：assignment.assignee_principal_id 等于当前 principal。
- OWN_SUBMISSIONS：upload.created_by_principal_id 等于当前 principal，且来源为 Web/工作站。
- ASSIGNED_ROBOT_SUMMARY：机器人自动上传只返回与已分配任务相关的汇总，不返回 Raw 对象或可下载地址。
- EXPLICIT_RESOURCE_SET：管理员选择的不可变资源集合版本。
- APPROVED_VERSIONS：仅已审核通过且待发布的版本。

不接受浏览器提交 SQL、路径前缀、正则、自由 JSON policy 或 owner id。principal、project、effective_from、revoked_by 由服务端从认证和预检事实生成。

### 4.4 Raw 下载

- Cleaner 和 Collector 模板均不含 Raw 下载。
- 特例必须单独授予高风险 Raw Download 模板，指定 project/resource set、原因和复核记录；不能靠 Developer 固定角色间接获得。
- 每次下载重新校验账户、grant、data scope、资源状态和当前 authorization_revision。
- 浏览器不得获得长期对象凭据。优先使用服务端受控 stream/opaque grant；若必须 presigned GET，TTL 尽可能短、单对象/单方法、private/no-store、bucket policy 限 signature age。
- 生成、拒绝、开始、完成和失败均审计；审计不得存 URL、query signature、object key 全路径或 STS。
- 撤权后立即禁止新授权和续期；已签 bearer URL 在过期前可能仍可用，这是必须显式测试和记录的剩余风险。

## 5. P18 信息架构与交互

### 5.1 页面结构

保留 /settings/access，同一功能页按 capability 显示区域，不复制“内部版/外包版/管理员版”。

| 区域 | 内容 | 可见条件 |
|---|---|---|
| 注册审批 | PENDING 队列、风险摘要、申请详情、批准/拒绝 | account.registration.manage |
| 项目成员 | 当前项目成员、账户状态、模板摘要、数据范围、最后活动 | project.access.read |
| 项目授权 | 按账户或 grant 查看当前/已撤销授权，授予与撤销 | project.access.manage |
| 权限模板 | 业务化模板说明、版本、数据范围、关键禁止项；raw key 渐进展开 | project.access.read 或 template.read |

页面摘要使用“待审批、当前项目成员、长期 grant、近期撤权”四类真实指标，不再展示三固定角色 capability 数量。

列表默认字段：

- 注册审批：用户名、提交时间、状态、风险等级/原因码、处理人；技术 ID 在详情展开。
- 项目成员：显示名/用户名、账户状态、当前项目模板、数据范围、最近登录、grant 状态。
- 项目授权：被授权人、项目、模板、数据范围、生效时间、最近复核、状态、允许动作。
- 日期、数量使用 Intl 和当前用户/IANA 时区；完整 ISO、ID、revision 放“技术详情”。

### 5.2 授权流程

授予项目：

1. 选择 ACTIVE 账户和一个项目。
2. 选择一个或多个版本化模板。
3. 选择该模板允许的结构化 data scope。
4. 输入原因。
5. 请求服务端 preflight；展示新增/删除 capability、可见数据范围、Raw 风险、最后管理员影响和并发版本。
6. 高风险 grant 要求近期密码再认证和明确确认。
7. commit 只提交单次 preflight_token，带 Idempotency-Key 与 If-Match。
8. 成功后取消受影响查询、清缓存、刷新 authorization_revision，并向被影响在线会话推送撤权/变权信号。

撤销：

- 对话框必须展示账户、项目、模板、数据范围、受影响任务/正在进行操作、已签 URL 剩余窗口和原因。
- “撤销全部项目访问”“撤销 Raw Download”“停用账户”采用危险样式和二次确认。
- 禁止撤销最后一个 Project Access Manager；服务端在 commit 事务中再次检查。
- 成功后状态由服务端返回，不使用乐观成功文案。

### 5.3 响应式与可访问性

- 1440：列表 + 右侧 overlay drawer；不建议固定 360px split 挤压主表。
- 1280：导航存在时仍保持完整列表核心字段；低频字段隐藏到详情；表格仅在自身容器滚动。
- 1024/768：卡片化摘要、筛选收进“更多筛选”；授权编辑仍使用全宽 Drawer。
- 390：仅支持注册审批的轻量查看/确认、账户状态和撤权确认；复杂模板配置提示转桌面。
- 所有抽屉打开后聚焦标题/首个控件，关闭/Escape 后返回触发按钮。后台非模态 inspector 不应使用 dialog 语义。
- 搜索最多 3–5 个常用筛选，敏感用户名搜索不写 URL；稳定非敏感筛选、Tab 和 opaque selection 可进入 URL。
- 服务端 cursor page limit 最大 50；超过 50 的目录必须分页或经测量后虚拟化。

### 5.4 P18 状态

必须单独设计：首次加载、刷新、无待审批、无成员、筛选为空、局部摘要失败、全部失败、无查看权限、有查看无管理权限、grant 预检冲突、最后管理员阻断、权限在操作中被撤销、429、离线、合同不匹配、未知模板版本、超长用户名/模板名、大数据量。

## 6. P19 审计规格

### 6.1 审计作用域

P19 需要两类服务端作用域：

- PLATFORM：注册、登录、失败登录、账户状态、平台审批、平台 break-glass。
- PROJECT：项目 grant、任务/数据操作、Raw/导出、清洗、审核、发布。

当前 core.audit_events.project_id 非空，无法自然表达 PLATFORM 事件；正式迁移必须增加明确 scope_type 并允许 platform 事件没有 project_id，同时保持 project RLS。不能用虚构 project 或 organization id 填充。

P19 顶部作用域切换只显示当前用户有权读取的 scope。浏览器提交 projectId 不构成授权；服务端必须验证并过滤。

### 6.2 事件目录

最低事件族：

- auth.registration.submitted、approved、rejected、rate_limited。
- auth.login.succeeded、failed、rate_limited。
- auth.session.created、refreshed、revoked、expired、logout。
- auth.password.changed、reset_requested、reset_completed。
- account.enabled、disabled。
- project.home.created。
- access.project_grant.preflighted、created、revoked、rejected。
- access.template.version_published。
- access.break_glass.started、ended、denied。
- collection.task.assigned、upload.web_created、upload.workstation_created、robot_summary.viewed。
- cleaning.draft.updated、preview.requested、submit.requested/completed。
- review.approved、returned、rejected。
- dataset_version.publish.requested/completed/failed。
- dataset_version.raw_download.authorized/denied/started/completed/failed。
- audit.event.viewed、audit.export.requested/approved/completed/downloaded/expired。
- security.rate_limit.triggered、security.suspicious_activity.detected。

事件名必须来自后端版本化 allowlist。当前前端目录明确只是 placeholder，且缺注册/登录事件：frontend/src/features/audit/event-catalog.ts:1-15,136-150。

### 6.3 安全投影

服务端持久化前做 allowlist，而不是把任意 details JSON 写入后再由浏览器正则过滤。

安全事件字段：

- event_id、event_name、schema_version。
- occurred_at、recorded_at，均由服务端 UTC 生成。
- actor：USER、SERVICE_ACCOUNT、SYSTEM、ANONYMOUS；principal_id 可空；显示名为受控 snapshot。
- scope：PLATFORM 或 PROJECT，project_id/region_code 按类型约束。
- resource：类型、opaque id、安全显示名、父引用。
- request：服务端 request_id、job_id、client_type；IP/设备按字段 profile 脱敏。
- outcome：SUCCEEDED、DENIED、FAILED、PARTIAL 与稳定 reason_code。
- risk：等级和受控 signal code。
- change：受控 changed_fields、短摘要码、允许字段的前后值；不存完整 payload。
- producer：服务、producer_event_id、contract version。
- authorization：决策 revision、模板版本、data scope 结果摘要。
- integrity：UNKNOWN、PASSED、FAILED；只有验证服务给出证明时才能显示 PASSED。

永不持久化或返回：

- 密码、口令 blocklist 命中值、cookie、session id、access/refresh token、Authorization header。
- STS、access key、secret key、presigned URL、URL query、完整 object key/host path。
- 完整请求/响应 body、SQL、stack trace、连接串、环境变量、源代码。
- 不必要的完整 IP、User-Agent、设备指纹或个人信息。

失败登录对未知账户只存 keyed subject hint 和风险信号，不存用户提交密码。完整 IP 如确需事件响应，应进入独立受控安全字段/存储和更短保留策略，不出现在普通 P19 投影。

### 6.4 完整性与保留

目标最小保障：

- 应用角色只有 INSERT/SELECT，不得 UPDATE/DELETE；数据库 trigger/权限阻止修改，修正使用 correction event。
- 每条事件计算 canonical digest，并包含前序 digest 或批次 Merkle 证明。
- 定期生成签名 checkpoint，复制到启用对象锁/WORM 的独立安全存储。
- 校验任务持续验证缺口、重排、修改和删除，P19 显示验证时间与 checkpoint。
- 写审计与业务事务/Outbox 原子关联；关键安全事件即使无业务 project 也可靠落地。
- 审计读取、导出、下载和 break-glass 本身再次审计。

当前只有 before/after SHA-256 和普通表，且测试会 DELETE 测试事件；这只能证明哈希函数与原子写基础，不能宣称不可篡改：backend/src/hc_data_platform/security/audit.py:15-41；backend/migrations/security/001_core.sql:24-44。

在线保留天数、归档时长、Legal Hold、数据主体请求和删除例外尚未由业务/合规批准。UI 在批准前只能显示“保留策略未配置/未验证”，不能写死 180 天。

### 6.5 P19 信息架构

- 摘要：今日认证失败、待审批/授权变更、高风险事件、Raw/导出事件；每项显示 as_of 和部分失败。
- 常用筛选：时间范围、事件类别、结果、风险、关键词。
- 更多筛选 Drawer：actor、资源、request id、region、client type；九项筛选不再同排。
- 列表默认只显示本地化事件名、业务时间、操作者、目标、结果、风险；event id/request id 放详情或复制入口。
- 详情按“发生了什么、谁、在哪个 scope、结果、授权依据、安全变更摘要、关联事件、完整性”分组。
- 原始 event code、producer、digest、revision 放“技术详情”，默认折叠。
- 资源深链由各目标页 route builder 生成并再次授权；当前 pending-links 全为空，不能画成可用。
- 列表与 detail 返回 private,no-store；前端 detail 关闭后立即移除或极短缓存，退出/scope/revoke 必须全清。

### 6.6 审计导出

导出不是同步拼 CSV：

1. 选择已冻结筛选、格式、字段 profile 和原因。
2. preflight 返回 snapshot_at、预计条数、脱敏策略、完整性状态和风险。
3. 大导出需要独立审批；阈值需安全/合规确认。
4. 创建异步 job，服务端每阶段重新授权。
5. artifact 只含批准的脱敏字段，带水印/导出主体、scope、时间和 policy version。
6. 下载时再次授权，返回短期 opaque stream grant；不在响应或审计中持久化 signed URL。
7. 下载、拒绝、过期、撤权均审计；授权变更后旧 grant 立即失效。

## 7. 正式 API、数据与并发合同建议

### 7.1 公共约束

- 运行时 backend OpenAPI 是唯一事实源；生成 frontend types，CI clean regenerate 必须无 diff。
- 错误统一扁平 RFC 9457 扩展：type、title、status、detail、code、request_id、retryable、invalid_params；禁止同时维护嵌套 error。
- 所有响应回传 X-Request-ID；429 回 Retry-After；认证、授权、账户、审计和 download grant 使用 Cache-Control: private, no-store。
- 所有 list 使用 opaque keyset cursor，绑定 scope、filter hash、sort、snapshot 和 authorization_revision。
- 所有命令使用 Idempotency-Key；并发实体使用 If-Match；预检 token 单次、短时、只存在内存。
- 请求体 additionalProperties=false；principal、actor、scope、policy revision 等服务端字段禁止浏览器写入。

### 7.2 建议路径

| 领域 | 路径 | 说明 |
|---|---|---|
| 注册 | POST /auth/registrations | 公开；创建 PENDING；统一反枚举/限流 |
| 登录 | POST /auth/sessions | 返回业务 session 或受限 account-status session |
| 当前会话 | GET /auth/session/bootstrap | principal、account status、available scopes、authorization revision |
| 刷新 | POST /auth/session:refresh | cookie rotation、Origin/CSRF 校验 |
| 退出 | POST /auth/session:logout | 幂等撤销当前 session family |
| 注册队列 | GET /account-registration-requests | PLATFORM scope、cursor |
| 审批预检/提交 | POST /account-registration-requests/{id}:preflight-approve；:approve | 原子激活 + home project + viewer grant |
| 拒绝/停用 | 对应 preflight + commit | 高风险、原因、ETag、审计 |
| P18 bootstrap | GET /access/bootstrap?scope=... | 模板版本、summary、allowed actions |
| 模板 | GET /permission-templates | 版本化只读目录 |
| 成员/grant | GET /projects/{projectId}/members；GET /projects/{projectId}/grants | scope-safe projection |
| 授权预检/提交 | POST /projects/{projectId}/access-changes:preflight；POST /access-changes | 单次 token、ETag、幂等 |
| 授权撤销 | 同一 access change command | 立即提升 authorization revision |
| P19 | GET /audit/bootstrap、/events/facets、/events、/events/{id} | PLATFORM/PROJECT 结构化 scope |
| 审计导出 | preflight、job、authorize-download、stream | 异步、再授权、no-store |

路径仅是正式 OpenAPI 的输入。不得直接把 frontend/src/shared/api/generated/access.ts 复制为后端实现，因为该草案仍绑定三角色、邀请和组织成员模型。

### 7.3 核心类型

Account：

- account_id、canonical_username、display_username。
- status、credential_version、authorization_revision。
- created_at、approved_at/by、disabled_at/by。
- home_project_id、etag。
- 不返回 password_hash、rate-limit key 或风险原始特征。

PermissionTemplate：

- template_id、version、display_name、description。
- capability_keys、allowed_data_scope_kinds。
- prohibited_capabilities、risk_level、status。

ProjectGrant：

- grant_id、account_id、project_id。
- template_id、template_version。
- data_scope_rules。
- status ACTIVE/REVOKED。
- activated_at/by/reason、revoked_at/by/reason。
- reviewed_at/by、etag；valid_to 在 V1 恒为空。

AuthorizationSnapshot：

- principal_id、account_status、scope。
- effective_capabilities、effective_data_scope。
- template evidence、policy_version、authorization_revision。
- evaluated_at、expires_at、refresh_state。

AuditEventSafeProjection 采用 6.3 的字段，服务端按 field_profile 直接省略未授权字段，不依赖浏览器字段隐藏。

### 7.4 数据库与迁移

最低新模型：

- accounts、account_credentials、registration_requests、sessions、session_revocations。
- projects 的 home/private 属性与唯一 home owner 约束。
- permission_templates、project_grants、grant_scope_rules、grant_revision ledger。
- assignment/ownership 可被 Repository 直接联结的索引。
- 平台/项目双 scope audit event、event catalog、integrity checkpoint、retention/hold、export job。

迁移采用 expand → backfill/reconcile → switch → contract。恢复旧备份后不能复活已撤销 session/grant；需要单独的 revocation ledger 重放与恢复验收。

## 8. 状态机、高风险确认与失败语义

### 8.1 状态机

| 流程 | 状态 |
|---|---|
| 注册 | EDITING → SUBMITTING → PENDING；失败回 EDITING 并保留非敏感输入 |
| 审批 | PENDING → PREFLIGHTING → REVIEWABLE → COMMITTING → ACTIVE；或 REJECTED |
| Grant | DRAFT → PREFLIGHTING → REVIEWABLE → COMMITTING → ACTIVE → REVOKING → REVOKED |
| 授权快照 | LOADING → FRESH；REFRESHING；STALE/EXPIRED/FAILED 均 fail closed |
| 审计读取 | LOADING/REFRESHING/READY/EMPTY/FILTERED_EMPTY/PARTIAL_ERROR/FATAL_ERROR/FORBIDDEN/GONE/RATE_LIMITED/OFFLINE |
| 审计导出 | PREFLIGHT → AWAITING_CONFIRMATION/APPROVAL → QUEUED → RUNNING → SUCCEEDED/PARTIAL/FAILED/CANCELLED → DOWNLOAD_AUTHORIZED → EXPIRED/REVOKED |

### 8.2 高风险动作

| 动作 | 必须显示 | 确认 |
|---|---|---|
| 批准注册 | 激活账户、创建私有项目、仅 viewer、不会进入真实项目 | 明确确认 + 原因 |
| 拒绝注册/停用账户 | 登录影响、当前 session、申诉/恢复状态 | 危险确认 + 原因 |
| 授予项目管理或 Raw | 项目、模板、data scope、长期有效、数据量/风险 | 近期密码再认证 + 二次确认 |
| 撤销 grant | 立即失效范围、活动任务、已签 URL 剩余窗口 | 危险确认 + 原因 |
| 审核/发布 | 职责分离、版本、不可变结果 | 服务端预检 + ETag + 确认 |
| 审计导出/下载 | scope、时间、条数、字段 profile、脱敏、保留 | 预检；达到阈值则审批 |

禁止只用 disabled 按钮解释风险。按钮不可用时在附近显示服务端 blocked reason 和下一步。

## 9. Mock 场景与自动化测试矩阵

### 9.1 Mock 场景

Mock 始终标注“仅前端场景”，不得证明安全或后端完成。

P18：

- register-happy、duplicate-generic、register-rate-limited、challenge-required。
- login-wrong-generic、pending、rejected、disabled、session-expired。
- approval-happy、approval-conflict、home-project-rollback、reject。
- members-empty、cursor-next/previous、long-username、unknown-template。
- grant-preflight、grant-raw-warning、grant-revoke、last-manager-blocked。
- collector-assigned-own-only、collector-cross-user-denied。
- cleaner-preview-only、cleaner-raw-denied、cleaner-review-submit-only。
- permission-revoked-mid-flight、scope-switch-race、contract-mismatch、partial/fatal/offline/429。

P19：

- platform-auth-events、project-access-events、failed-login-burst。
- registration-approved、grant-created/revoked、Raw authorized/denied/downloaded。
- cleaner-submit、review-approved/returned、publish-completed/failed。
- empty、filtered-empty、cursor drift、retained-out、unknown-event。
- redaction poison corpus：token、cookie、STS、signed URL、SQL、stack、host path、password、长 PII。
- integrity-unknown、integrity-failed；只有测试证明的 fixture 才使用 passed。
- cross-project-not-visible、field-profile-restricted、permission-revoked。
- export-preflight、approval-required、job-partial、download-expired/revoked、429/offline。

Fixture 不能包含看似真实的 token、密钥、signed URL、邮箱或完整 IP；使用明显的测试占位符和确定性 opaque id。

### 9.2 测试矩阵

| 层 | 必测内容 | 发布门禁 |
|---|---|---|
| 纯函数/单元 | username 规范化、账户/Grant 状态机、模板 + data scope 求值、deny precedence、Intl、query codec、焦点返回 | 每个状态/边界/未知枚举 |
| 组件 | 注册/登录/status、审批、grant preflight/revoke、P19 筛选/详情/导出状态 | 键盘、读屏、重复提交、错误恢复 |
| OpenAPI contract | runtime schema → generated types；正负 examples；RFC 9457 | clean regenerate 无 diff |
| 后端单元 | 密码 hash、session rotation/revoke、限流、授权计算、审计 allowlist/digest | secret/PII 不进入响应/日志 |
| PostgreSQL 集成 | 原子审批、唯一 home project、最后 manager、RLS、跨 scope、append-only、cursor 并发 | 外部 DB 不得 skip release job |
| 对象存储 | bucket private、CORS allowlist、presign method/key/TTL、撤权后续期拒绝、Raw stream | 无长期 bearer、无列表权限 |
| Real API E2E | VITE_MOCK_MODE=off：register → pending → approve → login → grant/revoke → P19 查证 | 网络断言无 MSW；双用户/双项目 |
| 安全负向 | 枚举、credential stuffing、CSRF、CORS、XSS/CSP、IDOR、mass assignment、replay、audit injection、恶意上传 | 自动化矩阵 + 独立人工复核 |
| 性能 | 注册/登录 burst、成员/审计最大窗口、cursor、导出背压 | p95/p99、DB EXPLAIN、资源预算 |
| 部署/恢复 | TLS/header、WAF/rate limit、Secret、备份/恢复、撤权不复活、升级/回滚 | 当前 chart/pilot 证据，不继承历史 PASS |

关键验收样例：

- 批准后只看到 home project；访问真实 project id 返回统一 not visible。
- Collector A 猜测 Collector B 的 task/upload id 不可读取；机器人只显示汇总。
- Cleaner 获得 preview，但任何 Raw metadata/download/URL 均不可见。
- grant 撤销后，旧 Tab 的下一请求失败，查询与媒体清空；不能靠旧 JWT 继续访问。
- 同一 principal 不能批准后又发布同一版本。
- 审计中注入 token 字段必须在持久化前失败或脱敏，不能只被前端 Zod 拒绝。
- 备份恢复后，已撤销 session/grant 仍是撤销状态。

## 10. Web Guidelines、React 19/Vite 与页面质量审查

### 10.1 当前逐文件发现

| 严重度 | 发现 | 证据 | 目标修正 |
|---|---|---|---|
| P0 | P19 显示“日志不可篡改”，当前没有不可修改约束或校验证明 | frontend/src/pages/p19-audit/page.tsx:287-294；backend/migrations/security/001_core.sql:24-44 | 未证明时显示“完整性未验证”；由服务端 proof 决定 |
| P0 | 浏览器 capability 决定 P19 字段显示，不是安全边界 | frontend/src/features/audit/field-visibility.ts:11-31；frontend/src/mocks/handlers/audit.handlers.ts:142-150 | 服务端直接省略未授权字段，前端只渲染 |
| P0 | 退出登录无行为，store 清理不完整 | frontend/src/app/shell/PlatformShell.tsx:525-543；frontend/src/shared/scope/shell-store.ts:36-46 | 实现 3.5 的完整注销事务 |
| P1 | P19 datetime-local 把 UTC 字符串直接 slice 成本地输入，Asia/Shanghai 会发生时移 | frontend/src/pages/p19-audit/components/AuditFilterPanel.tsx:22-42 | 使用显式 IANA 时区转换与半开区间 |
| P1 | P18/P19 直接展示 ISO、event code、内部 ID | frontend/src/pages/p18-access/page.tsx:82-86,163-170；frontend/src/pages/p19-audit/components/AuditEventTable.tsx:31-77 | Intl 业务格式；技术值折叠/复制 |
| P1 | P18/P19 表单控件缺稳定 name/autocomplete | frontend/src/pages/p18-access/page.tsx:128-136,201-204；frontend/src/pages/p19-audit/components/AuditFilterPanel.tsx:17-117 | 可见 label + name + autocomplete/inputmode |
| P1 | P19 在 1200 就启用 360px inspector，同时把九个筛选压为一行；1280 有明显挤压风险 | frontend/src/pages/p19-audit/page.tsx:119-135；frontend/src/pages/p19-audit/styles.module.css:16-20,68-70,121-129 | overlay drawer；常用筛选 + 更多筛选 |
| P1 | P18 capability matrix 超过 50 行且无虚拟化/分页；成员 limit 可到 100 | frontend/src/pages/p18-access/page.tsx:89-101；frontend/src/pages/p18-access/query-codec.ts:18-21 | 模板替代 raw matrix；page limit 50 |
| P2 | P18 桌面 inspector 关闭后没有可靠返回触发焦点 | frontend/src/pages/p18-access/page.tsx:147-172,240-244 | 保存触发 ref，Escape/关闭后恢复 |
| P2 | P19 inspector 自身滚动未设置 overscroll contain | frontend/src/pages/p19-audit/styles.module.css:56-60 | Drawer/body 设置 overscroll-behavior: contain |

正向项：

- P18/P19 路由已使用 lazy 动态导入：frontend/src/pages/p18-access/routes.tsx:4-6；frontend/src/pages/p19-audit/routes.tsx:4-9。
- DataTable 使用语义按钮、aria-sort、内部水平滚动和稳定 row id：frontend/src/shared/ui/data/DataTable.tsx:87-102,119-168,180-197。
- EntityDrawer 有键盘关闭、destroyOnHidden 和焦点恢复基础：frontend/src/shared/ui/layout/EntityDrawer.tsx:29-57。
- capability snapshot 缺失、过期或 scope 不匹配时 fail closed：frontend/src/shared/auth/use-capabilities.ts:12-35。

### 10.2 React 19 + Vite 实现约束

- 保留 route lazy；审批/导出对话框和大技术详情按需加载。
- bootstrap、facets、list 等独立请求并行；不要在 Effect 中串联可并行网络请求。
- query key 必须包含 principal/session family、authorization_revision 和 scope，不能只靠 project 字符串。
- 详情关闭、scope 变化、撤权、账户停用和 logout 要移除敏感 Query 缓存。
- 派生权限和筛选在 render/useMemo 中计算，不用 Effect 复制；高频输入 debounce 或“应用筛选”后请求。
- page limit 20/50 时无需先上虚拟化；若产品坚持 100+，先 profile 再启用虚拟化。
- 为 P18、P19 各加 route-level error boundary，避免单个详情 schema 错误摧毁整个 App。
- 不把页面子组件定义在 render 内；避免无必要 shared/ui barrel 导入导致 chunk 边界模糊。
- 统一 Intl.DateTimeFormat/NumberFormat，不在组件散落 locale 或 ISO slice。
- 不使用 Next.js Server Component、Server Action、next/dynamic 或服务端专属缓存规则。

### 10.3 视觉与无障碍验收

- 1440×900、1280×800、1024、768、390 均检查；1280 无整页水平滚动。
- 键盘可完成注册、登录、审批、grant/revoke、审计筛选、详情和导出预检。
- 焦点环达到共享 token；状态不只靠颜色；风险图标有文本。
- 动态错误使用 role=alert；保存/加载状态使用非打断式 live region；加载超过 300ms 保留布局。
- 高风险对话框初始焦点放标题或安全的取消按钮，不默认聚焦危险确认。
- 超长用户名、模板名、reason、event 名支持换行/截断和查看全文；不把 Tooltip 作为唯一信息。
- prefers-reduced-motion 下禁用非必要抽屉/高亮动效。

## 11. 公网威胁模型与基础设施安全

### 11.1 资产与信任边界

高价值资产：

- 密码 hash、session/refresh family、authorization revision。
- 项目 grant、任务 assignment、Raw/preview/export 数据。
- 对象存储凭据和短期授权。
- 审计事件、完整性 checkpoint、备份。

信任边界：

- 未认证互联网 → CDN/WAF/Ingress。
- Browser → same-origin frontend/gateway → API。
- API/Worker → PostgreSQL、Temporal、对象存储。
- 内部人员与外包人员共享相同 UI，但 project/data scope 不同。
- 管理员、Security Auditor、break-glass 是独立高风险边界。

### 11.2 网络级攻击与可用性

目标控制：

- 生产只暴露 TLS ingress；API、metrics、ready、数据库、Temporal 和对象存储管理端不直接公网暴露。
- HSTS、TLS 版本/cipher、证书续期、OCSP 行为由平台验证。
- WAF/edge + app 双层限流；注册、登录、审批、审计查询、导出和下载分别预算。
- 设置 request body、header、连接、read/write timeout、最大并发、慢客户端和队列背压。
- /health/ready 与 /metrics 仅集群/运维可见；/health/live 只返回最小信息。
- 上传限制文件数、单文件/任务/账户/项目配额；类型、签名、Manifest、解压比、解析沙箱、恶意内容隔离。
- 连接器/外部 URL 采用 egress allowlist、防 DNS rebinding/SSRF；对象存储 endpoint 不由用户任意提交。

当前 gateway 没有上述强制项，属于代码已证实的配置缺口；实际生产是否由外部 LB/WAF 补齐属于项目级待验证。

### 11.3 CSP、CORS、CSRF 与浏览器边界

CSP 目标：

- 先 Report-Only 收集，再强制。
- default-src 'none'；script-src 仅 self/nonce；object-src 'none'；base-uri 'none'；frame-ancestors 'none'；form-action 'self'。
- connect-src、media-src、img-src、worker-src 只列部署批准的 same-origin 和对象/媒体端点；禁止通配符。
- 不允许 unsafe-eval；Ant Design 动态 style 如需 nonce，由共享 Provider/网关注入，不能永久依赖宽泛 unsafe-inline。
- 保留 nosniff、Referrer-Policy、Permissions-Policy；TLS ingress 加 HSTS。评估 COOP/CORP 后再强制，避免破坏跨域媒体。

CORS：

- 首选前后端同源并禁用 CORS。
- 若分域，只允许精确 HTTPS origin、方法和 header，Vary: Origin；credentialed 请求绝不使用星号。
- 对象存储 CORS 只允许平台 origin、必要 PUT/GET/header，不能开放 bucket list。
- CORS 不是服务端授权，也不替代 CSRF。

CSRF：

- cookie/session 认证的所有非安全方法校验 synchronizer token 或签名 double-submit，并检查 Origin、Sec-Fetch-Site 与 JSON content type。
- refresh、logout、注册审批、grant/revoke、Raw download authorize、audit export 都在保护范围。
- GET/HEAD 不产生副作用；拒绝简单 form content type 执行 JSON 命令。

当前 FastAPI 未配置 CORS/CSRF/security-header middleware；当前 bearer header 模式降低传统 CSRF，但目标 HttpOnly session 会重新引入必须设计的 CSRF 边界。

### 11.4 Token、对象 URL 与 Raw

- presigned URL 是 bearer；不得进入地址栏以外的持久存储、日志、审计、错误、analytics 或 clipboard 自动复制。
- upload PUT 绑定 bucket/key/method/part/checksum/TTL；最小签名主体不能拥有 list/get/delete。
- preview 和 Raw GET 分开；preview 只对分配任务和当前 revision 签发。
- Raw 优先经授权 stream，支持 Range 但每次校验 grant；响应 attachment、nosniff、private/no-store。
- 权限撤销后取消新签发、refresh、multipart complete 和下载 stream；对象策略限制 signature age。
- pagehide、scope、logout、403/revoke 必须销毁前端 vault、File、media、blob URL。

### 11.5 外包越权

- 不依赖“外包”标签；每次按 assignment、created_by、resource set 和 capability 判断。
- 所有 list 先在 Repository/SQL 过滤，不能拉全量后在前端过滤。
- 资源 ID 猜测、改 path/query/header、复用 cursor、换 project/region 均返回统一 not visible。
- Collector 只能本人 Web/工作站上传和分配任务；机器人上传只返回汇总。
- Cleaner 只得到服务端生成 preview，不得到 Raw object key、Manifest 下载、bucket 或 presigned URL。
- Project Access Manager 的管理能力不自动授予业务数据读取。
- 定期访问复核是必需运营控制，因为 grant 持续到撤销；离场流程必须停用账户或撤销全部 grant。

### 11.6 日志、监控、备份与回滚

- 应用/网关/对象日志使用同一 request id，但 metric label 不放 username、principal、project 等高基数/PII。
- 安全告警：注册洪泛、credential stuffing、跨项目拒绝、Raw 授权异常、批量导出、管理员 grant、break-glass、审计完整性失败。
- 备份加密、密钥分离、访问审计；Postgres、对象、Temporal、audit checkpoint 做一致性恢复。
- 明确 RPO/RTO、保留、Legal Hold 和销毁；定期 restore drill。
- 升级/回滚不得回滚授权 revision 或复活 session/grant；不可逆迁移保留旧应用兼容窗口。
- 当前 chart 的升级/回滚只有旧版本历史 PASS，新 strict-readiness chart 仍 NOT RUN：deploy/evidence/VALIDATION.md:8-17,49-53。

## 12. 里程碑、依赖与交接

| 门禁 | 交付 | 前置 | 完成标准 |
|---|---|---|---|
| S0 产品/安全冻结 | 账户恢复、拒绝后重申、会话时长、审计保留、导出阈值、四眼原则 | 产品、安全、合规、运营 | 决策有 owner、反例、验收样例 |
| S1 威胁模型/ADR | session 模式、scope 模型、模板目录、audit integrity、Raw transport | S0 | chosen/rejected/why、回滚点 |
| S2 唯一合同/迁移 | runtime OpenAPI、accounts/grants/audit schema、RFC 9457 | S1 | provider contract、migration rehearsal |
| S3 后端安全链 | 注册/登录/审批、即时撤权、P18/P19 read/write、审计 emission | S2 | 单元 + 外部 DB/对象集成 |
| S4 前端改版 | 认证页、P18/P19 IA、logout、Intl/a11y、真实 API | S3、T01 Shell Owner | VITE_MOCK_MODE=off 可用 |
| S5 安全/E2E/性能 | 双用户双项目、IDOR、CSRF/CSP、Raw、审计 poison、负载 | S4 | required CI，无意外 skip |
| S6 生产验收 | TLS/WAF/header、监控、备份恢复、当前升级回滚 | S5、平台 Owner | 证据绑定 release digest |

共享依赖：

- T01/全局 Shell Owner：认证路由、session/bootstrap、logout、scope、query/cache 清理、全局错误页。
- T02/工作台 Owner：preview/Raw viewer 的授权输入与资源释放合同。
- 后端 Security Owner：账户、模板/grant、session/revocation、审计。
- Gateway/Platform Owner：TLS、CSP/CORS、CSRF、WAF、rate limit、NetworkPolicy、Secret。
- Object Storage Owner：bucket/IAM、CORS、presign、encryption、object lock、日志。
- Product/Security/Compliance：恢复、保留、Legal Hold、导出审批、break-glass、四眼原则。

## 13. 交付路径

/home/czy/hc_DataPlatform/plan/frontend-ux-parallel/T10-P18-P19-SECURITY-SPEC.md

## 14. 建议复核命令

以下命令均为只读或验证；本文没有执行产品代码修改：

- git status --short
- git diff --no-index /dev/null plan/frontend-ux-parallel/T10-P18-P19-SECURITY-SPEC.md
- sed -n '1,260p' plan/frontend-ux-parallel/T10-P18-P19-SECURITY-SPEC.md
- sed -n '261,620p' plan/frontend-ux-parallel/T10-P18-P19-SECURITY-SPEC.md
- sed -n '621,800p' plan/frontend-ux-parallel/T10-P18-P19-SECURITY-SPEC.md
- rg -n 'PENDING|Raw|CSP|CORS|CSRF|Mock|不可验证|Top 10' plan/frontend-ux-parallel/T10-P18-P19-SECURITY-SPEC.md
- rg -n 'AuditRecord\\(' backend/src backend/tests
- rg -n 'registration|login|session|members|audit/events' backend/openapi.generated.yaml
- rg --files frontend/src | rg '(p18-access|p19-audit|features/access|features/audit).*(test|spec)\\.(ts|tsx)$'
- cd backend && .venv/bin/python -m hc_data_platform.core.openapi --check
- pnpm --dir frontend typecheck
- docker compose -f compose.dev.yaml config -q

## 15. 无法验证项

- 四个点名 Skill 不在本会话 Skill 清单中，无法执行其 SKILL.md；已使用共享 MASTER、最新上游 Web Guidelines 和仓库 React/Vite 事实替代。
- 未访问真实生产/预发布环境，无法确认外部 CDN、LB、WAF、TLS、CSP、CORS、NetworkPolicy、Secret Manager 是否补齐仓库缺口。
- 未获得真实身份恢复流程；无邮箱验证、无 MFA 时，安全的自助找回无法由当前需求确定。
- 审计在线/归档保留、Legal Hold、完整 IP/PII 合法性、导出审批阈值未获合规批准。
- 对象存储的生产 bucket policy、CORS、encryption、versioning、object lock、signatureAge 和访问日志未提供。
- 没有生产账号、跨项目数据或外部 PostgreSQL/MinIO/Temporal，本轮不能执行真实 IDOR、撤权、Raw、审计、备份恢复或容量验收。
- 当前 upgrade/rollback 未在现行 strict-readiness chart 上执行，不能继承历史证据。
- 当前工作树已有其他终端/用户未提交修改；本文只依据读取时事实，不判断其最终合并状态。

## 16. 公网安全缺口 Top 10

| 排名 | 缺口 | 类型与证据 | 关闭门禁 |
|---:|---|---|---|
| 1 | 真实注册、登录、session/bootstrap、审批链不存在，真实模式全站授权失败 | 代码已证实：frontend/src/app/router/index.tsx:188-207；plan/P01-P19-REAL-API-COVERAGE-AUDIT.md:59-69 | S2–S4 完成，off 模式真实 E2E |
| 2 | 无邮箱验证、无 MFA，又没有已确认的密码找回/身份恢复与反自动化运营流程 | 已确定业务风险 + 项目级待验证 | S0 批准恢复、限流、风控和剩余风险 |
| 3 | 前后端权限模型不一致，且后端 human admin 隐式跨项目绕过 | 代码已证实：backend/src/hc_data_platform/security/auth.py:13-38；backend/src/hc_data_platform/security/scope.py:30-69 | 模板 + data scope + 无隐式 bypass 的 DB/IDOR 测试 |
| 4 | 撤权与 logout 不完整；JWT project/role claim、缓存和浏览器资源可能继续生效 | 代码已证实：frontend/src/shared/scope/shell-store.ts:27-46；frontend/src/app/shell/PlatformShell.tsx:525-543 | session family 撤销、authorization revision、跨 Tab 清理 E2E |
| 5 | Raw 下载没有正式端点/策略；presigned URL 是 bearer，撤权窗口和对象策略未验证 | 代码已证实 + 项目级待验证：backend/src/hc_data_platform/publishing/s3.py:59-90；AWS 官方将 presigned URL 定义为 bearer | 受控 stream/短授权、bucket policy、错 scope/撤权测试 |
| 6 | 审计只有基础表/缓冲写入；无业务发射、读取、持久化前脱敏、保留或不可抵赖证明 | 代码已证实：backend/src/hc_data_platform/security/audit.py:28-45；backend/src/hc_data_platform/security/postgres.py:352-381 | 平台/项目事件、append-only、checkpoint、P19 security E2E |
| 7 | 公网浏览器基线不完整：无默认 CSP/HSTS，CORS/CSRF 合同未实现 | 代码已证实：frontend/container/nginx.conf:7-10；deploy/compose/gateway.conf:1-46；backend/src/hc_data_platform/core/app.py:71-129 | TLS ingress + 强制 header + CSRF/CORS 自动测试 |
| 8 | 网关无 route rate/body/timeout/query budget；注册、登录、审计、导出和上传易被滥用或 DoS | 代码已证实：deploy/compose/gateway.conf:10-45；plan/BACKEND-PRODUCTION-READINESS-GAPS.md:72 | WAF/app 双限流、上传隔离、burst/slow-client/load 证据 |
| 9 | 外包隔离依赖尚未实现的 assignment/ownership/grant 数据模型；无跨用户/跨项目真实测试 | 代码/文档已证实：plan/P01-P19-REAL-API-COVERAGE-AUDIT.md:54-55；plan/P01-P19-REAL-API-IMPLEMENTATION-PLAN.md:101-102 | 双用户双项目 IDOR、Cleaner no-Raw、Collector own/assigned E2E |
| 10 | 备份恢复、撤权不复活、当前 chart 升级/回滚与生产容量均无现行证据 | 项目级待验证：plan/BACKEND-PRODUCTION-READINESS-GAPS.md:63-75；deploy/evidence/VALIDATION.md:49-53 | restore drill、revocation replay、现行 upgrade/rollback、容量验收 |
