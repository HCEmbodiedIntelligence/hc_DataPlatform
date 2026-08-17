# 安全与作用域持久化（BE-02）

该包提供认证和 PostgreSQL 事务基础能力；它不定义业务领域状态或数据表。

## 运行时用法

- 为 `JwtVerifier` 配置一个静态验证密钥、HTTPS OIDC JWKS URL 或自定义
  `SigningKeyResolver`。校验器要求令牌包含 `exp`、`iat`、`sub`、`iss` 和 `aud`，
  会验证配置的受众与颁发者，并且只接受明确指定的签名算法。
- 每次数据库操作都必须从 `PostgresScopedUnitOfWork` 开始。进入工作单元时会验证所选项目
  和可选区域，随后设置供 RLS 使用的 PostgreSQL 事务本地 `app.*` 变量。API 或 Worker
  绝不能使用 PostgreSQL 超级用户，因为 PostgreSQL 会有意让超级用户绕过 RLS。
- Worker 使用 `AuthContext.service(...)` 和 `PostgresScopedUnitOfWork.for_worker(...)`。
  服务身份必须包含 Worker 所选择的确切项目和区域；即使是 `admin` 服务角色，也不能绕过作用域限制。
- 在调用 `await uow.commit()` 前完成业务写入，并调用 `uow.audit.append(...)` 和
  `uow.outbox.stage(...)`。工作单元会在同一个事务中插入这三项内容。未提交便退出上下文时，
  所有操作都会回滚。
- 在同一个事务中调用 `await uow.idempotency.execute(...)`。其 PostgreSQL 唯一约束会
  串行化对同一作用域/键组合的并发使用。使用不同请求正文会抛出 `IDEMPOTENCY_KEY_REUSED`
  （HTTP 409）。

`InMemoryScopedRepository`、`InMemoryIdempotencyStore` 和
`InMemoryScopedUnitOfWork` 是供单元测试使用、不依赖 PostgreSQL 的替身。

## 迁移与 RLS

在空数据库上运行 `migrations/security/001_core.sql`，该迁移可重复执行。所有独立维护的
模块迁移完成后再运行一次，以便 `core.reconcile_project_rls()` 为每张包含 `project_id`
的表安装并强制启用策略。模块迁移执行器也可以在创建项目表后立即调用
`SELECT core.apply_project_rls('schema.table'::regclass)`。

缺少 `app.project_id` 时，RLS 会拒绝访问。对于包含区域的行，`app.region_code` 也是必需的。
设置这两个值的受支持方式是使用 `PostgresScopedUnitOfWork`。

## 验证

在 `backend/` 目录运行：

```bash
uv run --extra dev pytest tests/security -m 'not integration'
uv run --extra dev --extra database ruff format --check src/hc_data_platform/security tests/security
uv run --extra dev --extra database ruff check src/hc_data_platform/security tests/security
uv run --extra dev --extra database mypy src/hc_data_platform/security
HC_TEST_POSTGRES_DSN=postgresql+asyncpg://... \
  uv run --extra dev --extra database pytest tests/security -m integration
```

如果未提供集成测试 DSN，PostgreSQL 测试会被跳过，所有基于替身的单元测试仍会运行。

## 故障排查

- 查询已知行却返回空结果，通常表示事务选择了错误的项目/区域，或未使用带作用域的工作单元。
- 服务身份收到 `SERVICE_SCOPE_REQUIRED` 时，必须为其签发并选择明确的项目/区域；
  不要增加数据库绕过机制。
- 卡住的发件箱事件会保持未发布状态，可由显式限定作用域的 Worker 重试；暂存事件绝不会直接从
  请求事务中发布。
