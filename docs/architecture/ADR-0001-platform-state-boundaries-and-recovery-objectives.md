# ADR-0001：平台状态边界、恢复目标与 Temporal 模式

- 状态：已接受
- 决策日期：2026-08-28
- 实施任务：DR0-01；BAK2-05（Temporal 策略 adapter 与未完成任务 inventory）；BAK2-06（整站备份创建、独立复验与 catalog 查询）
- 机器可读合同：[`platform-state-inventory.yaml`](platform-state-inventory.yaml)
- 取代方式：只能由新 ADR 显式取代；不允许通过 Helm 值或运维脚本静默改变状态边界

## 背景

当前平台已把大部分业务事实外置到 PostgreSQL、S3/MinIO 和 Temporal，但没有一份可执行的全站恢复合同。领域数据集导出、Helm 回滚和单次 dump 证据都不能代替这份合同。

以下工作树事实是本决策的输入：

- `backend/src/hc_data_platform/core/config.py` 配置 PostgreSQL、对象存储、Temporal 和应用主密钥依赖；
- `backend/src/hc_data_platform/security/outbox.py` 与 Temporal workflow ID 实现业务任务的持久去重和租约；
- `backend/migrations/security/008_audit_integrity_chain.sql` 把 P19 审计链作为 PostgreSQL 内的业务事实；
- `deploy/helm/hc-data-platform` 部署无本地业务持久卷的 API/Worker，并从外部 Secret 注入凭据；
- `compose.dev.yaml` 中的单节点 Temporal/MinIO/PostgreSQL 仅是本地开发拓扑；
- `backend/tests/load/CAPACITY-REPORT.md` 确认 5 TB/日 + 30% 裕量仍未通过，不得将开发机测量当成生产容量证据。

## 决策

### 1. 恢复单元和事实源

一个恢复单元由稳定的 `platform_id` 和 `environment_id` 唯一标识。其必需状态域固定为：

1. PostgreSQL 业务库，包括权限、P19 审计链、Outbox、领域投影、迁移账本和后续的平台目录；
2. 对象存储的精确对象版本，包括 Raw、上传包、Lance、发布产物、Manifest 和持久预览；
3. Temporal 外部 namespace 的工作流历史、schedule 和在途任务身份；
4. GitOps/Helm 部署配置及脱敏后的有效公开配置；
5. Secret Manager/KMS 中的主密钥、认证密钥和外部系统凭据依赖；
6. 镜像仓库和发布仓库中的精确镜像 digest、Chart、release manifest、SBOM 和签名；
7. 独立备份仓库中的备份集、清单、校验和签名；
8. 运行日志后端，以及身份提供者、SMTP、自动标注网关等外部依赖。

Temporal 内部表不是业务 PostgreSQL 备份的一部分；运行日志不能代替 P19 审计事实；领域数据集导出不能代替任一上述状态域。

### 2. 恢复目标

以下值是工程和生产发布的强制基线，变更必须取代本 ADR：

| 场景/状态                     |                    RPO |                                          RTO/停写目标 |
| ----------------------------- | ---------------------: | ----------------------------------------------------: |
| 计划内跨服务器迁移            |                      0 |                                    停写不超过 30 分钟 |
| PostgreSQL 灾难恢复           |         不超过 15 分钟 |                                         不超过 2 小时 |
| 对象存储灾难恢复              |          不超过 1 小时 | 按实际引用对象字节和实测带宽验收；基础功能不设固定规模下限 |
| Temporal 工作流状态           |         不超过 15 分钟 |                                         不超过 2 小时 |
| 配置、Secret/KMS 与精确发布物 |            0（版本化） |                                        不超过 30 分钟 |
| 独立备份仓库中的已完成备份集  | 0（对象锁 + 异地复制） |                                 不超过 4 小时恢复可读 |
| 普通运行日志                  |          不超过 1 小时 |           不超过 4 小时恢复查询；缺失不代表审计可丢失 |
| 单个应用节点故障              |                      0 |                                      5 分钟内恢复容量 |

独立性能基准模型是每日 5,000,000,000,000 bytes，并保留 30% 余量；全链路最低持续吞吐为 75,231,481.48 bytes/s。同时保留单个 20 GiB rollout 和 50 并发上传会话的控制面模型。这些是生产吞吐尺度和发布阻断条件，不是基础备份、恢复或迁移的容量下限，也不是已通过声明；当前性能证据状态仍为 `NOT_PASSED`。基础功能按 inventory 的 `actual_referenced_bytes` 动态计算，100 MB 真实对象可以用于功能验收。

### 3. 恢复责任

| 角色                              | 责任                                                                             |
| --------------------------------- | -------------------------------------------------------------------------------- |
| Platform SRE / Recovery Commander | 发起和编排备份、恢复、维护栅栏、跨域 reconciliation 与是否开放写入的最终运维决策 |
| Database Operations               | PostgreSQL HA、物理基线、WAL/PITR、逻辑 dump、角色/扩展和迁移账本校验            |
| Object Storage Operations         | bucket 版本控制、对象锁、异地复制、精确版本 inventory 与对象恢复                 |
| Temporal Operations               | 外部 namespace 的 HA/SLA、官方备份或快照、schedule 和工作流恢复                  |
| Security/KMS Operations           | Secret/KMS 版本、包络密钥、break-glass 身份、签名和密钥恢复                      |
| Release Engineering               | GitOps 权威配置、不可变镜像/Chart/release manifest/SBOM/签名的可用性             |
| Observability Operations          | 中心日志后端、保留、快照、查询和告警链路                                         |
| Data Owner / Security Approver    | 审批恢复抽样策略、审计链结果和解除只读；不执行底层恢复命令                       |

每个生产环境必须把这些角色映射到可联系的值班组。映射缺失时，备份预检不得通过，不允许以应用开发者作为默认替代人。

### 4. Temporal 模式

生产 V1 固定为平台 Helm release 之外的 `external_managed` Temporal HA 服务，每个环境使用独立 namespace。平台备份只记录脱敏集群引用、namespace、Temporal 服务版本、schedule 和在途 workflow inventory；工作流历史由 Temporal 运维方依据其受支持的 HA/备份机制恢复。

- 本地 Compose 的 `temporalio/auto-setup` 是 `development_only`，不能产生生产恢复证据。
- 自建 Temporal 只能在后续 ADR 指定独立 HA 拓扑、数据库、精确版本、停服一致性快照和已验证恢复流程后启用。
- BAK2-05 必须对 `external_managed` 生成可验证策略结果，对未授权的 `external_self_hosted` 显式拒绝；禁止热 dump Temporal 内部表。
- 恢复后必须对 PostgreSQL 中的 job/Outbox/领域投影与 Temporal 的 open/closed workflow 做 reconciliation，不得根据单一事实源猜测成功。

### 5. 漏项和开放写入策略

备份清单必须对每个状态域标记 `included`、`external` 或 `not_applicable`。没有记录等同于失败。

- PostgreSQL、对象存储、Temporal、GitOps 配置、Secret/KMS、发布物或备份仓库任一必需域缺失，必须使备份或恢复预检失败。
- 外部服务必须记录 owner、SLA、身份/密钥指纹和重连方法；恢复时未就绪则保持只读。
- 普通运行日志是唯一可在合规政策允许时降级的域，但缺失必须写入恢复报告并告警。P19 审计事实不属于该例外。
- 任一校验不确定、owner 不可达、fencing 所有权不明或核心域未验证时，API/Worker 保持只读/停止，由 Recovery Commander 人工处置。

## 备份与恢复顺序约束

1. 在维护栅栏下为 PostgreSQL 和对象版本生成同一一致性坐标；Temporal 记录对应的 schedule/workflow inventory。
2. 恢复时先准备精确 release、Secret/KMS 和外部依赖，再恢复对象的精确版本，然后恢复 PostgreSQL，最后恢复/重连 Temporal。
3. 只读 API 完成引用到对象、迁移账本、审计链、权限、Outbox 和 workflow reconciliation 后才能启动写 Worker。
4. 先恢复备份记录的同版本平台，验证后再根据兼容矩阵向前升级；禁止直接把旧备份导入最新代码。

## BAK2-05 实施证据与保留边界

- 生产 adapter 只接受 `external_managed`：目标必须是非 loopback TLS，并使用 API key 或成对 mTLS 身份。`external_self_hosted` 在没有取代本 ADR 的独立 HA、精确版本、冷快照和恢复合同前稳定拒绝 `BACKUP_TEMPORAL_SELF_HOSTED_NOT_AUTHORIZED`；`development_only` 只允许无凭据的 loopback plaintext `development_probe`，不能创建生产 artifact。
- `TemporalSdkAdminAdapter` 只使用 Temporal 支持的 Namespace/Cluster/System、Schedule 和 Visibility API，稳定读取 namespace/cluster identity 指纹、服务版本、retention、已暂停 schedule 的安全定义 metadata，以及 `ExecutionStatus = "Running"` 的 workflow identity metadata。它不读取 workflow history、workflow/schedule payload、memo、search attribute 或 Temporal 内部数据库；清单明确记录 `internal_database_in_business_dump=false` 和 `workflow_histories_exported_by_platform=false`。
- 生产 policy 要求每个 schedule 已暂停，并以有界重试取得两次相同 inventory；schedule/open workflow 数量都有硬上限。严格 `hc-temporal-provider-recovery/v1` 合同绑定 cluster reference、namespace、identity hashes、精确服务版本、Temporal Operations owner、provider 官方 HA/backup 与恢复方法、RPO 不超过 900 秒、RTO 不超过 7200 秒、保护点/evidence hash、恢复 runbook 和有效期。缺失、过期、URI 携带 userinfo/query/fragment 或与 live identity 不符均 fail closed。
- `hc-temporal-backup-policy/v1` snapshot 使用 `repository_kms_only`，portable 使用 age 1.3.1 X25519；两者都在 PostgreSQL maintenance fence 内创建，使用 owner-only `0600` staging/receipt、no-overwrite publish、失败回滚、artifact/inventory hash 和当前 provider preflight 闭合。外部 `hc-temporal-backup snapshot-create/portable-create/verify/probe` 只从结构化环境读取 target、credential、TLS key、provider policy、PostgreSQL password 和 age identity。
- 真实本地 Temporal 1.25.2 / Python SDK 1.31.0 验证了支持 API 的 namespace/cluster/schedule/visibility 读取：测试临时创建一个携带 Secret sentinel 的 paused schedule 和一个 open workflow，inventory 计数分别精确增加 1，artifact/probe 中 sentinel 为零，清理后恢复基线。该环境只产生 `development_probe_only` 和协议兼容证据；测试中注入该 client 走 managed 合同路径也不构成生产 provider 证据。
- 最终 non-root Temporal maintenance 镜像 UID/GID 65532，固定 age 1.3.1，digest `sha256:6dc7859b78d2f4ae561b0913c1d0178514a43046115dd6543b58807cc2d64632`、128,622,842 bytes，且不携带 `pg_dump`。RST3-03 已在 disposable PostgreSQL/MinIO/Temporal/Vault/kind 上完成 PostgreSQL/Outbox/领域投影与 Temporal 的九域只读 reconciliation；当前仍没有外部托管 Temporal 的真实 HA、备份、SLA、故障切换或恢复时长证据，也未执行双 Compose 真实对象 planned migration、生产 Kubernetes 多节点或 DR7。

## BAK2-06 实施证据与保留边界

- `hc-platform backup create` 在同一不可变 `hc-whole-backup-plan/v1` 下组合 PostgreSQL、对象、配置和 Temporal adapter，并为九个状态域生成闭合的 `hc-platform-checksums/v1` evidence。已有且 hash/plan 完全匹配的 receipt、加密 PostgreSQL artifact 和 repository checkpoint 可安全复用；冲突或不完整证据 fail closed。
- 完整清单只能以 `INTEGRITY_VERIFIED` 发布。独立仓库重新下载并验证 manifest、signature 和全部 artifact 后，catalog 才以同一事务追加 `CREATING → CREATED → INTEGRITY_VERIFIED`；`backup verify` 只能追加同状态 integrity fact。重复 create/verify 幂等，任何路径都不产生 `RESTORE_VERIFIED`。
- 真实 MinIO 验证了 versioning、COMPLIANCE Object Lock、未来 retention、SSE-KMS identity、5 MiB multipart checkpoint/续跑和实际异站复制；真实 Vault Transit 验证了不可导出的 Ed25519 key、精确 v1 签名、轮换后 v1 仍可用及 minimum version 提升后的拒绝。MinIO 使用 disposable static KMS secret、Vault 使用 dev mode，因此这些是 provider 协议证据，不是生产 KES/HSM、Vault HA/auto-unseal 或 IAM 分权证据。
- 一次性 kind v1.32.2 集群实际运行 Helm 生成的 non-root `backup verify` Job：Secret plan、ConfigMap/Secret 环境和 PVC staging 进入 Pod，service-account token 自动挂载关闭；Vault 非 loopback HTTP 首次按合同拒绝，补上一次性 TLS/受信 CA 后同一 Job 连续两次成功，catalog 中该 operation 始终只有一条 integrity fact。该单节点 kind 证据不替代 SYS1 多节点故障、生产 CSI/PVC、workload identity 或 DR7。
- BAK2-06 只创建和独立验证完整备份集，没有执行恢复或跨域 reconciliation。外部托管 Temporal HA/backup/SLA、生产 WAL archive、生产 KMS/HSM/IAM 分权、日志后端/外部依赖证据生产者、独立 5 TB/day 性能容量和真实隔离恢复仍是 RST3/OBS5/REL6/DR7 的增强发布阻断项；固定 5 TB 数据量不再阻塞基础恢复。

## RST3-01 实施证据与保留边界

- `hc-platform restore plan` 已实现只读恢复预检。签名清单先完成 backup/source/target、精确 release、PostgreSQL major、signer/KMS exact version 绑定；仓库在此阶段只读取 manifest/signature，不下载业务 payload。
- 新备份签名记录源 PostgreSQL 名称和 `pg_database_size()`。planner 分别对 PostgreSQL 逻辑大小、对象总字节和 staging artifact bytes 计算 30% 裕量，并拒绝缺容量证据的旧 V1、源/目标碰撞、非空数据库/对象前缀、versioning 关闭、部署 release/Temporal/依赖/KMS readiness 漂移，以及 API writes/Worker fence 未关闭。
- disposable PostgreSQL 16.10 template0 空库与 versioned MinIO 验证成功、错 release、错 KMS、非空库、非空 prefix 和动态容量不足均零恢复写入；最终用户对象和目标 prefix 均回到 0。该证据只验证 planner 与只读 provider API，不是 5 TB/day 性能、生产 quota/KMS/IAM、托管 Temporal SLA 或恢复耗时证据。
- 成功计划固定 `dry_run=true`、`writes_enabled=false`、`payloads_downloaded=false`，只产生 deterministic checkpoint。RST3-01 没有 restore execute、reconciliation、服务开放写入或 `RESTORE_VERIFIED` 代码路径。

## 后果

- DR0-02 与后续 manifest schema 必须消费机器可读清单，不得另维护一份冲突的状态列表。
- BAK2-06 和 RST3-01 完成不等于 Temporal provider SLA、生产备份基础设施或恢复能力完成。目前只证明完整备份和只读计划，生产容量、Temporal 供应商 HA/备份/SLA、外部 owner 值班映射和真实恢复时间都还没有实证；它们继续阻断生产发布和 `RESTORE_VERIFIED`。
- 本 ADR 冻结工程目标，不声称灾备能力已完成。只有 DR7 的真实隔离恢复和故障演练可以产生该声明。
