# 第一波门禁发现的合同冲突

审计日期：2026-08-17。本文只报告冲突，不修改任何领域状态机。

| Owner / 终端 | 冲突 | 门禁采用的规则 | 所需同步 |
| --- | --- | --- | --- |
| T02 认证/P18 | `T10-P18-P19-SECURITY-SPEC.md` 第 3.1～3.4 节仍把注册建模为 `PENDING → 管理员审批 → 自动 home_project` | UXF-02/03、PD-FE-01/02：用户名+密码直接注册为空账户；不审批注册、不自动创建项目；只审批加入项目和权限申请 | T02 的 runtime OpenAPI、迁移、Repository 和 E2E 禁止继承旧 PENDING/home_project 流程 |
| T03 P20 | PICO 方向文档允许多人、多设备、多机器人使用同一任务码，旧文案易被误读为云端任务“分派” | UXF-05～07：P20 不分派人员/设备/机器人；任务码仅归类，来源从上传 Manifest 只读发现 | P20 禁用字段合同负测必须覆盖 assignee/device/robot/start/end/pause/resume/topic |
| T04 上传/Manifest | PICO 文档以 `data_package_id` 为全局唯一幂等身份；当前合同同时暴露 `rollout_id` 与 `data_package_id`，两者的唯一性和兼容关系尚未在冻结 OpenAPI 中说明 | 不擅自改名或改状态机；恶意 Manifest 门禁已覆盖重复 key、大小/深度/节点/Topic/路径/selector 边界，并坚持任务码和 SHA 都不是包身份 | T04 在冻结 Schema 中写明 `data_package_id` 的唯一性及 `rollout_id` 的兼容语义，保留“同任务码多包、SHA 不跨包删除”反例 |
| T02/T10 公网安全 | 旧 T10 规格的 Real API E2E 是 `register → pending → approve` | 本轮预留主链固定为“注册空账户 → 申请加入项目/权限 → 审批申请”，没有账户审批步骤 | 第二波 Playwright fixture/selector/API adapter 必须按新主链实现 |

方向文件未形成的能力不进入当前 PASS：云平台不参与 PICO/机器人 100 Hz 实时控制闭环；机器人录制状态机与 P20 云端任务状态机不是同一个状态机。

审计期间曾出现 Manifest Workflow input fixture 漂移、Temporal sandbox 连带导入 PyJWT/cryptography、annotation 第 20 项迁移的中间版本冲突，以及聚合 OpenAPI 漂移；对应 owner 在最终复核前完成收敛。当前从零应用 20 项迁移并通过 checksum 检查，正式 Worker healthy，外部集成 6 PASS，replay/kill 5 PASS，OpenAPI drift gate PASS。本文不把已收敛问题继续列为当前阻断项。
