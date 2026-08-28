# 数据平台整站备份恢复、多机高可用、日志与滚动升级实施计划

> 基线日期：2026-08-28  
> 计划状态：待评审，所有工程任务初始为“未开始”  
> 适用范围：当前 `hc_DataPlatform` 工作树；生产目标为 Kubernetes/Helm，多机不以 Docker Compose 为生产方案  
> 参考实现：`QuantumNous/new-api` 主线及固定审计提交 `e468b73915e5028e9849de62c5018a0faa203012`  
> 重要结论：本计划实现的是**整个平台**的备份、恢复和迁移，不把现有“数据集导出”误认为整站备份，也不把“检查新版本”误认为热更新。

## 1. 目标和完成口径

本计划完成后，平台必须具备以下能力：

1. 将一个服务器或集群中的 PostgreSQL 业务数据、对象存储数据、平台配置、密钥依赖和版本信息形成可验证的备份集。
2. 在一台全新服务器或一个全新 Kubernetes 集群中，先部署无数据的同版本平台，再导入备份并恢复为可用平台。
3. 支持计划内跨服务器迁移：对象预复制、短暂停写、最终增量同步、恢复验证、切换流量和原站保留回退窗口。
4. 支持多个 API、Frontend、Worker、Media Worker 实例跨机器运行；任何单个应用节点退出都不丢业务状态、不重复执行不可重入任务。
5. 提供可搜索的运行日志、不可篡改审计日志、节点与版本视图、备份/恢复历史和告警。
6. 支持应用版本的零停机或低停机滚动升级、升级前检查、数据库兼容门禁、失败自动停止/回滚；不在运行中的 API 容器内自我替换二进制。
7. 定期执行恢复演练。完整性校验只允许标记为 `INTEGRITY_VERIFIED`；只有真实恢复到隔离环境并通过校验的备份，才允许标记为 `RESTORE_VERIFIED`。

### 1.1 暂定服务目标

以下数值用于先行设计，进入生产前必须由产品、运维和数据负责人签字确认：

| 场景                |    暂定 RPO |          暂定 RTO/停机 | 说明                                             |
| ------------------- | ----------: | ---------------------: | ------------------------------------------------ |
| 计划内跨服务器迁移  |           0 | 写入暂停不超过 30 分钟 | 大对象在停机前预复制；停机窗口只做最终同步和切换 |
| PostgreSQL 灾难恢复 |     15 分钟 |                 2 小时 | 需要连续归档/PITR，不以每日逻辑备份单独承诺      |
| 对象存储灾难恢复    |      1 小时 |           按数据量验收 | 需要版本控制、异地复制和带宽容量证据             |
| 配置和密钥恢复      | 0（版本化） |                30 分钟 | 密钥必须由独立 Secret/KMS 备份策略保证           |
| 应用节点故障        |           0 |       5 分钟内恢复容量 | 共享状态服务正常时，单节点故障不得造成数据丢失   |

## 2. 当前能力基线和缺口

| 能力                  | 当前工作树事实                                                                                         | 结论                               | 本计划补齐项                                                         |
| --------------------- | ------------------------------------------------------------------------------------------------------ | ---------------------------------- | -------------------------------------------------------------------- |
| 数据集导出            | P21 与 Publishing API 可导出发布数据集并下载任务产物                                                   | 已有领域级导出；不是整个平台备份   | 保持独立，不混入平台灾备语义                                         |
| PostgreSQL/MinIO 恢复 | `deploy/evidence/VALIDATION.md` 记录过一次专用 Schema dump/restore 和 MinIO 快照校验                   | 有验证证据，没有可复用产品能力     | CLI/Job、清单、加密、调度、校验、恢复编排和演练                      |
| 多副本                | Helm 默认 Frontend 2、API 2、Worker 2、Media Worker 1；已有 PDB 和滚动策略                             | 已具备应用层雏形                   | 跨节点调度、共享依赖 HA、节点目录、单例任务协调和故障演练            |
| Worker 去重           | Outbox 使用 claim token/lease 和 `FOR UPDATE SKIP LOCKED`；Temporal workflow ID 有去重约束             | 领域任务已有良好基础               | 保留现有机制；只为平台维护任务增加独立协调，不再造一套领域队列       |
| 日志与审计            | P19 有审计查询、保留、Legal Hold、脱敏 JSONL 导出和哈希链；部署有 OTLP 配置                            | 审计较完整，运行日志仍缺中心化闭环 | 结构化日志、Collector、存储查询、告警、节点/版本关联                 |
| 升级                  | Helm 有 `pre-install,pre-upgrade` migration Job、RollingUpdate、digest release manifest 和回滚演练脚本 | 有部署骨架，没有完整安全升级控制面 | expand/contract、兼容矩阵、预检、备份门禁、canary、自动停止和版本 UI |
| 全量恢复              | 未发现将 PostgreSQL、对象、Temporal 策略、配置和密钥作为一个备份集恢复的 API/CLI                       | 不具备                             | 本计划 P0 核心交付                                                   |

说明：P21 在当前工作树可见，但其文件是否已进入正式发行版本应由发布清单确认；任何 UI 存在都不能替代生产部署证据。

## 3. 对 `new-api` 的借鉴结论

参考资料：

- [new-api 仓库与多机部署注意事项](https://github.com/QuantumNous/new-api)
- [官方集群部署文档](https://github.com/QuantumNous/new-api-docs-v1/blob/main/content/docs/en/installation/deployment-methods/cluster-deployment.mdx)
- [主进程中的配置同步和系统任务启动](https://github.com/QuantumNous/new-api/blob/e468b73915e5028e9849de62c5018a0faa203012/main.go#L108-L152)
- [版本检查前端实现](https://github.com/QuantumNous/new-api/blob/e468b73915e5028e9849de62c5018a0faa203012/web/src/features/system-settings/maintenance/update-checker-section.tsx#L48-L92)
- [Docker Compose 示例](https://github.com/QuantumNous/new-api/blob/e468b73915e5028e9849de62c5018a0faa203012/docker-compose.yml)

| `new-api` 做法                                                               | 本平台决策                                                                             | 原因                                                                               |
| ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| 所有节点共享主数据库和 `SESSION_SECRET`；共享 Redis 时再共享 `CRYPTO_SECRET` | 采用“所有持久状态外置、所有节点共享同一事实源和同一认证/加密密钥”的原则                | 无状态应用节点才能任意扩缩容和故障转移                                             |
| `NODE_NAME`/hostname 节点身份、节点心跳、版本/资源状态列表                   | 采用并扩展为 `instance_id + node_name + role + release_id + pod/node metadata`         | 运维需要知道每个副本运行的版本、角色和最后心跳                                     |
| 主节点负责计划任务，数据库锁/租约避免多主重复执行                            | 只借鉴其“租约 + owner + heartbeat + fencing”模型                                       | 本平台领域任务已经使用 Temporal/Outbox，不能再造重复队列；租约只用于平台级维护任务 |
| 周期从数据库重载 Options                                                     | 仅用于明确标记为可热加载的运行参数，采用版本号和轮询兜底                               | Secret、数据库连接、镜像和 Schema 不允许未经重启动态替换                           |
| SQLite/共享 SQL、可选 Redis 的多种拓扑                                       | 生产只支持共享高可用 PostgreSQL、对象存储和 Temporal；暂不为了“像参考项目”而引入 Redis | 当前系统没有必须依赖 Redis 的跨节点事实；新增依赖必须有容量或功能证据              |
| 文件日志与数据库业务日志                                                     | 不照搬文件轮转；Kubernetes 使用 JSON stdout/OTLP，审计继续独立存储                     | 容器本地文件会随节点消失，且不适合跨机器检索                                       |
| 前端请求 GitHub latest release 并打开发布页                                  | 只借鉴“版本可见性”；升级必须由受信发布控制器/GitOps 执行                               | 该实现是更新检查，不是自动热更新，也没有数据库兼容和回滚编排                       |
| 当前仓库未提供本平台所需的 PostgreSQL + 对象存储 + Temporal 整站备份恢复闭环 | 不照搬，按本平台状态边界独立实现                                                       | 两个平台的数据规模、对象引用和工作流一致性要求不同                                 |

## 4. 目标架构

```text
                         ┌────────────────────────────┐
Users ── LB/Ingress ───▶ │ Frontend/API replicas      │
                         │ no local durable state      │
                         └──────────────┬─────────────┘
                                        │
                 ┌──────────────────────┼──────────────────────┐
                 ▼                      ▼                      ▼
       ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
       │ PostgreSQL HA    │   │ Object Store HA  │   │ Temporal HA      │
       │ business/outbox  │   │ immutable objects│   │ workflow history │
       └────────┬─────────┘   └────────┬─────────┘   └────────┬─────────┘
                │                      │                      │
                └─────────────┬────────┴──────────────┬───────┘
                              ▼                       ▼
                    ┌──────────────────┐    ┌──────────────────┐
                    │ Worker replicas  │    │ Media workers    │
                    └──────────────────┘    └──────────────────┘

  Secret/KMS ─────▶ all workloads      OTel Collector ─────▶ Logs/Metrics/Traces
  GitOps/Release Controller ────────▶ Helm rollout, preflight, canary, rollback
  Backup Controller/CLI ────────────▶ backup repository + isolated restore target
```

生产约束：

- Docker Compose 只用于单机开发和最小演练，不宣称多机高可用。
- API/Worker Pod 不挂载承载业务事实的本地持久卷。
- PostgreSQL、对象存储、Temporal、Secret/KMS 和备份库都必须独立定义 HA/备份 SLA。
- 恢复控制器必须运行在被恢复平台之外；平台数据库损坏时，不能依赖平台自身 API 执行恢复。

## 5. 状态边界和备份策略

### 5.1 必须纳入备份合同的状态

| 状态域                                       | 事实源                      | 备份方式                                                                  | 恢复注意事项                                                           |
| -------------------------------------------- | --------------------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| 业务元数据、权限、审计、Outbox、平台配置版本 | PostgreSQL 业务库           | `pg_dump --format=custom` 逻辑备份；生产同时启用物理基线 + WAL/PITR       | 先恢复到同主版本 PostgreSQL；迁移账本和审计链必须校验                  |
| 原始文件、Lance/发布产物、预览、清单         | S3/MinIO 兼容对象存储       | 版本控制 + 跨站复制；便携模式使用分片、加密对象包                         | 保留 logical key、version id、size、ETag/sha256；不能只备份数据库引用  |
| 运行中工作流                                 | Temporal                    | 优先使用外部/托管 Temporal 的 HA 与官方备份；自建时只允许停服后一致性快照 | 不把 Temporal 内部表混入业务库逻辑 dump；恢复必须匹配 Temporal 版本    |
| 部署配置                                     | Git/Helm values、ConfigMap  | 导出脱敏有效配置、版本与 digest；GitOps 仓库是权威源                      | 环境地址可重映射，业务语义配置必须版本一致                             |
| 密钥与数据源凭据主密钥                       | Secret Manager/KMS          | 独立版本化备份，或使用 KMS envelope 生成加密密钥包                        | `HC_DATA_SOURCE_CREDENTIAL_KEY` 等主密钥丢失会让数据库内密文永久不可用 |
| 运行日志                                     | 日志后端                    | 独立快照/保留策略，默认不阻塞业务恢复                                     | 审计日志属于业务库且必须恢复；普通诊断日志可按合规需求选择             |
| 发布物                                       | Registry + release manifest | 保存镜像 digest、Chart、迁移校验和和 SBOM                                 | 恢复时先拉起备份记录的精确版本，再做前向升级                           |

### 5.2 两种备份模式

1. `portable`：真正可搬走的离线备份。包含数据库 dump、加密配置/密钥包、对象清单和分片对象包，适合隔离网、新服务器迁移和长期归档。
2. `snapshot`：备份集记录 PostgreSQL 快照/PITR 坐标、对象版本或复制点和外部 Secret/Temporal 引用，适合同云快速灾备。

对象量达到 TB 级后，不生成一个巨大 ZIP。便携备份按固定上限分片，可断点续传、并行校验和单片重试；清单始终独立、签名并可先下载。

### 5.3 备份集格式 `hc-platform-backup/v1`

```text
backup-<backup_id>/
├── manifest.json                 # 格式、来源、快照坐标、数量、兼容范围
├── db/platform.dump              # PostgreSQL custom dump
├── objects/inventory.jsonl.zst   # key/version/size/hash/storage class
├── objects/parts/*.tar.zst.age   # portable 模式可选，分片且加密
├── temporal/policy.json          # 外部集群引用或自建快照说明
├── config/public.yaml            # 可公开的有效配置，不含 secret
├── secrets/envelope.json         # KMS 引用或加密包元数据，不含明文
├── checksums.sha256
└── signature.sig
```

`manifest.json` 至少包含：

- `format_version`、`backup_id`、创建/完成时间和状态；
- 来源 platform/release ID、Git commit、Chart 版本、镜像 digest；
- PostgreSQL 版本、迁移列表与校验和、dump hash/PITR 坐标；
- 对象 bucket/prefix、版本控制状态、对象数、总字节数、清单 hash；
- Temporal 部署模式、namespace、服务版本和恢复策略；
- 公共配置 hash、必需 Secret 名称与指纹、KMS key reference；
- 备份工具版本、签名算法、验证结果和兼容范围；
- 每个状态域的 `included/external/not_applicable`，禁止静默漏项。

备份清单和签名必须存放在独立备份库；主库中的 catalog 只是可重建索引，不能是唯一副本。

备份状态固定为 `CREATING → CREATED → INTEGRITY_VERIFIED → RESTORE_VERIFIED`，失败分支为 `FAILED/CORRUPT/EXPIRED`。状态只能追加验证事实，不能把失败结果覆盖成成功；一次新的校验或恢复演练生成新的 operation 和证据引用。

## 6. 一致性、恢复和迁移流程

### 6.1 V1：计划内冷一致性备份

1. 预检目标库容量、对象版本控制、备份库写权限、KMS、Temporal 状态和当前 release。
2. 创建维护操作和 fencing token；Ingress/API 进入 `READ_ONLY_MAINTENANCE`，所有新写请求和新预签名上传授权返回稳定的 `503 PLATFORM_MAINTENANCE`。
3. 暂停平台计划任务和 Temporal schedule，停止新 Outbox claim；等待已 claim 任务完成或 lease 过期。Temporal 工作流按领域选择完成、暂停或记录待重建清单。
4. 等待已签发上传 URL 到期，或在对象存储策略层封住写入；校验未完成 multipart、临时对象、孤儿对象以及业务表到对象引用的不可变边界。未引用的新对象可以不进入备份，但维护坐标之后不得产生新的数据库引用。
5. 将所有会写业务状态的 Worker/Media Worker/维护控制器缩容到 0，或由预检证明其最新 fencing token 已失效；writer inventory 不为零时中止备份。
6. 在同一一致性窗口生成 PostgreSQL dump/PITR 坐标和对象版本清单；portable 模式复制清单指定的精确版本。
7. 生成校验和与签名，在隔离进程中执行 `verify`；成功后只将状态标记为 `INTEGRITY_VERIFIED`。
8. 恢复各类副本、调度和 Outbox claim，退出只读模式；记录完整审计事件和停写时长。

任何步骤失败都必须安全释放维护状态；若无法确认 fencing token 所有权则保持只读并要求人工处理，不能冒险恢复写入。

### 6.2 在全新服务器恢复

1. `restore plan` 只读检查：目标必须为空或是显式命名的临时恢复实例；检查容量、版本、域名、证书、KMS、外部依赖和备份签名。
2. 部署备份清单记录的**精确平台版本**，保持 API/Worker 副本为 0 或只读。
3. 准备同一 Secret/KMS key；若要更换主密钥，必须在源端解密后按明确映射重新加密，禁止猜测或自动生成替代密钥。
4. 恢复对象的精确版本到暂存 bucket/prefix，完成全量 hash、数量和字节数校验后再切换目标别名。
5. 恢复业务 PostgreSQL；确认扩展、角色、迁移账本和约束均匹配，不立即执行新版本迁移。
6. 按 Temporal 模式恢复外部 namespace 连接、官方快照，或依据领域状态重建允许重建的未完成工作流。
7. 以只读模式启动 API，执行数据库引用→对象存在性、审计哈希链、权限、发布清单、Outbox 和工作流 reconciliation。
8. 启动 Worker，运行合成 smoke；人工确认后解除写入。恢复完成后再单独执行平台升级。

禁止“把旧备份直接导入最新代码然后让启动迁移自动修复”。支持路径必须是：**恢复同版本 → 验证 → 按兼容矩阵逐步升级**。

### 6.3 跨服务器低停机迁移

1. 新服务器部署同版本空平台并完成基础设施预检。
2. 源站在线做 PostgreSQL base backup/WAL 同步和对象版本预复制；目标保持不可写。
3. 进入维护窗口，停止新写和新任务，等待任务栅栏。
4. 应用最终 WAL/逻辑增量和对象增量，记录最终一致性坐标。
5. 在目标只读验证；降低 DNS TTL 后切流量，解除目标写入。
6. 源站保持只读且保留约定回退窗口。目标一旦产生新写，不能直接把流量切回旧源；必须执行反向同步或故障迁移流程。

## 7. 平台控制面设计

### 7.1 新增最小数据模型

| 表/资源                             | 用途                                  | 关键约束                                                          |
| ----------------------------------- | ------------------------------------- | ----------------------------------------------------------------- |
| `platform_instances`                | 节点心跳、角色、版本和 readiness      | `instance_id` 唯一；90 秒无心跳为 stale；历史按保留期清理         |
| `platform_maintenance_operations`   | 维护状态、owner、lease、fencing token | 同一环境只允许一个 active 操作；所有续租/完成都校验 owner + token |
| `platform_backup_catalog`           | 备份清单的可重建索引                  | 只保存清单 URI/hash/状态，不保存明文 Secret                       |
| `platform_restore_runs`             | 恢复计划、步骤、验证结果              | 默认只由外部 CLI/Job 写入；不可通过普通在线 API 执行破坏性恢复    |
| `platform_release_history`          | 部署、兼容、canary、回滚历史          | release ID + image digest 不可变                                  |
| `platform_runtime_config_revisions` | 明确可热加载配置的版本                | 配置项有 allowlist、schema、revision、审计和回滚版本              |

迁移使用独立 `platform` Schema 或现有治理 Schema 中清晰隔离的命名空间；平台管理员可读，backup/release operator 才可执行命令。恢复用 break-glass 身份不复用日常网页登录权限。

### 7.2 后端 API

| 方法与路径                                  | 用途                                                        | 约束                                                              |
| ------------------------------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------- |
| `GET /api/v1/platform/version`              | 当前 release、commit、镜像 digest、Schema/Temporal 兼容状态 | 所有环境可读最小信息，敏感部署细节仅管理员可见                    |
| `GET /api/v1/platform/instances`            | 节点/角色/版本/心跳/ready 状态                              | 支持 stale 筛选；节点资源值不得含主机 Secret                      |
| `GET /api/v1/platform/backups`              | 备份目录和验证状态                                          | 游标分页、脱敏 URI、platform admin/backup viewer                  |
| `POST /api/v1/platform/backups`             | 创建在线可安全执行的备份任务                                | 幂等键、二次确认、操作租约；该接口不能直接标记 `RESTORE_VERIFIED` |
| `POST /api/v1/platform/backups/{id}:verify` | 校验清单/签名/对象/数据库可读性                             | 异步执行，只能产生 `INTEGRITY_VERIFIED`；结果不可覆写             |
| `GET /api/v1/platform/operations/{id}`      | 查询备份/验证/发布操作                                      | 稳定状态机和 redacted logs                                        |
| `GET /api/v1/platform/releases`             | 查询受信发布元数据和兼容矩阵                                | 后端校验签名；浏览器不直接信任 GitHub latest                      |
| `POST /api/v1/platform/restores:plan`       | 生成脱敏恢复计划                                            | 只做预检和计划，不在线执行数据库覆盖                              |

完整恢复、目标数据库覆盖、流量切换和发布应用不暴露为普通 Web API。

### 7.3 外部 CLI/Job

```text
hc-platform backup create --mode snapshot|portable --target <backup-repository>
hc-platform backup verify <backup-id> [--deep]
hc-platform backup list
hc-platform restore plan <backup-id> --target <environment>
hc-platform restore execute <plan-id> --confirmation-file <signed-approval>
hc-platform restore verify <restore-id>
hc-platform migrate-server plan|execute|verify
hc-platform release preflight <release-id>
hc-platform release apply <release-id>
hc-platform release rollback <release-id>
```

所有命令必须支持 `--dry-run`、结构化结果、request/operation ID 和可恢复 checkpoint。破坏性命令拒绝宽泛目标、通配符、未解析变量和非空目标；需要显式环境 ID、签名批准文件与二次确认。

### 7.4 管理 UI

在平台设置中新增“系统运维”，而不是复用 P21 数据集导出：

- 版本：当前 release、commit、镜像 digest、Schema 版本、可升级版本和兼容结果；
- 节点：instance、角色、版本、心跳、ready、所在 Kubernetes node/zone；
- 备份与灾备：备份目录、验证/演练状态、RPO/RTO、最近失败原因；
- 日志：按 request/operation/workflow/node/release 查询脱敏运行日志；
- 升级：只展示预检和由外部控制器执行的进度，不把集群管理员凭据交给浏览器。

## 8. 多机高可用设计

### 8.1 节点身份和角色

- `instance_id` 默认取 Kubernetes Pod UID；本地运行使用启动时生成 UUID，不把可复用 hostname 当唯一身份。
- `node_name` 为人类可读名称，来源顺序为显式配置、Pod 名、hostname。
- 角色使用 `frontend/api/worker/media-worker/maintenance-controller`，不采用容易误解的通用 `master/slave`。
- 心跳每 30 秒 upsert，90 秒未更新标记 stale；保存 `release_id`、启动时间、Python/runtime、pod/node/zone 和 readiness 摘要。
- 节点列表只用于运维可见性，不作为分布式一致性的唯一依据。

### 8.2 共享状态和调度

- HTTP Session、认证签名、游标签名、数据源凭据主密钥在所有节点保持一致，来源为 Secret Manager。
- API 不依赖本机内存保存幂等、锁、上传完成态或权限事实。
- Outbox 继续使用 PostgreSQL claim/lease；Temporal 继续承载可重试领域工作流。
- 平台维护任务使用 `owner + lease_until + fencing_token`；数据库写操作必须携带最新 fencing token，旧 leader 即使恢复也不能继续提交。
- Kubernetes CronJob 只负责触发，任务事实和幂等仍落 PostgreSQL/备份库。
- 同一个任务绝不同时接入“Temporal 去重”和另一套数据库任务队列；每类任务在 ADR 中指定唯一 owner。

### 8.3 Kubernetes 必做项

- 为 Frontend、API、Worker 配置 topology spread/anti-affinity，至少跨两个 node；生产值不得继续为空。
- 为 API/Worker 增加 HPA 或基于队列/lag 的 KEDA 策略，设置 requests/limits 和扩缩容上限。
- Frontend/API 的 PDB 保持 `maxUnavailable: 0` 语义；Worker 按任务可中断性定义 PDB 和 termination grace period。
- 区分 `/live`、`/ready` 和 startup probe；readiness 必须检查必要共享依赖但避免级联抖动。
- SIGTERM 先摘流量，再停止 claim，等待请求/Activity checkpoint，超时后安全退出。
- 至少 2 API + 2 Worker 分布在不同机器；Media Worker 是否多副本按 GPU/任务租约专项验收。
- PostgreSQL、对象存储、Temporal 不使用单 Pod 无冗余配置来宣称平台 HA。

### 8.4 配置热同步

- 只有 allowlist 中的低风险运行参数支持热加载，例如限流阈值、展示开关和调度频率。
- 配置写入带 schema、revision、actor、reason 和生效范围；节点通过通知获取变更，30 秒轮询作为漏通知兜底。
- 每个节点上报 `applied_config_revision`；版本不一致超过阈值立即告警。
- 数据库 DSN、Secret、对象存储凭据、TLS、镜像和 Schema 变更必须走受控发布并重启 Pod。

## 9. 日志和可观测性

### 9.1 三类记录严格分离

| 类型       | 目标                         |                               默认保留 | 禁止项                                        |
| ---------- | ---------------------------- | -------------------------------------: | --------------------------------------------- |
| 运行日志   | 故障诊断、请求和任务关联     |    30 天热存储 + 90 天冷存储（待确认） | Token、Cookie、密码、完整对象正文、数据源凭据 |
| 指标/Trace | SLO、容量、跨服务延迟        | 指标 13 个月、Trace 7～30 天（待确认） | project/user/object key 等无限高基数标签      |
| 审计日志   | 谁在何时对什么执行了什么操作 |     延续 P19 retention/legal hold 策略 | 删除、覆盖、未经审批导出、明文 Secret         |

### 9.2 运行日志字段

统一 JSON 字段：`timestamp`、`severity`、`service`、`instance_id`、`node_name`、`role`、`release_id`、`request_id`、`trace_id`、`operation_id`、`workflow_id`、`event_code`、`duration_ms`、`retry_count`、`error_type`。业务 scope 仅记录不可逆 hash 或受控 ID，不记录凭据和对象正文。

### 9.3 日志链路

`stdout/OTLP → OpenTelemetry Collector → Loki/OpenSearch 等中心日志后端 → Grafana/受控平台查询`。具体存储后端由容量和现有基础设施 ADR 决定，应用代码只依赖 OTel/结构化合同。

告警至少覆盖：备份过期/失败、深度校验失败、节点版本漂移、stale instance、迁移失败、Outbox lag、Temporal task queue lag、对象复制 lag、审计链校验失败、5xx/429 异常、磁盘和数据库连接池饱和。

## 10. 平台版本和滚动升级

### 10.1 “热更新”的准确边界

本计划中的热更新是**外部控制器驱动的低/零停机滚动发布**，不是进程内替换代码：

- 镜像以不可变 digest 部署，release manifest 签名；
- 前端和 API 可 canary/rolling；Worker 按 Temporal build ID/任务兼容策略滚动；
- 数据库使用 expand → 应用滚动 → contract 的多版本兼容迁移；
- Schema 发生不可逆变化后，只能回滚应用到仍兼容的版本，不能盲目回滚数据库；
- 高风险升级必须同时有最新 `INTEGRITY_VERIFIED` 备份和处于有效期内的 `RESTORE_VERIFIED` 演练证据。

### 10.2 Release manifest

每个 release 固定：语义版本、Git commit、Chart 版本、所有镜像 digest、SBOM/签名、数据库迁移 from/to、支持的 PostgreSQL/对象存储/Temporal 版本、前一应用版本兼容窗口、功能开关、健康检查和回滚条件。

后端 `GET /version` 不再只依赖源码中的硬编码版本；构建时注入 release manifest，前后端、Worker 和 OTel `service.version` 必须一致。

### 10.3 发布步骤

1. 验证 release 签名、镜像、SBOM、依赖版本、容量和当前集群无 active maintenance operation。
2. 验证最近备份/演练门禁，创建 release operation 和审计。
3. 用 advisory lock 执行一次 expand migration；旧版与新版应用都必须通过兼容测试。
4. 发布 canary API/Worker，验证错误率、延迟、关键写链、Outbox/Temporal、审计和对象读写。
5. 指标满足窗口后滚动剩余实例；否则停止并回滚应用 digest。
6. 稳定观察期结束后再执行 contract migration；contract 阶段必须是单独 release gate。

升级检查可以像 `new-api` 一样展示新版本，但升级动作只允许 GitOps/受信 release controller 执行。

## 11. 分阶段实施任务

### 阶段 0：合同、威胁模型和恢复设计（1 周）

| ID      | 任务                                                    | 交付物                                    | 前置   | 验收                                                   | 优先级/状态 |
| ------- | ------------------------------------------------------- | ----------------------------------------- | ------ | ------------------------------------------------------ | ----------- |
| DR0-01  | 冻结状态域、RPO/RTO、数据规模、恢复责任和 Temporal 模式 | ADR + 状态清单                            | 无     | 每个状态域有 owner、backup/restore 方法和漏项失败策略  | P0/未开始   |
| DR0-02  | 冻结备份格式、安全、KMS、签名和备份库                   | `hc-platform-backup/v1` schema + 威胁模型 | DR0-01 | 明文 Secret 扫描为零；篡改/错版本/错目标均 fail closed | P0/未开始   |
| DR0-03  | 冻结维护状态机、租约/fencing 和任务 owner               | ADR + 状态图                              | DR0-01 | Temporal、Outbox、K8s Job、DB lease 无重复 owner       | P0/未开始   |
| REL0-01 | 冻结升级兼容矩阵和 expand/contract 规则                 | Release ADR                               | 无     | 任意相邻版本明确 DB/Temporal/API 兼容和回滚边界        | P0/未开始   |

### 阶段 1：版本身份、节点目录和维护栅栏（1～2 周）

| ID      | 任务                                         | 修改范围                                                       | 前置    | 验收                                                          | 优先级/状态 |
| ------- | -------------------------------------------- | -------------------------------------------------------------- | ------- | ------------------------------------------------------------- | ----------- |
| SYS1-01 | 构建并暴露统一 release identity              | backend/frontend/Worker build、Helm、OTel、`/platform/version` | REL0-01 | 同一 Pod 的 API/UI/日志/节点目录显示相同 digest/release       | P0/未开始   |
| SYS1-02 | 实现 instance heartbeat 和 stale 清理        | migration、repository、service、API、Helm env                  | DR0-03  | 30 秒心跳；kill Pod 后 90 秒 stale；新 Pod 是新 instance      | P0/未开始   |
| SYS1-03 | 实现 maintenance operation、lease 和 fencing | migration、middleware、Worker/Outbox hooks                     | DR0-03  | 旧 owner 续租失败后无法恢复写；只读状态覆盖所有命令路径       | P0/未开始   |
| SYS1-04 | 添加平台 capability、审计和脱敏错误合同      | security/P19/OpenAPI                                           | DR0-02  | viewer/operator/release/break-glass 权限互斥且跨 scope 不泄漏 | P0/未开始   |

### 阶段 2：备份创建和验证（2～3 周）

| ID      | 任务                                          | 修改范围                                            | 前置            | 验收                                                        | 优先级/状态 |
| ------- | --------------------------------------------- | --------------------------------------------------- | --------------- | ----------------------------------------------------------- | ----------- |
| BAK2-01 | 实现 manifest schema、签名、catalog adapter   | backend platform domain + backup repository adapter | DR0-02、SYS1-01 | golden manifest、向后兼容、篡改拒绝、catalog 可重建         | P0/未开始   |
| BAK2-02 | 实现 PostgreSQL dump/PITR adapter             | external CLI/Job                                    | SYS1-03         | 同版本空库恢复；迁移/约束/行数/hash 校验一致                | P0/未开始   |
| BAK2-03 | 实现对象 inventory、snapshot 和 portable 分片 | storage adapter/CLI                                 | BAK2-01         | 精确版本、断点续传、并发重试、缺片/错 hash 失败             | P0/未开始   |
| BAK2-04 | 实现配置/Secret dependency export             | Helm/Secret/KMS adapter                             | BAK2-01         | 无明文；缺少任一必需 key 时 restore preflight 失败          | P0/未开始   |
| BAK2-05 | 实现 Temporal 策略 adapter 和未完成任务清单   | Temporal/admin adapter                              | DR0-01          | 托管/自建两种模式有明确结果，不直接热 dump 内部表           | P0/未开始   |
| BAK2-06 | 实现 `backup create/list/verify` 与 API 查询  | CLI、K8s Job、OpenAPI                               | BAK2-02～05     | 中断可续跑；重复幂等；本阶段最高只标记 `INTEGRITY_VERIFIED` | P0/未开始   |

### 阶段 3：恢复、迁移和 reconciliation（2～3 周）

| ID      | 任务                                     | 修改范围                   | 前置    | 验收                                                 | 优先级/状态 |
| ------- | ---------------------------------------- | -------------------------- | ------- | ---------------------------------------------------- | ----------- |
| RST3-01 | 实现 `restore plan` 空目标/容量/兼容预检 | CLI                        | BAK2-06 | 非空目标、错 release、错 KMS、空间不足均无写入失败   | P0/未开始   |
| RST3-02 | 实现对象→数据库→Temporal→服务恢复编排    | external Job/CLI           | RST3-01 | 每步 checkpoint；失败不开放写；可从最后安全点恢复    | P0/未开始   |
| RST3-03 | 实现 reconciliation 和恢复报告           | backend read-only verifier | RST3-02 | DB 引用/对象/审计链/Outbox/工作流/权限全量或抽样门禁 | P0/未开始   |
| RST3-04 | 实现 planned migration 预复制和最终切换  | runbook + CLI              | RST3-03 | 5 TB 级目标用容量环境验证；RPO 0；停写符合目标       | P0/未开始   |
| RST3-05 | 编写裸机和 Kubernetes 恢复 runbook       | `deploy/runbooks`          | RST3-03 | 未参与开发的值班人员可按文档完成演练                 | P0/未开始   |

### 阶段 4：多机生产硬化（2 周，可与阶段 3 后半并行）

| ID     | 任务                                                 | 修改范围                | 前置       | 验收                                                 | 优先级/状态 |
| ------ | ---------------------------------------------------- | ----------------------- | ---------- | ---------------------------------------------------- | ----------- |
| HA4-01 | 配置跨 node/zone spread、PDB、probes、graceful drain | Helm                    | SYS1-02/03 | 任一应用 node drain 时关键链路持续，任务无重复副作用 | P0/未开始   |
| HA4-02 | 配置 API/Worker autoscaling 与容量门禁               | Helm/metrics            | SYS1-01    | 扩缩容不破坏 lease；峰值下 SLO 和 backlog 恢复达标   | P1/未开始   |
| HA4-03 | 审计进程内状态、文件和本地缓存                       | backend/frontend/deploy | SYS1-03    | 重启/换节点后 session、幂等、上传、任务均保持正确    | P0/未开始   |
| HA4-04 | 配置共享依赖的 HA/故障切换和 Secret 一致性           | infrastructure/runbook  | DR0-01     | PG/Object/Temporal 各执行主故障切换，应用自动恢复    | P0/未开始   |
| HA4-05 | 实现 allowlisted runtime config revision 同步        | backend/API             | SYS1-02    | 通知丢失后 30 秒内收敛；Secret 不能通过该通道修改    | P1/未开始   |

### 阶段 5：中心日志和运维 UI（1～2 周）

| ID      | 任务                                               | 修改范围                          | 前置             | 验收                                                       | 优先级/状态 |
| ------- | -------------------------------------------------- | --------------------------------- | ---------------- | ---------------------------------------------------------- | ----------- |
| OBS5-01 | 统一 JSON logging 和 redaction                     | backend/Worker/frontend gateway   | SYS1-01          | request→workflow→operation 可关联；Secret/PII 测试为零泄漏 | P0/未开始   |
| OBS5-02 | 部署 OTel Collector、日志后端、dashboard 和 alerts | Helm/observability                | OBS5-01          | kill 节点后日志仍可查；关键告警端到端送达                  | P0/未开始   |
| OBS5-03 | 将 backup/restore/release 事件接入 P19 审计        | platform domain/P19               | BAK2-06          | 事件不可改、可验证、按 capability 脱敏导出                 | P0/未开始   |
| UI5-01  | 实现版本、节点、备份、日志和升级状态 UI            | frontend/OpenAPI/generated client | SYS1-04、OBS5-02 | Mock-off E2E；普通管理员看不到 Secret/主机敏感细节         | P1/未开始   |

### 阶段 6：安全滚动升级（2 周）

| ID      | 任务                                                  | 修改范围                    | 前置             | 验收                                                   | 优先级/状态 |
| ------- | ----------------------------------------------------- | --------------------------- | ---------------- | ------------------------------------------------------ | ----------- |
| REL6-01 | 实现签名 release feed、compatibility API 和 preflight | release controller/backend  | REL0-01、SYS1-01 | 伪造/降级/不兼容 release 被拒绝                        | P0/未开始   |
| REL6-02 | 拆分 expand/contract migration 并加 advisory lock     | migrations/Helm/CI          | REL0-01          | old+new 双版本集成；并发升级只有一个迁移 owner         | P0/未开始   |
| REL6-03 | 实现 API/Frontend canary 和指标门禁                   | GitOps/Helm                 | OBS5-02、REL6-01 | 注入 5xx/延迟后自动停止并回滚 digest                   | P0/未开始   |
| REL6-04 | 实现 Temporal Worker build/version routing            | Worker/Temporal deploy      | REL0-01          | 升级中旧 workflow 可回放，新 workflow 路由到兼容 build | P0/未开始   |
| REL6-05 | 实现 release history、UI 状态和手工批准               | backend/frontend/controller | REL6-01～04      | 每步可审计；浏览器没有 cluster-admin credential        | P1/未开始   |

### 阶段 7：灾备和故障演练（持续门禁）

| ID     | 演练                                  | 通过标准                                                                                         | 频率       | 优先级/状态 |
| ------ | ------------------------------------- | ------------------------------------------------------------------------------------------------ | ---------- | ----------- |
| DR7-01 | 备份恢复到全新隔离服务器              | 同版本启动、业务计数/对象 hash/审计链/权限/工作流 reconciliation 通过，并标记 `RESTORE_VERIFIED` | 每月       | P0/未开始   |
| DR7-02 | 跨服务器迁移并切流量                  | RPO 0、停写达标、旧源保持安全回退窗口                                                            | 每季度     | P0/未开始   |
| DR7-03 | API/Worker/K8s node 故障              | 无数据丢失、无重复不可重入副作用、容量在 5 分钟内恢复                                            | 每次大版本 | P0/未开始   |
| DR7-04 | PostgreSQL/Object/Temporal 单依赖故障 | fail closed、恢复后自动 reconciliation、告警到达                                                 | 每季度     | P0/未开始   |
| DR7-05 | 错误 release/canary/迁移失败          | 自动停止；应用回滚符合兼容矩阵；数据库不盲目 downgrade                                           | 每次大版本 | P0/未开始   |
| DR7-06 | 丢失/错误 Secret 和篡改备份           | 预检拒绝且不启动写服务；无静默生成替代 key                                                       | 每半年     | P0/未开始   |

## 12. 测试矩阵和发布门禁

| 层级            | 必测内容                                                                                   |
| --------------- | ------------------------------------------------------------------------------------------ |
| 单元            | manifest schema、hash/signature、状态机、lease/fencing、配置 revision、redaction、兼容矩阵 |
| PostgreSQL 集成 | dump/restore、PITR 坐标、迁移账本、审计链、并发 owner、旧 fencing token 拒绝               |
| 对象存储集成    | 版本清单、multipart、断点续传、缺对象、错 hash、bucket/prefix 重映射                       |
| Temporal 集成   | drain、在途 workflow、Worker build ID、重放、恢复后 reconciliation                         |
| Kubernetes E2E  | 跨两个 node 的副本、drain/kill/滚动、PDB、readiness、leader 丢失和网络分区                 |
| 安全            | 越权备份、路径/目标注入、清单篡改、KMS 权限、日志 Secret/PII 扫描、备份库最小权限          |
| 容量            | PostgreSQL dump/WAL、对象预复制/最终增量、日志吞吐、恢复耗时和备份窗口                     |
| Mock-off 浏览器 | 系统版本、节点、备份目录、验证状态、日志筛选、升级预检；无 MSW/fixture 回落                |

任何版本不得仅凭 `backup create` 成功发布“可恢复”能力。P0 发布证据必须包括：

- 一个全新目标环境的真实 restore；
- 数据库行数/关键 aggregate、对象总数/字节数/hash 和审计链一致；
- Secret/密钥依赖完整且无明文进入 artifact/log；
- 恢复后的真实登录、读写、上传、发布、审计和至少一个 Temporal 工作流 smoke；
- 演练结束后目标环境和临时备份按精确范围清理，无跨环境残留。

## 13. 建议代码边界

```text
backend/src/hc_data_platform/platform_ops/
  models.py schemas.py repository.py service.py router.py
  leases.py instances.py release.py backup_catalog.py reconciliation.py

backend/src/hc_data_platform/cli/
  backup.py restore.py migrate_server.py release.py

backend/migrations/platform/
  001_platform_instances.sql
  002_maintenance_operations.sql
  003_backup_restore_catalog.sql
  004_release_and_config_revisions.sql

deploy/helm/hc-data-platform/
  templates/backup-cronjob.yaml
  templates/maintenance-controller.yaml
  templates/otel-collector*.yaml
  values.schema.json

deploy/runbooks/
  backup.md restore-new-server.md planned-migration.md
  multi-node-failover.md rolling-upgrade.md disaster-recovery.md

frontend/src/pages/system-operations/
  version instances backups logs upgrades
```

最终目录需遵循仓库既有模块、迁移账本和路由装配规则；上面是职责边界，不授权绕开现有架构另建第二套应用。

## 14. 风险和防护

| 风险                                    | 防护                                                                           |
| --------------------------------------- | ------------------------------------------------------------------------------ |
| 只恢复数据库，实际对象丢失              | manifest 记录精确对象版本；恢复门禁验证 DB→object 引用和 hash                  |
| 加密主密钥遗漏                          | Secret 依赖清单 fail closed；定期在无源环境演练；不允许自动生成替代 key        |
| 热备份跨 PostgreSQL/对象不一致          | V1 先实现维护栅栏冷一致性；在线模式必须有不可变对象证明和一致性坐标            |
| Temporal 内部库被错误 dump              | 明确 managed/self-hosted 两种策略；只用官方兼容快照或领域重建，不混入业务 dump |
| 多节点重复执行                          | 现有 Temporal/Outbox 单一 owner；维护任务使用 lease + fencing；故障注入验证    |
| 迁移 Job 先改坏 Schema，旧 Pod 仍在运行 | 所有变更 expand/contract；旧+新双版本 contract test；contract 延后独立执行     |
| “自动更新”获得过高权限                  | 控制器在外部最小权限运行；浏览器只读状态；release/镜像签名和人工批准           |
| 中心日志泄密或高基数失控                | 字段 allowlist、redaction 测试、采样/基数预算、敏感查询 capability             |
| 备份 catalog 随主库一起丢失             | 独立备份库保存清单和签名；主库 catalog 仅为可重建索引                          |
| 切换目标写入后又直接回旧源              | runbook 明确 point of no return；需要反向同步或正式故障迁移                    |

## 15. 执行顺序和首批落地切片

建议按以下关键路径执行：

`DR0/REL0 合同 → release identity + instance heartbeat → maintenance fencing → backup manifest/CLI → 新服务器 restore → 多机故障演练 → 中心日志 → canary/滚动升级 → 持续 DR 演练`

首个可合并切片只做四件事，不先做大而全的管理 UI：

1. `SYS1-01`：统一 release identity 和 `/api/v1/platform/version`；
2. `SYS1-02`：节点心跳/节点列表；
3. `SYS1-03`：维护模式、lease/fencing 和写请求拒绝；
4. `BAK2-01`：`hc-platform-backup/v1` manifest schema、签名和 golden tests。

第二个切片完成 PostgreSQL + 对象存储 portable backup；第三个切片必须在全新隔离环境完成一次真实 restore。通过第三个切片后，平台才可以对外声明“支持整站导出、迁移和恢复”。

## 16. Definition of Done

只有同时满足以下条件，本计划才算完成：

- 整站备份不是手工命令集合，而是有版本化合同、CLI/Job、状态、审计、签名和恢复演练的产品能力；
- 新服务器无需旧服务器本地磁盘即可恢复，所有外部依赖和密钥都有显式清单；
- 至少两个物理/虚拟节点承载应用副本，单节点故障和滚动升级期间关键业务链路通过；
- 日志可按 request/workflow/operation/node/release 关联查询，P19 审计完整且无 Secret 泄漏；
- 升级具备签名 release、兼容预检、备份门禁、canary、自动停止和安全回滚证据；
- 连续三次定期恢复演练通过，并达到最终签字的 RPO/RTO；
- 运维 runbook 由未参与实现的人员在隔离环境成功执行一次。
