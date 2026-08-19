# ADR-0001：公开注册与项目访问申请（草案）

- 状态：Draft / Product decision required
- 日期：2026-08-17
- 责任域：F0-02、F1-01、F2-01
- 决策前提：UXF-02、UXF-03 已确认；OPEN-01～03 尚未确认

## 已确认且已实现的边界

1. `username + password` 注册直接创建 `ACTIVE` 空账户；账户可立即登录。
2. 注册事务不创建项目、不创建 membership、不创建 capability grant，也没有账户
   `PENDING` 状态或账户审批 API。
3. 项目加入申请和权限申请是两个独立资源，分别支持创建、按可见范围查询、批准、
   拒绝、申请人撤回和管理员撤销。
4. membership 批准只产生项目 scope；capability 批准只产生请求中明确列出的 capability。
   有效 capability 始终与 active membership 求交集，不能脱离项目 scope 使用。
5. session 只在响应中返回一次 opaque token，数据库仅保存 SHA-256 摘要。每次认证和
   bootstrap 都读取当前 membership/grant；撤销后旧 token 的下一次请求立即得到新 scope。
6. 普通管理员没有跨项目绕过。申请人查询强制 `requester_id`，管理员查询强制精确
   `project_id` 与管理 capability；Repository 本身重复执行该过滤。
7. 命令使用 `Idempotency-Key`；审批在 PostgreSQL 行锁事务中完成。相同终态重复审批返回
   已提交结果，相反终态竞争返回 409。

## 尚未确认，当前不得冻结的产品/风控规则

### OPEN-01：无邮箱/手机号时的账户恢复

- 当前不提供密码找回、重置凭据或管理员恢复端点。
- OpenAPI 不声明任何恢复时长、一次性凭据期限或身份核验步骤。
- 确认后需单独 ADR、威胁模型、审计事件和正反例，再加入 runtime OpenAPI。

### OPEN-02：验证码、分层限流、渐进延迟与锁定

- `AccessService` 只定义可注入的 `AbuseProtection` 接口。
- 当前代码不内置 IP/设备/账户桶、次数、时间窗、冷却时间、验证码触发阈值或锁定值。
- runtime OpenAPI 保留统一 429 Problem Details 合同；是否触发及 `Retry-After` 数值由后续
  已批准策略提供。没有该策略不能据此宣称公网风控已完成。

### OPEN-03：权限申请是否必须先成为项目成员

- `AccessService` 只定义可注入的 `CapabilityRequestAdmissionPolicy`，不在 Router 或
  Repository 写死申请前置条件。
- 草案允许存储申请/审批结果，但 bootstrap 只在 active membership scope 内返回有效
  capability；因此未入项目时不会获得业务访问。
- 产品确认后，可将“提交时必须已有 membership”或其他规则实现为 admission policy，
  不修改 grant 的最小权限交集语义。

## 安全与日志约束

- 密码使用 scrypt + 随机 salt 存储；plaintext、password hash、session token 不进入审计、
  Problem Details 或日志。
- FastAPI 422 只返回错误类型、字段位置和安全消息，不回显 Pydantic 的原始 `input`/`ctx`。
- 审计 `safe_details` 使用字段 allowlist，不保存申请/审批自由文本、Authorization、token、
  cookie、请求体或客户端指纹。
- 401/403/409/422/429 都使用扁平 RFC 9457 扩展：`type/title/status/detail/code/request_id/retryable/details`。

## 后续决策影响

OPEN-01～03 的 Owner、选项、反例和验收样例获批前，本 ADR 保持 Draft；不得把默认 no-op
policy 当成生产安全结论，也不得在部署配置中自行发明数值。

