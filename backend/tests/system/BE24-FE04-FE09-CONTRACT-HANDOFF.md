# BE24 → FE04–FE09 Real API 合同交接

审计时点：2026-08-18。本文只描述 production-composed runtime 已挂载合同；Browser Mock、
效果图和 fragment 草案都不是前端 wire type 的事实源。

## 正式版本与复现

- 唯一 runtime 类型源：production-composed `create_app(...).openapi()`。
- runtime/formal：85 paths、95 HTTP operations；path、method、request/response schema ref、
  security、operationId 全部一致，`contract_issue_count=0`。
- Runtime OpenAPI SHA-256：
  `aefb7ddefd1122a0db460607acee4d6339b5002d21764f1f875d531e289ebfd9`。
- Fragment aggregate SHA-256：
  `352cee7b6d3cf8fc08209b81b48c5876edbce7a595ae98eb7033eee6de2eac0f`。
- 正式生成类型：`frontend/src/shared/api/generated/platform.ts`，SHA-256：
  `755894b5c58629cc7b1c2421c3462d30803de5a2237521fab6014f6080faf209`。
- 生成器使用仓库锁定的 `openapi-typescript 7.9.1`；禁止手写或从 Mock 推导 wire type。
- BE23 记录的 56 个 operationId drift 已在本审计时点闭合为 0。

复现命令：

```bash
cd backend
.venv/bin/python -m hc_data_platform.core.openapi --runtime --check
.venv/bin/python -m hc_data_platform.core.openapi --runtime --output /tmp/hc-runtime-openapi.yaml
cd ../frontend
pnpm gen:api --check
pnpm typecheck
```

生产 HTTP 默认关闭 `/docs`、`/redoc` 和 `/openapi.json`；这不禁用内部 schema。
上面的 CLI 直接从 app 对象生成，不依赖公开 HTTP 文档端点。

前端从 `components["schemas"]` 使用 `DashboardSnapshotResponse`、
`DashboardActivityResponse`、`DashboardCoverageResponse`、
`DashboardPendingItemsResponse`，从 `operations` 使用四个 `getDashboard*` operation。

## Dashboard 请求合同

四项均为 `GET`，要求 Bearer 身份、精确 `project_id + region_code` scope 和
`dashboard.read` capability：

| operation | 路径 | 分页参数 | 响应 section |
| --- | --- | --- | --- |
| `getDashboardSnapshot` | `/api/v1/projects/{project_id}/dashboard/snapshot` | 无 | `sections.signal_pipeline/episodes/work` |
| `getDashboardActivity` | `/api/v1/projects/{project_id}/dashboard/activity` | `cursor?`, `limit?` | `activity` |
| `getDashboardCoverage` | `/api/v1/projects/{project_id}/dashboard/coverage` | 无 | `coverage` |
| `getDashboardPendingItems` | `/api/v1/projects/{project_id}/dashboard/pending-items` | `cursor?`, `limit?` | `pending_items` |

所有请求必须显式发送：

- `region_code`：单一授权 region，不做跨 region 聚合；
- `from`：带 UTC offset 的 RFC 3339 闭区间起点；
- `to`：带 UTC offset 的 RFC 3339 开区间终点；
- `timezone`：IANA timezone，无服务端默认值；
- `limit`：仅 activity/pending-items，默认 50，范围 1–100；
- `cursor`：仅 activity/pending-items，绑定 principal、project、region、range、timezone、
  endpoint 和完整稳定排序元组，不能跨查询复用。

四类响应共有 `schema_version="1"`、`project_id`、`region_code`、`from`、`to`、
`timezone`、`as_of`。section 状态为 `READY | EMPTY | PARTIAL | STALE | ERROR | BLOCKED`，
并带 `as_of` 与结构化 `error`；`BLOCKED` 不是数值 0。分页 section 另有 `page_info`，
阻断态可为 `null`。

## BR01 已冻结的 P01 语义

- P01 没有 region storage、storage bytes 或 storage months；容量属于 P12。
- `signal_pipeline.stages` 唯一顺序为
  `COLLECTED → RECEIVED → AUTO_QC → ALIGNED_30_HZ → LANCE → ANNOTATION → REVIEW → PUBLISHED`；
  Cleaning 不是独立阶段。
- `signal_pipeline.published_region` 提供 `lineage_count`、`publication_count` 和
  `unresolved_history_count`；只有具有确定 rollout-publication-region 血缘的事实进入前两项。
- activity 是持久业务事件流，不是吞吐/字节 bucket。事件类型固定为
  `UPLOAD_COMMITTED | QC_COMPLETED | TAG_REVIEW_DECIDED | DATASET_PUBLISHED`，每项包含稳定
  event/source/dedup identity、事实时间、安全展示文本和受控 target。
- coverage 在 versioned collection plan、robot group、task catalog 和 denominator 完整前返回
  `BLOCKED`；前端不得显示伪造的 0 或百分比。
- pending 四来源固定为 `UPLOAD_FAILED | QC_ANOMALY | TAG_REVIEW_PENDING |
  PUBLICATION_PENDING`，响应还给出按 principal capability 交集后的
  `authorized_source_types`。深链只能使用服务端 allowlist 返回的相对 target。

共享 P01 adapter 已直接引用上述生成类型，focused Vitest 2 PASS；类型 drift 与全量
TypeScript typecheck 均退出 0。FE04–FE09 不得重新引入旧 storage 字段或 Mock wire interface。

## 错误与客户端约束

runtime 的非 2xx 响应为扁平 RFC 9457 `application/problem+json`，包含
`type/title/status/detail/code/request_id/retryable/details`（可选字段按 schema）。5xx detail
已固定脱敏。当前共享 `request<T>` 仍只解析旧式 `{error: {...}}` envelope，因此 FE04–FE09
接入真实网络前必须让它兼容正式 Problem Details，否则稳定 `code`、`detail` 和 `request_id`
会丢失。Real API 错误必须显示错误/阻断态，禁止静默回退 MSW 或 Browser Mock。

请求级已冻结错误码包括：

- `AUTHENTICATION_REQUIRED`, `PROJECT_SCOPE_DENIED`, `CAPABILITY_REQUIRED`；
- `REQUEST_VALIDATION_FAILED`, `DASHBOARD_PRINCIPAL_SCOPE_DENIED`；
- `DASHBOARD_TIMEZONE_OFFSET_REQUIRED`, `DASHBOARD_TIME_RANGE_INVALID`,
  `DASHBOARD_TIME_RANGE_TOO_LARGE`, `DASHBOARD_TIMEZONE_INVALID`；
- `DASHBOARD_PAGE_LIMIT_INVALID`, `INVALID_CURSOR`, `DASHBOARD_QUERY_RATE_LIMITED`；
- `INTERNAL_SERVER_ERROR`。

section 级事实缺失/来源状态必须按 wire 的 `status/error` 展示，不能转换为空数据。尚未确认的
产品数值包括 production query-admission 阈值与 freshness SLO；它们不能由前端猜测。

## BR02 自动启动与前端边界

OpenAPI 不存在公开 start-ingest 或 annotation-task create。生产链路是持久化 outbox → 正式
Temporal launcher → Worker activities → 自动 annotation dependency；BE22 issuer/provisioner、
decoder 和 cleanup 只属于 test profile。前端只能观察正式任务/工作流状态，不能自行补一个
启动接口。包含 `/` 的 workflow ID 放入 URL path segment 时必须整体 percent-encode。

BE24 已通过真实 gateway/API/Worker 使用隔离 scope 连续执行两条 BE22 主链；
`be22/be24-proof-a.json` 与 `be22/be24-proof-b.json` 均为 9/9 stages PASS，前后 cleanup 全
CLEAN。这只证明 API/Worker 链，不等于浏览器验收。

## FE04–FE09 / FE10 签收条件

当前完整浏览器主链状态仍为 `NOT RUN`。FE04–FE09 完成页面接线后，由 FE10 使用
`VITE_MOCK_MODE=off` 的真实 Playwright 运行，网络证据必须经过 gateway，且不得出现 MSW
fallback。至少签收精确 region/range/timezone、事件流、coverage BLOCKED、pending 四来源、
发布血缘、Problem Details/request ID、scope 切换与分页 cursor 失效行为。

未闭合项：

| 项目 | 当前状态 | owner / 依赖 / 下一步 |
| --- | --- | --- |
| 完整真实浏览器主链 | NOT RUN | FE04–FE09 页面完成后由 FE10 跑 `VITE_MOCK_MODE=off` Playwright |
| RFC 9457 共享错误解析 | PARTIAL | FE 共享 client owner；兼容扁平 Problem Details 并补网络测试 |
| 公网 rate/session 产品策略 | FAIL | 产品 + Security/Platform；确认阈值、TTL、并发和全局 revoke 语义 |
| 逐路由安全完整性/跨域拓扑 | PARTIAL | 各 domain owner + Security/Platform；补 authz/scope/audit/idempotency/page/CORS 证据 |
| 5 TB/day + 30% 容量 | XFAIL | Capacity/Platform；production-like 环境持续至少 30 分钟 E2E 后重测 |

任何 `FAIL`、`PARTIAL`、`NOT RUN` 或 `XFAIL` 都不能提升为生产发布 PASS。
