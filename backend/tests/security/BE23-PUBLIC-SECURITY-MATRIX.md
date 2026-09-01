# BE23 公网路径安全覆盖与严格回归差距矩阵

审计时点：2026-08-18；2026-08-29 已追加当前 runtime 路径清单。事实源是当前工作树 `create_app(...).openapi()`，不是
`backend/openapi.generated.yaml`、Mock 或历史验收统计。当前 runtime 有 261 个 schema path、
294 个 HTTP operation（其中 292 个 `/api/v1` operation）；另有 5 个未进入 schema 的公开面。

状态口径：`PASS` 只表示该单元格列出的行为已真实执行；`PARTIAL`、`FAIL`、`G-*` 都不是
发布通过。相同证据代码在每行重复出现，是为了让每个公网 path 都有测试路径或明确缺口。

## 1. 可执行证据代码

| 代码 | 测试路径 / 结论 |
| --- | --- |
| N1 | `tests/security/test_public_api_security_gate.py::test_every_runtime_public_resource_operation_rejects_anonymous_before_validation`：动态枚举每个 runtime operation；除 N0 外必须先返回 401。 |
| N0 | 明确匿名面：注册、登录、auto-annotation feature discovery、health/docs/OpenAPI/metrics；是否应继续匿名逐行说明。 |
| N3 | 运行时自定义依赖能 401，但 OpenAPI 没有 `bearerAuth`；`test_openapi_auth_gaps_are_runtime_protected_and_explicitly_marked` 锁定该合同缺口。 |
| Z1 | `tests/security/test_authorization_pairs.py::test_same_project_capability_pair_cross_project_idor_and_old_session_revocation`：同项目不同 capability、同 ID 跨项目、撤权后旧会话。 |
| Z2 | `tests/security/test_authorization_pairs.py::test_legacy_multi_project_region_arrays_fail_closed_without_explicit_pairs`：拒绝 legacy JWT project×region 笛卡尔积。 |
| Z3 | `tests/security/test_runtime_router_auth.py`：preview、publishing、jobs、Lance 的基础 authz/scope；没有覆盖每个资源 ID。 |
| Z4 | `tests/annotation/test_annotation_api.py::test_api_denies_cross_project_and_wrong_role_and_disables_vlm`。 |
| Z5 | `tests/collection_tasks/test_collection_task_api.py::test_scope_idor_and_read_write_permissions`。 |
| Z6 | `tests/ingest/test_router_contract.py` 的匿名、role、project/region/session scope 负测。 |
| Z7 | `tests/storage/test_capacity_service.py::test_capacity_scope_and_cursor_pagination_are_enforced` 与 `tests/storage/test_lifecycle_service.py::test_policy_conflict_etag_scope_and_protected_target_negative_cases`。 |
| Z8 | `tests/dashboard/test_dashboard_api.py::test_cross_project_region_capability_and_anonymous_requests_are_denied`、`test_injected_query_admission_policy_returns_429`，以及 `tests/dashboard/test_dashboard_postgres.py::test_postgres_repository_exact_scope_keyset_audit_and_index_contract`。 |
| C1 | `tests/security/test_authorization_pairs.py::test_pagination_cursor_cannot_be_reused_with_the_same_snapshot_id_in_another_scope`；collection task 自有 cursor/filter 测试见 `tests/collection_tasks/test_collection_task_service.py::test_cursor_is_bound_to_project_and_filter`。 |
| A1 | `tests/security/test_access_api.py::test_two_users_are_repository_scoped_and_revocation_is_immediate`：access 审计动作和 reason/password 脱敏。 |
| A2 | `tests/storage/test_lifecycle_service.py::test_policy_crud_enable_pause_audit_and_idempotency`。 |
| A3 | `tests/security/test_account_settings_api.py`：opaque-session self-only、资料 ETag/CAS/幂等、改密、其他会话撤销、密码/审计脱敏与并发 winner；PostgreSQL 原子竞态见 `test_access_postgres.py::test_postgres_account_profile_and_password_change_are_atomic_under_race`。 |
| A4 | `tests/security/test_session_lifecycle.py` 与 `test_access_postgres.py::test_postgres_session_expiry_touch_credential_revision_and_atomic_cap`：UTC idle/absolute 边界、受限 touch、credential revision fail-closed、默认 5 个 active session、并发登录原子上限；超额登录以 `SESSION_LIMIT_REACHED` + 有界 `Retry-After` 拒绝且不创建/淘汰会话，`EXPIRED` 持久化与 admission/expiry 安全审计可验证；过期 API token 返回 `SESSION_INVALID`。 |
| B1 | `tests/security/test_public_abuse_controls.py`：注册竞态单账户、登录防枚举、注册/登录 abuse hook、输入/请求理由/Idempotency-Key 上限。生产限流策略仍为 G-RATE。 |
| B2 | `tests/ingest/test_manifest_security_gate.py`：Manifest 字节/深度/节点/topic/path 等有界输入；upload data-plane 速率仍为 G-RATE。 |
| I1 | access 重复/冲突/并发：`test_public_abuse_controls.py`、`test_access_api.py::test_concurrent_duplicate_approval_has_one_effect_and_conflicting_decision_is_409`。 |
| I2 | annotation：`tests/annotation/test_annotation_contract.py` 的并发 winner/replay 和 Tag submit idempotency tests。 |
| I3 | collection task：`tests/collection_tasks/test_collection_task_service.py` 的 create/close replay、concurrency、ETag tests。 |
| I4 | ingest：`tests/ingest/test_ingest.py` 的 manifest replay、duplicate package、concurrent same rollout、pause/resume/cancel tests。 |
| I5 | storage：`tests/storage/test_lifecycle_service.py` 的 Idempotency-Key、ETag、危险动作和 OPEN-10 fail-closed tests。 |
| X1 | `tests/security/test_public_api_security_gate.py::test_cross_origin_preflight_is_not_permissive_and_cookie_cannot_authenticate`：当前同源 bearer 模式下无宽松 CORS，cookie 不能认证。跨域部署 allowlist 仍为 G-CORS。 |
| E1 | `test_public_api_security_gate.py::test_unexpected_exception_response_and_log_do_not_echo_sensitive_values`，以及 `tests/core/test_core.py` validation/unexpected-error 脱敏。Starlette/FastAPI `HTTPException(5xx)` 仍为 G-HTTP500。 |
| S1 | `tests/gates/sensitive_artifact_scan.py` 与 `test_sensitive_artifact_scan.py`：扫描 token/password/signed URL/object locator/traceback，只输出文件名和类别。 |

## 2. 明确缺口代码

| 代码 | 当前状态 | owner / 依赖 / 下一步 |
| --- | --- | --- |
| G-AUTHZ | PARTIAL | 只有上述成对用例；没有对每个资源 route 做同项目不同 capability 与同 ID 跨项目测试。各领域 owner 补 route+Repository pair，BE24 汇总。 |
| G-SCOPE | PARTIAL | 部分 Repository 已强制 scope，但无法从通用测试证明每个查询。领域 owner 增加真实 PostgreSQL scoped pair；不能只测 Router。 |
| G-AUDIT | PARTIAL | access、storage lifecycle、dashboard 实现有局部证据；多数读写/拒绝/危险动作没有审计事件断言。领域 owner 定义 allowlist 并测试。 |
| G-RATE | PARTIAL | 账户注册/登录已经使用生产 PostgreSQL 的 HMAC source/subject/global bucket、渐进延迟、临时锁、Turnstile 和全局能力受限的管理解锁；真实 HTTP/数据库证据见 A4/B1 与 `test_auth_abuse_postgres.py`。其余业务 route 仍无应用层/网关 burst、slow-client、per-principal budget 证据，OPEN-02/SLO 与平台级 gateway 仍由 BE24 完成。 |
| G-CORS | PARTIAL | 当前同源 fail-closed 已测；若跨域部署，明确 origin/method/header/credential allowlist 尚未确认。BE24/deploy owner 按部署拓扑配置并实测。 |
| G-IDEMP | PARTIAL | 只读为 N/A；有状态命令逐行标 I1-I5 或 G-IDEMP。缺口由领域 owner 增加 duplicate body reuse、并发 winner、stale revision。 |
| G-HTTP500 | FAIL | `HTTPException(500, detail=...)` 会把 detail 原样写入 Problem Details；复现命令记录于本文件第 5 节。`core/app.py` 属 BE24 共享边界，需对 5xx 固定脱敏 detail 并回归。 |
| G-ARTIFACT | FAIL | 当前 `public-security-expanded.xml` 含测试 PostgreSQL DSN 密码和失败内部栈。BE24 gate runner 对 JUnit/log 做 secret-safe 运行与最终 S1 扫描；不得打印匹配值。 |
| G-SESSION | PARTIAL | A4 已实现可配置 idle/absolute TTL、受限 `last_seen_at` touch、credential revision、默认 5 个 active opaque session，并以 account row lock 原子拒绝最新超额签发，保留全部既有有效会话；`EXPIRED` 与 `auth.session.admission.denied` 审计已落库，拒绝携带有界 `Retry-After`。`/api/v1/platform/accounts/{principal_id}:unlock` 只接受全局 `platform.account_security.manage`，实际解锁才留下单一 `auth.login.unlocked` 审计；仍缺管理员全局撤销和真实浏览器管理端入口。 |
| G-PAGE | PARTIAL | access request/audit、jobs、annotation 多个 list 无统一 cursor/limit；owner 增加稳定排序、上限及跨 scope cursor tests。 |

## 3. Runtime OpenAPI 逐路径矩阵

“错误/跨域”列的 `E1/X1` 只证明通用分支；每行仍继承 `G-HTTP500`，不能标为完整 PASS。

| Runtime path | 方法 | Authn | Authz | IDOR / Repository scope | 审计 | 限流/资源上限 | CORS/CSRF | 幂等/并发 | 错误/脱敏 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `/api/v1/auth/config` | GET | N0；显式公开 | N/A（只读公开策略） | N/A | N/A | 固定有界响应；no-store | X1；无 secret | N/A | 严格响应模型；E1 | PASS（发现合同）；平台仍继承 G-RATE/G-SESSION |
| `/api/v1/auth/registrations` | POST | N0 | N/A（建空账户） | N/A | A1 局部 | B1；G-RATE | X1 | 并发唯一 B1；非幂等 | E1 | PARTIAL：G-RATE/G-SESSION |
| `/api/v1/auth/sessions` | POST | N0 | 防枚举 B1 | N/A | A1 局部 | B1；G-RATE | X1；不发 cookie | G-SESSION | E1 | FAIL：G-RATE/G-SESSION |
| `/api/v1/auth/password-recovery-requests` | POST | N0；统一 202 防枚举 | N/A | 仅内部解析账号/邮箱 | 请求审计不含邮箱/token | 公共认证限流；Turnstile 开启时强制验证 | X1；no-store | 新请求使旧令牌失效 | token/邮箱不入响应与审计 | PASS（恢复请求） |
| `/api/v1/auth/password-recovery-confirmations` | POST | N0；单次高熵 token | self-only token scope | token hash + principal 原子锁 | `auth.password.recovered` | 单次/限时 token；密码策略 | X1；no-store | 消费一次；重放 422 | 不要求原密码；不返回密码 | PASS（恢复确认） |
| `/api/v1/auth/session/bootstrap` | GET | N1 | Z1 撤权即时 | Z1 | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/auth/session:logout` | POST | N1 | 本 session | token hash scope；缺全局撤销 | A1 局部 | G-RATE | X1 | 重复 revoke 安全；缺并发 test | E1 | PARTIAL：G-SESSION |
| `/api/v1/account/profile` | GET/PATCH | N1；仅 opaque session | self-only A3 | token resolve + principal key A3；JWT 不能碰撞授权 | A3 changed_fields only | 密码策略投影有界；G-RATE | X1 | PATCH ETag+key+16 路 CAS winner A3 | 资料值/密码/token 不入审计 A3；E1 | PASS（本资源）；平台仍继承 G-RATE/G-SESSION |
| `/api/v1/account/password:change` | POST | N1；当前密码二次证明 A3 | self-only A3 | 当前 opaque session + account row lock A3 | 单一 `auth.password.changed` A3 | 15..128/blocklist/context；G-RATE | X1 | ETag+key，PostgreSQL 双路仅一 winner A3 | 写字段 writeOnly，响应/审计无 secret A3；E1 | PASS（本资源）；平台仍继承 G-RATE/G-SESSION |
| `/api/v1/account/recovery-email-verifications` | POST | N1 | self-only | 当前 principal 绑定 | 不记录邮箱/token | 邮件投递与 TTL 有界 | X1；no-store | 新请求使旧 token 失效 | SMTP 错误固定脱敏 | PASS（邮箱验证请求） |
| `/api/v1/account/recovery-email:confirm` | POST | N1 + 单次 token | self-only | token principal 必须与 session principal 一致 | `auth.recovery_email.configured` | 单次/限时 token | X1；no-store | 原子消费 | 仅返回掩码邮箱 | PASS（邮箱绑定） |
| `/api/v1/platform/accounts/{principal_id}:unlock` | POST | N1 | 仅全局 `platform.account_security.manage`；项目 admin 不可代替 | 不枚举未知或已解锁账户 | 仅实际解除临时锁写一条 `auth.login.unlocked` | 无 payload；幂等 204 | X1 | 重复 unlock 无额外副作用 | A5 单元/API/PostgreSQL；E1 | PASS（账户安全解锁）；全局撤销仍缺 |
| `/api/v1/organizations/{organization_id}/projects/{project_id}/membership-requests` | POST/GET | N1 | Z1 | exact organization/project scope；G-SCOPE(Postgres 可执行) | A1 | B1；G-RATE/G-PAGE | X1 | I1 | E1 | PARTIAL |
| `/api/v1/organizations/{organization_id}/projects/{project_id}/membership-requests/{access_request_id}` | GET | N1 | Z1 | 同 ID 跨组织或跨项目 Z1 | A1 | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/organizations/{organization_id}/projects/{project_id}/membership-requests/{access_request_id}:approve` | POST | N1 | 同组织项目 capability pair Z1 | 同 ID 跨组织或跨项目 Z1 | A1 | abuse hook；G-RATE | X1 | I1 | E1 | PARTIAL |
| `/api/v1/organizations/{organization_id}/projects/{project_id}/membership-requests/{access_request_id}:reject` | POST | N1 | Z1 | Z1/G-SCOPE | A1 | abuse hook；G-RATE | X1 | I1 | E1 | PARTIAL |
| `/api/v1/organizations/{organization_id}/projects/{project_id}/membership-requests/{access_request_id}:revoke` | POST | N1 | Z1 | Z1/G-SCOPE | A1 | abuse hook；G-RATE | X1 | I1；旧会话 Z1 | E1 | PARTIAL |
| `/api/v1/organizations/{organization_id}/projects/{project_id}/membership-requests/{access_request_id}:withdraw` | POST | N1 | requester-only test in `test_access_api` | requester + organization/project filter | A1 | abuse hook；G-RATE | X1 | I1 | E1 | PARTIAL |
| `/api/v1/organizations/{organization_id}/projects/{project_id}/capability-requests` | POST/GET | N1 | Z1 | Z1/G-SCOPE | A1 | B1；G-RATE/G-PAGE | X1 | I1 | E1 | PARTIAL |
| `/api/v1/organizations/{organization_id}/projects/{project_id}/capability-requests/{access_request_id}` | GET | N1 | Z1 | Z1/G-SCOPE | A1 | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/organizations/{organization_id}/projects/{project_id}/capability-requests/{access_request_id}:approve` | POST | N1 | 同组织项目 capability pair Z1 | Z1/G-SCOPE | A1 | abuse hook；G-RATE | X1 | I1 | E1 | PARTIAL |
| `/api/v1/organizations/{organization_id}/projects/{project_id}/capability-requests/{access_request_id}:reject` | POST | N1 | Z1 | Z1/G-SCOPE | A1 | abuse hook；G-RATE | X1 | I1 | E1 | PARTIAL |
| `/api/v1/organizations/{organization_id}/projects/{project_id}/capability-requests/{access_request_id}:revoke` | POST | N1 | Z1 | Z1/G-SCOPE | A1 | abuse hook；G-RATE | X1 | I1；旧会话 Z1 | E1 | PARTIAL |
| `/api/v1/organizations/{organization_id}/projects/{project_id}/capability-requests/{access_request_id}:withdraw` | POST | N1 | requester-only test in `test_access_api` | requester + organization/project filter | A1 | abuse hook；G-RATE | X1 | I1 | E1 | PARTIAL |
| `/api/v1/organizations/{organization_id}/projects/{project_id}/access-audit-events` | GET | N1 | manager capability Z1 | exact organization/project scope Z1 | A1（自审计读取未定义） | G-RATE/G-PAGE | X1 | N/A | reason/password 脱敏 A1；E1 | PARTIAL |
| `/api/v1/projects/{project_id}/collection-tasks` | POST/GET | N1 | role pair Z5 | Z5；Postgres skip 待执行 | G-AUDIT | limit/cursor；G-RATE | X1 | I3 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/collection-tasks/{collection_task_id}` | GET/PATCH | N1 | Z5 | 同 ID 跨项目 Z5 | G-AUDIT | G-RATE | X1 | ETag I3；PATCH 缺 Idempotency-Key | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/collection-tasks/{collection_task_id}/progress` | GET | N1 | Z5 | project+region Z5/Z2 | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/collection-tasks/{collection_task_id}:close` | POST | N1 | Z5 | same ID Z5；close race Postgres | G-AUDIT | G-RATE | X1 | I3 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/regions/{region_code}/upload-manifests:preflight` | POST | N1；OpenAPI authn 缺口 N3 | role/scope Z6 | Repository N/A（纯校验）；path scope Z6/Z2 | G-AUDIT | B2；G-RATE | X1 | N/A | manifest errors bounded；E1 | PARTIAL：合同缺口 |
| `/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions` | POST/GET | N1；OpenAPI authn 缺口 N3 | Z6 | session/project/region Z6；Postgres skip | G-AUDIT | body/parts limit；G-RATE/G-PAGE(GET 无 cursor) | X1 | I4 | E1；signed URL 仅授权响应 | PARTIAL：合同缺口 |
| `/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}` | GET | N1；OpenAPI authn 缺口 N3 | Z6 | same session ID scope Z6 | G-AUDIT | G-RATE | X1 | N/A | E1；对象定位器响应需再审 | PARTIAL |
| `/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}/raw-media` | GET | N1；OpenAPI authn 缺口 N3 | uploader + project/region/session Z6 | committed raw object + project/region exact match；跨 scope 不可签发 | `raw.media.access_authorized` 记录 actor/request/session/rollout/byte length，绝不记录 key 或 URL | committed-only、TTL≤1h、响应 no-store；G-RATE | X1；无 cookie | N/A | 严格 `RawMediaSourceV1`；无 bucket/key；签名下载响应 no-store；E1 | PARTIAL：G-RATE/contract authn 缺口 |
| `/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}/manifest` | GET | N1；OpenAPI authn 缺口 N3 | Z6 | Z6/G-SCOPE | G-AUDIT | G-RATE | X1 | N/A | Manifest 可能含对象路径；缺最小披露 test | PARTIAL |
| `/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}/parts` | GET | N1；OpenAPI authn 缺口 N3 | Z6 | Z6/G-SCOPE | G-AUDIT | G-RATE/G-PAGE | X1 | N/A | signed/object locator 最小披露缺口 | PARTIAL |
| `/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}:renew` | POST | N1；OpenAPI authn 缺口 N3 | Z6 | Z6/G-SCOPE | G-AUDIT | part max；G-RATE | X1 | I4 局部 | signed URL 不落错误/日志尚缺 pilot | PARTIAL |
| `/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}:complete` | POST | N1；OpenAPI authn 缺口 N3 | Z6 | Z6/G-SCOPE | G-AUDIT | parts≤10000；G-RATE | X1 | I4 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}:retry-parts` | POST | N1；OpenAPI authn 缺口 N3 | Z6 | Z6/G-SCOPE | G-AUDIT | failures≤256；G-RATE | X1 | I4 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}:pause` | POST | N1；OpenAPI authn 缺口 N3 | Z6 | Z6/G-SCOPE | G-AUDIT | G-RATE | X1 | I4 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}:resume` | POST | N1；OpenAPI authn 缺口 N3 | Z6 | Z6/G-SCOPE | G-AUDIT | parts≤256；G-RATE | X1 | I4 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}:cancel` | POST | N1；OpenAPI authn 缺口 N3 | Z6 | Z6/G-SCOPE | G-AUDIT | G-RATE | X1 | I4 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}:commit-manifest` | POST | N1；OpenAPI authn 缺口 N3 | Z6 | Z6/G-SCOPE | G-AUDIT | B2；G-RATE | X1 | I4 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/tag-schemas` | POST | N1；OpenAPI authn 缺口 N3 | role tests在 tag suite | G-AUTHZ/G-SCOPE | G-AUDIT | schema model limits 局部；G-RATE | X1 | 版本唯一；缺 HTTP Idempotency-Key | E1 | PARTIAL：合同缺口 |
| `/api/v1/projects/{project_id}/tag-schemas/{schema_id}/versions` | GET | N1；OpenAPI authn 缺口 N3 | Z4 局部 | G-AUTHZ/G-SCOPE | G-AUDIT | G-RATE/G-PAGE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/tag-schemas/{schema_id}/versions/{version}` | GET | N1；OpenAPI authn 缺口 N3 | Z4 局部 | G-AUTHZ/G-SCOPE | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/tag-schemas/{schema_id}/versions/{version}/publish` | POST | N1；OpenAPI authn 缺口 N3 | reviewer/admin tests局部 | G-AUTHZ/G-SCOPE | G-AUDIT | G-RATE | X1 | 缺 Idempotency-Key；并发版本 test 局部 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/annotation-tasks` | GET | N1；OpenAPI authn 缺口 N3 | Z4 | path project scope Z4；Repo pair 不全 | G-AUDIT | G-RATE/G-PAGE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}` | GET | N1；OpenAPI authn 缺口 N3 | Z4 | header-selected project + resource Z4 | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/claim` | POST | N1；OpenAPI authn 缺口 N3 | Z4 | G-AUTHZ/G-SCOPE | G-AUDIT | G-RATE | X1 | claim concurrency局部；无 Idempotency-Key | E1 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/draft` | GET | N1；OpenAPI authn 缺口 N3 | Z4 | G-AUTHZ/G-SCOPE | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/current` | GET | N1；OpenAPI authn 缺口 N3 | Z4 | G-AUTHZ/G-SCOPE | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/revisions` | POST/GET | N1；OpenAPI authn 缺口 N3 | Z4 | G-AUTHZ/G-SCOPE | G-AUDIT | payload limits局部；G-RATE/G-PAGE | X1 | I2/If-Match；POST 无 Idempotency-Key | E1 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/revisions/{revision}` | GET | N1；OpenAPI authn 缺口 N3 | Z4 | G-AUTHZ/G-SCOPE | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/revisions:restore` | POST | N1；OpenAPI authn 缺口 N3 | Z4 | task 与历史 revision 的同项目范围；G-AUTHZ/G-SCOPE | G-AUDIT | client mutation ≤256；G-RATE | X1 | I2/If-Match/client_mutation_id 重放 | E1 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/submit` | POST | N1；OpenAPI authn 缺口 N3 | Z4 | G-AUTHZ/G-SCOPE | G-AUDIT | G-RATE | X1 | I2/If-Match/Idempotency-Key | E1 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/submissions` | GET | N1；OpenAPI authn 缺口 N3 | Z4 | G-AUTHZ/G-SCOPE | G-AUDIT | G-RATE/G-PAGE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/submissions/{submission_id}` | GET | N1；OpenAPI authn 缺口 N3 | Z4 | 同 task/submission pair 未测 | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/reviews` | POST/GET | N1；OpenAPI authn 缺口 N3 | Z4；自审规则 test | G-AUTHZ/G-SCOPE | G-AUDIT | comment≤10000；G-RATE/G-PAGE | X1 | I2/If-Match；POST 无 Idempotency-Key | E1 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/history` | GET | N1；OpenAPI authn 缺口 N3 | Z4 | G-AUTHZ/G-SCOPE | G-AUDIT | G-RATE/G-PAGE | X1 | N/A | history敏感字段 allowlist 未测 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/exclusions` | GET | N1；OpenAPI authn 缺口 N3 | Z4 | G-AUTHZ/G-SCOPE | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/rollouts/{rollout_id}/approved-annotation` | GET | N1；OpenAPI authn 缺口 N3 | Z4 局部 | rollout ID pair 未测 | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/auto-annotation` | POST | N1；OpenAPI authn 缺口 N3 | Z4/feature disabled | task scope Z4 | G-AUDIT | G-RATE | X1 | 缺 Idempotency-Key/concurrent job test | provider error leakage未测 | PARTIAL |
| `/api/v1/capabilities/auto-annotation` | GET | N0（公开 feature discovery） | N/A | N/A | N/A | G-RATE | X1 | N/A | 响应字段小；E1 | PARTIAL：公开性需产品确认 |
| `/api/v1/platform/version` | GET | N0（公开最小 release identity） | N/A | N/A | N/A | G-RATE | X1 | N/A（不可变只读） | 仅 release/git/chart/migration/image digest，无主机或 Secret | PASS：staging/production sentinel/zero identity fail closed；OpenAPI `security: []` 显式 |
| `/api/v1/platform/audit/events` | GET | N1；OpenAPI bearer 显式 | exact `platform.operations.read` 或只读兼容 `platform.admin`；release/backup/restore operator 不可代替 | 全局 PLATFORM stream；不接受 project/organization scope 猜测 | 成功/拒绝追加 PLATFORM audit；source 与 integrity entry 均 append-only | limit≤200、HMAC cursor≤16384、可选 UTC window；G-RATE | X1；private no-store | snapshot+occurred_at/event_id keyset | actor/resource 仅 HMAC ref，`safe_details` 值从不出站，只返回 allowlisted key 名；E1 | PASS：backup/restore/release 分类、分页、错 capability、sentinel zero match |
| `/api/v1/platform/audit/integrity` | GET | N1；OpenAPI bearer 显式 | 仅 exact `platform.maintenance.verify`；admin/viewer/operator 不通配 | 单一全局 PLATFORM hash chain | 校验动作自身追加新链事件；结果不返回 digest | 单 aggregate；G-RATE | X1；private no-store | 重算 source hash、sequence、predecessor、head | 只返回 count/status/time；tamper 时 FAILED；E1 | PASS：真实 PostgreSQL source/entry mutation 拒绝与 tamper 检测 |
| `/api/v1/platform/audit/events:export` | GET | N1；OpenAPI bearer 显式 | 仅 exact `platform.maintenance.verify`；viewer/operator 不可导出 | 与校验同一全局 chain；完整性失败不产生 payload | 成功导出追加 PLATFORM audit；payload 为同一脱敏 projection | ≤10000 event，超限/非法 window fail closed；G-RATE | X1；private no-store + attachment | export 前完整链验证；不覆盖旧 artifact | 无 actor/resource 原值、Secret/URL/object locator/`safe_details` value；E1 | PASS：三域真实事件 JSONL、wrong capability 403、tamper 409、sentinel zero match |
| `/api/v1/platform/runtime-config` | GET | N1；OpenAPI bearer 显式 | exact `platform.operations.read` 或只读兼容 `platform.admin`；项目 capability 不可代替 | environment 固定为本部署；只返回本节点已应用 revision | 成功/拒绝均写 PLATFORM append-only audit，仅含 capability、schema/revision 和 environment | 固定三个 allowlist key；G-RATE | X1；private no-store | N/A（只读节点 snapshot） | 无 DSN/Secret/TLS/image/schema payload；受保护 reason metadata 先做敏感 marker 拒绝；E1 | PASS：匿名/错 capability/脱敏/正式与 runtime OpenAPI 有测试 |
| `/api/v1/platform/runtime-config/revisions` | GET/POST | N1；OpenAPI bearer 显式 | GET 要 exact viewer/admin；POST 仅 exact `platform.release.operate`，admin/viewer 不通配 | environment 固定；PostgreSQL head row lock + expected revision CAS | GET/成功/拒绝/失败均写 PLATFORM append-only audit；mutation audit 只含 key 名，不含值/原因正文 | limit≤100；patch 1～3 key；整数 10～86400/严格 boolean；G-RATE | X1；private no-store | monotonic revision + expected revision CAS；同 revision 单 winner | unknown/Secret/DSN/TLS/image/schema key 在落库前 422；stale 409；response/audit 不回显拒绝值；E1 | PASS：真实 PG notification/lost-notification、职责和泄密负测 |
| `/api/v1/platform/runtime-config/revisions/{target_revision}:rollback` | POST | N1；OpenAPI bearer 显式 | 仅 exact `platform.release.operate` | environment 固定；target revision 必须存在于同一环境 | rollback 追加新 revision/event + PLATFORM audit，不更新/删除历史 | target/expected revision 非负，reason 8～500；G-RATE | X1；private no-store | head CAS；rollback 新 revision 严格递增 | 不存在 404、stale 409、非法 metadata 422；无值/Secret audit；E1 | PASS：baseline/历史 rollback、immutable trigger 与 monotonic event 有真实 PG 测试 |
| `/api/v1/platform/instances` | GET | N1；OpenAPI bearer 显式 | exact `platform.operations.read` 或只读兼容 `platform.admin`；项目 capability 不可代替 | 无租户 ID；PostgreSQL 全局控制面表，90 秒 stale 由 DB 时钟判定 | 成功/拒绝均写 PLATFORM append-only audit，包含 actor/request/capability，不含依赖 detail | stale/role 筛选，响应最多 1000 行；G-RATE | X1；private no-store | N/A（心跳由进程内部写入） | readiness 仅 allowlist 状态/检查名；无 DSN、错误 detail 或 Secret；E1 | PASS：viewer 与命令 capability 分离，真实/内存审计断言 |
| `/api/v1/platform/backups` | GET | N1；OpenAPI bearer 显式 | exact `platform.operations.read` 或只读兼容 `platform.admin`；项目 capability 不可代替 | 强制当前 `HC_PLATFORM_ENVIRONMENT_ID`，不接受调用者指定环境 | 成功/拒绝均写 PLATFORM append-only audit，仅含 capability 与稳定资源 ID | status 枚举、limit≤100、签名 cursor≤1024；G-RATE | X1；private no-store | N/A（只读 keyset page） | 仅返回脱敏 catalog 摘要，不返回 manifest URI、signature hash、KMS 或物理 bucket；E1 | PASS：匿名/错 capability/非法 cursor/脱敏/正式与 runtime OpenAPI 均有测试 |
| `/api/v1/platform/maintenance-operations` | POST | N1；OpenAPI bearer 显式 | BACKUP/OTHER=`platform.maintenance.operate`；MIGRATION/RELEASE=`platform.release.operate`；RESTORE=`platform.break_glass`；`platform.admin` 不通配 | environment 固定为本部署 `HC_PLATFORM_ENVIRONMENT_ID`；同环境只允许一个 nonterminal operation | 成功与 maintenance event 同事务追加 PLATFORM audit；拒绝/失败追加 outcome + 稳定错误码 | operation/actor/digest 长度有界；G-RATE | X1；private no-store | operation ID 唯一但无 Idempotency-Key | 403/409 固定脱敏 detail；审计无 digest/token/owner；E1 | PASS：5 种 operation kind 真实 PG capability pair |
| `/api/v1/platform/maintenance-operations/{operation_id}` | GET | N1；OpenAPI bearer 显式 | exact viewer 或只读兼容 `platform.admin` | 只返回本部署 environment；跨 environment ID 与不存在均固定 404 | 成功/跨环境失败均为 PLATFORM append-only audit | 单行响应；G-RATE | X1；private no-store | N/A | 404 固定脱敏，不披露其他 environment；E1 | PASS：正式/runtime capability policy 与跨环境负向用例 |
| `/api/v1/platform/maintenance-operations/{operation_id}:acquire` | POST | N1；OpenAPI bearer 显式 | 按 operation kind 要求 exact operator capability | 本 environment operation；PostgreSQL row lock + DB clock | acquire 成功与 operation mutation 同事务追加 maintenance + PLATFORM audit | 30 秒 lease；G-RATE | X1；private no-store | REQUESTED 单 winner；单调 fencing token | owner/state 失配固定 409；无 token/owner audit detail；E1 | PASS |
| `/api/v1/platform/maintenance-operations/{operation_id}:renew` | POST | N1；OpenAPI bearer 显式 | 按 operation kind 要求 exact operator capability | 本 environment；exact owner/token/unexpired lease | 每次 lease renewal 追加 maintenance + PLATFORM audit；失败只记录稳定错误码 | 30 秒 lease、DB clock；G-RATE | X1；private no-store | exact owner/token/state/version CAS | stale owner/token 固定脱敏 409；E1 | PASS |
| `/api/v1/platform/maintenance-operations/{operation_id}:takeover` | POST | N1；OpenAPI bearer 显式 | 仅 exact `platform.break_glass`；admin/operator 不通配 | 本 environment；仅 DB clock 已过期 lease | takeover 成功原子追加；拒绝/失败 outcome 可查 | 仅过期后接管；G-RATE | X1；private no-store | row lock 单 winner；新 token 严格递增 | 活跃 lease/非法 state 固定 409；E1 | PASS：高危授权独立 |
| `/api/v1/platform/maintenance-operations/{operation_id}:transition` | POST | N1；OpenAPI bearer 显式 | 普通 transition 按 kind；FAILED_READ_ONLY recovery 与 SUCCEEDED write-enable 仅 exact break-glass | 本 environment；exact owner/token/state/version；writer inventory 为零才可 FENCED | transition/approval 与 PLATFORM audit 同事务；拒绝/失败另记 outcome | 冻结状态机；G-RATE | X1；private no-store | CAS；READ_ONLY/WRITE_ENABLE 与 fence 同事务；SUCCEEDED 要 reconciliation + approval | 旧 owner/epoch、非法 transition 固定脱敏 409；E1 | PASS：operator 无法 write-enable，break-glass 仍受状态机约束 |
| `/api/v1/platform/maintenance-operations/{operation_id}:reconcile` | POST | N1；OpenAPI bearer 显式 | 仅 exact `platform.maintenance.verify` | 本 environment；exact owner/token/VERIFYING/version | reconciliation 成功原子追加；拒绝/失败另记 outcome | 单次 receipt；G-RATE | X1；private no-store | exact version CAS | 非 VERIFYING/旧 owner 固定脱敏 409；E1 | PASS：verifier 与 operator 分离 |
| `/api/v1/aligned-media/authorize` | POST | N1 | Z3 read permission | body project + organization/region headers exact scope；selector includes Dataset version/rollout/camera | successful grant writes aligned-media audit | G-RATE | X1；private no-store | stateless read; no job/session mutation | only a short-lived direct S3 GET URL; no object credentials or local paths | PASS：425 before Lance commit, 409 stable failure, READY returns one MP4 URL |
| `/api/v1/datasets/publication-preflight` | POST | N1 | publish permission Z3 | request project Z3；Repo pair不全 | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/datasets/publications` | POST | N1 | publish permission Z3 | request project；Repo pair不全 | G-AUDIT | G-RATE | X1 | service idempotency局部；HTTP key缺 | E1 | PARTIAL |
| `/api/v1/datasets/{dataset_id}/versions/{dataset_version}` | GET | N1 | read Z3 | project query/body source；same ID pair不全 | G-AUDIT | G-RATE | X1 | N/A | manifest/object locators最小披露未测 | PARTIAL |
| `/api/v1/datasets/{dataset_id}/versions/{dataset_version}/exports` | POST | N1 | publish Z3 | project request model；same ID pair不全 | G-AUDIT | G-RATE | X1 | export retry service test；HTTP key缺 | signed URL/object path artifact gap | PARTIAL |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/versions` | GET | N1 | read Z3 | path scope；Repo pair不全 | G-AUDIT | G-RATE/G-PAGE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/versions/{version}` | GET | N1 | read Z3 | path scope；same ID pair不全 | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/rollouts/{rollout_id}/steps` | GET | N1 | read Z3 | path scope；rollout/dataset pair不全 | G-AUDIT | step range有界；G-RATE | X1 | N/A | modalities敏感字段 policy未定义 | PARTIAL |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/rollouts/{rollout_id}/lineage` | GET | N1 | read Z3 | path scope；rollout pair不全 | G-AUDIT | G-RATE | X1 | N/A | object lineage最小披露未测 | PARTIAL |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/reconciliation` | POST | N1 | administer Z3 | selected RLS scope Z3 | G-AUDIT | G-RATE | X1 | reconciliation idempotency service局部；HTTP key缺 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/quality-profiles` | POST | N1 | administer source gate | path scope；Repo pair不全 | G-AUDIT | model limits局部；G-RATE | X1 | immutable version局部；HTTP key缺 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/quality-profiles/{profile_id}/versions/{profile_version}` | GET | N1 | read source gate | path scope；same ID pair不全 | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/regions/{region_code}/rollouts/{rollout_id}/quality` | GET | N1 | read source gate | project+region Z2；Repo pair不全 | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/regions/{region_code}/rollouts/{rollout_id}/raw-verification` | GET | N1 | read source gate | project+region Z2；Repo pair不全 | G-AUDIT | G-RATE | X1 | N/A | report/object detail最小披露未测 | PARTIAL |
| `/api/v1/projects/{project_id}/regions/{region_code}/rollouts/{rollout_id}/alignment` | GET | N1 | read source gate | project+region Z2；Repo pair不全 | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/jobs` | GET | N1 | read Z3 | project选择 Z3；Repo pair不全 | G-AUDIT | G-RATE/G-PAGE | X1 | N/A | workflow locator可见性未成对测 | PARTIAL |
| `/api/v1/jobs/{job_id}` | GET | N1 | read Z3 | job加载后 scope；同 ID pair不全 | G-AUDIT | G-RATE | X1 | N/A | workflow error/stack最小披露未测 | PARTIAL |
| `/api/v1/jobs/{job_id}:cancel` | POST | N1 | administer Z3 | job加载后 scope；same ID pair不全 | G-AUDIT | G-RATE | X1 | cancel replay/concurrency缺口 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/storage/capacity` | GET | N1 | role/scope Z7 | Repository scope Z7 | G-AUDIT(read) | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/storage/inventory` | GET | N1 | role/scope Z7 | cursor cross scope C1；Postgres skip | G-AUDIT(read) | limit≤100；G-RATE | X1 | N/A | object path最小披露未成对测 | PARTIAL |
| `/api/v1/projects/{project_id}/storage/lifecycle-policies` | POST/GET | N1 | read/admin Z7 | Repository scope Z7 | A2 | limit≤100；G-RATE | X1 | POST I5 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/storage/lifecycle-policies/{policy_id}` | GET/PUT/DELETE | N1 | read/admin Z7 | same ID跨项目 route pair缺 | A2 | G-RATE | X1 | PUT/DELETE I5/ETag/key | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/storage/lifecycle-policies/{policy_id}/enable` | POST | N1 | admin Z7 | scope Z7；same ID pair缺 | A2 | G-RATE | X1 | I5/ETag/key/concurrent conflict | E1 | PARTIAL；危险动作需批准策略 |
| `/api/v1/projects/{project_id}/storage/lifecycle-policies/{policy_id}/pause` | POST | N1 | admin Z7 | scope Z7；same ID pair缺 | A2 | G-RATE | X1 | I5/ETag/key | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/storage/lifecycle-audit` | GET | N1 | admin/read source gate | project scope Z7 | A2 | limit≤100；G-RATE | X1 | N/A | safe details局部；对象路径未测 | PARTIAL |
| `/api/v1/projects/{project_id}/dashboard/activity` | GET | N1 | `dashboard.read` Z8 | signed cursor + PostgreSQL exact scope Z8 | source audit Z8 | limit≤100；injected 429 Z8；G-RATE | X1 | N/A | E1/G-HTTP500 | PARTIAL：G-RATE/G-HTTP500 |
| `/api/v1/projects/{project_id}/dashboard/coverage` | GET | N1 | `dashboard.read` Z8 | PostgreSQL exact project/region/time Z8 | source audit Z8 | injected 429 Z8；production no-op/G-RATE | X1 | N/A | E1/G-HTTP500 | PARTIAL：G-RATE/G-HTTP500 |
| `/api/v1/projects/{project_id}/dashboard/pending-items` | GET | N1 | `dashboard.read` Z8 | signed cursor + PostgreSQL exact scope Z8 | source audit Z8 | limit≤100；injected 429 Z8；G-RATE | X1 | N/A | E1/G-HTTP500 | PARTIAL：G-RATE/G-HTTP500 |
| `/health/live` | GET | N0（预期匿名） | N/A | N/A | N/A | G-RATE | X1 | N/A | 固定小响应 | PARTIAL：入口流控未测 |
| `/health/ready` | GET | N0（预期匿名） | 依赖状态可见性需部署确认 | N/A | N/A | probe timeout有界；G-RATE | X1 | N/A | dependency detail 可能泄内部信息，需 gateway review | PARTIAL |

## 4. OpenAPI 之外的公开面

| Path | 当前行为 | 状态 / owner |
| --- | --- | --- |
| `/metrics` | 匿名 Prometheus exposition、`include_in_schema=False`；无公网 allowlist test。 | FAIL：deploy/BE24 应限制为内网或独立认证，并验证 label 无 project/user/secret。 |
| `/openapi.json` | 匿名；暴露完整合同。 | PARTIAL：是否公网公开需产品/安全确认；若公开需限流。 |
| `/docs` | 匿名 Swagger UI。 | PARTIAL：生产是否启用需安全决定。 |
| `/docs/oauth2-redirect` | 匿名 docs helper。 | PARTIAL：随 docs 一并决定。 |
| `/redoc` | 匿名 ReDoc。 | PARTIAL：生产是否启用需安全决定。 |

## 5. 可重复缺陷证据与非 PASS owner

### 5.1 `HTTPException(500)` detail 泄露（FAIL）

当前最小复现返回 `500 HTTP_500 leaked=True`：在 test-only route 抛出
`HTTPException(500, detail='be23-secret-in-http-exception')`，响应原样包含 sentinel。
owner 是 `core/app.py`/BE24；下一步是仅允许 4xx 的安全业务 detail，所有 5xx 固定通用 detail，
新增正式回归后再移除本矩阵 FAIL。

### 5.2 artifact 泄露（FAIL）

对 `artifacts/test-gates/latest` 的 43 个文件做不回显扫描，发现：

- `public-security-expanded.xml`：`credential_in_dsn`，并包含失败内部栈；
- `dependency-start.log`、`dependency-worker.log` 的 `file://` 构建 URI是本地构建定位器，
  不属于数据对象 key，未按对象泄露计数。

owner 是 BE24 gate runner；依赖是对失败 JUnit/log 的脱敏策略。下一步在每个 gate 结束后运行
`python tests/gates/sensitive_artifact_scan.py /artifacts/latest`，任何 finding 非零退出；scanner
永远不打印命中值。

### 5.3 外部依赖 skip（本轮基线由 8 个增至 9 个，宿主严格回归仍为 FAIL）

| skip | owner | 现有 test Compose 条件 | 本轮隔离执行 / 下一步 |
| --- | --- | --- | --- |
| annotation PostgreSQL | annotation | `HC_ANNOTATION_TEST_POSTGRES_DSN` + migration | PASS；BE24 把同一环境注入严格 gate。 |
| collection task PostgreSQL | collection_tasks | `HC_TEST_POSTGRES_DSN` | PASS；BE24 把同一环境注入严格 gate。 |
| ingest MinIO | ingest | MinIO endpoint/bucket/credentials | PASS；BE24 串行化隔离 bucket。 |
| ingest PostgreSQL | ingest | `HC_TEST_POSTGRES_DSN` | PASS；BE24 把 migration/RLS 环境注入 gate。 |
| access PostgreSQL | security/BE23 | `HC_TEST_POSTGRES_DSN` | PASS；BE24 把 access migration 环境注入 gate。 |
| core security PostgreSQL | security/BE23 | `HC_TEST_POSTGRES_DSN` | PASS；BE24 把原子性环境注入 gate。 |
| storage PostgreSQL | storage | `HC_TEST_POSTGRES_DSN` | PASS；BE24 把 inventory/lifecycle 环境注入 gate。 |
| MinIO network recovery | system | MinIO variables + `hc.be12.disposable=true` 的可 pause container | 独立 disposable container PASS；现有 Compose service 缺 label，BE24 补共享 Compose 后纳入 gate。 |
| dashboard PostgreSQL（本轮并发新增的第 9 个） | dashboard | `HC_TEST_POSTGRES_DSN` + dashboard migration | PASS；BE24 把同一环境注入严格 gate。 |

上述 9 项在 `hc-data-be23-gates` 隔离项目（network recovery 使用另一个显式 disposable
container）均真实执行通过；但无依赖环境的宿主严格命令仍得到 9 skip，所以整体不得记 PASS。

### 5.4 容量（XFAIL/FAIL，不降低门槛）

`tests/system/test_release_blockers.py::test_release_gate_has_measured_end_to_end_capacity_pass`
必须继续 XFAIL/严格门禁非零：没有 5 TB/day + 30% 全链路 PASS artifact。现有 MinIO multipart
P50 56.48 MB/s，低于 75.23 MB/s 约 25%，且尚未叠加 API、Temporal、QC/align、Lance、
preview/transcode、export。

owner：capacity/platform。下一阶段按 control plane、MinIO、Worker queue/lag、QC/align、Lance、
preview、export 分段采集 p50/p95/p99、CPU/memory/network/disk、retry/error/saturation，再在
production-like 环境持续至少 30 分钟做 E2E；不得用稀疏文件、内存 session 或本地 SHA 替代。

### 5.5 需要产品确认

- OPEN-02：验证码、注册/IP/设备限流、登录渐进延迟/锁定与准确阈值；未确认前不硬编码数字。
- session TTL、并发 session 上限、账户全局 logout/recovery（OPEN-01 关联）。
- 是否允许跨域部署及 CORS allowlist；当前只证明同源 fail-closed。
- auto-annotation capability、docs/OpenAPI/metrics 是否允许公网匿名。
- 审计保留/读取字段/导出与拒绝事件目录；未从 Mock 反推。

### 5.6 本轮严格回归实测

- 宿主全量严格回归：`448 passed, 9 skipped, 1 xfailed`，退出码 1；skip/XFAIL 未过滤。
- 隔离 Compose 公网安全/负载/ingest manifest：`95 passed`；宿主 gate：`5 passed`。
- 9 个外部依赖用例均在隔离依赖中 PASS；迁移检查 `expected=applied=21`，无 missing、drift、unknown。
- 容量仍由 5.4 的 XFAIL 阻断发布；artifact scan 仍由 5.2 的 finding 阻断发布。
- 全仓 Ruff lint 与 mypy 分别 PASS（127 个 source）；format check 因 2 个 storage source、
  2 个 storage test、1 个 dashboard test 的并发未格式化改动退出 1。它们超出 BE23 修改边界，
  owner 为 storage/dashboard，下一步由各领域终端格式化后 BE24 重跑全仓 gate。

## 6. 2026-08-20 当前 Runtime 路径增量清单

本节不改写上方 2026-08-18 历史缺陷记录。它把后来组成的 P02、P05–P07、P09–P19 正式
路径纳入动态 N1 匿名拒绝测试的覆盖范围；每条路径同时由所属领域 API/合同/PostgreSQL
测试及 `VITE_MOCK_MODE=off` 页面验收覆盖。未知或未批准的写操作仍必须返回正式
`FEATURE_UNAVAILABLE`，不能被页面伪造成成功。

| Runtime path | 当前安全合同 |
| --- | --- |
| `/api/v1/account/access-overview` | N1；当前 opaque session 的 self-only 组织、项目与申请投影；不要求 project scope。 |
| `/api/v1/account/organization-membership-requests` | N1；ACTIVE 个人账户 self-only 提交；Idempotency-Key；不要求 project scope。 |
| `/api/v1/account/organization-membership-requests/{access_request_id}:withdraw` | N1；仅申请人可撤回自己的 PENDING 组织申请；Idempotency-Key。 |
| `/api/v1/organization-membership-requests/{access_request_id}:approve` | N1；仅全局 `platform.admin`；批准只生成组织关系，不生成 project scope。 |
| `/api/v1/organization-membership-requests/{access_request_id}:reject` | N1；仅全局 `platform.admin`；状态转换与 Idempotency-Key。 |
| `/api/v1/organization-membership-requests/{access_request_id}:revoke` | N1；仅全局 `platform.admin`；撤销访问关系但保留个人账户与 session。 |
| `/api/v1/projects/{project_id}/dashboard/task-status` | N1；dashboard capability 与 project/region 查询范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/device-capture-facts` | N1；ingest 读 capability 与 exact project/region 范围。 |
| `/api/v1/organizations/{organization_id}/robot-model-versions/{version_id}` | N1；组织-项目范围与 robot-model capability。 |
| `/api/v1/organizations/{organization_id}/robot-model-versions/{version_id}:create-draft` | N1；组织范围、robot-model 写 capability、版本状态与幂等合同。 |
| `/api/v1/platform/accounts/{principal_id}:unlock` | N1；仅全局 `platform.account_security.manage`，不接受项目管理员替代。 |
| `/api/v1/organizations/{organization_id}/robot-model-versions/{version_id}/upload-sessions` | N1；未批准上传合同。 |
| `/api/v1/organizations/{organization_id}/robot-model-versions/{version_id}:preflight-publish` | N1；未批准发布合同。 |
| `/api/v1/organizations/{organization_id}/robot-model-versions/{version_id}:publish` | N1；正式 `FEATURE_UNAVAILABLE`。 |
| `/api/v1/organizations/{organization_id}/robot-models` | N1；P14 组织范围。 |
| `/api/v1/organizations/{organization_id}/stream-schemas` | N1；P17 组织范围。 |
| `/api/v1/organizations/{organization_id}/stream-schemas/{schema_id}/versions/{schema_version}` | N1；schema 版本读取。 |
| `/api/v1/organizations/{organization_id}/stream-schemas/{schema_id}/versions/{schema_version}:preflight-publish` | N1；未批准发布合同。 |
| `/api/v1/organizations/{organization_id}/stream-schemas/{schema_id}/versions/{schema_version}:publish` | N1；正式 `FEATURE_UNAVAILABLE`。 |
| `/api/v1/projects/{project_id}/audit/bootstrap` | N1；P19 红脱敏读投影。 |
| `/api/v1/projects/{project_id}/audit/events` | N1；P19 keyset 范围。 |
| `/api/v1/projects/{project_id}/audit/events/facets` | N1；P19 范围。 |
| `/api/v1/projects/{project_id}/audit/events/{event_id}` | N1；P19 单资源范围。 |
| `/api/v1/projects/{project_id}/datasets` | N1；P05 project 范围。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/bootstrap` | N1；P06 读投影。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/deletion-checks` | N1；不可执行预检。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/lance-versions` | N1；P06 范围。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/lance-versions/{version}` | N1；P06 范围。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/versions/{version_id}/bootstrap` | N1；P07 固定版本范围。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/versions/{version_id}/capacity-facts` | N1；P07 范围。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/versions/{version_id}/deletion-checks` | N1；不可执行预检。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/versions/{version_id}/diff-jobs` | N1；P07 范围。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/versions/{version_id}/episode-revisions/{revision_id}` | N1；P07 资源范围。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/versions/{version_id}/episodes` | N1；P07 范围。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/versions/{version_id}/manifest` | N1；P07 最小披露。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/versions/{version_id}/operational-inventory` | N1；P07 范围。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/versions/{version_id}/required-storage` | N1；P07 范围。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/versions/{version_id}/review-checks` | N1；P07 范围。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/versions/{version_id}/schema` | N1；P07 范围。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/versions/{version_id}/schema-summary` | N1；P07 范围。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/versions/{version_id}/source-provenance` | N1；P07 范围。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/versions/{version_id}:approve` | N1；P07 审核 capability 与审计。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/versions/{version_id}:return` | N1；P07 审核 capability 与审计。 |
| `/api/v1/projects/{project_id}/datasets:facets` | N1；P05 project 范围。 |
| `/api/v1/projects/{project_id}/datasets:page-capabilities` | N1；P05 capability 读投影。 |
| `/api/v1/projects/{project_id}/datasets:summary` | N1；P05 project 范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/annotation-tasks/{task_id}/manifest-discovery` | N1；P08 task-bound 脱敏 discovery。 |
| `/api/v1/annotations/revisions` | N1；P08 项目/区域 Scope、签名游标与脱敏读审计。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/calibration-sets` | N1；P16 RLS 范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/calibration-sets/{set_id}` | N1；P16 RLS 范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/calibration-sets/{set_id}/versions/{version}:preflight-publish` | N1；未批准发布合同。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/calibration-sets/{set_id}/versions/{version}:publish` | N1；正式 `FEATURE_UNAVAILABLE`。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/cleaning-drafts` | N1；P10 只读 RLS 投影。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/cleaning-drafts/{draft_id}/bootstrap` | N1；P11 workbench 范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/cleaning-drafts/{draft_id}/commits` | N1；P11 提交 capability/幂等。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/cleaning-drafts/{draft_id}/edl` | N1；P11 ETag/CAS。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/cleaning-drafts/{draft_id}/events` | N1；P10 范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/cleaning-drafts/{draft_id}/previews` | N1；P11 范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/cleaning-drafts/{draft_id}/review-findings` | N1；P11 只读审核反馈。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/cleaning-drafts/{draft_id}/summary` | N1；P10 范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/cleaning-drafts:summary` | N1；P10 范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/components/{component_id}/channels` | N1；P15 RLS 范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/components/{component_id}/frames` | N1；P15 RLS 范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/data-sources` | N1；P02 RLS 与 capability。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/data-sources/page` | N1；P02 范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/data-sources/{source_id}` | N1；P02 单资源范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/data-sources/{source_id}:disable` | N1；P02 管理 capability。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/data-sources/{source_id}:enable` | N1；P02 管理 capability。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/data-sources/{source_id}:rotate-credential` | N1；P02 凭据不回显。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/data-sources/{source_id}:test-connection` | N1；P02 异步工作范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/manual-issues` | N1；P09 RLS 范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/manual-issues/{issue_id}` | N1；P09 单资源范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/manual-issues/{issue_id}/cleaning-drafts` | N1；P09→P11 范围 handoff。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/manual-issues/{issue_id}:resolve` | N1；P09 capability/审计。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/manual-issues/{issue_id}:triage` | N1；P09 capability/审计。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/manual-issues:page` | N1；P09 范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/robots` | N1；P15 RLS 范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/search` | N1；A1 shell 机器人检索只在 `robot.read`、项目/区域 RLS 范围内执行，游标绑定主体、权限版本、作用域和查询。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/robots/{robot_id}/bootstrap` | N1；P15 RLS 范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/robots/{robot_id}/components` | N1；P15 RLS 范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/route-resolutions/p15-to-p17` | N1；P15→P17 范围深链。 |
| `/api/v1/account/notifications` | N1；A2 当前账户收件箱，session-only、recipient RLS、无项目头。 |
| `/api/v1/account/notifications/unread-count` | N1；A2 当前账户未读计数，session-only、no-store。 |
| `/api/v1/account/notifications/{notification_id}:read` | N1；A2 当前账户已读幂等写入与脱敏审计。 |
| `/api/v1/datasets/{dataset_id}/versions/{dataset_version}/exports/{job_id}` | N1；P07 导出任务 project 读取范围。 |
| `/api/v1/datasets/{dataset_id}/versions/{dataset_version}/exports/{job_id}/download` | N1；P07 导出下载当前授权范围。 |

## 7. 2026-08-24 运行时路径增量

本节补齐当前运行时新增的 P12/P13、P19、平台账户与自动标注 job 路径。每行首先受动态 N1
匿名拒绝测试保护；更细的授权、RLS、审计、幂等和容量缺口仍以第 1、2 节的代码为准，不能据此
提升为发布 PASS。

| Runtime path | 当前安全合同 |
| --- | --- |
| `/api/v1/annotation-tasks/{task_id}/auto-annotation-jobs/{job_id}` | N1；task/project/region scope，自动标注任务读取。 |
| `/api/v1/annotation-tasks/{task_id}/auto-annotation-jobs/{job_id}:apply` | N1；annotator scope、ETag 与人工确认后才应用建议。 |
| `/api/v1/annotation-tasks/{task_id}/auto-annotation-jobs/{job_id}:cancel` | N1；annotator scope，持久化 job 状态转换。 |
| `/api/v1/annotation-tasks/{task_id}/auto-annotation-jobs/{job_id}:retry` | N1；annotator scope，失败/取消任务写入新的 durable outbox 事件。 |
| `/api/v1/platform/accounts` | N1；平台账户目录只由全局账户管理 capability 访问。 |
| `/api/v1/platform/accounts/{principal_id}` | N1；平台账户单资源管理范围。 |
| `/api/v1/platform/accounts/{principal_id}:disable` | N1；全局账户管理 capability 与安全审计。 |
| `/api/v1/platform/accounts/{principal_id}:enable` | N1；全局账户管理 capability 与安全审计。 |
| `/api/v1/platform/accounts/{principal_id}:reset-password` | N1；管理员重置不回显凭据或恢复 token。 |
| `/api/v1/platform/accounts/{principal_id}:role` | N1；全局角色变更受账户管理 capability 保护。 |
| `/api/v1/platform/version` | N0；公开最小不可变 release identity，不包含节点、主机或 Secret。 |
| `/api/v1/projects/{project_id}/audit/exports` | N1；P19 organization/project/region exact scope，创建持久化导出任务。 |
| `/api/v1/projects/{project_id}/audit/exports/{job_id}` | N1；P19 导出任务按 organization/project/region 隔离。 |
| `/api/v1/projects/{project_id}/audit/exports/{job_id}/download` | N1；仅成功任务可签发短时、no-store 下载授权。 |
| `/api/v1/projects/{project_id}/audit/exports/{job_id}:cancel` | N1；P19 导出任务取消状态转换。 |
| `/api/v1/projects/{project_id}/audit/exports/{job_id}:retry` | N1；P19 导出重试写入 durable outbox 事件。 |
| `/api/v1/projects/{project_id}/audit/legal-holds` | N1；P19 legal-hold 的 organization/project/region scope。 |
| `/api/v1/projects/{project_id}/audit/legal-holds/{hold_id}:release` | N1；P19 legal-hold release 写操作和审计。 |
| `/api/v1/projects/{project_id}/audit/retention-policy` | N1；P19 retention policy 的 ETag/CAS 与 organization scope。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}/processing` | N1；上传处理状态只在 project/region/session scope 内读取。 |
| `/api/v1/projects/{project_id}/datasets/{dataset_id}/episodes/{episode_id}/revision-history` | N1；P07 episode revision history 按 project/dataset/episode 范围读取。 |
| `/api/v1/projects/{project_id}/storage/capacity/portfolio` | N1；P12 portfolio capacity 读取受项目 scope 保护。 |
| `/api/v1/projects/{project_id}/storage/lifecycle-executions` | N1；P13 生命周期执行列表/创建受项目 scope 保护。 |
| `/api/v1/projects/{project_id}/storage/lifecycle-executions/{execution_id}` | N1；P13 execution 单资源 scope。 |
| `/api/v1/projects/{project_id}/storage/lifecycle-executions/{execution_id}/logs` | N1；P13 execution log 受项目 scope 与分页上限保护。 |
| `/api/v1/projects/{project_id}/storage/lifecycle-executions/{execution_id}:approve` | N1；P13 危险执行审批与审计。 |
| `/api/v1/projects/{project_id}/storage/lifecycle-executions/{execution_id}:cancel` | N1；P13 execution 取消状态转换。 |
| `/api/v1/projects/{project_id}/storage/lifecycle-executions/{execution_id}:retry` | N1；P13 execution 重试状态转换。 |
| `/api/v1/projects/{project_id}/storage/lifecycle-executions/{execution_id}:start` | N1；P13 execution start 受生命周期写权限保护。 |
| `/api/v1/projects/{project_id}/storage/lifecycle-executions:dry-run` | N1；P13 dry-run 仍需项目 scope，不生成实际对象副作用。 |
| `/api/v1/projects/{project_id}/storage/lifecycle-schedules` | N1；P13 lifecycle schedule 列表/写操作受项目 scope 保护。 |
| `/api/v1/projects/{project_id}/storage/lifecycle-schedules/{schedule_id}` | N1；P13 schedule 单资源 scope。 |
| `/api/v1/projects/{project_id}/storage/lifecycle-schedules/{schedule_id}/enable` | N1；P13 schedule enable 状态转换与审计。 |
| `/api/v1/projects/{project_id}/storage/lifecycle-schedules/{schedule_id}/pause` | N1；P13 schedule pause 状态转换与审计。 |
| `/api/v1/projects/{project_id}/storage/multipart-uploads/{multipart_id}:abort` | N1；P12 multipart abort 受项目 scope 与对象所有权保护。 |
| `/api/v1/projects/{project_id}/storage/objects` | N1；P12 object inventory 受项目 scope 与分页限制保护。 |
| `/api/v1/projects/{project_id}/storage/objects/{object_id}` | N1；P12 object 单资源 scope。 |
| `/api/v1/projects/{project_id}/storage/objects/{object_id}:download` | N1；P12 仅当前授权对象可签发短时下载。 |
| `/api/v1/projects/{project_id}/storage/objects/{object_id}:restore` | N1；P12 recoverable restore 受项目 scope 与审计保护。 |
| `/api/v1/projects/{project_id}/storage/objects/{object_id}:transition` | N1；P12 storage-class transition 受项目 scope 与审计保护。 |
| `/api/v1/projects/{project_id}/storage/objects/{object_id}:trash` | N1；P12 recoverable delete 受项目 scope、保留期与审计保护。 |
| `/api/v1/datasets/{dataset_id}/versions/{dataset_version}/exports/{job_id}:cancel` | N1；P07 导出取消 capability。 |
| `/api/v1/datasets/{dataset_id}/versions/{dataset_version}/exports/{job_id}:retry` | N1；P07 导出重试 capability。 |
| `/api/v1/organizations/{organization_id}/projects/{project_id}/regions/{region_code}/stream-schemas/{schema_id}/versions/{schema_version}/dataset-references` | N1；P17 schema/dataset 关联范围。 |
| `/api/v1/organizations/{organization_id}/robot-model-asset-uploads/{upload_id}:authorize-parts` | N1；P14 未批准 asset 上传授权。 |
| `/api/v1/organizations/{organization_id}/robot-model-asset-uploads/{upload_id}:complete-file` | N1；P14 未批准 asset 上传完成。 |
| `/api/v1/organizations/{organization_id}/robot-model-versions/{version_id}/assets` | N1；P14 版本 asset 范围。 |
| `/api/v1/organizations/{organization_id}/robot-model-versions/{version_id}/assets/{asset_id}/download` | N1；P14 asset 下载范围。 |
| `/api/v1/organizations/{organization_id}/robot-model-versions/{version_id}/bindings` | N1；P14 关节绑定范围。 |
| `/api/v1/organizations/{organization_id}/robot-model-versions/{version_id}/joint-mappings` | N1；P14 关节映射范围。 |
| `/api/v1/organizations/{organization_id}/stream-schemas/{schema_id}/versions/{schema_version}:validate` | N1；P17 schema 校验范围。 |
| `/api/v1/organizations/{organization_id}/stream-schemas:import` | N1；P17 schema 导入范围。 |
| `/api/v1/projects/{project_id}/audit/integrity` | N1；P19 审计完整性只读校验。 |
| `/api/v1/projects/{project_id}/collection-tasks/{collection_task_id}/packages` | N1；P20 collection task 与 package 投影的同项目读取范围。 |
| `/api/v1/projects/{project_id}/collection-tasks/{collection_task_id}:cancel` | N1；P20 collection task 取消 capability。 |
| `/api/v1/projects/{project_id}/collection-tasks/{collection_task_id}:reopen` | N1；P20 collection task 重新打开 capability。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/continuous-recordings` | N1；project/region 精确读取范围，分页边界由路由合同限制。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/continuous-recordings/{recording_id}` | N1；project/region 与 recording 归属必须同时匹配。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/continuous-recordings/{recording_id}/slice-draft` | N1；切片草稿命令要求精确 project/region/recording 写权限。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/continuous-recordings/{recording_id}/slice-draft:finalize` | N1；finalize 使用同一 scope 与草稿状态机约束。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/calibration-sets/{set_id}/versions` | N1；P16 校准版本范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/calibration-sets/{set_id}/versions/{version}/dataset-associations` | N1；P16 dataset 关联范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/calibration-sets/{set_id}/versions/{version}/document` | N1；P16 版本文档范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/calibration-sets/{set_id}/versions/{version}:validate` | N1；P16 校验范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/calibration-validation-reports/{report_id}` | N1；P16 校验报告范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/components/{component_id}` | N1；P15 component 单资源范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/components/{component_id}:transition` | N1；P15 component 状态写 capability。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/robots/{robot_id}` | N1；P15 robot 单资源范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/robots/{robot_id}/maintenance-records` | N1；P15 维保记录范围。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/robots/{robot_id}:transition` | N1；P15 robot 状态写 capability。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/quality-problems` | N1；quality 读 capability 与精确 project/region 分页范围。 |
| `/api/v1/projects/{project_id}/storage/capacity/history` | N1；P12 容量历史 project 范围。 |

## 8. 2026-08-29 平台运维与安全发布增量

| Runtime path | 当前安全合同 |
| --- | --- |
| `/api/v1/platform/overview` | N1；仅 `platform.admin` / `platform.operations.read`，主机、Pod、仓库和环境标识 HMAC 脱敏。 |
| `/api/v1/platform/logs` | N1；仅固定 allowlist 过滤器，不接受自由 LogQL，结果移除节点/实例/trace 标识。 |
| `/api/v1/platform/releases` | N1；只读脱敏发布历史；操作员标识只返回 HMAC reference。 |
| `/api/v1/platform/releases:preflight` | N1；仅 `platform.release.operate`；验证固定 Ed25519 key、单调序列、兼容矩阵、节点 digest 和备份门禁。 |
| `/api/v1/platform/releases/{release_id}:approve` | N1；仅 `platform.release.operate`；CAS 状态修订与 distinct approver，不接收集群凭据。 |
| `/api/v1/platform/releases/{release_id}:transition` | N1；仅 `platform.release.operate`；外部控制器按有限状态机记录步骤，不接收集群凭据。 |
| `/api/v1/platform/object-store-location` | N1；任意已认证账户可读取对象存储逻辑位置和配置来源，不返回 endpoint、bucket、access key 或 Secret。 |
| `/api/v1/platform/object-store-config` | N1；仅 exact `platform.admin`；GET 不返回 Secret，PUT 使用 revision CAS，并将凭据加密后持久化，成功与拒绝均写 PLATFORM audit。 |
| `/api/v1/platform/organizations` | N1；仅 exact `platform.admin`；GET/POST 独立管理全局组织目录，创建冲突 fail closed，成功与拒绝均写 PLATFORM audit。 |
| `/api/v1/platform/projects` | N1；仅 exact `platform.admin`；GET/POST 操作全局项目目录，创建冲突 fail closed，成功与拒绝均写 PLATFORM audit。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/continuous-recordings/uploads` | N1；写 capability 与 organization/project/region 精确 scope；只签发有界 multipart 上传授权，不返回对象存储凭据。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/continuous-recordings/uploads/{upload_id}` | N1；读 capability 与精确 scope；upload ID 必须归属当前 scope，响应不含对象 key 或凭据。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/continuous-recordings/uploads/{upload_id}/assets/{asset_id}:authorize-parts` | N1；写 capability 与精确 scope；仅为当前 UPLOADING asset 的声明分片签发短期授权。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/continuous-recordings/uploads/{upload_id}/assets/{asset_id}:complete` | N1；写 capability 与精确 scope；按声明 part 集合完成上传，并校验 size、CRC64 与 SHA-256。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/continuous-recordings/uploads/{upload_id}:commit` | N1；写 capability 与精确 scope；仅在全部不可变资产校验通过后提交，并返回 no-store/ETag。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/continuous-recordings/{recording_id}/episodes` | N1；读 capability 与精确 scope；仅返回当前 recording 的 Episode 处理状态。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/continuous-recordings/{recording_id}/episodes/{episode_id}/video-sources` | N1；读 capability 与精确 scope；仅为 finalized Episode 返回短期原视频读取 URL，不物化预览副本。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/continuous-recordings/{recording_id}/slice-proposals` | N1；写 capability 与精确 scope；模型切片提案受 If-Match/ETag 并发保护。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/continuous-recordings/{recording_id}/video-sources` | N1；读 capability 与精确 scope；仅签发短期原视频读取 URL，不返回对象存储凭据或物理 key。 |

## 9. 2026-09-01 机器人绑定与 LeRobot 导入增量

| Runtime path | 当前安全合同 |
| --- | --- |
| `/api/v1/organizations/{organization_id}/robots` | N1；机器人目录按精确 organization scope 读取。 |
| `/api/v1/organizations/{organization_id}/robots/model-bindings` | N1；模型绑定列表按精确 organization scope 读取。 |
| `/api/v1/organizations/{organization_id}/robots/{robot_id}/bootstrap` | N1；robot ID 必须归属当前 organization，返回当前模型绑定启动信息。 |
| `/api/v1/organizations/{organization_id}/robots/{robot_id}/model-bindings` | N1；模型绑定写入要求精确 organization scope 与机器人管理 capability。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/lerobot-imports` | N1；LeRobot 导入创建要求精确 organization/project/region scope。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/lerobot-imports/{import_id}/assets:authorize-parts` | N1；只为当前导入和声明资产签发有界分片授权。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/lerobot-imports/{import_id}/assets:complete` | N1；按精确导入 scope 校验并完成声明资产。 |
| `/api/v1/projects/{project_id}/regions/{region_code}/lerobot-imports/{import_id}:commit` | N1；仅在当前导入资产全部校验通过后提交。 |
