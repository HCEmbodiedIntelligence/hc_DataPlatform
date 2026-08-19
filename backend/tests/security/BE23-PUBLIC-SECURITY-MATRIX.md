# BE23 公网路径安全覆盖与严格回归差距矩阵

审计时点：2026-08-18。事实源是当前工作树 `create_app(...).openapi()`，不是
`backend/openapi.generated.yaml`、Mock 或历史验收统计。当前 runtime 有 85 个 schema path、
95 个 HTTP operation（其中 93 个 `/api/v1` operation）；另有 5 个未进入 schema 的公开面。

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
| G-RATE | FAIL | `AccessService` 只有可注入 hook，生产 composition 使用 no-op；其余 route 无应用层/网关 burst、slow-client、per-principal budget 证据。OPEN-02/SLO 需要产品确认，平台安全 owner 实现分布式策略，BE24 接入 Compose/gateway。 |
| G-CORS | PARTIAL | 当前同源 fail-closed 已测；若跨域部署，明确 origin/method/header/credential allowlist 尚未确认。BE24/deploy owner 按部署拓扑配置并实测。 |
| G-IDEMP | PARTIAL | 只读为 N/A；有状态命令逐行标 I1-I5 或 G-IDEMP。缺口由领域 owner 增加 duplicate body reuse、并发 winner、stale revision。 |
| G-HTTP500 | FAIL | `HTTPException(500, detail=...)` 会把 detail 原样写入 Problem Details；复现命令记录于本文件第 5 节。`core/app.py` 属 BE24 共享边界，需对 5xx 固定脱敏 detail 并回归。 |
| G-ARTIFACT | FAIL | 当前 `public-security-expanded.xml` 含测试 PostgreSQL DSN 密码和失败内部栈。BE24 gate runner 对 JUnit/log 做 secret-safe 运行与最终 S1 扫描；不得打印匹配值。 |
| G-SESSION | FAIL | access session 表/模型无 expires_at，登录并发可无限创建长期 session；需要安全产品确认 TTL/并发会话/全局撤销，security migration + BE24 manifest。 |
| G-PAGE | PARTIAL | access request/audit、jobs、annotation 多个 list 无统一 cursor/limit；owner 增加稳定排序、上限及跨 scope cursor tests。 |

## 3. Runtime OpenAPI 逐路径矩阵

“错误/跨域”列的 `E1/X1` 只证明通用分支；每行仍继承 `G-HTTP500`，不能标为完整 PASS。

| Runtime path | 方法 | Authn | Authz | IDOR / Repository scope | 审计 | 限流/资源上限 | CORS/CSRF | 幂等/并发 | 错误/脱敏 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `/api/v1/auth/registrations` | POST | N0 | N/A（建空账户） | N/A | A1 局部 | B1；G-RATE | X1 | 并发唯一 B1；非幂等 | E1 | PARTIAL：G-RATE/G-SESSION |
| `/api/v1/auth/sessions` | POST | N0 | 防枚举 B1 | N/A | A1 局部 | B1；G-RATE | X1；不发 cookie | G-SESSION | E1 | FAIL：G-RATE/G-SESSION |
| `/api/v1/auth/session/bootstrap` | GET | N1 | Z1 撤权即时 | Z1 | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/auth/session:logout` | POST | N1 | 本 session | token hash scope；缺全局撤销 | A1 局部 | G-RATE | X1 | 重复 revoke 安全；缺并发 test | E1 | PARTIAL：G-SESSION |
| `/api/v1/projects/{project_id}/membership-requests` | POST/GET | N1 | Z1 | Z1；G-SCOPE(Postgres 可执行) | A1 | B1；G-RATE/G-PAGE | X1 | I1 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/membership-requests/{access_request_id}` | GET | N1 | Z1 | 同 ID 跨项目 Z1 | A1 | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/membership-requests/{access_request_id}:approve` | POST | N1 | 同项目 capability pair Z1 | 同 ID 跨项目 Z1 | A1 | abuse hook；G-RATE | X1 | I1 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/membership-requests/{access_request_id}:reject` | POST | N1 | Z1 | Z1/G-SCOPE | A1 | abuse hook；G-RATE | X1 | I1 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/membership-requests/{access_request_id}:revoke` | POST | N1 | Z1 | Z1/G-SCOPE | A1 | abuse hook；G-RATE | X1 | I1；旧会话 Z1 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/membership-requests/{access_request_id}:withdraw` | POST | N1 | requester-only test in `test_access_api` | requester + project filter | A1 | abuse hook；G-RATE | X1 | I1 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/capability-requests` | POST/GET | N1 | Z1 | Z1/G-SCOPE | A1 | B1；G-RATE/G-PAGE | X1 | I1 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/capability-requests/{access_request_id}` | GET | N1 | Z1 | Z1/G-SCOPE | A1 | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/capability-requests/{access_request_id}:approve` | POST | N1 | 同项目 capability pair Z1 | Z1/G-SCOPE | A1 | abuse hook；G-RATE | X1 | I1 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/capability-requests/{access_request_id}:reject` | POST | N1 | Z1 | Z1/G-SCOPE | A1 | abuse hook；G-RATE | X1 | I1 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/capability-requests/{access_request_id}:revoke` | POST | N1 | Z1 | Z1/G-SCOPE | A1 | abuse hook；G-RATE | X1 | I1；旧会话 Z1 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/capability-requests/{access_request_id}:withdraw` | POST | N1 | requester-only test in `test_access_api` | requester + project filter | A1 | abuse hook；G-RATE | X1 | I1 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/access-audit-events` | GET | N1 | manager capability Z1 | project scope Z1 | A1（自审计读取未定义） | G-RATE/G-PAGE | X1 | N/A | reason/password 脱敏 A1；E1 | PARTIAL |
| `/api/v1/projects/{project_id}/collection-tasks` | POST/GET | N1 | role pair Z5 | Z5；Postgres skip 待执行 | G-AUDIT | limit/cursor；G-RATE | X1 | I3 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/collection-tasks/{collection_task_id}` | GET/PATCH | N1 | Z5 | 同 ID 跨项目 Z5 | G-AUDIT | G-RATE | X1 | ETag I3；PATCH 缺 Idempotency-Key | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/collection-tasks/{collection_task_id}/progress` | GET | N1 | Z5 | project+region Z5/Z2 | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/collection-tasks/{collection_task_id}:close` | POST | N1 | Z5 | same ID Z5；close race Postgres | G-AUDIT | G-RATE | X1 | I3 | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/regions/{region_code}/upload-manifests:preflight` | POST | N1；OpenAPI authn 缺口 N3 | role/scope Z6 | Repository N/A（纯校验）；path scope Z6/Z2 | G-AUDIT | B2；G-RATE | X1 | N/A | manifest errors bounded；E1 | PARTIAL：合同缺口 |
| `/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions` | POST/GET | N1；OpenAPI authn 缺口 N3 | Z6 | session/project/region Z6；Postgres skip | G-AUDIT | body/parts limit；G-RATE/G-PAGE(GET 无 cursor) | X1 | I4 | E1；signed URL 仅授权响应 | PARTIAL：合同缺口 |
| `/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}` | GET | N1；OpenAPI authn 缺口 N3 | Z6 | same session ID scope Z6 | G-AUDIT | G-RATE | X1 | N/A | E1；对象定位器响应需再审 | PARTIAL |
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
| `/api/v1/annotation-tasks/{task_id}/submit` | POST | N1；OpenAPI authn 缺口 N3 | Z4 | G-AUTHZ/G-SCOPE | G-AUDIT | G-RATE | X1 | I2/If-Match/Idempotency-Key | E1 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/submissions` | GET | N1；OpenAPI authn 缺口 N3 | Z4 | G-AUTHZ/G-SCOPE | G-AUDIT | G-RATE/G-PAGE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/submissions/{submission_id}` | GET | N1；OpenAPI authn 缺口 N3 | Z4 | 同 task/submission pair 未测 | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/reviews` | POST/GET | N1；OpenAPI authn 缺口 N3 | Z4；自审规则 test | G-AUTHZ/G-SCOPE | G-AUDIT | comment≤10000；G-RATE/G-PAGE | X1 | I2/If-Match；POST 无 Idempotency-Key | E1 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/history` | GET | N1；OpenAPI authn 缺口 N3 | Z4 | G-AUTHZ/G-SCOPE | G-AUDIT | G-RATE/G-PAGE | X1 | N/A | history敏感字段 allowlist 未测 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/exclusions` | GET | N1；OpenAPI authn 缺口 N3 | Z4 | G-AUTHZ/G-SCOPE | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/projects/{project_id}/rollouts/{rollout_id}/approved-annotation` | GET | N1；OpenAPI authn 缺口 N3 | Z4 局部 | rollout ID pair 未测 | G-AUDIT | G-RATE | X1 | N/A | E1 | PARTIAL |
| `/api/v1/annotation-tasks/{task_id}/auto-annotation` | POST | N1；OpenAPI authn 缺口 N3 | Z4/feature disabled | task scope Z4 | G-AUDIT | G-RATE | X1 | 缺 Idempotency-Key/concurrent job test | provider error leakage未测 | PARTIAL |
| `/api/v1/capabilities/auto-annotation` | GET | N0（公开 feature discovery） | N/A | N/A | N/A | G-RATE | X1 | N/A | 响应字段小；E1 | PARTIAL：公开性需产品确认 |
| `/api/v1/previews/sessions` | POST | N1 | Z3 read permission | body project scope Z3；adapter IDOR 未全测 | G-AUDIT | G-RATE | X1 | 缺 Idempotency-Key/concurrent create test | signed URL 仅授权响应；日志 pilot 缺 | PARTIAL |
| `/api/v1/previews/sessions/{session_id}` | GET | N1 | Z3 | descriptor project scope Z3；same ID pair不全 | G-AUDIT | G-RATE | X1 | N/A | signed URL/对象 locator 最小披露缺 pilot | PARTIAL |
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
| `/api/v1/projects/{project_id}/dashboard/snapshot` | GET | N1 | `dashboard.read` Z8 | PostgreSQL exact project/region/time Z8 | source audit Z8 | injected 429 Z8；production no-op/G-RATE | X1 | N/A | E1/G-HTTP500 | PARTIAL：G-RATE/G-HTTP500 |
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
