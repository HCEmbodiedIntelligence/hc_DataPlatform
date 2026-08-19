# P01–P19 真实 API 分阶段实施计划

> 基线日期：2026-08-17  
> 输入：`P01-P19-REAL-API-COVERAGE-AUDIT.md`、当前运行时 OpenAPI、实际迁移/Worker/测试，以及 `PRODUCT-DESIGN-DECISIONS-REQUIRED.md`。  
> 当前状态：本计划只记录后续工作，**未在本次审计中实现任何业务功能**。所有任务初始为“未开始”。

## 1. 总体依赖与建议顺序

`阶段 0 产品/合同口径 → 阶段 1 正式 OpenAPI/数据模型 → 阶段 2 基础查询与授权 → 阶段 3 聚合/异步能力 → 阶段 4 前端真实接入 → 阶段 5 集成/E2E/性能/安全 → 阶段 6 试点/升级回滚/生产验收`

关键路径不是先写 P01 React，而是：**PD-01～PD-10 → auth bootstrap/error contract → P01 正式合同与事实模型 → 四个聚合实现 → 前端切换 → E2E/性能/越权 → 试点**。

## 2. 三类待办严格分离

### A. 需要产品设计/业务确认

全部问题、背景、推荐选项、备选方案和阻塞关系维护在 `plan/PRODUCT-DESIGN-DECISIONS-REQUIRED.md`。其中 PD-01～PD-10 是 P0；没有这些答案，不得冻结 P01、权限、时间范围或跨区域合同。

### B. 需要技术设计

| 设计编号 | 页面 | 技术设计项 | 推荐方案 | 备选方案 | 主要权衡 | 状态 |
|---|---|---|---|---|---|---|
| TD-01 | 全局 | 正式 OpenAPI 事实源 | 以 `app.openapi()` + `backend/openapi.generated.yaml` 为唯一事实源；前端只从 CI 产物生成 | 单独合同仓库并版本化发布 | 单仓简单且不漂移；独立仓适合多消费者但增加发布协调 | 待设计评审 |
| TD-02 | 全局/P18 | auth bootstrap 与 scope | 新增后端 session/authorization bootstrap，返回 principal、可选 project/region、capability snapshot；Router/Repository 双层 scope enforcement | 前端解析 JWT 并本地推导 capability | 服务端权威可撤销且易审计；本地推导低延迟但易漂移/越权 | 待 PD-01/02/23 |
| TD-03 | 全局 | 错误合同 | 后端继续 RFC 9457，扩展稳定 `code/request_id/details`；前端客户端原生解析 problem+json | 后端包成前端现有 `{error:{...}}` | RFC 标准化、工具支持好；改前端成本较低，避免双 envelope | 可直接评审 |
| TD-04 | 全局 | 分页、筛选、排序、时间 | cursor 包含稳定 sort tuple；allowlist filter/sort；UTC + IANA timezone；`from` 含/`to` 不含 | offset pagination；各页面自定义 | cursor 抗插入且适合审计/对象大表，但调试和任意跳页较弱 | 待 PD-03 |
| TD-05 | P01 | 聚合查询与一致性 | 首版 indexed SQL/CTE + 每段 `as_of/status`；用同一读时点/refresh token；对缺失事实建明确 projection 表 | 每次扫对象存储/Lance；或立即做物化视图 | 在线 SQL 上线快且可追溯；大范围性能可能需要后续 rollup | 待 PD-04～10 |
| TD-06 | P01/P12 | 缓存/rollup | 无容量证据时不引入新缓存；先设 query budget。超阈值后用 Outbox/Temporal 增量 rollup，支持重算 | Redis response cache；Postgres materialized view | rollup 可审计但写链路复杂；Redis 快但权限键/失效风险高；MV 刷新简单但新鲜度较差 | 待性能基线 |
| TD-07 | P11/P13/P14/P16/P17 | Worker 边界 | 长时、可重试、需补偿的 preview/apply/simulate/execute/asset validation/compatibility 交给 Temporal；CRUD/读取同步 | 全同步 HTTP；或后台进程/cron | Temporal 已存在且有重试/可观测基础，但必须增加幂等、版本和回放测试 | 待对应产品状态机 |
| TD-08 | P02/P09–P19 | 缺失数据模型 | 按领域独立 schema，使用不可变 identity/version + append-only event/ledger；引用既有 project/region scope | 单一通用 JSON entity 表 | 领域表约束和查询好；通用表迭代快但一致性/索引/迁移差 | 待页面产品模型 |
| TD-09 | P03/P07/P08/P10/P11/P13/P18 | 幂等与并发 | 所有命令支持 Idempotency-Key；资源带 revision/ETag，冲突返回 409；Worker activity 幂等 | 仅数据库锁；客户端防重复 | 显式协议可跨重试，代价是 key 存储和状态机测试 | 可直接评审 |
| TD-10 | 全局 | 权限校验位置 | Router 校验 capability，Service 构造 scope，Repository 每个查询强制 scope predicate；敏感表可加 RLS | 只在网关/Router 校验 | 纵深防御减少遗漏，但测试和 query plumbing 增加 | 待 TD-02 |
| TD-11 | 全局 | 错误与降级 | 真实模式只显示真实 empty/403/404/409/partial/stale 状态；绝不回落 fixture；聚合接口返回分段状态 | 整页失败；或静默缓存/Mock | 明确状态可信但 UI 分支更多 | 待 PD-10 |
| TD-12 | 全局 | 可观测性与性能目标 | 记录 route/code/scope-safe labels、query latency/result size、Worker lag/attempt、rollup age；定义 p95 和容量门禁 | 只用通用 HTTP 指标 | 领域指标能定位瓶颈，但必须避免 project/user 高基数和敏感标签 | 待 SLO 确认 |

### C. 已设计边界内可以直接进行代码实现

下列是工程任务类型，不把未知产品值写死；它们在相应正式合同冻结后可直接开始：

| 类别 | 可直接实现的具体内容 | 对应任务 |
|---|---|---|
| 前端 | 接通 auth/scope bootstrap；解析 Problem Details；以正式生成类型替换手写 wire types；逐页移除 Mock-only gate；实现真实 empty/error/partial/stale 状态 | I4-01～I4-13 |
| 后端 Router | 按冻结的 OpenAPI 添加 bootstrap、页面 read/query、command Router；统一 scope、cursor、request id、错误响应 | I2-01～I2-14、I3-01～I3-08 |
| Service/Repository | 为已有事实表构建 scoped projection；实现状态机、聚合、幂等、乐观并发；不把 Mock fixture 当事实 | I2-03～I3-08 |
| 数据库迁移 | 添加缺失的成员绑定、页面 projection/inventory/issue/draft/lifecycle/robot/calibration/registry 表和索引；迁移可升级/回滚验证 | I1-09、I1-10、I2-08～I2-13 |
| Temporal Worker | 注册草稿 apply、lifecycle simulate/execute/restore、资产/标定/Schema 校验和可选 dashboard/storage rollup；保证 activity 幂等与 workflow replay | I3-05～I3-08 |
| OpenAPI/类型生成 | 将正式路径合并到后端 runtime OpenAPI，运行 drift check，从该产物生成前端类型并在 CI 检查 | I1-01～I1-08、I1-11 |
| 单元测试 | 公式边界、状态机、scope predicate、cursor、Problem Details、前端 schema/降级组件 | I5-01 |
| 集成测试 | Postgres/MinIO/Temporal 真实适配器、迁移、幂等重试、一致性、外部依赖失败 | I5-02、I5-03 |
| E2E 测试 | 使用 `VITE_MOCK_MODE=off` 和真实 API 覆盖登录/scope、关键读写、403/409/partial/stale，不启动 MSW | I5-04～I5-06 |
| Docker/部署 | real-api compose profile、worker health、迁移 job、网关/problem+json、环境变量、chart pilot/rollback | I6-01～I6-05 |
| README/运维文档 | 真实/Mock 启动方式、OpenAPI 生成、迁移、SLO、故障处置、升级/回滚和验收证据 | I6-01、I6-06 |

## 3. 阶段 0：需求和合同确认

| 任务编号 | 所属页面 | 任务描述 | 修改范围 | 前置依赖 | 是否需要我确认 | 验收标准 | 建议测试 | 风险 | 优先级 | 工作量 | 完成状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| I0-01 | 全局/P18 | 冻结 principal×project×region×role 权限矩阵、跨区域规则和 capability 映射 | 产品需求、权限矩阵、威胁模型 | 无 | 是：PD-01/02/23 | 每个角色在每个 scope 的 allow/deny 有例子；最后管理员和权限撤销规则明确 | 表驱动权限用例评审 | 决策错误会影响全部页面和迁移 | P0 | L | 未开始 |
| I0-02 | P01 | 冻结 activity、unique bytes、episode 漏斗、storage role、coverage denominator、pending source 的公式 | P01 指标字典、验收样例 | I0-01 | 是：PD-04～09 | 每个指标给出分子/分母、去重键、时间字段、状态集合、空值和反例 | 用现有表构造 10 组手算样例 | Mock 结构诱导错误合同 | P0 | L | 未开始 |
| I0-03 | 全局/P01/P12/P19 | 冻结时间范围、时区、数据新鲜度和部分失败/空/无权限展示 | 产品需求、错误/降级说明 | I0-01 | 是：PD-03/10 | UTC/IANA、边界、最大窗口、SLO、per-section 状态均明确 | DST/跨日/陈旧/部分失败用例评审 | 时区和缓存不一致 | P0 | M | 未开始 |
| I0-04 | P02–P04 | 确认首批连接器、凭据、上传创建字段、去重和命令状态机 | ingest 产品需求 | I0-01 | 是：PD-11/12 | connector allowlist、session state diagram、每个命令前后置条件批准 | 状态转换和重复请求样例 | 凭据/SSRF和重复上传 | P0 | M | 未开始 |
| I0-05 | P05–P08 | 确认 dataset identity/lifecycle、version review 和 annotation 分配/复核 | dataset/annotation 产品需求 | I0-01 | 是：PD-13～15 | identity、唯一性、不可变边界、审批/复核职责和冲突规则批准 | 状态图/角色泳道评审 | 与现有底层状态不兼容 | P0 | L | 未开始 |
| I0-06 | P09–P11 | 确认 manual issue 与 cleaning draft/EDL 的独立模型、操作集和提交/回滚规则 | cleaning 产品需求 | I0-01 | 是：PD-16/17 | issue/draft/operation 状态机和失败补偿例子批准 | 冲突、半提交、重放样例 | 直接改数据导致不可恢复 | P0 | XL | 未开始 |
| I0-07 | P12/P13 | 确认 P12 V1 是否包含成本；冻结容量口径及 lifecycle 的 legal hold、dry-run、审批、恢复 SLA | storage 产品/合规需求 | I0-01/I0-03 | 是：PD-18/19 | 当前三 Tab 与残留 cost 草案的去留明确；策略优先级及不可删除条件可执行 | 典型/冲突策略手算和误删演练设计 | 合规、范围漂移和不可逆删除 | P0 | L | 未开始 |
| I0-08 | P14–P17 | 确认 robot/model/component、calibration、schema version/compatibility 规则 | robotics/schema 产品需求 | I0-01 | 是：PD-20～22 | identity、版本、有效期、兼容/批准/废弃规则批准 | 兼容与历史引用样例 | 破坏历史数据解释能力 | P0 | XL | 未开始 |
| I0-09 | P19 | 确认审计可见范围、脱敏、保留、不可抵赖和导出审批 | 安全/合规需求 | I0-01 | 是：PD-24 | event field allowlist、retention、export threshold/approval 确认 | PII/secret/越权导出用例 | 审计本身泄密 | P0 | M | 未开始 |
| I0-10 | 全局 | 对 TD-01～TD-12 作 ADR 评审并选择方案 | I0-01～I0-09 对应答案 | I0-01～I0-09（可分批） | 否；技术评审，业务值仍需确认 | 每项有 chosen/rejected/why、容量假设和回滚点 | ADR walkthrough | 未记录权衡导致反复改合同 | P0 | L | 未开始 |

## 4. 阶段 1：OpenAPI 与数据模型设计

| 任务编号 | 所属页面 | 任务描述 | 修改范围 | 前置依赖 | 是否需要我确认 | 验收标准 | 建议测试 | 风险 | 优先级 | 工作量 | 完成状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| I1-01 | 全局 | 建立唯一 OpenAPI 生成链：runtime schema→generated file→前端 types，并在 CI 阻断 drift | `backend/openapi*`, core OpenAPI 工具, frontend generator, CI | TD-01 | 否 | 一条命令可重现；手改产物或后端 drift 会失败；前端不再依赖未版本化外部草案 | OpenAPI check + clean regenerate diff | 多源合同继续漂移 | P0 | M | 未开始 |
| I1-02 | 全局/P18 | 定义 auth/session/scope/capability bootstrap 正式合同和 401/403 语义 | OpenAPI security schemas/paths | I0-01, TD-02/10 | 是：确认返回的业务 scope 内容 | 合同含 principal、可选 scope、capability、expiry/revision；有 allow/deny examples | Schema examples + contract test | capability 泄漏或无法即时撤销 | P0 | M | 未开始 |
| I1-03 | 全局 | 统一 RFC 9457 扩展、stable code、request id、validation details | core OpenAPI/common errors, front http types | TD-03 | 否 | 400/401/403/404/409/422/429/5xx schema 唯一；现有后端错误可映射 | OpenAPI negative examples | 破坏现有客户端 | P0 | M | 未开始 |
| I1-04 | P01 | 为 snapshot/activity/coverage/pending-items 定义正式合同，不照搬 Mock；明确 scope/range/timezone/as_of/section status/cursor | dashboard OpenAPI 新文件及 common schemas | I0-02/I0-03, TD-04/05 | 是：PD-04～10 最终值 | 四路径进入 runtime OpenAPI；每字段可追溯指标字典；缺失源/部分失败有 schema | OpenAPI examples + formula contract tests | 指标语义漂移 | P0 | L | 未开始 |
| I1-05 | P02–P04 | 设计 data source 和 upload page projection 合同；决定适配现有 upload API 而非制造同名不兼容资源 | ingest OpenAPI、schemas | I0-04, I1-03 | 是：创建/状态业务字段 | 列表/detail/objects/events/verification/commands 与现有 session id/state 有明确映射 | 前后端 consumer/provider contract | 双合同长期分叉 | P0 | L | 未开始 |
| I1-06 | P05–P08 | 设计 dataset/list/detail/version review/annotation 页面合同并映射现有 Lance/publishing/annotation | domain OpenAPI files | I0-05, I1-03 | 是：identity/status/approval | 每个页面字段有底层源或明确新 projection；命令状态机和 scope 一致 | 状态转换、分页、权限合同测试 | BFF 投影与底层漂移 | P0 | XL | 未开始 |
| I1-07 | P09–P13 | 定义 manual issue、cleaning draft/EDL、storage inventory、lifecycle 合同；仅在 PD-18 选择启用时纳入 cost | 新 domain OpenAPI files | I0-06/I0-07, I1-03 | 是：PD-16～19 | CRUD/命令/preview/simulate/execute/restore 和安全限制完整；deferred cost 不进入正式 V1 合同 | 危险命令/幂等/部分失败 examples | 不可逆操作合同不足和残留草案误发布 | P0 | XL | 未开始 |
| I1-08 | P14–P19 | 定义 robot/model/calibration/schema/member/audit 合同 | 新 domain OpenAPI files | I0-08/I0-09/I0-01, I1-03 | 是：PD-20～24 | identity/version/compatibility/member change/audit redaction 均有 schema | 兼容、越权、脱敏合同测试 | 管理面 API 安全风险 | P0 | XL | 未开始 |
| I1-09 | P01/P12 | 设计 canonical episode/storage inventory/聚合 projection 和必要索引；列出可回填来源与未知字段 | backend migrations + data ADR | I1-04/I1-07, TD-05/06/08 | 否 | ERD、唯一键、scope、时间字段、索引、backfill/reconciliation 计划批准 | explain-plan 原型、回填 dry-run | 现有事实无法完整回填 | P0 | L | 未开始 |
| I1-10 | P02/P09–P18 | 为缺失域设计 normalized schema、ledger、外键/引用和迁移顺序 | domain migrations + ERD | I1-05/I1-07/I1-08, TD-08/09 | 否 | 每域主键/version/scope/audit/retention/索引明确；无伪造历史 | migration up/down/forward compatibility 设计测试 | 大量新表与历史引用迁移 | P1 | XL | 未开始 |
| I1-11 | 全局 | 从正式 schema 生成前端 types/client facade，建立禁止手写 wire type 的边界 | frontend generated/API client, CI | I1-01～I1-08 | 否 | 生成无 diff；runtime client 引用 generated types；草案类型只保留迁移说明 | typecheck + generated drift test | 大规模类型迁移 | P0 | L | 未开始 |

## 5. 阶段 2：后端基础查询接口

| 任务编号 | 所属页面 | 任务描述 | 修改范围 | 前置依赖 | 是否需要我确认 | 验收标准 | 建议测试 | 风险 | 优先级 | 工作量 | 完成状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| I2-01 | 全局/P18 | 实现 session/scope/capability bootstrap，并在 Router/Repository 强制 scope | backend security Router/Service/Repository/model | I1-02/I1-10 | 否 | 真实 JWT 可获得授权 snapshot；撤权后拒绝；跨 scope 读不到数据 | 单测+Postgres RLS/越权集成 | 全站安全关键路径 | P0 | XL | 未开始 |
| I2-02 | 全局 | 实现 Problem Details 扩展、中间件 request id 与前端所需稳定错误码 | backend core/errors/app | I1-03 | 否 | 所有错误 content-type/schema 一致；日志/响应 request id 相关联 | 负向 Router contract test | 错误信息泄露 | P0 | M | 未开始 |
| I2-03 | P02 | 实现 allowlisted data-source CRUD、secret-reference 和连接测试接口 | new source Router/Service/Repository/migration | I1-05/I1-10, PD-11 | 否 | 不存明文 secret；scope 隔离；连接测试受 egress allowlist；变更有审计 | 单元+Postgres+SSRF/secret tests | 密钥和网络访问 | P1 | XL | 未开始 |
| I2-04 | P03/P04 | 在现有 ingest 上实现页面列表/detail projection、对象/验证/event 游标查询和兼容命令适配 | ingest Router/Service/Repository/OpenAPI | I1-05, I2-01/02 | 否 | 当前前端需求字段都有真实来源；创建/暂停/恢复/取消/重试与状态机一致 | 单元+Postgres/MinIO+幂等并发 | 状态和身份映射错误 | P0 | XL | 未开始 |
| I2-05 | P05/P06 | 实现 dataset 列表/facets/create 和 detail/version/episode/source/capacity 查询投影 | lance catalog + projection Repository | I1-06/I1-09, I2-01/02 | 否 | scope/filter/cursor 正确；episode 和容量不以 rollout 假冒；空数据明确 | 单元+Postgres+Lance reference | 聚合性能/一致性 | P0 | XL | 未开始 |
| I2-06 | P07 | 实现 version detail/checks/inventory/diff job/read 和 approve/return 命令适配 | catalog/publishing Router/Service | I1-06, I2-01/02 | 否 | 状态机、不可自审、ETag/Idempotency-Key、审计均生效 | 状态表驱动+并发/重复命令集成 | 错误发布/双重审批 | P0 | XL | 未开始 |
| I2-07 | P08 | 对齐 annotation 页面与真实 Router：scope、列表/detail、租约、review 命令和响应投影 | annotation Router/Service/Repository | I1-06, I2-01/02 | 否 | 前端不调用 colon 草案路径；现有数据可访问；锁/复核权限明确 | 现有回归+Postgres+并发/越权 | 破坏已有 annotation API | P0 | L | 未开始 |
| I2-08 | P09 | 实现 manual issue 模型、evidence、状态历史、列表/detail/assign/resolve/reopen | new issue domain + migration | I1-07/I1-10, I2-01/02 | 否 | 独立于 annotation 且可关联；状态/SLA/审计/scope 完整 | 状态机、重复来源、越权集成 | issue 重复和错误关闭 | P1 | XL | 未开始 |
| I2-09 | P10/P11 | 实现 cleaning draft、base version、operation log、乐观锁和只读 preview job contract | new cleaning domain + migration | I1-07/I1-10, I2-01/02 | 否 | operation 可重放；revision 冲突 409；未提交不修改正式数据 | property/replay+并发+Postgres | 数据损坏/操作格式演化 | P1 | XL | 未开始 |
| I2-10 | P12 | 实现 scoped storage inventory/object/multipart read repository 和 reconciliation 状态；cost 仅在 PD-18 启用后作为独立增量 | storage domain + migration/adapters | I1-07/I1-09, I2-01/02 | 否 | 物理/逻辑计量分离；游标稳定；对象路径按权限脱敏；未批准 cost 不暴露 | Postgres/MinIO inventory+分页/权限 | 全桶扫描、重复计量和孤儿合同 | P1 | XL | 未开始 |
| I2-11 | P14/P15 | 实现 robot model/version/asset metadata、robot/component 目录 CRUD 与历史引用 | robotics domain + migration | I1-08/I1-10, I2-01/02 | 否 | identity/version/有效期/scope/停用规则受约束；ingest 引用可迁移 | DB constraint+历史引用+越权 | 标识冲突/旧数据断链 | P1 | XL | 未开始 |
| I2-12 | P16/P17 | 实现 calibration/schema registry 的版本化 CRUD、发布前状态和引用查询 | calibration/schema domains + migration | I1-08/I1-10, I2-01/02 | 否 | 已发布版本不可变；有效期/兼容 policy 可查询；引用可追踪 | version constraint+compat fixtures | 破坏兼容或错标定 | P1 | XL | 未开始 |
| I2-13 | P18 | 实现成员/binding 列表、邀请/变更/移除、最后管理员保护和审计 | security member Router/Service/Repo/migration | I1-08/I1-10, I2-01 | 否 | 权限即时生效；不能移除最后 admin；所有变更审计 | 越权、自锁、并发成员变更 | 管理员锁死/权限缓存 | P0 | XL | 未开始 |
| I2-14 | P19 | 在 `core.audit_events` 上实现 scoped list/facets/detail、脱敏和稳定游标 | audit read Router/Service/Repository/index migration | I1-08, I2-01/02 | 否 | scope/field allowlist/retention 规则生效；无 secret/PII 泄漏 | 分页稳定性+脱敏+越权+大表 explain | 审计泄密/索引膨胀 | P0 | L | 未开始 |

## 6. 阶段 3：聚合接口与 Worker 能力

| 任务编号 | 所属页面 | 任务描述 | 修改范围 | 前置依赖 | 是否需要我确认 | 验收标准 | 建议测试 | 风险 | 优先级 | 工作量 | 完成状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| I3-01 | P01 | 实现 dashboard activity：按正式 unique/terminal 口径聚合上传桶，支持 scope/range/timezone/as_of | dashboard Repository/Service/Router | I1-04, I2-01/02/04, I1-09 | 否 | 手算 fixtures 与 SQL 一致；边界/DST/重传不重复；无跨 scope 数据 | Repository 单测+Postgres integration+EXPLAIN | 大范围 group-by | P0 | L | 未开始 |
| I3-02 | P01 | 实现 dashboard snapshot：storage、episode funnel、work sections，缺源时返回 section status 而非伪值 | dashboard Repository/Service/Router | I1-04/I1-09, I2-05/08/09/10 | 否 | 每字段有事实源；四段同一 `as_of`；部分失败可辨认 | 公式/一致性/部分失败/权限 tests | 跨域一致性和缺失事实 | P0 | XL | 未开始 |
| I3-03 | P01 | 实现 dashboard coverage：按版本化 collection plan/robot group/task denominator 计算矩阵 | dashboard Repository/Service/Router | I1-04, I2-11, coverage plan model | 否 | denominator 可追溯版本；未计划组合不入分母；0 denominator 有定义 | 手算矩阵+scope+大矩阵性能 | 分母漂移/高基数 | P0 | L | 未开始 |
| I3-04 | P01 | 实现 dashboard pending-items：真实 source catalog、稳定 priority/sort/cursor/deep-link 和去重 | dashboard Repository/Service/Router | I1-04, I2-04/06/07/08/09/10 | 否 | 只返回已实现 source；source id 唯一；分页不重不漏；动作受权限控制 | union/cursor/权限/源失败 tests | 多源分页和过期待办 | P0 | XL | 未开始 |
| I3-05 | P01/P12 | 建立可选 inventory/dashboard rollup workflow，仅在在线查询未达预算时启用；支持回放/重算 | migration, Outbox, Temporal workflow/activity | I3-01～04 性能基线, TD-06 | 否 | feature flag 可切换；结果与源查询一致；lag/重算/补偿可观测 | replay+幂等+故障注入+差异对账 | 双写漂移和陈旧缓存 | P1 | XL | 未开始 |
| I3-06 | P11 | 实现 cleaning preview/apply/commit/compensation workflow；commit 原子生成新 revision | cleaning/preview/Lance Temporal workflow/activity | I2-09, PD-17 | 否 | activity 幂等；失败不改正式版本或能补偿；workflow replay 通过 | Temporal replay+故障注入+端到端数据校验 | 不可恢复半提交 | P1 | XL | 未开始 |
| I3-07 | P13 | 实现 lifecycle simulate/approve/execute/restore workflow、批次上限、legal hold 和 execution ledger | lifecycle domain, Temporal, object adapter | I1-07/I1-10/I2-10, PD-19 | 否 | dry-run 可复现；批准绑定 simulation revision；hold 永不删除；失败可恢复/重试 | 沙箱对象存储+误删保护+replay | 不可逆删除/成本 | P0 | XL | 未开始 |
| I3-08 | P14/P16/P17 | 实现机器人资产、标定和 Schema 的解析/格式/兼容校验与发布 workflow | domain workflows/activities, asset adapters | I2-11/I2-12, PD-20～22 | 否 | 不可信输入隔离；发布仅在 checks 通过后；版本不可变；replay 通过 | 恶意文件+compat corpus+Temporal replay | 供应链/解析器漏洞 | P1 | XL | 未开始 |

## 7. 阶段 4：前端切换真实 API

| 任务编号 | 所属页面 | 任务描述 | 修改范围 | 前置依赖 | 是否需要我确认 | 验收标准 | 建议测试 | 风险 | 优先级 | 工作量 | 完成状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| I4-01 | 全局/P18 | 在 PlatformShell 接通真实 session/scope/capability loader、token renewal 和 scope switch | frontend shell/store/router/auth | I2-01, I1-11 | 否 | off 模式可选择真实 scope；P02–P19 不再因 loader 缺失失败；撤权后立即阻断 | unit+component+real API E2E | 认证循环/权限陈旧 | P0 | XL | 未开始 |
| I4-02 | 全局 | 让 HTTP client 原生解析 RFC 9457，统一 401/403/409/422/partial 和 request id 展示 | frontend shared/api/error UI | I2-02, I1-11 | 否 | 错误不退回 fixture；request id 可复制；details 安全展示 | client unit+component negative cases | 错误信息暴露 | P0 | M | 未开始 |
| I4-03 | P01 | 移除编译期 Mock-only dashboard gate，使用真实 `dashboard.read` 与四个正式 client；保留 empty/partial/stale/403 | P01 page/features/dashboard | I3-01～04, I4-01/02 | 否 | off 模式命中四条真实路径；无 MSW；各 section 状态准确；刷新保持 scope/range | component+E2E+network assertion | 把 partial 显示成 0 | P0 | L | 未开始 |
| I4-04 | P02 | 将 source 表/详情/表单接入真实 CRUD/connection test，secret 不回显 | P02/features/ingest | I2-03, I4-01/02 | 否 | off 模式完成 create/test/disable；无明文 secret；403/SSRF 错误可理解 | form/component+E2E | 凭据泄漏 | P1 | L | 未开始 |
| I4-05 | P03/P04 | 用正式 ingest types 替换 Mock contract，接入列表/create/detail/objects/events/commands | P03/P04/features/ingest | I2-04, I4-01/02 | 否 | 真实创建到状态更新可观察；游标/筛选/重试/冲突正确；无草案路径 | component+real MinIO E2E | 轮询压力/状态映射 | P0 | XL | 未开始 |
| I4-06 | P05/P06 | 接入 dataset 列表/创建、详情/version/episode/source/capacity 与真实预览授权 | P05/P06/features/datasets/viewer | I2-05, I4-01/02 | 否 | scope/filter/cursor/empty 正确；viewer 不绕过授权 | component+E2E+large list | 大列表和预览越权 | P0 | XL | 未开始 |
| I4-07 | P07 | 接入 version checks/inventory/diff/approve/return，处理 ETag/409 和异步 job | P07/features/datasets | I2-06, I4-01/02 | 否 | 重复提交不双发；冲突提示刷新；审批后状态来自服务端 | component+E2E+concurrency | 误批准 | P0 | L | 未开始 |
| I4-08 | P08 | 用正式 annotation 合同替换 scoped/colon Mock paths，接入任务租约和复核 | P08/features/annotation/viewer | I2-07, I4-01/02 | 否 | 列表/detail/annotate/review 可用；租约失效和403明确 | component+E2E+two-user conflict | 丢失更新 | P0 | L | 未开始 |
| I4-09 | P09–P11 | 接入 issue/draft/operation/preview/commit workflow，显示 revision conflict 和补偿状态 | P09-P11/features/cleaning/viewer | I2-08/09, I3-06, I4-01/02 | 否 | 从 issue 建 draft 到新 revision 全链路；失败不伪装成功 | component+E2E+fault injection | 数据安全关键 | P1 | XL | 未开始 |
| I4-10 | P12/P13 | 接入 storage inventory 和 lifecycle simulation/approval/execute/restore；按 PD-18 决定删除/隔离 cost 残留或另行接入；危险操作二次确认 | P12/P13/features/storage/lifecycle | I2-10, I3-07, I4-01/02 | 是：PD-18 和危险操作文案/确认流程 | 计量时点明确；V1 不出现未批准 cost；simulation revision 绑定执行；hold/403/失败可见 | component+E2E+safe sandbox | 误删、范围漂移和错误成本 | P0 | XL | 未开始 |
| I4-11 | P14–P17 | 接入 robot/model/component/calibration/schema CRUD、上传校验和发布 job | P14-P17/features/robotics | I2-11/12, I3-08, I4-01/02 | 否 | 版本/有效期/compat checks 显示真实状态；不允许客户端绕过发布门禁 | component+E2E+malformed asset | 管理面误配置 | P1 | XL | 未开始 |
| I4-12 | P18 | 接入成员/binding/角色变更，显示最后管理员保护和权限生效 | P18/features/access | I2-13, I4-01/02 | 否 | 两用户 E2E 能观察 grant/revoke；不能自锁或移除最后 admin | component+multi-user E2E | 全站权限风险 | P0 | L | 未开始 |
| I4-13 | P19 | 接入审计 list/facets/detail/游标与受控导出状态，严格脱敏 | P19/features/audit | I2-14, I4-01/02 | 否 | 真实变更可查询；跨 scope/敏感字段不可见；大数据分页稳定 | component+E2E+security | 审计泄密 | P0 | L | 未开始 |

## 8. 阶段 5：测试、性能与安全

| 任务编号 | 所属页面 | 任务描述 | 修改范围 | 前置依赖 | 是否需要我确认 | 验收标准 | 建议测试 | 风险 | 优先级 | 工作量 | 完成状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| I5-01 | 全局/P01–P19 | 建立当前源码的单元/contract 测试基线，覆盖公式、状态机、cursor、scope、错误和降级 | backend/frontend tests, package scripts | I2/I3/I4 对应能力 | 否 | 每个正式 path 至少正向/空/无权限/validation；每个命令含重复/冲突；CI 可运行 | pytest/vitest/contract suite | 只测 Mock 的虚假覆盖率 | P0 | XL | 未开始 |
| I5-02 | 后端全局 | 将当前 4 个 skipped 外部 Postgres/MinIO 场景接入可重复 CI service，保留失败证据 | test compose/CI/integration tests | 阶段2/3 | 否 | 外部适配器用真实依赖执行，不再因 env 缺失 skip；失败不自动 xfail | Postgres/MinIO/Temporal integration | CI 不稳定/资源成本 | P0 | L | 未开始 |
| I5-03 | P03/P04/P06/P08/P11/P13/P14/P16/P17 | 对 Worker 做 replay、幂等重试、超时、取消、补偿和版本兼容测试 | Temporal test environment/system tests | I3-05～08 | 否 | 所有 workflow replay 通过；activity 重试不重复副作用；升级兼容 | replay/fault injection/worker restart | 工作流升级不可回放 | P0 | XL | 未开始 |
| I5-04 | P01/P18/P19 | 建立 `VITE_MOCK_MODE=off` 的 auth→scope→dashboard→audit E2E；断言无 MSW、越权失败 | Playwright config/fixtures/real stack | I4-01/03/12/13 | 否 | CI 可执行；网络只到真实 gateway；覆盖 partial/stale/403/revoke | Playwright+network capture | 测试账号/数据隔离 | P0 | L | 未开始 |
| I5-05 | P02–P08 | 建立 source→upload→dataset→version→annotation 真实主链 E2E | E2E seed/Playwright/system fixtures | I4-04～08 | 否 | 创建可追踪到真实后端/Worker/DB；轮询有上限；失败可诊断 | Playwright+system assertions | 运行时间和不稳定 | P0 | XL | 未开始 |
| I5-06 | P09–P17 | 建立 cleaning、storage lifecycle、robot/calibration/schema 的真实危险操作沙箱 E2E | E2E sandbox/Playwright | I4-09～11 | 否 | commit/restore/compat failure 在隔离数据上可重复；不触及共享真实数据 | Playwright+object snapshot diff | 误操作测试数据 | P1 | XL | 未开始 |
| I5-07 | P01/P03/P05/P06/P12/P19 | 对最大时间窗、对象/episode/audit 大表和 pending union 做容量/EXPLAIN/索引验收 | load scripts/capacity reports | I3-01～05/I2-04/05/10/14 | 是：确认 SLO/容量目标 | p95/p99、吞吐、资源、最大结果达到批准阈值；无全表/全桶无界扫描 | k6/Locust/SQL EXPLAIN/5TB 等价模型 | 环境不代表生产 | P0 | XL | 未开始 |
| I5-08 | 全局 | 执行 capability/scope/IDOR、SSRF、secret/PII、审计脱敏、危险命令 CSRF/重放安全测试 | security tests/threat model | 阶段2～4 | 否 | 每个对象 id 在错 scope 返回统一拒绝；敏感值不进响应/日志；重复命令安全 | automated negative matrix+manual review | 安全漏测 | P0 | XL | 未开始 |
| I5-09 | 全局 | 补齐 route/query/Worker/rollup 指标、结构化日志、trace/request id 和告警；控制标签基数 | observability code/dashboards/runbooks | TD-12, 阶段2/3 | 否 | pilot 能捕获指标/日志/trace；不含 user/project 高基数或敏感值；告警可演练 | synthetic failures+dashboard checks | 观测成本/敏感日志 | P0 | L | 未开始 |
| I5-10 | 前端全局 | 恢复当前源码 lint/Playwright 门禁并处理大 chunk；不引用已移除历史测试作为证据 | eslint/playwright/vite config, CI | I4-01～13 | 否 | lint 0 error；E2E 可发现；关键路由 lazy split；构建无未批准超限 chunk | eslint/typecheck/vitest/build/bundle budget | 配置工作掩盖功能缺口 | P1 | L | 未开始 |

## 9. 阶段 6：部署和生产验收

| 任务编号 | 所属页面 | 任务描述 | 修改范围 | 前置依赖 | 是否需要我确认 | 验收标准 | 建议测试 | 风险 | 优先级 | 工作量 | 完成状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| I6-01 | 全局 | 增加明确 real-api 本地/集成启动 profile、Worker health、迁移 job 和环境变量文档；默认模式不可含糊 | compose/Docker/gateway/README | I4/I5 基线 | 否 | 一条文档命令启动 off 模式；health 等待真实依赖；browser Mock 单独标识 | compose config+smoke+fresh volume | 默认仍误用 Mock | P0 | M | 未开始 |
| I6-02 | 后端全局 | 在生产副本上 dry-run 全部新迁移、backfill、索引和 forward/backward-compatible rollout | migrations/deploy runbook | I1-09/10, 阶段2/3 | 否 | 空库和生产规模副本均成功；耗时/锁/磁盘记录；失败恢复路径演练 | migration rehearsal+checksum | 长锁/数据不可回退 | P0 | XL | 未开始 |
| I6-03 | 全局/P01–P19 | 用当前 chart 执行认证、scope、完整数据管线、页面真实 API、指标/日志/告警试点 | Helm/pilot evidence | I5-01～10, I6-01/02 | 否 | 验收矩阵不含本阶段 FAIL/PARTIAL/NOT RUN；证据含版本/时间/命令/结果 | pilot script+manual UX acceptance | 环境/身份依赖 | P0 | XL | 未开始 |
| I6-04 | 后端/P01/P12 | 重跑 5 TB/day 等价容量和恢复测试，关闭 BE12-008 XFAIL 或记录批准例外 | capacity harness/report | I5-07, production-like env | 是：容量/SLO 例外需批准 | 吞吐、p95、Worker lag、DB/object/Lance 一致性达到阈值；无未解释数据丢失 | sustained load+network recovery | 成本和长时运行 | P0 | XL | 未开始 |
| I6-05 | 全局 | 对当前镜像/chart/迁移做升级、回滚、Worker workflow version 和前端/API 合同兼容演练 | deploy scripts/evidence | I6-02/03 | 否 | upgrade/rollback 不再 NOT RUN；旧/新实例滚动期间请求和 workflow 可用；数据不降级 | blue/green or rolling rehearsal | migration 不可逆/回放失败 | P0 | XL | 未开始 |
| I6-06 | 全局 | 完成 README、运维 runbook、SLO、告警、备份恢复、DR、故障处置和正式验收矩阵 | README/docs/deploy evidence | I6-03～05 | 是：业务验收签字 | 文档命令可由独立人员复现；RPO/RTO/owner/escalation 明确；产品验收签字 | tabletop+restore drill+doc walkthrough | 纸面流程未演练 | P0 | L | 未开始 |

## 10. 里程碑与退出条件

| 里程碑 | 最低退出条件 | 可开始的后续工作 |
|---|---|---|
| M0 合同可冻结 | I0-01～10 完成，PD 决策有批准记录 | 阶段 1 |
| M1 可实现 | 正式 OpenAPI 无 drift；数据模型/迁移/权限/error ADR 通过 | 阶段 2，可并行开发不依赖域 |
| M2 基础页面后端可联调 | auth bootstrap/error 可用；目标页面 Router→DB 集成测试通过 | 对应阶段 4 前端接入 |
| M3 P01 可切换 | I3-01～04 通过公式、越权、部分失败和性能基线 | I4-03、I5-04 |
| M4 整站真实 API 候选 | P01–P19 off 模式关键操作均有真实 E2E，MSW 未启动 | 阶段 5 完整门禁 |
| M5 试点候选 | 单元/外部集成/E2E/安全/性能通过；无未批准 XFAIL | 阶段 6 pilot |
| M6 生产候选 | 当前 chart pilot、容量、升级/回滚、备份恢复和产品验收均有证据 | 发布决策 |

## 11. 推荐启动批次

1. **先做阶段 0 的 PD-01～PD-10**，同时技术团队完成 TD-01～TD-05 的 ADR 草案。
2. 第一条代码关键路径选 **I1-01/I1-02/I1-03 → I2-01/I2-02 → I1-04 → I3-01～04 → I4-03 → I5-04**。这会先消除全局真实模式阻断，并让 P01 从 Mock-only 变成可验证的真实读链路。
3. 第二批复用现有后端资产，优先 P03/P04、P08、P05/P06/P07，而不是先做当前完全没有模型的 P09–P18。
4. P13 生命周期、P11 清洗提交属于不可逆高风险能力，必须在状态机、幂等、补偿和沙箱测试完成后再启用。

## 12. 2026-08-17 前端效果评审回写

本计划的前述任务基于首轮审计，保留作为历史基线。用户随后确认了会改变正式 API 和数据模型的页面规则，因此以下内容覆盖前文冲突项：

- 认证不是 `PENDING` 账户审批：注册即建立空账户；新增的是项目加入申请和权限申请合同。
- P20 纳入正式范围，但不设计 assignment、start/end、pause/resume；只设计创建、编辑、关闭和真实数据包进度。
- P03 必须有上传操作合同，Manifest 负责相机/Topic 自动发现；上传记录只是同一功能页的查询 Tab。
- P08/P11 合并为一个数据标注域；正式合同需要多级 Tag、区间、属性、Schema 版本、修订和 Tag 审核，不再新增独立 cleaning 页面合同。
- P12 容量按 Raw/标注完成/待标注/问题数据聚合；P13 不暴露 simulate/impact API 给前端。
- 全局 Shell 和前端不再出现独立上传记录或数据清洗导航。

具体替换任务、依赖和验收标准见 `plan/P01-P20-POST-EFFECT-REVIEW-IMPLEMENTATION-PLAN.md`。在该文件 OPEN-01～10 完成前，不应执行本计划中与账户审批、P20 分派/有效期、独立 cleaning domain、冷热层或生命周期模拟有关的旧任务描述。
