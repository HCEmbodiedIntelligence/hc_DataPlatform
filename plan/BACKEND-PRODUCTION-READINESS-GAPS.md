# 后端生产就绪缺口

> 审计日期：2026-08-17  
> 结论：当前后端是**有较完整底层数据管线和回归测试的开发/集成候选**，不是 P01–P19 完整后端，也不是生产发布候选。`backend/tests/system/ACCEPTANCE-MATRIX.md:38-39` 明确规定任何 XFAIL、PARTIAL、NOT RUN、NOT PASSED 或 FAIL 都会阻止 BE-12 完成；当前仍存在这些状态。

## 1. 当前就绪度

| 层级 | 状态 | 已有证据 | 主要阻断 |
|---|---|---|---|
| 本地后端开发 | 可用 | API/Worker/DB/MinIO/Temporal Compose 可启动；283 passed；OpenAPI check 通过 | 部分外部依赖测试 skip；直启与 Compose migration enforcement 默认值存在差异 |
| 本地整站开发 | 部分可用 | Browser Mock 下 P01–P19 UI 可演示；网关可代理 `/api` | real mode 缺 auth bootstrap，页面 API 未接入；默认 Compose 前端仍是 browser Mock |
| 后端集成测试 | 部分可用 | reference/in-process pipeline、Temporal、Postgres、MinIO 的既有测试证据 | 本次完整回归仍有 4 skipped；当前 deployed production Worker 全流水线未重跑 |
| P01–P19 前后端集成 | 不可用 | 无 | 正式页面 API 大量不存在；相似底层合同不兼容；无 real-api E2E |
| 试点 | 未达到 | 历史 2026-08-14 kind 证据；当前 chart lint/template 和镜像 smoke 通过 | 当前 strict-readiness chart 的完整 pilot、metrics/log capture、upgrade/rollback 未重跑 |
| 生产 | 未达到 | 有部署资产、健康检查、PDB/rolling strategy、可观测资产 | 5 TB/day 未通过、容量 XFAIL、当前升级/回滚 NOT RUN、页面覆盖/权限/安全/备份恢复未闭合 |

## 2. 实际测试与验收状态

### 2.1 本次可重复运行结果

| 验证 | 当前结果 | 说明 |
|---|---|---|
| 后端完整 pytest | **283 passed、4 skipped、1 xfailed、1 warning，52.03s** | 当前源码结果；替代验收矩阵中较旧的 262/6 统计，但不改变容量和试点状态 |
| OpenAPI drift | PASS | runtime `app.openapi()` 与生成文件一致，共 44 paths；没有 dashboard/P01–P19 页面聚合路径 |
| 前端 typecheck/build | PASS | 只证明可编译/构建；build 有 >500 KB chunk warning |
| 前端当前单测 | **7 files、28 tests passed** | 含新增 cleaning Mock scope tests；仍无页面真实 API E2E，历史已归档测试不计当前证据 |
| ESLint | FAIL：无 config | 当前没有 lint release gate |
| Playwright discovery | FAIL：No tests found | 当前没有可执行 real-api E2E suite |
| Compose config | PASS | 语法和插值可解析；不证明生产依赖和真实页面 |

### 2.2 XFAIL / SKIPPED

| 类型 | 项目 | 当前原因/事实 | 关闭条件 |
|---|---|---|---|
| XFAIL | `backend/tests/load/test_capacity_controls.py` / BE12-008 | 5 TB/day + 30% headroom 未通过；完整 E2E 容量不可运行 | 在生产等价环境通过持续全流水线容量和恢复门禁，或由发布负责人批准有期限的明确例外 |
| SKIPPED | `backend/tests/annotation/test_annotation_postgres.py` | 本次运行缺该测试要求的外部 PostgreSQL 环境变量/fixture | 纳入 CI service，应用真实迁移后执行，不再默认 skip |
| SKIPPED | `backend/tests/ingest/test_minio_integration.py` | 本次运行缺该测试要求的 MinIO 环境 | 启动隔离 MinIO，执行 multipart/校验/清理 |
| SKIPPED | `backend/tests/security/test_postgres_integration.py` | 本次运行缺外部 PostgreSQL 安全集成环境 | 在真实 PostgreSQL 验证 JWT scope/RLS/idempotency/audit |
| SKIPPED | `backend/tests/system/test_minio_network_recovery.py` | 本次完整回归没有启用可暂停容器的外部 MinIO 场景 | 在隔离 CI runner 运行 pause/recover 并保留证据 |

### 2.3 当前验收矩阵中的 PARTIAL / NOT RUN / NOT PASSED

| 验收项 | 状态 | 直接证据 | 尚缺什么 |
|---|---|---|---|
| Production Worker 执行全部 pipeline activities | PARTIAL | `backend/tests/system/ACCEPTANCE-MATRIX.md:21` | 当前源码/镜像在 pilot 依赖上执行完整 workflow，不只是 14 ports 可构造 |
| upload/workflow/QC/Lance/transcode/export metrics | PARTIAL | `backend/tests/system/ACCEPTANCE-MATRIX.md:26` | 当前 pilot OTLP capture、指标值与 labels 验证 |
| runtime logs locator/no token | PARTIAL | `backend/tests/system/ACCEPTANCE-MATRIX.md:27` | 当前 pilot 日志采集和 `verify_logs.py` 证据 |
| 当前 Kubernetes upgrade/rollback | PARTIAL / current NOT RUN | `backend/tests/system/ACCEPTANCE-MATRIX.md:30`; `deploy/evidence/VALIDATION.md:12-13,49-53` | 对当前 strict-readiness chart、当前镜像和新迁移重跑升级/回滚 |
| 20 GiB + 50 concurrent sessions | PARTIAL | `backend/tests/system/ACCEPTANCE-MATRIX.md:31`; capacity report `:57-67` | 当前 deployed gateway+JWT+API+DB 成功创建 50/50；旧 pilot 只测到 401 |
| 5 TB/day + 30% headroom | NOT PASSED | `backend/tests/system/ACCEPTANCE-MATRIX.md:32`; `backend/tests/load/CAPACITY-REPORT.md:1-11,41-51,69-90` | 全流水线 sustained p50/p95/p99、饱和点、retry/lag；MinIO stage 先达到 75.23 MB/s |
| same-day cached API image ready | PARTIAL | `deploy/evidence/VALIDATION.md:17,23-25` | 在完整外部依赖下 ready=200；隔离 smoke 的 503 是预期但不是 pilot |

## 3. 生产阻断缺口清单

| 缺口编号 | 领域 | 当前事实 | 生产风险 | 推荐关闭动作 | 验收证据 | 优先级 | 状态 |
|---|---|---|---|---|---|---|---|
| PR-01 | P01–P19 API 覆盖 | runtime OpenAPI 44 paths，无 dashboard；多数页面 path 不存在，P03–P08 也只存在不兼容底层能力 | 前端无法在真实模式工作，功能验收失真 | 按实施计划 I1～I5 建正式合同、页面 Router/Service/Repo、真实前端和 E2E | off 模式 network trace + provider/consumer contract + E2E | P0 | OPEN |
| PR-02 | Auth/bootstrap | 前端真实模式没有 principal/token/scope/capability loader；P02–P19 被 guard 阻断 | 全站不可用或临时绕过产生越权 | 实现服务端权威 bootstrap、scope binding、撤权和 capability mapping | 多角色/多 scope/撤权/IDOR 集成和 E2E | P0 | OPEN |
| PR-03 | 错误合同 | 前端期待嵌套 error；后端返回扁平 RFC 9457 | request id/code/details 丢失，用户无法诊断，降级错误 | 统一 RFC 9457 扩展和前端解析 | 400～5xx contract + UI negative cases | P0 | OPEN |
| PR-04 | OpenAPI/类型供应链 | 前端类型来自仓库外草案且 runtime client 不消费 | 合同漂移、同名假兼容、发布不可复现 | runtime schema 为唯一源，生成/消费/CI drift 门禁 | clean regenerate 无 diff，故意 drift 会失败 | P0 | OPEN |
| PR-05 | 数据模型覆盖 | 无 canonical project/member/source/issue/draft/inventory/lifecycle/robot/calibration/registry 等表 | 聚合无法计算、权限无法持久化、只能继续 Mock | 产品确认后按域建 schema/ledger/index/backfill/reconciliation | migration rehearsal + data integrity/constraint tests | P0 | OPEN |
| PR-06 | Worker 能力与 pilot | 现有 7 workflows/9 activities 覆盖主数据管线；无 dashboard/lifecycle/asset/calibration/schema；当前 production pilot 未重跑 | 长任务不可恢复、部署配置错误只在生产暴露 | 增加必要 workflow，做 replay/idempotency/compensation；当前 pilot 全执行 | replay/fault injection + pilot workflow history | P0 | PARTIAL |
| PR-07 | 容量 | MinIO upload P50 56.48 MB/s，低于 75.23 MB/s；无完整 E2E sustained 数据 | 无法承担声明容量，积压/超时/数据延迟 | 找出网络/并发/chunk/bucket/Worker/Lance 瓶颈，生产等价持续压测 | 5 TB/day+30% 全链路报告，BE12-008 关闭 | P0 | XFAIL/NOT PASSED |
| PR-08 | 外部集成测试 | 当前完整回归 4 skips | adapter、网络恢复、RLS 在发布分支未被持续验证 | CI 提供 Postgres/MinIO/Temporal 隔离 services，不允许 release job skip | release suite 0 unexpected skip | P0 | OPEN |
| PR-09 | 前端质量门禁 | 无 ESLint config、无当前 Playwright suite；历史 tests 已移除 | 回归/无障碍/真实合同错误无法在 CI 阻断 | 恢复 lint、real-api Playwright、网络断言和 trace artifacts | lint/typecheck/unit/E2E 全部为 required checks | P0 | OPEN |
| PR-10 | 部署升级/回滚 | 当前 chart 只 lint/template；升级/回滚沿用旧 chart 证据 | readiness、迁移或 workflow 版本导致滚动失败 | 对当前镜像/chart/DB migration/workflow version 重跑 | 当前版本 upgrade/rollback + proof hashes + request continuity | P0 | NOT RUN |
| PR-11 | 可观测性 | source emitter/assets 通过；当前 pilot metrics/log capture PARTIAL | 故障不能定位，SLO 无法证明 | 采集 route/query/result/Worker lag/attempt/rollup age；演练告警 | OTLP/log/trace capture + runbook drill | P0 | PARTIAL |
| PR-12 | 分页/筛选/排序/时间 | 底层部分 API 有 cursor；页面草案各自定义，P01 硬编码时区假设 | 重复/漏项、DST 错误、无界查询 | 统一 cursor tuple、allowlist sort/filter、UTC/IANA/半开区间和最大窗口 | 属性测试+DST+并发插入+large list | P0 | OPEN |
| PR-13 | 幂等/并发 | 后端已有 idempotency 基础，但新页面命令和草案合同未统一 ETag/revision | 重试产生双副作用、审批/编辑丢失更新 | 所有命令 Idempotency-Key + revision/ETag + 409；Worker activity 幂等 | duplicate/concurrent/fault tests | P0 | PARTIAL |
| PR-14 | 日志/隐私/审计读取 | 有审计写入表和部分 no-token source checks；无 read redaction/retention/export 规则 | PII/secret 泄漏或审计不可用 | event allowlist、脱敏、scope、retention/partition、受控导出 | PII corpus/IDOR/retention/export tests | P0 | PARTIAL |
| PR-15 | 健康与启动 | Compose 主要服务 healthy，但 worker 无 Compose healthcheck；直启 `.env.example` migration enforcement 与 Compose 强制值不同 | worker 假活、schema 未就绪、环境行为不一致 | 增加 worker dependency/composition health，统一安全默认值和启动文档 | fresh-volume compose smoke + dependency loss/recovery | P1 | OPEN |
| PR-16 | 网关/流控 | 网关可代理 `/api`；未找到页面级请求限流、查询预算或大导出背压证据 | 聚合/审计/导出压垮 API/DB | 按 route 设置 timeout/body/rate/query budget；大任务异步化 | burst/slow-client/large-query load tests | P1 | OPEN |
| PR-17 | 前端性能 | build 通过但 viewer/app/analytics chunk 超 500 KB | 首屏慢、弱网试点不可用 | route lazy split、重依赖按需加载、设 bundle budget | Lighthouse/真实导航/CI bundle budget | P1 | OPEN |
| PR-18 | 备份恢复/DR | 仓库中未找到当前 Postgres/object/Lance/Temporal 生产备份恢复演练的可验收证据 | 数据永久丢失，RPO/RTO 未知 | 明确 owner/RPO/RTO，做一致性 snapshot/restore 和灾备演练 | restore drill + cross-store consistency hashes | P0 | NEEDS TECH VALIDATION |
| PR-19 | 数据迁移/回填 | 新页面域尚无表；现有生产规模/历史数据未知 | 长锁、回填不完整、版本回滚失败 | expand-migrate-contract、在线索引、可重入 backfill、对账和回滚边界 | production-copy rehearsal + metrics/checksum | P0 | OPEN |
| PR-20 | 发布治理 | 当前验收矩阵包含旧统计与保留历史证据，容易被误读为当前 release pass | 错误批准生产 | 每次 release 记录 commit/image/chart/schema/command/time；历史证据明确失效范围 | 独立复核可重现且矩阵无未批准状态 | P0 | OPEN |

## 4. 性能与容量专项

当前容量报告的目标是 5,000,000,000,000 bytes/day，带 30% headroom，要求持续 75,231,481.48 bytes/s（`backend/tests/load/CAPACITY-REPORT.md:6-11`）。已测 MinIO multipart upload P50 为 56.48 MB/s，约低 25%，而且尚未叠加 API、网络、Temporal、Lance、transcode 和 export 成本（同文件 `:41-51,69-74`）。因此：

- 不得用本地 SHA 1.5 GB/s 或 50 个 in-process session 作为全链路容量结论；
- 先分别测 API control plane、MinIO multipart、Worker queue/lag、QC/align、Lance commit、preview/transcode、export；
- 再做生产等价 sustained E2E，报告 p50/p95/p99、saturation、retry、queue lag、CPU/memory/network/disk 和跨存储一致性；
- P01/P12/P19 的聚合/库存/审计大表必须加入同一容量模型，避免上线后反向拖垮摄取主链；
- 任何 rollup/cache 只能在正确性对账和失效/重算测试通过后启用。

## 5. 日志、指标和安全

### 已有基础

- 后端已有结构化错误、JWT/scope/idempotency/audit/outbox、健康路由和 OTel/指标相关实现与源级测试。
- 观测 dashboard/alerts/runbook 资产测试通过（验收矩阵 `:28`）。
- 当前 chart 具备分离 API/Worker 镜像、资源、readiness、PDB 和滚动策略的静态验证（`deploy/evidence/VALIDATION.md:8-16`）。

### 尚需生产证据

- 当前 pilot 的真实 metrics、logs、traces 和告警演练；source emitter 测试不足以证明 collector/exporter/labels 正确。
- 日志必须含 request/project/resource/workflow locator，但禁止 token、signed URL、secret、PII；project/user 不应作为高基数 metric label。
- 对 P01 聚合增加 query latency/result rows/section status/as_of age；对 Worker 增加 schedule-to-start/activity attempt/lag/compensation；对 DB 增加 slow query/connection/lock；对对象存储增加 multipart retry/throughput/error。
- 验证 IDOR、跨 project/region、capability downgrade、撤权、最后管理员、SSRF、恶意资产解析、审计脱敏和危险 lifecycle 命令。
- 如果生产经同源 gateway，CORS 可保持最小；若另域部署，必须以明确 allowlist 配置并测试。当前不得假设任意来源可访问。

## 6. 数据库、升级与回滚

- 现有迁移覆盖 ingest/workflow/quality/Lance/annotation/publishing/security 主链，但不覆盖大多数页面管理域。
- 新迁移应采用 expand→backfill/reconcile→switch reads/writes→contract，避免一次性长锁；索引尽量在线创建并记录生产副本耗时。
- 已发布 version/audit/operation/ledger 采用 append-only；修正用新 revision/event，避免 UPDATE 抹掉历史。
- 所有 backfill 必须可重入、可暂停、有 progress/lag/error 指标，并按 project/region 分批。
- 回滚要区分应用回滚和 schema/data 回滚。对不可逆数据转换不承诺自动 down migration，而应保持旧应用兼容窗口和恢复快照。
- Temporal workflow 代码升级必须保留 replay 兼容或显式 version marker；仅验证 Python import 不够。
- 当前 chart 的 upgrade/rollback 仍 NOT RUN，历史 proof hash 只能证明旧 chart，不可直接继承给新迁移和新 workflow。

## 7. 关闭生产阻断的推荐顺序

1. PR-02/03/04：auth、错误和唯一 OpenAPI 事实源。
2. PR-01/05：按页面优先级冻结合同和数据模型；先 P01 + 可复用现有后端的 P03/P04/P08/P05–P07。
3. PR-08/09：让外部集成和 real-api E2E 成为当前源码的 required gates。
4. PR-06/12/13/14：补 Worker、查询规范、幂等并发和审计/隐私。
5. PR-07/11/16/17：容量、可观测、流控和前端性能。
6. PR-18/19/10/20：备份恢复、迁移演练、当前 chart 升级回滚和版本化发布证据。

只有在验收矩阵所有 release-blocking 行为 PASS，且产品批准的 P01–P19 范围都有 `VITE_MOCK_MODE=off` 的真实 E2E 后，才能重新评估“生产发布可用”。

## 8. 2026-08-17 效果图评审新增生产缺口

用户已确认的平台规则改变了公网暴露面和后端合同。以下缺口追加到 PR-01～20，不替换原结论：

| 缺口编号 | 领域 | 新确认事实 | 生产风险 | 关闭动作与证据 | 优先级 | 状态 |
|---|---|---|---|---|---|---|
| PR-21 | 公网注册 | 用户名/密码直接注册，无管理员、邮箱或手机号审批/验证 | 批量注册、撞库、账户恢复争议和不可验证身份 | 确认恢复流程；验证码/分层限流/渐进延迟/锁定；注册与登录滥用测试、告警和审计 | P0 | OPEN |
| PR-22 | P18 授权 | 空账户只能通过项目加入和权限申请获得业务 scope | 错误默认权限、跨项目 IDOR、撤权延迟 | membership/capability request 独立模型；Repository scope；两用户跨项目和撤权 E2E | P0 | OPEN |
| PR-23 | P20 | 无分派、时间窗、暂停/继续；数据包进度是唯一事实 | 旧草案字段/命令继续暴露，关闭与摄取竞态 | 正式 OpenAPI 负面断言；close 并发/幂等；无 assignment/effective/pause schema | P0 | OPEN |
| PR-24 | Manifest | 相机/Topic 由外部上传 Manifest 自动发现 | 恶意/超大 Manifest、路径穿越、解析耗尽、伪造来源 | 格式 allowlist、大小/深度/数量上限、隔离解析、路径规范化、签名/校验和、fuzz/恶意样本测试 | P0 | OPEN |
| PR-25 | 多级 Tag | 标注与清洗合并，新增多级 Tag/属性/Schema 审核 | 历史 cleaning 数据丢失、循环层级、Schema 演进破坏旧版本 | 版本化不可变 Schema、迁移/兼容 adapter、层级/冲突 property tests、历史审计对账 | P0 | OPEN |
| PR-26 | 生命周期 | 用户界面不提供影响模拟 | 危险策略缺少可见预演后直接造成大范围变更 | 保留服务端保护校验、最小权限、审批/确认、批次上限、execution ledger、恢复演练；UI 不伪装 simulate | P0 | OPEN |

生产可用判定范围同步扩展为 P01–P20，并必须包含空账户、项目加入/权限申请、P20 关闭、Manifest 恶意输入和 Tag Schema 迁移的真实验证证据。
