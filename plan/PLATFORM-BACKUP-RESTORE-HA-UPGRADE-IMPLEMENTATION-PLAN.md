# 数据平台整站备份恢复、多机高可用、日志与滚动升级实施计划

> 基线日期：2026-08-28  
> 计划状态：待评审，所有工程任务初始为“未开始”  
> 适用范围：当前 `hc_DataPlatform` 工作树；生产目标为 Kubernetes/Helm，多机不以 Docker Compose 为生产方案  
> 参考实现：`QuantumNous/new-api` 主线及固定审计提交 `e468b73915e5028e9849de62c5018a0faa203012`  
> 重要结论：本计划实现的是**整个平台**的备份、恢复和迁移，不把现有“数据集导出”误认为整站备份，也不把“检查新版本”误认为热更新。
> 2026-08-31 产品决策：RST3-04/DR7-02 的功能迁移容量按本次实际引用字节动态计算，不再设置固定 5 TB/6.5 TB 下限；100 MB 是当前可重复功能演练样本，不是性能基准。历史 5 TB blocker 记录保留但已由本决策显式 supersede；HA4-02 的独立吞吐/持续容量基准不受此决策影响。

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
| DR0-01  | 冻结状态域、RPO/RTO、数据规模、恢复责任和 Temporal 模式 | ADR + 状态清单                            | 无     | 每个状态域有 owner、backup/restore 方法和漏项失败策略  | P0/已完成   |
| DR0-02  | 冻结备份格式、安全、KMS、签名和备份库                   | `hc-platform-backup/v1` schema + 威胁模型 | DR0-01 | 明文 Secret 扫描为零；篡改/错版本/错目标均 fail closed | P0/已完成   |
| DR0-03  | 冻结维护状态机、租约/fencing 和任务 owner               | ADR + 状态图                              | DR0-01 | Temporal、Outbox、K8s Job、DB lease 无重复 owner       | P0/已完成   |
| REL0-01 | 冻结升级兼容矩阵和 expand/contract 规则                 | Release ADR                               | 无     | 任意相邻版本明确 DB/Temporal/API 兼容和回滚边界        | P0/已完成   |

### 阶段 1：版本身份、节点目录和维护栅栏（1～2 周）

| ID      | 任务                                         | 修改范围                                                       | 前置    | 验收                                                          | 优先级/状态 |
| ------- | -------------------------------------------- | -------------------------------------------------------------- | ------- | ------------------------------------------------------------- | ----------- |
| SYS1-01 | 构建并暴露统一 release identity              | backend/frontend/Worker build、Helm、OTel、`/platform/version` | REL0-01 | 同一 Pod 的 API/UI/日志/节点目录显示相同 digest/release       | P0/已完成   |
| SYS1-02 | 实现 instance heartbeat 和 stale 清理        | migration、repository、service、API、Helm env                  | DR0-03  | 30 秒心跳；kill Pod 后 90 秒 stale；新 Pod 是新 instance      | P0/已完成   |
| SYS1-03 | 实现 maintenance operation、lease 和 fencing | migration、middleware、Worker/Outbox hooks                     | DR0-03  | 旧 owner 续租失败后无法恢复写；只读状态覆盖所有命令路径       | P0/已完成   |
| SYS1-04 | 添加平台 capability、审计和脱敏错误合同      | security/P19/OpenAPI                                           | DR0-02  | viewer/operator/release/break-glass 权限互斥且跨 scope 不泄漏 | P0/已完成   |

### 阶段 2：备份创建和验证（2～3 周）

| ID      | 任务                                          | 修改范围                                            | 前置            | 验收                                                        | 优先级/状态 |
| ------- | --------------------------------------------- | --------------------------------------------------- | --------------- | ----------------------------------------------------------- | ----------- |
| BAK2-01 | 实现 manifest schema、签名、catalog adapter   | backend platform domain + backup repository adapter | DR0-02、SYS1-01 | golden manifest、向后兼容、篡改拒绝、catalog 可重建         | P0/已完成   |
| BAK2-02 | 实现 PostgreSQL dump/PITR adapter             | external CLI/Job                                    | SYS1-03         | 同版本空库恢复；迁移/约束/行数/hash 校验一致                | P0/已完成   |
| BAK2-03 | 实现对象 inventory、snapshot 和 portable 分片 | storage adapter/CLI                                 | BAK2-01         | 精确版本、断点续传、并发重试、缺片/错 hash 失败             | P0/已完成   |
| BAK2-04 | 实现配置/Secret dependency export             | Helm/Secret/KMS adapter                             | BAK2-01         | 无明文；缺少任一必需 key 时 restore preflight 失败          | P0/已完成   |
| BAK2-05 | 实现 Temporal 策略 adapter 和未完成任务清单   | Temporal/admin adapter                              | DR0-01          | 托管/自建两种模式有明确结果，不直接热 dump 内部表           | P0/已完成   |
| BAK2-06 | 实现 `backup create/list/verify` 与 API 查询  | CLI、K8s Job、OpenAPI                               | BAK2-02～05     | 中断可续跑；重复幂等；本阶段最高只标记 `INTEGRITY_VERIFIED` | P0/已完成   |

### 阶段 3：恢复、迁移和 reconciliation（2～3 周）

| ID      | 任务                                     | 修改范围                   | 前置    | 验收                                                 | 优先级/状态 |
| ------- | ---------------------------------------- | -------------------------- | ------- | ---------------------------------------------------- | ----------- |
| RST3-01 | 实现 `restore plan` 空目标/容量/兼容预检 | CLI                        | BAK2-06 | 非空目标、错 release、错 KMS、空间不足均无写入失败   | P0/已完成   |
| RST3-02 | 实现对象→数据库→Temporal→服务恢复编排    | external Job/CLI           | RST3-01 | 每步 checkpoint；失败不开放写；可从最后安全点恢复    | P0/已完成   |
| RST3-03 | 实现 reconciliation 和恢复报告           | backend read-only verifier | RST3-02 | DB 引用/对象/审计链/Outbox/工作流/权限全量或抽样门禁 | P0/已完成   |
| RST3-04 | 实现 planned migration 预复制和最终切换  | runbook + CLI              | RST3-03 | 按实际引用字节和迁移模式动态预检；RPO 0；停写符合目标 | P0/已完成（功能合同与双 Compose 实跑） |
| RST3-05 | 编写裸机和 Kubernetes 恢复 runbook       | `deploy/runbooks`          | RST3-03 | 未参与开发的值班人员可按文档完成演练                 | P0/进行中   |

### 阶段 4：多机生产硬化（2 周，可与阶段 3 后半并行）

| ID     | 任务                                                 | 修改范围                | 前置       | 验收                                                 | 优先级/状态 |
| ------ | ---------------------------------------------------- | ----------------------- | ---------- | ---------------------------------------------------- | ----------- |
| HA4-01 | 配置跨 node/zone spread、PDB、probes、graceful drain | Helm                    | SYS1-02/03 | 任一应用 node drain 时关键链路持续，任务无重复副作用 | P0/已完成   |
| HA4-02 | 配置 API/Worker autoscaling 与容量门禁               | Helm/metrics            | SYS1-01    | 扩缩容不破坏 lease；峰值下 SLO 和 backlog 恢复达标   | P1/进行中   |
| HA4-03 | 审计进程内状态、文件和本地缓存                       | backend/frontend/deploy | SYS1-03    | 重启/换节点后 session、幂等、上传、任务均保持正确    | P0/已完成   |
| HA4-04 | 配置共享依赖的 HA/故障切换和 Secret 一致性           | infrastructure/runbook  | DR0-01     | PG/Object/Temporal 各执行主故障切换，应用自动恢复    | P0/已完成   |
| HA4-05 | 实现 allowlisted runtime config revision 同步        | backend/API             | SYS1-02    | 通知丢失后 30 秒内收敛；Secret 不能通过该通道修改    | P1/已完成   |

### 阶段 5：中心日志和运维 UI（1～2 周）

| ID      | 任务                                               | 修改范围                          | 前置             | 验收                                                       | 优先级/状态 |
| ------- | -------------------------------------------------- | --------------------------------- | ---------------- | ---------------------------------------------------------- | ----------- |
| OBS5-01 | 统一 JSON logging 和 redaction                     | backend/Worker/frontend gateway   | SYS1-01          | request→workflow→operation 可关联；Secret/PII 测试为零泄漏 | P0/已完成   |
| OBS5-02 | 部署 OTel Collector、日志后端、dashboard 和 alerts | Helm/observability                | OBS5-01          | kill 节点后日志仍可查；关键告警端到端送达                  | P0/已完成   |
| OBS5-03 | 将 backup/restore/release 事件接入 P19 审计        | platform domain/P19               | BAK2-06          | 事件不可改、可验证、按 capability 脱敏导出                 | P0/已完成   |
| UI5-01  | 实现版本、节点、备份、日志和升级状态 UI            | frontend/OpenAPI/generated client | SYS1-04、OBS5-02 | Mock-off E2E；普通管理员看不到 Secret/主机敏感细节         | P1/已完成   |

### 阶段 6：安全滚动升级（2 周）

| ID      | 任务                                                  | 修改范围                    | 前置             | 验收                                                   | 优先级/状态 |
| ------- | ----------------------------------------------------- | --------------------------- | ---------------- | ------------------------------------------------------ | ----------- |
| REL6-01 | 实现签名 release feed、compatibility API 和 preflight | release controller/backend  | REL0-01、SYS1-01 | 伪造/降级/不兼容 release 被拒绝                        | P0/已完成（实现）   |
| REL6-02 | 拆分 expand/contract migration 并加 advisory lock     | migrations/Helm/CI          | REL0-01          | old+new 双版本集成；并发升级只有一个迁移 owner         | P0/进行中（实现完成；0.1.1 双版本实跑待目标制品）   |
| REL6-03 | 实现 API/Frontend canary 和指标门禁                   | GitOps/Helm                 | OBS5-02、REL6-01 | 注入 5xx/延迟后自动停止并回滚 digest                   | P0/已完成（实现；真实候选演练归 DR7-05）   |
| REL6-04 | 实现 Temporal Worker build/version routing            | Worker/Temporal deploy      | REL0-01          | 升级中旧 workflow 可回放，新 workflow 路由到兼容 build | P0/进行中（实现完成；真实相邻 build/history 实跑待目标制品）   |
| REL6-05 | 实现 release history、UI 状态和手工批准               | backend/frontend/controller | REL6-01～04      | 每步可审计；浏览器没有 cluster-admin credential        | P1/已完成（实现）   |

### 阶段 7：灾备和故障演练（持续门禁）

| ID     | 演练                                  | 通过标准                                                                                         | 频率       | 优先级/状态 |
| ------ | ------------------------------------- | ------------------------------------------------------------------------------------------------ | ---------- | ----------- |
| DR7-01 | 备份恢复到全新隔离服务器              | 同版本启动、业务计数/对象 hash/审计链/权限/工作流 reconciliation 通过，并标记 `RESTORE_VERIFIED` | 每月       | P0/待外部演练（runbook/签名证据门禁已完成）   |
| DR7-02 | 跨服务器迁移并切流量                  | RPO 0、停写达标、旧源保持安全回退窗口                                                            | 每季度     | P0/本地双服务器功能演练已完成；生产等价季度复演独立保留   |
| DR7-03 | API/Worker/K8s node 故障              | 无数据丢失、无重复不可重入副作用、容量在 5 分钟内恢复                                            | 每次大版本 | P0/已完成（当前版本） |
| DR7-04 | PostgreSQL/Object/Temporal 单依赖故障 | fail closed、恢复后自动 reconciliation、告警到达                                                 | 每季度     | P0/待生产故障域演练（本地 protocol drill 已完成）   |
| DR7-05 | 错误 release/canary/迁移失败          | 自动停止；应用回滚符合兼容矩阵；数据库不盲目 downgrade                                           | 每次大版本 | P0/待真实候选演练（注入门禁/精确回滚实现已完成）   |
| DR7-06 | 丢失/错误 Secret 和篡改备份           | 预检拒绝且不启动写服务；无静默生成替代 key                                                       | 每半年     | P0/待独立外部演练（负向合同/签名证据门禁已完成）   |

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

Stage 2 按可独立验收的小切片依次完成 PostgreSQL、对象存储、Secret/KMS、Temporal 和最终 backup create/verify；Stage 3 必须在全新隔离环境完成一次真实整站 restore。只有整站恢复、reconciliation 和 DR7 证据全部通过后，平台才可以对外声明“支持整站导出、迁移和恢复”。

## 16. Definition of Done

只有同时满足以下条件，本计划才算完成：

- 整站备份不是手工命令集合，而是有版本化合同、CLI/Job、状态、审计、签名和恢复演练的产品能力；
- 新服务器无需旧服务器本地磁盘即可恢复，所有外部依赖和密钥都有显式清单；
- 至少两个物理/虚拟节点承载应用副本，单节点故障和滚动升级期间关键业务链路通过；
- 日志可按 request/workflow/operation/node/release 关联查询，P19 审计完整且无 Secret 泄漏；
- 升级具备签名 release、兼容预检、备份门禁、canary、自动停止和安全回滚证据；
- 连续三次定期恢复演练通过，并达到最终签字的 RPO/RTO；
- 运维 runbook 由未参与实现的人员在隔离环境成功执行一次。

## 17. 实施账本

### 2026-08-28｜Stage 1 checkpoint：SYS1-01 / SYS1-02

| 项目                       | 当前事实                                                                                                                                                                                                                        | 结论                                                                            |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| SYS1-01 release identity   | 后端、Worker、前端构建、Compose/Helm 注入、启动日志、`GET /api/v1/platform/version` 和 instance directory 共用严格 release manifest；前端导航构建铭牌显示 release ID 与 manifest digest，状态在模块初始化时只读取一次           | 实现与本地真实运行验证完成；尚无真实 Kubernetes Pod 验收，保持 `进行中`         |
| SYS1-02 instance directory | 新增 `platform.platform_instances`、30 秒 DB-clock heartbeat、90 秒 stale、7 天保留、API/Worker/media-worker 注册、受保护列表 API、allowlist metadata/readiness、Helm Downward API Pod UID/node/pod 注入                        | 实现与 PostgreSQL/Compose 故障验证完成；缺少真实 `kill Pod` 验收，保持 `进行中` |
| 迁移                       | fresh 临时库完成 101/101；已有且无漂移的 `hc_data_preview_acceptance_20260828` 由正常 runner 从 100 升至 101，状态 `current`                                                                                                    | 通过；未改写迁移历史或 checksum                                                 |
| 真实进程故障               | 停止一个 Compose media-worker 后，DB-clock 在约 105 秒将旧实例标为 stale；重启生成新 UUID，旧 stale 行保留；新实例约 30.68 秒后再次 heartbeat                                                                                   | 仅证明应用进程合同，不冒充 Kubernetes Pod/节点故障证据                          |
| API/安全                   | bearer + `platform.admin` 临时门禁；`stale`/`role` 过滤，最多 1000 行，`private, no-store`，错误和 readiness 不回传依赖详情/Secret                                                                                              | SYS1-02 边界通过；viewer/operator/release/break-glass 拆分与审计归 SYS1-04      |
| Helm/UI                    | API、Worker、media-worker 渲染 Pod UID、Pod name、Kubernetes node name；前端实际 dev release identity 可见；Helm lint/template 与前端生产 build 通过                                                                            | 静态/本地运行通过；不计作 Mock-off Kubernetes/UI E2E                            |
| 后端全门禁                 | 独立真实 PostgreSQL + MinIO internal endpoint + 仓库 Nginx browser edge + Temporal：`1005 passed, 2 xfailed, 0 skipped`，306.66 秒；xfail 为既有 BE12-001/BE12-008 release blockers                                             | 通过；xfail 继续阻塞最终 release，不隐藏、不删除                                |
| 静态门禁                   | Ruff format/check 通过；mypy `227 source files` 通过；`git diff --check` 通过；OpenAPI runtime/formal/client、Helm contract、前端 typecheck、`609 passed` 和 production build 通过                                              | 通过                                                                            |
| 测试隔离/清理              | 三个 task-owned PostgreSQL 数据库、disposable MinIO、browser edge 和专用 Docker network 在完整门禁后按精确名称删除；共享 PostgreSQL/MinIO/Temporal 和应用容器保持健康                                                           | 通过；临时数据不可恢复，共享数据未删除                                          |
| 环境阻塞                   | `kubectl` 无 current context，且当前 endpoint 不是可用 Kubernetes API；默认 `hc_data` 仍有既存 annotation 0010/0011 checksum drift 且缺 0012/platform，未被修补或改账本；本地应用容器当前显式使用无漂移的 preview acceptance DB | 真实 Pod 验收和默认开发库升级均不得伪造；Stage 1 最终签字前必须补齐             |

### 2026-08-28｜Stage 1 checkpoint：SYS1-03

| 项目                    | 当前事实                                                                                                                                                                                                                                                                 | 结论                                                                                        |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| PostgreSQL 持久化与时钟 | 新增第 102 个 forward-only migration：environment fence、每环境唯一活动 operation、单调 fencing sequence、database-clock lease、writer permit/inventory、append-only event；DB function 在提交期复核 environment/mode/epoch/expiry                                       | clean acceptance DB 正常由 101 升至 102，102/102 current；未改写默认漂移库或历史 checksum   |
| 状态机与旧 owner        | acquire/renew/takeover/transition/reconcile 全部用 owner、token、lease、state/version predicate；真实 PostgreSQL 验证接管产生更大 token，旧 owner 不能续租、transition 或 commit；跨环境 operation HTTP 查询/变更返回 404 且不产生 mutation                              | 本地 PostgreSQL 并发合同通过；Kubernetes 多节点断连/恢复待 HA4/DR7                          |
| 全写路径栅栏            | API command、Outbox claim、Temporal activity、Preview attempt、storage/preview maintenance loop、DB commit adapter 均取得且长任务自动续租 permit；预签名 multipart grant 把 API permit 保留到 grant TTL；opaque read session 在只读期间验证但不更新/撤销/audit           | 只读拒绝命令且保留无副作用查询；未来 backup/restore Kubernetes Job writer 由 BAK2/RST3 接入 |
| 环境隔离与真实进程      | Helm production/dev/staging/ci 和 Compose dev/test 使用精确且互异 environment ID；API/Worker/media-worker 显式运行于 `hc-local` 和 clean acceptance DB，readiness healthy，真实 422 command 的 released `api_command` permit 在 DB 可见；实例数为 API 1/Worker 1/media 2 | Compose 证据通过；不冒充 Kubernetes Pod/网络分区验收                                        |
| 安全与 API              | maintenance REST path 已进入公开安全矩阵并要求 bearer；控制 endpoint 仅绕过业务写栅栏且仍受认证，跨环境 fail closed；正式 OpenAPI、runtime schema 和 generated client digest 一致                                                                                        | 临时 `platform.admin` 边界通过；capability 细分和 P19 审计归 SYS1-04                        |
| 严格门禁                | 独立真实 PostgreSQL（102 migrations）+ disposable MinIO + 仓库 Nginx browser edge + live Temporal：`1018 passed, 2 xfailed, 0 skipped`；xfail 仍为 BE12-001/BE12-008；Frontend `609 passed`、typecheck、ESLint、client generation 和 production build 通过               | 通过；两个 release blocker 原样保留                                                         |
| 清理与限制              | task-owned DB、MinIO、browser edge 和 network 已按精确名称删除；共享依赖及应用保持健康；默认 `hc_data` 既存 annotation checksum drift 未触碰；当前无可用 Kubernetes context，storage inventory singleton task lease 和多节点 kill/reconnect 尚无证据                     | SYS1-03 保持 `进行中`，不得声明 HA 完成                                                     |

### 2026-08-28｜Stage 1 checkpoint：SYS1-04

| 项目                    | 当前事实                                                                                                                                                                                                                                                                                    | 结论                                                                                      |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| 平台 capability         | 新增 exact `platform.operations.read`、`platform.maintenance.operate`、`platform.release.operate`、`platform.maintenance.verify`、`platform.break_glass`；`platform.admin` 只保留只读兼容，不能作为运维命令 wildcard；project access request 对全部 `platform.*` fail closed                | viewer/operator/release/verifier/break-glass 的命令边界已分离，普通 tenant admin 不能越权 |
| 职责互斥与会话撤销      | 第 103 个 forward-only migration 在数据库约束每个 principal 最多一个 active operation capability；grant 变化原子递增 capability revision，并以 `PLATFORM_CAPABILITY_CHANGED` 撤销 active session；未自动授予任何高权限 capability                                                           | 真实 PostgreSQL 正/负向证明互斥、revision bump 和 session revoke                          |
| 操作授权与 break-glass  | BACKUP/OTHER 只接受 maintenance operator，MIGRATION/RELEASE 只接受 release operator，RESTORE/takeover/FAILED_READ_ONLY recovery/SUCCEEDED 只接受 break-glass，reconcile 只接受 verifier；break-glass 仍受状态机、owner/token/lease/fencing predicate 约束                                   | 高权限不绕过维护状态机；错 capability、错 operation kind 和旧 owner 均拒绝                |
| P19 append-only 审计    | request/acquire/renew/takeover/transition/reconcile/read/authorization denial/contract failure 均写全局 `access_control.audit_events`；成功 mutation 与审计在同一 PostgreSQL transaction；拒绝也留痕；detail 不含 owner UUID、fencing token、plan digest、Secret                            | 真实 PostgreSQL 证明 action/outcome/safe detail 和既有 append-only trigger                |
| 脱敏错误与 OpenAPI      | 403 不泄漏所需 capability；跨 environment 与不存在 operation 共用稳定 404；409 不回传 owner/token；正式/运行时 OpenAPI 与 generated client 以 exact-capability policy 对齐，runtime/client digest 为 `bf383602f2fdf6f8858ed98b43f52fa2cdfbd187897ca00d0b2c0c1caae4c3ce`                     | 跨 scope、错权限和 stale token 的 HTTP 负向测试通过                                       |
| 迁移与 release identity | clean acceptance DB 正常由 102 升至 103，103/103 current、zero drift；release compatibility matrix 同步 migration manifest/OpenAPI digest；默认 `hc_data` 的既存 annotation checksum drift 未触碰                                                                                           | forward-only 与相邻 release fail-closed 合同保持一致                                      |
| 严格门禁                | 全新独立 PostgreSQL（103 migrations）+ 全新 MinIO bucket + 仓库 Nginx browser edge + live Temporal：`1025 passed, 2 xfailed, 0 skipped`，310.01 秒；xfail 仍为 BE12-001/BE12-008；Ruff 449、mypy 228、OpenAPI/client drift、ESLint、typecheck、Frontend `609 passed`、production build 通过 | SYS1-04 可关闭；两个既有 release blocker 不隐藏，仍阻断最终发布                           |
| 测试隔离与清理          | 9 个 task-owned PostgreSQL 数据库、disposable MinIO、browser edge 和专用 Docker network 已按预核验的精确名称删除；残留计数为零；共享 API/Worker/media-worker/PostgreSQL/MinIO/Temporal 保持 healthy                                                                                         | 临时资源不可恢复；共享数据和默认 `hc_data` 未删除或迁移                                   |
| 阶段限制                | SYS1-01/02/03 的真实 Kubernetes Pod、多节点 kill/reconnect 与 storage inventory singleton lease 证据仍不存在；未执行备份、恢复、HA 或 canary                                                                                                                                                | Stage 1 整体仍为 `进行中`，不得据此声明 DR/HA 完成                                        |

### 2026-08-28｜Stage 2 checkpoint：BAK2-01

| 项目                       | 当前事实                                                                                                                                                                                                                                                                                                      | 结论                                                                                                 |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| V1 schema 与 golden        | 保留严格 Draft 2020-12 manifest/signature/restore-target schema；新增不含私钥的 canonical golden manifest + detached signature + public key；历史 V1 由真实 Ed25519 验签，未知字段/版本、重复 key、明文 Secret 与单字段篡改均 fail closed                                                                     | golden manifest、向后兼容与篡改拒绝验收通过                                                          |
| 外部签名边界               | `ManifestSigningPort` 只接收不可变 key reference/public key 和外部 KMS/HSM 返回的 Ed25519 signature；生产函数 canonicalize 后自验签再返回 artifacts，API/Worker 没有本地私钥 adapter；错 signer identity、错误长度和伪造签名均拒绝                                                                            | 签名合同完成；BAK2-04 后仍欠真实 Ed25519 provider KMS/HSM 凭据与调用验收，归 BAK2-06 关闭            |
| PostgreSQL catalog         | 第 104 个 forward-only migration 新增 immutable catalog entry、append-only status facts、数据库 transition trigger 和 current view；`FAILED/CORRUPT/EXPIRED` 不能覆写回成功；同一 verification operation 幂等；重建成功与 P19 audit 在同一事务，audit 不含 manifest URI/hash、bucket、key reference 或 Secret | 主库只是可重建索引；真实 PG 验证 UPDATE/DELETE/非法回退拒绝、幂等和脱敏审计                          |
| 独立 S3 repository rebuild | S3 reader 只接受严格 manifest/signature pair、Enabled versioning、COMPLIANCE lock、未来 retain-until、非空 VersionId 和有界大小；物理 bucket 仅存在 adapter 内；pinned public-key store 验证后从独立仓库重建空 PostgreSQL catalog                                                                             | disposable MinIO 真实对象锁/版本验收通过；路径 identity 不符、缺 pair、过期/错误 lock 均 fail closed |
| 迁移与 release identity    | fresh DB 0→104 current；无漂移 acceptance DB 用正常 runner 103→104、104/104 current；migration manifest SHA-256 `1829eaabeea17dab811b156f5c068bd2b93cd08fd2afc0bc2744a0c4a60215b5` 已同步相邻 release compatibility matrix；默认漂移 `hc_data` 未触碰                                                         | forward-only/checksum/release identity 合同通过                                                      |
| 严格门禁                   | 全新 PostgreSQL（104 migrations）+ 普通 MinIO bucket + 独立 COMPLIANCE-lock repository bucket + Nginx browser edge + live Temporal：`1041 passed, 2 xfailed, 0 skipped`，304.89 秒；Ruff 455、mypy 230、OpenAPI/client drift、ESLint、typecheck、Frontend `609 passed`、3773-module production build 通过     | BAK2-01 可关闭；BE12-001/BE12-008 两个既有 release blocker 原样保留                                  |
| 清理与限制                 | 4 个 task-owned PostgreSQL DB、disposable MinIO/edge/network（含锁定测试对象）已按精确名称删除，残留为零；共享服务 healthy；尚未生成 PostgreSQL dump、对象 inventory/portable parts、Secret/KMS export、Temporal policy 或任何可恢复备份                                                                      | 不得标记 `INTEGRITY_VERIFIED`/`RESTORE_VERIFIED`，Stage 2 整体仍为 `进行中`                          |

该 checkpoint 当时的下一实施切片为 `BAK2-02` PostgreSQL dump/PITR adapter 与同版本空库恢复验证；SYS1-01/02/03 的 Kubernetes、多节点故障与 storage inventory singleton lease 缺口继续保留，待对应 HA 验收关闭。

### 2026-08-28｜Stage 2 checkpoint：BAK2-02

| 项目                        | 当前事实                                                                                                                                                                                                                                                                                                                                       | 结论                                                                                          |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Maintenance fence 与 Secret | 创建前精确验证 BACKUP/EXECUTING、owner、token、DB-clock lease 与环境 read-only fence；credentials 只从结构化环境进入临时 `0600` `.pgpass`，不进入 DSN/argv/receipt/log；旧 token 在 artifact 创建前拒绝                                                                                                                                        | SYS1-03 数据库栅栏已接入 PostgreSQL adapter；真实 Kubernetes 多节点故障证据仍按原计划保留     |
| Logical dump/restore        | exact-major PostgreSQL custom dump 使用同一个 exported repeatable-read snapshot 生成 payload 与证据；只向显式 template0 空库 single-transaction 恢复并保留 owner/ACL；104 migrations、4,103 schema facts、1,614 constraints、7 sequences、152 tables、238 rows 及每表排序 JSONB hash 精确匹配；重复恢复因目标非空而 fail closed                | 同版本空库恢复与 migration/schema/constraint/row/hash 验收通过                                |
| Physical baseline 与 PITR   | `pg_basebackup` plain/streamed-WAL、逐文件 hash、`pg_verifybackup`、`pg_controldata` 绑定 system ID/timeline/WAL range；真实 16.10 基线 2,174 files/约 60 MB；专用恢复实例回放到 LSN `0/A000860` 后 promote，包含目标事务、排除更晚事务，104 migrations/system ID 一致；未验证的 WAL anchor 不声明 recovery-ready                              | 真实 PostgreSQL PITR 验收通过；生产不可变 WAL archive provider/receipt 仍是后续部署集成边界   |
| External maintenance image  | `hc-postgres-backup logical-create/physical-create` 在独立 non-root 镜像运行；UID/GID 65532，六个 PG 工具均为 16.10，最终本地 digest `sha256:292810aab7d2d6965d8fee48590a995a9d442fe185e9c3073aca1c965bb4c4ec`、128,170,292 bytes；真实 Docker Job 的 logical/physical receipt 为 `0600`、Secret scan zero、verifybackup 通过                  | CLI/Job 交付完成；API/Worker 镜像未被维护工具污染，Kubernetes Job runtime 待有 context 时补充 |
| 严格门禁                    | 全新 PostgreSQL（104 migrations）+ 普通/COMPLIANCE-lock MinIO + browser edge + live Temporal：`1064 passed, 2 xfailed, 0 skipped`，334.58 秒；Ruff 497、mypy 232、OpenAPI/backup schema/client drift、lock check 通过；Frontend ESLint/typecheck、`609 passed`、client drift 与 3,773-module production build 通过                             | BAK2-02 可关闭；BE12-001/BE12-008 两个既有 release blocker 原样保留                           |
| 清理与范围限制              | 5 个 task-owned 容器、2 个卷、4 个共享 PostgreSQL 测试库和 4 个 `/tmp/hc-bak202-*` 路径已按精确名称删除，残留为零；共享服务 healthy，默认 `hc_data` 与 clean acceptance DB 保留。当前 artifact 仍只是 PostgreSQL 子域证据，不含对象 inventory/parts、Secret/KMS、Temporal policy、完整 manifest 或整站 reconciliation；尚未执行 DR7、HA/canary | 不得标记 `INTEGRITY_VERIFIED`/`RESTORE_VERIFIED`，Stage 2 整体仍为 `进行中`；临时数据不可恢复 |

该 checkpoint 当时的下一实施切片为 `BAK2-03` 对象 inventory、snapshot 与 portable 分片；SYS1-01/02/03 的 Kubernetes、多节点故障与 storage inventory singleton lease 缺口继续保留，待对应 HA 验收关闭。

### 2026-08-28｜Stage 2 checkpoint：BAK2-03

| 项目                        | 当前事实                                                                                                                                                                                                                                                                                                                                                                                              | 结论                                                                                               |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| 精确版本 snapshot inventory | 只接受 Enabled versioning；围栏窗口前后比较 current VersionId/ETag/size/UTC LastModified 集合，排除 current delete marker，显式 VersionId 读取实际 bytes 并计算 SHA-256；`null` version、prefix 越界、异常 provider 字段/分页与版本集变化均 fail closed。Snapshot 只写 `0600` zstd inventory，不复制 payload                                                                                          | 精确版本与 snapshot 验收通过；本地 `repository_kms_only` inventory 尚未构成真实 SSE-KMS 仓库存储   |
| Portable、续跑与 verifier   | 确定性 object/segment plan；每 part 为单成员 PAX tar → zstd checksum → age 1.3.1 X25519 ciphertext；实际 ciphertext 提交前硬校验 `max_part_bytes`。`0600` checkpoint 绑定 version-set coordinate、recipient hash、plan 和完成 part hash；完整 verifier 解密后重建 segment/object hash，缺片、篡改、错 hash/顺序/member 均拒绝                                                                         | 断点续传、并发有界重试、硬分片上限和完整性闭环验收通过                                             |
| External Job 与安全         | `hc-object-backup snapshot-create/portable-create` 只从结构化环境读取 S3/PG/lease/recipient；argv 无 endpoint/DSN/credential/identity。真实 Job 对 2 个对象、480,000 bytes 生成 snapshot 和 5 个 portable parts，解密重建通过，所有 artifact `0600`，receipt 精确凭据值与长凭据 byte scan 均为零；旧 fence 在 artifact 前脱敏拒绝                                                                     | 独立 CLI/Job 交付完成；未声称 Kubernetes Job runtime 或生产 Secret mount 证据                      |
| 维护镜像                    | 官方 age 1.3.1 release tarball 由固定 SHA-256 `bdc69c09cbdd6cf8b1f333d372a1f58247b3a33146406333e30c0f26e8f51377` 校验；独立 `object-maintenance` target 仅安装 backup extra，以 UID/GID 65532 运行。最终本地 digest `sha256:d131c6076e272a9ef2a09385f34e9c4dadb3607302568752d34de644861cd62b`，128,613,821 bytes                                                                                      | API/Worker 镜像未增加 age/backup Job 工具；最终镜像合同通过                                        |
| Provider 限制               | incomplete multipart fail-closed 有确定性单元测试；固定 MinIO `RELEASE.2024-11-07T00-52-20Z` 即使 multipart upload 已上传 6 MiB part，`list_multipart_uploads` 仍返回空，因此未形成真实 provider incomplete-multipart 可观测性证据                                                                                                                                                                    | 不伪造外部证据；生产 provider/版本兼容与故障注入保留给 BAK2-06/DR7                                 |
| 严格门禁                    | 最终全新 PostgreSQL（104 migrations）+ 普通/COMPLIANCE-lock MinIO + browser edge + 真实 age/PG16 backup tools：`1085 passed, 2 xfailed, 0 skipped`，357.06 秒；聚焦 21 passed；Ruff format 466、lint、mypy 234、uv lock、backup schema、formal/runtime OpenAPI 257 paths、generated client 与 diff/冲突检查通过；Frontend ESLint/typecheck、`609 passed`、3,773-module Mock-off production build 通过 | BAK2-03 可关闭；BE12-001/BE12-008 两个既有 release blocker 原样保留                                |
| 清理与范围限制              | 4 个 task-owned 容器、3 个命名卷、1 个 MinIO 匿名数据卷、1 个网络、共享 PG 中 1 个任务库、专用 PG 卷内 4 个测试库和 9 个 `/tmp/hc-bak203-*` 路径已精确删除，标签/路径残留为零；共享服务 healthy，默认 `hc_data` 与 clean acceptance DB 保留。尚无 Secret/KMS、Temporal policy、完整签名 manifest/checksums、独立仓库 payload 或 reconciliation                                                        | 不得标记 `INTEGRITY_VERIFIED`/`RESTORE_VERIFIED`；Stage 2 仍进行中，临时测试数据与身份文件不可恢复 |

该 checkpoint 当时的下一实施切片为 `BAK2-04` 配置/Secret dependency export 与真实 KMS/Secret fail-closed preflight；SYS1-01/02/03 的 Kubernetes、多节点故障与 storage inventory singleton lease 缺口继续保留，待对应 HA 验收关闭。

### 2026-08-28｜Stage 2 checkpoint：BAK2-04

| 项目                          | 当前事实                                                                                                                                                                                                                                                                                                                                                                                                                         | 结论                                                                                                             |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Helm public/dependency export | 严格、有界、拒绝 duplicate key 的 multi-document YAML parser 只接受具名 ConfigMap、literal/field env 和精确 `secretKeyRef`；optional/conflicting reference、未知 key/valueFrom、重复 env 与 secret-shaped plaintext 均 fail closed。真实 chart render 固定提取 1 个 ConfigMap、23 个公共 literal env、12 个 runtime field 和 7 个必需 Secret dependency，并绑定精确 render SHA-256                                               | 公共配置与恢复必需 Secret dependency 已闭合；不导出 Secret 原值                                                  |
| Vault KV v2/Transit preflight | 严格 provider adapter 固定 KV v2 精确 version/created time/key fingerprint 和 Transit exact key version/HMAC；非 loopback HTTP、生产非 HTTPS、redirect、错 prefix/identity/响应均拒绝。真实 Vault v2.0.3 验证 latest 更新不影响旧 version、delete/undelete/destroy 语义、Transit rotate 和提高 minimum version 后 pin v1 fail closed；最终 token 仅允许目标 KV read、Transit key read/HMAC update，其他 Secret/sys policy 被拒绝 | Secret/KMS dependency provider 边界通过真实 API 与最小 ACL 验收；Vault dev 不等于生产 HA/TLS/HSM/Kubernetes auth |
| Snapshot/portable 与 verifier | 双 fence 包围 provider capture/preflight；Snapshot 写公共 YAML 与只含引用的 JSON envelope，portable 用 age 1.3.1 X25519 加密 envelope；严格 schema/hash/render identity、no-overwrite publish、rollback 和 provider exact-version preflight 闭合。Snapshot 的 `repository_kms_only` 只是 BAK2-06 的仓库要求，不是本地 SSE-KMS 证据                                                                                               | 缺 Secret version、destroyed version 或禁用 Transit version 均在 restore write 前以稳定脱敏 code 拒绝            |
| External Job 与安全           | `hc-configuration-backup snapshot-create/portable-create/verify` 的 PG/Vault credential、token、age recipient/identity 仅来自结构化环境，argv/receipt/artifact 不含秘密。最终最小权限 Docker Job 的 snapshot/portable create+verify 均通过，7 个 Secret 原值、PG password、Vault token byte scan 为零；旧 fence 拒绝且 staging 为空；所有文件 `0600`、owner 65532                                                                | 独立 CLI/Job 交付完成；没有 Kubernetes context，故不声称 Kubernetes Job/Secret mount 验收                        |
| 维护镜像                      | 独立 `configuration-maintenance` target 以 UID/GID 65532 运行，固定 age 1.3.1；官方 release tarball 固定 SHA-256 `bdc69c09cbdd6cf8b1f333d372a1f58247b3a33146406333e30c0f26e8f51377`，本地构建输入再次校验同一 hash；最终 digest `sha256:3187e7c81fe5c07227704b575fea6f4fc92cb4d449e3537624b87a0fe6854b5d`、128,564,118 bytes                                                                                                     | API/Worker 镜像未增加配置维护工具；最终 image identity 已固定                                                    |
| 严格门禁                      | 配置聚焦 17 passed；真实 Vault 变异集成 1 passed；完整 backup 域 86 passed / 0 skip；全新独立 PostgreSQL/MinIO/Vault/age/PG tools 下全后端 `1103 passed, 2 xfailed, 0 skipped`，372.83 秒；Ruff 507、mypy 236、uv lock/schema、formal/runtime OpenAPI 256 paths、client/Helm/diff 通过；Frontend ESLint/typecheck、`609 passed`、3,773-module Mock-off production build 通过                                                     | BAK2-04 可关闭；BE12-001/BE12-008 两个既有 release blocker 原样保留                                              |
| 清理与范围限制                | 4 个 task-owned 容器、9 个卷、1 个网络、5 个 `/tmp` 路径、owner-only identity/artifact、Vault 临时镜像和 2 个试制镜像已精确永久删除，残留为零；最终维护镜像保留。共享服务 healthy，默认漂移 `hc_data` 与 clean acceptance DB 均保留。尚无 Temporal policy、完整签名 manifest/checksums、独立仓库 payload/SSE-KMS、Ed25519 provider 调用或 reconciliation；未执行 DR7、HA/canary                                                  | 不得标记 `INTEGRITY_VERIFIED`/`RESTORE_VERIFIED`；Stage 2 仍进行中；临时测试数据与身份不可恢复                   |

该 checkpoint 当时的下一实施切片为 `BAK2-05` Temporal 策略 adapter 和未完成任务清单；SYS1-01/02/03 的 Kubernetes、多节点故障与 storage inventory singleton lease 缺口继续保留，待对应 HA 验收关闭。

### 2026-08-29｜Stage 2 checkpoint：BAK2-05

| 项目                                | 当前事实                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | 结论                                                                                                                   |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| 模式与 provider recovery 合同       | 生产仅允许非 loopback TLS 且有 API key 或 mTLS 的 `external_managed`；`external_self_hosted` 在没有取代 ADR 的冷快照/恢复合同时稳定拒绝，`development_only` 只允许无凭据 loopback probe。严格 provider contract 绑定 cluster/namespace identity、精确版本、history owner/方法、RPO ≤ 900 秒、RTO ≤ 7200 秒、保护点、evidence hash、runbook 与有效期；非法 URI、过期证据和 identity/RPO 不符 fail closed                                                                                                                                                                                                         | 托管/自建/开发三种模式结果明确；adapter 完成不等于 provider HA、backup 或 SLA 已验收                                   |
| 支持 API 与 metadata-only inventory | 只调用 Namespace/Cluster/System、Schedule 和 Visibility API；双重稳定读取并有界重试/计数。生产要求全部 schedule paused，记录安全 action/spec hash 和 `ExecutionStatus = "Running"` workflow identity；不读取 history、payload、memo、search attribute 或内部库，policy 固定两个禁止导出标记为 false                                                                                                                                                                                                                                                                                                             | Temporal 内部表不会进入业务 dump；schedule/open workflow 未完成任务清单与 live identity 已闭合                         |
| Snapshot/portable、fence 与 CLI     | `hc-temporal-backup-policy/v1` 内嵌 schedule/open inventory hash 和 consistency coordinate；snapshot 为交给 BAK2-06 仓库保护的 `repository_kms_only`，portable 为 age 1.3.1 X25519。PG maintenance fence 包围 capture/publish；严格 verifier 检查 `0600` owner-only receipt/artifact、no-overwrite/rollback、hash/decrypt、live identity、paused schedule 和 provider evidence freshness。CLI 提供 create/verify/probe，所有敏感输入仅来自环境                                                                                                                                                                  | 生产 artifact 与开发 probe 分离；payload/credential 不进入 argv、receipt、artifact 或脱敏错误                          |
| 真实 Temporal 协议证据              | 共享本地 Temporal 1.25.2 / Python SDK 1.31.0 中创建携带 Secret sentinel 的 paused schedule 和 open workflow；支持 API inventory 计数分别精确 +1、sentinel scan zero，精确清理后恢复基线。真实 dev client 注入 managed-contract protocol path 的 snapshot create+verify 仅为测试；probe 始终返回 `development_probe_only`                                                                                                                                                                                                                                                                                        | 真实 API/SDK 与 metadata-only 行为通过；不把开发 Temporal 或协议注入测试冒充外部托管 provider 证据                     |
| 维护镜像                            | 独立 `temporal-maintenance` target 以 UID/GID 65532 运行，固定 age 1.3.1 与 Temporal SDK 1.31.0，不包含 `pg_dump`；最终 digest `sha256:6dc7859b78d2f4ae561b0913c1d0178514a43046115dd6543b58807cc2d64632`、128,622,842 bytes，镜像内 probe 通过                                                                                                                                                                                                                                                                                                                                                                  | API/Worker 镜像未增加 Temporal backup CLI；最终 image identity 已固定                                                  |
| 严格门禁与失败复测                  | 16 个确定性 unit/CLI、真实 Temporal integration 1 passed、完整 backup 域 `103 passed, 0 skipped`；全后端最终 `1120 passed, 2 xfailed, 0 skipped`，358.67 秒；Ruff 512、mypy 238、uv lock/backup schema、formal/runtime OpenAPI 256 paths、generated client 与 Helm lint/render clean；Frontend ESLint/typecheck、`609 passed`、3,773-module Mock-off production build 通过。首次全量暴露并修复三项测试环境/既有 flaky：task browser edge CORS、随机 UUID 含 `501` 导致 body 子串误判、disposable MinIO 缺测试标签；定向 3 passed 后全量通过。Vault Transit 首次未 mount，启用官方 engine 后集成与 backup 域全绿 | BAK2-05 可关闭；BE12-001/BE12-008 两个既有 release blocker 原样保留，环境/测试修复均有定向再验证                       |
| 清理与限制                          | 5 个 task-owned 容器、2 个卷、1 个网络、3 个 `/tmp` 路径、6 个已核验 trash artifact 和 disposable Vault 镜像已精确永久删除，残留为零；最终 Temporal 镜像保留。共享服务 healthy，默认漂移 `hc_data` 与 clean acceptance DB 均保留。无外部托管 provider HA/backup/SLA、完整签名 manifest/checksums、独立仓库 payload/SSE-KMS、Ed25519 provider 调用、Kubernetes Job、reconciliation 或 DR7/HA/canary                                                                                                                                                                                                              | 不得标记 `INTEGRITY_VERIFIED`/`RESTORE_VERIFIED`；Stage 2 仍进行中；临时测试数据、workflow/schedule 与身份文件不可恢复 |

### 2026-08-29｜Stage 2 checkpoint：BAK2-06

| 项目 | 当前事实 | 结论 |
| --- | --- | --- |
| 整站 plan/checksums 与编排 | 新增严格 `hc-whole-backup-plan/v1` 和 `hc-platform-checksums/v1` Draft 2020-12 schema；九个状态域 evidence、artifact path/size/hash/encryption、release/tool/KMS/repository identity 全闭合。`hc-platform backup create` 组合 BAK2-02～05，匹配 receipt/artifact/checkpoint 可续跑，任一冲突/缺域/签名前篡改 fail closed | create 编排与确定性续跑完成；外部日志/依赖 evidence producer 仍归 OBS5/DR7 |
| 锁定仓库与 Ed25519 | `S3LockedBackupRepository` 预检 versioning/Object Lock/整秒 COMPLIANCE retention/精确 replication destination；所有对象 SSE-KMS、VersionId、metadata/hash，multipart 记录 part SHA/ETag 并可恢复；独立下载全量复验。`VaultTransitEd25519Signer` 使用 nonexportable exact-version Ed25519，显式 vN 签名并本地自验，minimum version/错 key fail closed | 真实 MinIO/Vault provider 协议闭合；static KMS、Vault dev 和管理员 MinIO credential 不冒充生产 HSM/HA/IAM 分权 |
| Catalog、CLI 与 API | 第 105 个 migration 允许追加同状态 integrity reverify fact 并加入 environment/status/completed/id keyset 索引。仓库独立复验后才原子记录 `CREATING → CREATED → INTEGRITY_VERIFIED`；create/verify/list 重复幂等，代码路径不产生 `RESTORE_VERIFIED`。`GET /api/v1/platform/backups` 强制当前环境、exact viewer capability、limit≤100、signed cursor、P19 审计和脱敏 page | BAK2-06 的最高状态精确限制为 `INTEGRITY_VERIFIED`；恢复状态留给 RST3/DR7 |
| 真实整站与篡改证据 | 真实 MinIO SSE-KMS/Object Lock/5 MiB multipart/实际异站复制 + 真实 Vault Transit Ed25519 + fresh 105 migration PostgreSQL catalog：首次 publish、重复全 resume、直接/CLI 重复 verify、CLI list 均成功；最新 manifest 替换为 `{}` 后独立 verifier 拒绝且 catalog 不变，无 restore fact | 完整 backup set、独立读取和 tamper fence 验收通过；没有执行 restore/reconciliation |
| Kubernetes Job 与镜像 | 一次性 kind v1.32.2 实际运行 Helm digest-pinned `backup verify` Job；Secret plan、ConfigMap/Secret env、PVC staging、显式 SA、`automountServiceAccountToken=false`、UID/fsGroup 65532。非 loopback Vault HTTP 首次按合同拒绝，补 TLS/CA 后同一 operation 连续两次成功且事实仍一条。最终镜像含 age 1.3.1/PG16.10，digest `sha256:13381e1091983bc6112cc5c50c027882a5dd9bbf8f8598be38ac4639f7774c76`、129,551,449 bytes | 真实单节点 Kubernetes Job 验收通过；不替代生产多节点、CSI、workload identity 或 SYS1 故障接管证据 |
| 严格门禁与失败复测 | backup 域 `127 passed, 0 skipped`；最终全后端 `1145 passed, 2 xfailed, 0 skipped`，420.61 秒；Ruff 523 files formatted/lint clean、mypy 241、uv lock、五份 backup schema、formal/runtime OpenAPI、generated client、Helm lint/default/create/verify render clean。Frontend ESLint/typecheck、`609 passed`、3,773-module Mock-off build 通过。首次全量的 release matrix 104→105 与 BE23 漏 path 已同步，定向 14 passed 后全量通过 | BAK2-06 可关闭；BE12-001/BE12-008 两个既有 release blocker 原样保留 |
| 清理与限制 | kind cluster/PVC/namespace、TLS proxy/CA、6 个长期 task container、4 个 volume、1 个 network、共享 PostgreSQL 8 个 task DB 和 owner-only staging 已按精确名称永久删除，`bak206-*` 残留为零；最终镜像保留。共享服务 healthy；clean acceptance DB 为 105/105 current；默认 `hc_data` 仍为 99 applied 且保留原 annotation 0010/0011 drift，未触碰 | Stage 2 实施任务完成；临时对象锁数据/测试身份不可恢复。RST3、生产 provider/IAM、HA/canary、容量和 DR7 仍未完成 |

### 2026-08-29｜Stage 3 checkpoint：RST3-01

| 项目 | 当前事实 | 结论 |
| --- | --- | --- |
| Manifest-only 信任顺序 | 仓库新增 `verify_manifest`，只读取具备 version/KMS/Object Lock/hash metadata 的 `manifest.json` 与 `signature.sig`；先验签并绑定 backup/source/target、精确 release、PostgreSQL major、signer trust 和 KMS exact versions，整个计划固定 `payloads_downloaded=false` | 错 release/KMS/source/target 在目标探针前 fail closed；RST3-01 不读取 dump/object part 或其他 payload |
| 空目标与源碰撞 | 新备份签名加入源数据库 name 与 `pg_database_size()`；PostgreSQL 探针只执行 read-only repeatable-read identity/major/user-object 查询，S3 探针只读 versioning 和具名前缀 `MaxKeys=1`，staging 只读 owner/mode/statvfs。非空目标、临时覆盖授权、源/目标 DB 或 bucket/prefix 碰撞、错 major/versioning 均拒绝 | 只接受具名、隔离、实际为空的目标；planner 没有 mutation port |
| 容量与 readiness | PostgreSQL 逻辑大小、对象总字节、staging artifact bytes 分别以纯整数计算 30% 裕量；旧 V1 可继续验签，但缺 size/source identity 时不猜测。owner-only request 精确绑定实际部署 release、API writes disabled、Worker replicas zero、KMS refs、Temporal identity、Secret dependency version/fingerprint、域名/证书与 provider capacity evidence/hash/有效期 | 成功只生成 `PREFLIGHT_PASSED` dry-run；provider readiness 输入不冒充生产 quota/KMS/Temporal SLA evidence producer |
| CLI、合同与 checkpoint | `hc-platform restore plan <backup-id> --target <environment> --backup-plan ... --target-document ... --staging-directory ... --dry-run` 已接入；新增 strict `hc-platform-restore-plan-request/v1` 与 `hc-platform-restore-plan/v1`，输出 request/operation/plan ID、12 项检查、三域容量和 deterministic checkpoint，下一动作固定要求 signed approval | 七份 Draft 2020-12 schema 可机械生成并 drift check；无 execute/verify restore 命令，无 `RESTORE_VERIFIED` 路径 |
| 真实失败矩阵 | disposable PostgreSQL 16.10 template0 空库与 versioned MinIO：成功、错 release、错 KMS、实际非空表、实际非空 prefix、容量不足全部符合稳定 code；拒绝后表与对象 sentinel 原样存在，测试精确清理后 DB 用户对象/prefix 对象均为 0。RST3 聚焦 `14 passed, 0 skipped` | RST3-01 验收完成；这不是 payload restore、reconciliation、RPO/RTO 或 DR drill |
| 门禁与镜像 | backup 域 `131 passed`；全后端 `1047 passed, 2 xfailed, 113 skipped`、零失败，其中 skip 是本轮未注入的其他专用外部集成环境，既有 BAK2 真实证据不重记；Ruff/mypy/schema 与 CLI smoke 通过。更新镜像 UID/GID 65532，age 1.3.1/PG16.10，ID `sha256:79d25520068516156c192671fe2cd0d74c96333bdea20e25efb241c1e78260c1`、129,589,192 bytes | BE12-001/BE12-008 和所有生产外部门禁保留；不能把标准全量中的 skip 记为 PASS |
| 清理与限制 | disposable `rst301-*` PostgreSQL/MinIO、volume/network 和 owner-only staging 在记录最终证据后按精确名称清理；最终维护镜像保留。生产 capacity API、KMS/IAM、托管 Temporal provider、restore execution/reconciliation、5 TB 与 DR7 未完成 | RST3-01 只关闭计划阶段；任何恢复写入和 `RESTORE_VERIFIED` 必须等待后续任务 |

### 2026-08-29｜Stage 3 checkpoint：RST3-02

| 项目 | 当前事实 | 结论 |
| --- | --- | --- |
| 签名内容与 mutation approval | 整份 manifest 新增 PostgreSQL migration/schema/constraint/sequence/逐表 row+digest aggregate，并由 backup signer 覆盖；restore plan 同时绑定 signer fingerprint、目标 database/bucket/prefix。mutation 还必须消费独立 Ed25519 approver key 签名的 approval，精确绑定 plan/checkpoint/operation/backup/target/有效期，且固定禁止 write enable 与 `RESTORE_VERIFIED`；backup signer 与 approver 相同会因职责冲突拒绝 | 旧 V1 可验签但缺 content evidence 时不能 execute；plan 并不隐含 mutation 权限，批准也不授权开放写入 |
| 固定顺序与安全 checkpoint | `hc-platform restore execute` 只执行 `payload_verified → objects_restored → postgresql_restored → temporal_ready → services_read_only`。每步前后验证 live Kubernetes/DB read-only fence；checkpoint 严格记录 plan/approval/owner/fencing token、单调 state/version、前驱 SHA-256、exact step prefix 和 receipt，owner-only `0600` 原子保存；冲突、篡改、换 owner/token/approval 均 fail closed | 对象/数据库成功而 Temporal 失败时只保留 `POSTGRESQL_RESTORED`，writes/reconciliation/restore-verified 仍为 false；可从该安全点继续 |
| 可恢复 payload 与三域 adapter | 锁定仓库只续用与签名 size/hash 相同的本地文件，局部下载和本地篡改均拒绝且不覆盖；snapshot 按源精确 VersionId 恢复 bytes，portable 校验 inventory/part/decrypt hash，目标冲突拒绝且相同内容续跑；PostgreSQL 只恢复具名空库并与签名 content evidence 完整比对，相同目标重试不变；Temporal 使用 live supported API identity/paused schedule/provider freshness preflight | 对象→PostgreSQL→Temporal 的 mutation/readiness 边界闭合；不读取 Temporal 内部库，不把物理 DB 大小当作确定性内容 hash |
| 目标 fence 与只读服务 | `PostgresRestoreTargetFence` 在启动 API 前安装目标 `READ_ONLY_MAINTENANCE`。Kubernetes runtime 要求所有 Worker/Media Worker spec/available/ready 均为 0，只将具名 API 扩为批准副本并等待 ready。Helm Job 默认关闭、digest pinned、显式 SA/PVC/Secret/ConfigMap、projected token audience 可配置、Temporal TLS Secret 可选、root filesystem 只读、UID/fsGroup 65532；token/CA/client identity 只复制到 private `emptyDir` | 成功终态固定 `READ_ONLY_READY`；Worker 不启动，API 仍受 DB 写栅栏保护，RST3-03 前不能开放写入 |
| 真实中断与恢复 | PostgreSQL 16.10 fresh 105 migrations、versioned MinIO source/target、SSE-KMS/Object-Lock 仓库及异站副本、Vault 1.20.3 Transit Ed25519 v1、Temporal 1.25.2 supported API protocol：对象与 PostgreSQL 真恢复后注入 Temporal 中断，checkpoint 精确停在 `POSTGRESQL_RESTORED`；显式重复对象/PG step 没有新增 target version 或内容漂移，随后 resume 到 `READ_ONLY_READY`，真实 rows、两个对象 bytes/version 与 source/target fence 精确 | 真实三域失败恢复通过；本地 managed-protocol Temporal 不是外部托管 provider SLA/backup 证据 |
| kind external Job 与幂等 replay | fresh backup `rst302-real-backup-195117` 由 digest `sha256:4dfbea9b8eef56ced1a8a3f54bc977178789c396d8747f40a19c898f5107f232` 的 non-root 镜像在 kind v1.32.2 执行；首次输出五步完成、API 1/1 ready、两个 Worker deployment 为 0。相同 Job/PVC/approval 第二次执行后 checkpoint SHA-256 `19449504405693c4c7a78025dbaf8a54ad7b060d98c765f8dbf71c64fbc3f00c`、PostgreSQL deterministic evidence 和对象 version tuple 均不变。超过 frozen 900 秒保护点的旧 Temporal evidence 曾在对应步骤稳定拒绝 | external Job/CLI、断点续跑与 complete replay 验收通过；RPO freshness 未被为了测试绕过 |
| 严格门禁、Secret 与清理 | RST3 direct integration `1 passed`（真实 Vault signer）；backup 域标准门禁 `159 passed, 12` 个显式外部 skip；全后端 `1075 passed, 115 skipped, 2 xfailed`、零失败，其中 skip 均是未注入的专用外部环境，xfail 仍为 BE12-001/BE12-008。Ruff 510 files、mypy 246、九份 schema、Helm lint/render 与 diff clean；21 个 Job/恢复 artifact 全为 `0600`，PG/MinIO/Vault credential byte scan zero，fresh repository 异站复制 8/8 exact。kind、7 个外围 container、3 个 named volume、1 个 network、task `/tmp` 已精确删除，`rst302-*` 残留零；最终镜像保留。clean acceptance DB 仍 105/105 current；默认 `hc_data` 仍 99 applied、原两项 annotation drift 与六项 missing 未触碰 | RST3-02 可关闭；disposable 数据/身份不可恢复。生产 KMS/HSM/IAM、Vault HA/auth、多节点 CSI、托管 Temporal provider、5 TB、reconciliation、HA/canary 与 DR7 仍未完成 |

### 2026-08-29｜Stage 3 checkpoint：RST3-03

| 项目 | 当前事实 | 结论 |
| --- | --- | --- |
| 严格恢复报告合同 | 新增 `hc-platform-restore-reconciliation-report/v1` 和固定九项检查顺序；报告精确绑定 restore plan、完成的五步 `READ_ONLY_READY` checkpoint SHA、operation/backup/target 和 verifier/time。`writes_enabled`、`workers_enabled`、`restore_verified` 均为 literal false，下一动作只能是独立 smoke/write-enable approval；报告以 owner-only `0600` 原子 no-overwrite 发布，相同 bytes 可 replay、不同 bytes 冲突拒绝 | reconciliation PASS 只是外部只读证据，不推进 maintenance checkpoint、不写恢复事实，也不授权写入或 `RESTORE_VERIFIED`；十份 Draft 2020-12 schema 可机械生成并 drift check |
| 全量只读检查 | PostgreSQL 在单一 `REPEATABLE READ, READ ONLY, DEFERRABLE` snapshot 中核对签名 business-table content、migration/schema/constraint、所有 DB→object 引用、全局 P19 audit sequence/predecessor/hash/head、Outbox claim/scope、workflow/reconciliation、账号/成员/grant/职责分离和全部 scope RLS policy。对象 inspector 对 inventory 每项读取目标当前精确 immutable version 并流式重算 size/hash/restore metadata；Temporal 读取 live namespace/cluster/version/schedule/open-workflow inventory并与 DB 对应；Kubernetes runtime 只 GET deployment 与数据库 fence | 九项均为 `coverage=FULL`；任一 inspector exception 转为脱敏 FAIL check 并保留报告，错误 checkpoint/plan 则在检查前 fail closed；所有 adapter 无 put/copy/SQL write/scale 路径 |
| CLI 与 Helm mode | `hc-platform restore reconcile` 重新验签锁定仓库 manifest、解析 authenticated payload/inventory、加载 immutable checkpoint 并发布报告；FAIL 仍保留报告且进程非零。现有 `backend.restoreJob.operation` 明确为 `execute|reconcile`，reconcile Job 不挂载/消费 mutation approval，继续使用 digest image、SA/PVC、private projected token/CA、Temporal TLS 和 non-root/read-only-root security context | 默认 Job 仍关闭；execute/reconcile 名称和参数互斥，reconcile 不复用 RST3-02 approval 权限 |
| 真实 direct reconciliation | 真实 PG16.10/105 migration、versioned MinIO、SSE-KMS/Object-Lock/异站复制仓库、Vault Transit Ed25519 v1 和 Temporal 1.25.2 上，RST3-02 注入失败/续跑之后直接运行 RST3-03；checkpoint SHA `ea3b68e2eb02fc187fda0ea33def6bda28ebc56bb752304a42442f05372c8edd`，PASS report SHA `5cc6564dea55261acecf00ec84ebe9bd6cde1ce242116939c189d0ff75b41740`，九项 PASS 且三个授权 flag 全为 false | 同一真实 integration 同时覆盖 payload restore、恢复中断、安全续跑和外部 reconciliation；这不是生产 provider、容量或 DR7 证据 |
| kind external Job 与 exact replay | fresh backup `rst302-real-backup-204142` 由 kind v1.32.2 中 Helm reconcile Job 执行，PASS report `restore-reconciliation-ccebc2ddbfd12ec95f677fa5` / SHA `f89ed25908a6f6422dde47c8fe1568c58fbdb752dc775be72433f0faeae45bf1`，绑定同一 checkpoint SHA；检查 count 依次为 5/150/3/0/0/0/4/122/1，全部 issue zero，API 1/1，两个 Worker deployment 均为 0。以相同 manifest、timestamp 和 PVC 重跑后 report ID/SHA、checkpoint SHA 及每项 evidence SHA 完全不变 | Helm external Job、live PostgreSQL/object/Temporal/Kubernetes inspector 和 immutable exact replay 验收通过；没有新增对象 version、checkpoint state 或写授权 |
| 失败保持与门禁 | 首次 external Job 因 disposable Temporal TLS 端点配置错误生成并保留脱敏 FAIL report SHA `a77f1ec355851f054c90fcddb79f8dbc35e6d407029e804c645dd3f3e6dba7f2`，只有 workflow inspector 为 `RESTORE_RECONCILIATION_INSPECTOR_ERROR`，其余八项 PASS，三个授权 flag 仍为 false；修正 CA leaf/HTTP2 ALPN/网络 bridge 后 live inventory PASS。该失败 artifact 先记录 SHA，再按精确 SHA 从 disposable PVC 删除，未被静默覆盖。聚焦 36 passed/1 external skip，真实 direct integration 1 passed；backup 域 166 passed/13 explicit external skips；全后端 `1082 passed, 116 skipped, 2 xfailed`、零失败，两个 xfail 仍为 BE12-001/BE12-008。Ruff format 514、lint、mypy 248、十份 schema、Helm lint/render、105/105 migration 和 diff clean | fail-closed 与成功/重放均有真实证据；标准 suite 的 external skip 不记为 PASS |
| 镜像、清理与限制 | 最终镜像 digest `sha256:31e2d349738cf5b15d7348bc0b09f189936a932107699b6e4906dff0e2e0bb5e`、129,704,071 bytes，保留作验收 artifact。kind cluster、5 个 task-owned 外围 container、2 个 named volume、1 个 network、PVC/Secret/短期 CA、owner-only evidence 和全部 `rst303-*` 临时路径已按精确名称永久删除，残留为零；共享 Compose 服务 healthy，Temporal 仅保留原 Compose network。clean acceptance DB 仍 105/105 current；默认 `hc_data` 仍 99 applied，原 annotation drift/六项 missing 未触碰 | RST3-03 可关闭；disposable 对象锁数据、测试身份、FAIL/PASS 文件不可恢复。生产 KMS/HSM/IAM、Vault HA/auth、多节点 CSI、5 TB planned migration、runbook、HA/canary 与 DR7 仍未完成 |

下一实施切片：推进 `RST3-04` planned migration 预复制和最终切换，以容量环境验证 5 TB 级目标、RPO 0 与停写时间；`RST3-05` runbook、SYS1 多节点、生产 provider/IAM、HA/canary 和 DR7 继续作为独立未完成门禁。RST3-03 报告本身不开放写入，也不产生 `RESTORE_VERIFIED`。

### 2026-08-29｜Stage 3 checkpoint：RST3-04 implementation / acceptance open

| 项目 | 当前事实 | 结论 |
| --- | --- | --- |
| 严格计划与职责分离 | 新增 planned-migration、cutover approval、observation、checkpoint 四份 V1 schema。计划固定 RPO bytes/seconds 为 0、停写上限 1,800 秒、5,000,000,000,000 实传字节、6,500,000,000,000 可用目标容量、24 小时预复制上限；源/目标必须同 release 且 identity 不同。cutover approver 与 live observer 使用不同 Ed25519 pin，签名 exact canonical bytes；双写、恢复旧源写、目标写入后的直接回切均无法授权 | 输入合同和职责边界已实现；签名 observer 是受信事实来源，但不能把虚构容量声明变成真实容量证据 |
| 七阶段 checkpoint | `precopy_verified → source_fenced → final_sync_verified → target_verified → traffic_switched → target_writes_enabled → rollback_window_retained` 固定前缀逐步推进。每步验证 PG system identifier/timeline/LSN/recovery、对象 count/bytes/inventory/pending replication、Temporal inventory、DB fence、Worker/writer/upload grant、reconciliation、DNS、pause 与 retention；live observation 最长 300 秒。checkpoint owner/token/plan/approval/predecessor-CAS 绑定，`0600` 原子发布与 exact replay | RPO 0、无双写和 point-of-no-return 是可执行门禁；目标写入后固定 `direct_rollback_allowed=false`、`reverse_sync_required=true` |
| CLI、Helm 与 runbook | `hc-platform migration plan/advance/status` 验证签名 observation 并一次只推进一阶段；默认关闭的 digest-pinned migration Job 使用显式 SA、PVC、Secret→owner-only staging init、只读 root/non-root context，运行容器只接收两把公钥，不挂私钥或默认 SA token。`deploy/runbooks/planned-migration-cutover.md` 记录预复制、fence、final delta、只读 reconciliation、DNS 切换、promote、回退窗口与失败动作 | 实现与静态部署合同完成；该 Job 是 evidence gate，不替代 PG/Object/Temporal/DNS provider operator |
| 负向验证 | focused migration + Helm + release-contract `34 passed`；覆盖七阶段/exact replay、错顺序/owner/token、签名篡改/错 key/过期、observer/approver 同 key、陈旧 observation、非零 WAL/object/Temporal/pending、timeline 未 promote、post-verify drift、双写、超停写、容量不足、目标空间不足和 sparse/synthetic claim 拒绝。backup 域 `187 passed, 13 skipped`；全后端 `1104 passed, 116 skipped, 2 xfailed`，skip 均为未注入的外部环境，xfail 仍为 BE12-001/008；Ruff 541、mypy 250、十四份 schema、23 migration tests、Helm lint/render 和 diff check 通过 | 实现回归零失败；两项已知 release blocker 和显式外部验收未隐藏 |
| 未关闭验收 | 2026-08-29 实测 workspace mount 总量 `1,081,101,176,832` bytes、可用 `750,493,618,176` bytes，无 Kubernetes context，且 migration plan/approval/observation/checkpoint、生产容量证据均未提供，无法诚实提供 5 TB 实传和 6.5 TB 可用目标容量。合同明确拒绝 sparse、deduplicated logical、synthetic 或 extrapolated bytes，因此未创建伪 PASS checkpoint，也未执行 target write-enable；机器可读 blocker 见 `backend/tests/system/results/rst3-04-external-readiness-summary.json` | RST3-04 保持 `进行中`；只有外部真实容量环境完成全七阶段、RPO 0 和 ≤1,800 秒停写才能改为完成 |
| 镜像、清理与共享状态 | `hc-data-platform-backup-maintenance:rst304` packaged CLI smoke 通过，digest `sha256:a4987cc9ab63e6d683a4de47228d9fdd46ed37ccf3f1d375d9d1837f939602ff`、129,737,089 bytes，保留作实现 artifact；task-owned Helm render/CLI help 已按精确路径删除，`/tmp/hc-rst304-*` 残留零。共享 Compose PG/MinIO/Temporal/API/Workers healthy；acceptance DB 仍 105，默认 `hc_data` 仍 99，既存 drift/missing 未触碰 | 实现临时文件不可恢复；没有创建 Kind/provider/capacity 数据，保留镜像不等于容量或 cutover 验收 |

下一可独立实施切片转向 `RST3-05` 裸机/Kubernetes 恢复 runbook 与独立值班演练准备；RST3-04 的真实 5 TB capacity/DR7-02 验收继续作为红色外部门禁保留，不能用本地 Kind、稀疏文件或签名自述替代。

### 2026-08-29｜Stage 3 checkpoint：RST3-05 operator packet / independent drill open

| 项目 | 当前事实 | 结论 |
| --- | --- | --- |
| 值班证据合同 | `restore-operator-evidence.md` 固定 executor/approver/verifier 三职责、immutable identity worksheet、全局 stop condition、每阶段 evidence、execute/reconcile JSON gate、九项固定顺序、exact replay 和失败保留。明确区分 `RST3-05 operator drill complete` 与 `DR7-01 complete` | 文档不能把 reconciliation PASS 冒充 write-enable 或 `RESTORE_VERIFIED`；当前 controller 没有这两条路径 |
| 裸机/新服务器 | `restore-new-bare-metal-server.md` 把干净物理机/VM 引导至仓库支持的 Kubernetes/Helm 架构，先检查 mount/disk/network，使用批准的 Kubernetes/CSI/CNI 版本，新 namespace/依赖/identity，部署 API/Worker/Frontend 全 0、ingress/PDB/normal migration/所有维护 Job 关闭的同版本 dormant release，再交接公共恢复流程。禁止 Compose/host `psql`/systemd improvised restore | 单节点新服务器演练只证明恢复流程，不证明 HA4/node loss；没有 curl-to-shell 或磁盘删除步骤 |
| Kubernetes runbook | `restore-kubernetes.md` 覆盖 owner-only 输入、dormant gate、真实 `restore plan` 12 checks/三域 30% headroom、独立 Ed25519 approval、具名 Deployment get/patch RBAC 且 Secret get=no、Secret/ConfigMap/PVC/age/Temporal TLS 分离、digest Job execute、无 approval 的 reconcile、失败采证、exact Job replay 与只读 retention。`restore-environment-reference.md` 枚举 plan/execute/reconcile 的 exact ConfigMap/Secret/file key 和 repository/source/target identity 分离 | 未列配置、shared broad cloud identity、public ingress、任一 Worker、非空 target、错 release/KMS/Temporal 均是 stop condition |
| 实现审计修复 | runbook 审计发现 planner 的 target S3 preflight 曾隐式复用 backup repository boto identity；现改为与 execute 相同的专用 `HC_RESTORE_OBJECT_STORE_*` client，repository/source/target 三身份可最小授权。新增回归验证 target adapter 只请求 dedicated prefix | 修复没有放宽 manifest/target binding；33 个 focused restore tests 通过，Ruff/mypy clean |
| 操作形态验证 | runbook/Helm contract `13 passed`；真实 Helm render 证明 dormant release 恰有 4 个 Deployment 且 replicas 全 0、无 Ingress/PDB/Job；execute render 恰有一个 `backoffLimit=0`、explicit SA、default token automount=false 的 restore Job，应用 replicas 仍全 0 | 文档与当前 Chart/CLI action name、Job name发现方式、安全终态一致；这不是 payload restore 的新证据，RST3-03 的真实证据仍适用 |
| 未关闭验收 | 当前会话没有一名未参与实现的值班人员，也没有可向其安全交接的外部 credential/provider/独立 evidence system。作者自行读文档或自动 contract test 不满足“未参与开发”验收 | RST3-05 保持 `进行中`；必须由独立值班人员按 packet 完成一次演练、签署 worksheet，并保留失败/重放证据后才能关闭 |

下一可独立切片进入 `HA4-01` Helm 多 node/zone spread、PDB、probe 与 graceful drain 实现；RST3-04/05 的外部容量与独立人员验收继续保留为红色门禁。

### 2026-08-29｜Stage 4 checkpoint：HA4-01 / DR7-03 current release

| 项目 | 当前事实 | 结论 |
| --- | --- | --- |
| topology、PDB 与 fail-fast | Chart 新增 HA 开关；Frontend/API/main Worker/media Worker 各生成 hostname + zone 两条 `DoNotSchedule` spread，`minDomains=2`、`maxSkew=1`、`matchLabelKeys=pod-template-hash`。HA 模式要求四类 workload 均 `replicas>=2`、PDB `1<=minAvailable<replicas`，node/zone key 非空且不同，drain/grace budget 为正且严格小于 termination budget；production example 开启 HA 且 media Worker 为 2 副本 | production 值不再允许空 spread、单副本、无 PDB 或无界 shutdown；负向 Helm render 对这些组合 fail closed |
| Worker graceful drain 与 readiness | Worker 捕获 SIGTERM/SIGINT，先把 in-process sentinel 从专用 `/tmp/hc-runtime` `emptyDir` 删除并停止 Outbox/preview/inventory claim loop，再调用 Temporal `worker.shutdown()` 等待 activity grace，最后退出。readiness 只检查 sentinel，不再每 10 秒导入完整 runtime；main/media 共用合同且保持 read-only root filesystem | 真实排空日志顺序为 `graceful drain started` → Temporal 60 秒 activity budget → `graceful drain completed`；聚焦 Worker/Helm 测试 6 passed，Ruff/mypy/Helm lint 通过 |
| Frontend/API 摘流 | API 先等待 10 秒 endpoint propagation，再由 Uvicorn 使用 30 秒 graceful window；Frontend 也先等待 10 秒，再向 PID 1 Nginx 发送 QUIT 并等待退出。Frontend 总 termination budget 30 秒，API 为 45 秒 | 首轮立即 QUIT 在真实 drain 的 40.972 秒处产生一次 2 秒 Frontend timeout；该失败保留并驱动 delay 修复。修复后的最终窗口为零失败，不能把首轮失败覆盖成 PASS |
| 真实多节点 drill | disposable kind v1.32.2：1 control-plane + zone-a/b/c 三 worker；四类 Deployment 均 2/2、PDB minAvailable=1。对承载四类 Pod 的 zone-a/zone-b 节点执行三次真实 drain，最终验收 drain 约 43 秒，replacement 落到另一可用 zone，全部 Deployment 回到 2/2、Pod restart=0。最终 180.070 秒 Service-proxy 观测：API `1745/1745`、Frontend `1745/1745`，failure 均 0，最大延迟分别 578.333 ms/33.044 ms | HA4-01 和当前版本 DR7-03 application-node 门禁通过；这是 Kubernetes 应用层证据，不冒充 PG/Object/Temporal provider HA |
| Temporal 与副作用 | 最终 drain 窗口按 0.4 秒节奏提交 300 个真实 `StorageLifecycleExecutionWorkflow`，300 accepted/300 completed/300 unique IDs，123.528 秒结束，median/max accept 9.690/42.193 ms；请求固定在 activity 前 `BLOCKED`，processed IDs 为空。前后 PostgreSQL 均为 105 migrations、`storage.lifecycle_executions=0`、items=0、Outbox=0 | Temporal task 可重试/恢复但只有一个有效终态；无对象或数据库不可重入副作用。旧 Pod heartbeat age 超过 90 秒，replacement 使用不同 instance UUID，关闭 SYS1-02 的真实 Kubernetes 缺口 |
| 镜像与可重复证据 | 最终 Worker drill image `sha256:1d3aeefe867fb4c4f63bbc401b5cce36992192afe6641b802e57e11cb70264de`、504,787,206 bytes；API `sha256:717f28de0fd7f92908f5d79931e7f476bc01d3df3d2121b3a099751744a792b0`；Frontend `sha256:13c4fe2e4566f337c45488cb756139ca26f217bb5dd4f35291656ba0827b3963`；最终 Helm render SHA `6b7cb342cd710694828a74adfab7a5aab9e8c5c6b96fc0b6a2125e26e22c8f65`。`deploy/runbooks/ha-node-drain.md` 固定隔离、连续 probe、side-effect、日志和 stop 条件 | drill 使用 shared local dependencies 的 test profile，因为 MinIO 保留 local-only credential；镜像/Chart/多节点调度和进程 shutdown 均为真实执行。生产 provider、OTel 后端和真实负载容量仍分别归 HA4-02/04、OBS5-02 |

下一可独立切片进入 `HA4-02` API/Worker autoscaling 与容量门禁；RST3-04 的真实 5 TB 环境、RST3-05 的独立值班人员、HA4-04 shared-provider failover 和 release canary 仍保持独立红色门禁。

### 2026-08-29｜Stage 4 checkpoint：HA4-02 implementation / production capacity open

| 项目 | 当前事实 | 结论 |
| --- | --- | --- |
| HPA 与 Helm fail-fast | Chart 为 API/main Worker 生成 `autoscaling/v2` CPU HPA；启用后 Deployment 不再写 `replicas`，并要求 `minReplicas>=2`、`maxReplicas>minReplicas`、CPU target 1～100 和非空 CPU request。HA PDB 校验改用 HPA minReplicas；base 默认关闭，production example 明确启用 API 2～10、Worker 2～20、target 70% | 静态副本和 HPA 不争夺 `/scale`；单副本/无 CPU request/非法 target 在 render 阶段 fail closed |
| Worker 有界容量 | Settings/Helm 新增 main Worker `max_concurrent_workflow_tasks=4`、`max_cached_workflows=16`，要求 cache 不小于 task；Worker 把两项显式传给 Temporal SDK。真实首轮 2,000 workflow 暴露 SDK 默认无界/1000 cache 导致约 1.3 GiB、`Workflow task not found` 和 eviction timeout；首次 32/64 修复在 1 CPU drill 中仍会 workflow-task timeout，最终按 1 CPU guaranteed limit 收敛为 4/16 | 失败 trial 被保留并驱动生产默认修复；横向 HPA 承担扩容，禁止靠单 Pod 堆积 workflow task。修复后继承 backlog 的 2,000/2,000 execution 全部恢复为唯一 `BLOCKED`，processed IDs 为空 |
| digest-bound capacity hook | 新增最多 1,000,000 bytes 的严格 capacity file gate 和 `hc-capacity-gate-result/v1` CLI；先核对 exact lowercase SHA-256，再调用既有 `hc-capacity-evidence/v1` 全链路 validator。Chart 以 weight -20 的 pre-install/pre-upgrade Job 挂载外部 ConfigMap，只读 root/evidence、无默认 SA token、backoff 0；production example 保留具名 ConfigMap/zero digest placeholder，因此未替换前生产 render/apply 不可放行 | 本地/短时/partial/错 digest/错 JSON/缺 stage/容量不足都不能被签名自述冒充 PASS；artifact 和 release input 被同一 digest 绑定 |
| Metrics API 与多节点环境 | disposable kind v1.32.2 使用 control-plane + zone-a/b/c 三 worker；Metrics Server v0.8.1 与 Kubernetes 1.31+ 官方兼容。因 `registry.k8s.io` 超时，drill 下载官方 linux-amd64 release binary（release/download SHA `dd2455a5902d100aab7e5f7f9bcd8f715b6d39f97f161bbe87e3c24f61fa296c`）并放入本地临时 wrapper；components manifest SHA `4a672c4891902573a3ff753cece5de1bf1f55dd053403dfec39df9d1636b7ff1`。self-signed Kind 仅在 disposable drill 使用 test-only insecure kubelet TLS | 官方 binary/version 和 Node/Pod metrics 被实测；临时 wrapper 不是生产交付镜像，也不能作为 registry provenance 或生产 Metrics Server 安装证据 |
| clean autoscaling / backlog / SLO | fresh zero-restart baseline 上提交 500 个真实 `StorageLifecycleExecutionWorkflow`：500 accepted/completed/unique、全为 `BLOCKED`、processed IDs 0、总耗时 108.319 秒；API/Worker HPA 均从 2→4，API 4/4 ready 约 10 秒，Worker 4/4 约 30 秒，Temporal backlog 457→0 约 80 秒，随后两者均回到 2。独立 Service path 连续观测 933 requests/failure 0，p95 101.563 ms、max 406.285 ms；终态所有 Pod Ready/restart 0、PDB allowed 1、queue 0，Worker 无新 task-not-found/eviction timeout | 本地多节点 HPA scale-out/scale-in、Service continuity、backlog recovery 和零重复副作用通过；前后 PostgreSQL 均为 105 migrations、lifecycle execution/items/Outbox 0 |
| lease continuity | 从 baseline API Pod owner UUID `6c0fc110-3dda-46cf-a96e-ca7133ea32d0` 持有真实 PostgreSQL maintenance lease，在另一个 API Pod 施压的 2→4→2 全窗口内每 8 秒续租；160.291 秒内 20 次 renew，fencing token 始终 26、deadline 单调前移，active 采样 lease remaining 均至少 21 秒，owner Pod restart 0。最后按合同进入 `FAILED_RELEASED`，state version 2，environment fence 始终 `READ_WRITE` | 扩缩容没有更换 owner/token、没有让 lease 过期或错误切换读写 fence；数据库时钟和持久化 lease 是 authority，不依赖 HPA replica 数 |
| 负向 release gate 与恢复 | 用 exact digest `sha256:d3dec9b5b283e10163485a7a87a4c1633b9b9a1798a8e7490d02b8ce1eb87b97` 绑定一个明确 `LOCAL`/incomplete artifact；pre-upgrade Job 在约 7 秒内 `BackoffLimitExceeded`，日志逐项拒绝 environment/stage/target/workload/recovery/cleanup，live 四个 Deployment digest/ready 均不变。Helm revision 8 记录 failed，随后精确 rollback revision 7 形成 deployed revision 9；失败 Job/ConfigMap 采证后按确切名称删除 | gate 在 workload mutation 前 fail closed，失败 release 可审计且可恢复；没有使用 `--no-hooks` 或修改 evidence 绕过 |
| 镜像、runbook、清理与未关闭验收 | 最终 API drill image `sha256:0baa71a9c55056830e1cf2065aa12d061c86bc9d43527660d7ade6303cff5dde`、Worker `sha256:d9415cb93267ec780a65c6f1479494b9f55f86a6a0ae99e6bf1a27699f574440`（504,798,780 bytes）、Frontend `sha256:13c4fe2e4566f337c45488cb756139ca26f217bb5dd4f35291656ba0827b3963`；`ha-autoscaling-capacity.md` 固定 evidence、lease、SLO/backlog、失败恢复和 stop 条件。最终 focused 19 passed，Ruff、mypy 3 source、Helm lint、`git diff --check` clean；CI render SHA `060a96fef41dba938815770c8b8d5851e46bbd765f0f3f70f650ff4acec5dd5e`。kind cluster/network、task DB、临时 evidence/binary、superseded Worker 和 Metrics wrapper 均精确删除，残留零；shared Compose healthy。clean acceptance DB 仍 105/105 current；默认 `hc_data` 仍为原 99 applied、annotation 0010/0011 drift 和六项 missing，未触碰 | 本地 autoscaling mechanics 与负向 gate 已完成；没有生产等价全链路 5 TB/day + 30%、>=30 分钟 artifact，故 HA4-02 仍为 `进行中`，不能把本地 workflow 数量或 CPU load 外推为容量 PASS |

下一可独立实施切片进入 `HA4-03` 进程内状态、文件和本地缓存审计；HA4-02 的真实生产等价容量 artifact、RST3-04 5 TB planned migration、RST3-05 独立值班演练和 HA4-04 provider failover 均继续保持红色门禁。

### 2026-08-29｜Stage 4 checkpoint：HA4-03 durable process state / real replacement

| 项目 | 当前事实 | 结论 |
| --- | --- | --- |
| 生产状态合同 | `Settings` 在 staging/production 对正常 API 与两类 Worker 强制 `runtime_backend=production`；alignment/preview 根目录必须是互不重叠的 `/tmp/hc-data` 后代。`platform-runtime-state-audit.yaml` 枚举 PostgreSQL session/幂等、PostgreSQL+S3 upload、Temporal+PostgreSQL task 四个 durable surface，禁止进程内/Pod 文件作为 source of truth | test-only memory backend 不能被普通生产 composition 显式启用；本地 scratch 只允许作为可重建 attempt state |
| bounded emptyDir | Frontend `/tmp=64Mi`、API `/tmp=256Mi`、main Worker `/tmp=20Gi` + `/tmp/hc-runtime=16Mi`、media Worker `/tmp=20Gi` + runtime `16Mi`；所有值均由 Helm `required` 校验，非法空值 render fail closed。API/Worker 两轮 drain 前写入的专用 sentinel 在 replacement 均不存在且没有复制 | 所有应用 Pod 临时卷有界；正确性路径没有读取旧 Pod 文件，scratch identity 随 Pod UID 一起更换 |
| session 与幂等 | 旧 API instance `d3968a17-ed3a-4dfe-9722-51c4b67bed4c` 创建 opaque session 和 profile mutation；两轮 node drain及全 API/Worker rolling update 后无需重新登录，原 token 仍解析同一 principal。revision 固定 3，唯一 durable idempotency row 的 replay response SHA 始终为 `ab318f07bbf16bbc4ac924468fe81fd6ff84eb4effa1300d11080d45b7afcf18`，replay 后没有第三条 profile audit mutation | session 与幂等 authority 均为 PostgreSQL；API 进程/Pod identity 更换不丢失或重复业务 mutation |
| 真实上传连续性 | 专用 MinIO bucket/user 初始化一个真实 multipart；应用先保存同一 session/provider multipart identity 为 `PAUSED`。替换 API 后可 resume/list/pause；再经隔离 TLS browser edge 使用应用新签发的 HTTPS presigned URL 实际 PUT part 1（30 bytes），API reconcile 为 `UPLOADED`。第二次 node replacement 后相同 provider identity、part ETag/size 和 `PAUSED` 状态仍可读；最后通过 API `cancel`，provider incomplete upload 与 bucket/user 均精确清理 | PostgreSQL 保存控制状态、MinIO 保存 payload/part；presigned URL 可更新但不是 durable state，真实 part 跨 replacement 保持 |
| Temporal 任务与副作用 | 第一轮 drain 的 backlog 窗口提交 500 个真实 `StorageLifecycleExecutionWorkflow`，1.702 秒 accepted，142.407 秒全部完成；500 unique、终态均为 protection `BLOCKED`、processed IDs 0。前后 task DB 为 105 migrations，`storage.lifecycle_executions=0`、items=0、workflow jobs=0、Outbox=0；replacement Worker 使用不同 UID 且 restart 0 | Temporal history/task queue 是 Worker ownership authority；节点替换没有丢任务或产生不可重入副作用 |
| Kubernetes 与交付证据 | disposable Kind v1.32.2 使用 4 eligible nodes/2 zones、四类 workload 各 2 replicas/PDB minAvailable 1。对承载旧 API/Worker 的 worker node 和首个 replacement 所在 control-plane 各执行一次无 `--force`、无 PDB bypass 的真实 drain；每次恢复 2/2，最终 Pod restart 全 0、PDB allowed 1。后续 public-endpoint rolling update 也保持原 session/upload/idempotency。API image `sha256:f86b80491eedc9707b31f612d82fa45be5fbd3431e2625496f718e2d9be2472f`（504,846,877 bytes），Worker `sha256:8b3da4dd88e6da95b0937d26e244b9bd8651ae0d94ea13712c434ac8724d27b9`（504,854,982 bytes），drill render SHA `70db963c0ed47e3b6ce09bf3e9ad9ff21bbc1df3dc17a3b22b5fbf700f76eafd` | HA4-03 验收关闭；`ha-state-restart.md` 与 machine audit 固化复演合同和已接受 evidence。该证据不外推为 shared provider failover、5 TB capacity 或 observability backend PASS |
| 回归与清理 | focused 49 passed；Ruff、mypy 3 source、Helm lint、`git diff --check` clean。Kind cluster/empty network、task DB、MinIO bucket/user/incomplete multipart、TLS proxy、port-forward、token/cert/tmp、HA4-02 superseded API/Worker images均按精确 identity 删除；只保留最终 HA4-03 images。shared Compose 全部 healthy，clean acceptance DB 105/105 current；默认 `hc_data` 的既存 99 applied、annotation 0010/0011 drift 与六项 missing 未触碰 | 演练凭据和不可恢复临时数据残留为零；共享服务、acceptance 与已知漂移基线保持原状 |

下一可独立实施切片进入 `HA4-04` shared PostgreSQL/Object/Temporal HA、故障切换与 Secret 一致性；HA4-02 的真实生产等价容量 artifact、RST3-04 5 TB planned migration、RST3-05 独立值班演练仍保持红色门禁。

### 2026-08-29｜Stage 4 checkpoint：HA4-04 shared-provider failover / Secret consistency

| 项目 | 当前事实 | 结论 |
| --- | --- | --- |
| 稳定 endpoint 与 Secret 合同 | 新增 `shared-provider-ha.yaml` 与 `ha-shared-provider-failover.md`，固定 PostgreSQL writer、versioned HA S3、external-managed Temporal 三类稳定 endpoint，顺序故障注入、旧 writer fencing、authority reconciliation 和职责边界。staging/production 新增非零 lowercase SHA-256 `HC_SECRET_BUNDLE_REVISION` fail-closed guard；HA Helm render 同样拒绝 missing/unversioned/zero/malformed revision；四 backend instance 均使用同一 revision | provider failover 不修改应用 DSN/target、不重启应用、不生成 credential；Secret 只允许新 immutable bundle 后整体 rolling，禁止 partial update 或动态 credential hot reload |
| 真实 PostgreSQL writer failover | disposable PostgreSQL 16.10 primary/standby 使用 streaming synchronous replication；故障前 writer/standby 均到 committed LSN `0/46E78F0`。终止 writer、promote caught-up standby 并只切换 provider VIP 后，两个 API 在 28.186 秒内恢复；原 opaque session 可用，pre-failover profile response SHA `b111da4745b68e7740cf1423bca951a34518cf984f5341dfd6801de104385a3d` byte-exact replay，幂等 row 仍 1，新 write 到 revision 3，105 migrations current | 旧 writer 保持 stopped/fenced，promoted node `pg_is_in_recovery=false`；replay 没有第二次 mutation，两个成功 audit 对应 pre/post 两次不同 write |
| 真实对象节点 failover | 四节点 distributed MinIO erasure set 初始 4/4 healthy；终止 VIP 后的 serving node 并只切换 provider target 后，剩余 3/3 quorum 与两 API 在 16.481 秒内恢复。versioning 保持 Enabled，原 version SHA `f8703720dfe7d3b9ffb85a69096b509540619490fac7c678f19128ba2386b310` 不变，同一 incomplete multipart 的 part1 保留，fresh presigned PUT 增加 part2，新 versioned object write 成功 | 对象 bytes/version 与 multipart authority 跨节点故障保持；没有更换应用 endpoint/access key/secret，也没有把本地文件当 payload authority |
| 真实 Temporal frontend failover | 两个 Temporal 1.25.2 all-in-one process 共享同一 promoted PostgreSQL provider/namespace `1e3fccb1-cfe3-43ac-ad4f-d46cd7907c78`。在 300 个 timer workflow 全 in-flight 时终止 serving frontend；authority visibility 在 38.785 秒内达到 300 Completed、0 Running/Failed，fresh client reconciliation 为 300/300 unique exact result；随后 20 个新 workflow 与一个 packaged main-Worker `StorageLifecycleExecutionWorkflow` 均完成，两个原 main Worker poller 出现在 surviving frontend | 首个 observer history long-poll 在 socket loss 时被取消，但 workflow authority 和同一 Worker 的 SDK retry 均完成；runbook 明确要求 fresh-client/visibility reconciliation，不能把单 observer socket 当 workflow state |
| 应用连续性与副作用 | 两 API、两 main Worker 的 exact container ID 全程不变，三次 provider event 后 restart 均 0、health 全 healthy，三个稳定 endpoint env 完全未变。task DB 最终 storage lifecycle execution/item、workflow jobs 均为 0；workflow timer 没有业务 side effect，database/workflow duplicate 均为 0 | HA4-04 自动恢复验收通过；同 Secret revision 和 zero restart 证明没有用应用 rollout/credential rotation 掩盖 provider recovery |
| 回归、Compose 修复与清理 | focused 76 passed；Ruff、mypy config、Helm lint/render、Compose config、release compatibility SHA 与 diff check clean。清理时 MinIO batch delete 的 `Content-MD5` stop condition 被保留，改为逐 exact version 删除；最终 multipart abort、object versions/bucket、17 containers、6 volumes、1 network、task database/Temporal history/credential/tmp 全清零，superseded HA4-03 images 删除，只保留 HA4-04 images。清理复核发现 Compose Worker 缺 `/tmp/hc-runtime`，已为 dev/test main/media Worker 增加 bounded 16 MiB tmpfs并只重建三 Worker，全部 healthy。acceptance DB 105 current；默认 `hc_data` 仍 99，既存 drift/missing 未触碰 | HA4-04 local multi-process protocol drill 关闭；same-host Docker、community MinIO/self-managed Temporal 不外推为 production provider SLA、correlated host/region loss、外部 Secret Manager 操作、容量或 backup restore PASS |

下一可独立实施切片进入 `HA4-05` allowlisted runtime config revision 同步；HA4-02 的真实生产等价容量 artifact、RST3-04 5 TB planned migration和 RST3-05 独立值班演练仍保持红色外部门禁。

### 2026-08-29｜Stage 4 checkpoint：HA4-05 runtime config revision convergence

| 项目 | 当前事实 | 结论 |
| --- | --- | --- |
| 精确 allowlist 与 release 边界 | release matrix 只开放 `scheduling.preview_gc_interval_seconds`、`scheduling.storage_inventory_interval_seconds` 和 `ui.maintenance_banner_enabled`；严格类型/范围验证。DSN、password/Secret/token/credential、TLS/certificate、镜像、schema/migration 和任意 unknown key 在 repository 写事务前拒绝；revision 0 从每个进程经验证的 release boot interval 构造，禁止小数秒静默取整 | 热加载没有成为 Secret 或 release identity 旁路；其他配置仍必须新 release + rolling restart。拒绝请求的值不进入 response、log 或 P19 audit |
| PostgreSQL authority 与回滚 | 新增 `platform/005_runtime_config_revisions.sql`：environment head、完整有效值 snapshot、append-only APPLY/ROLLBACK event 和节点 `applied_config_revision`。head row lock + expected revision 做 CAS；内容 canonical SHA-256；revision/event trigger 禁止 UPDATE/DELETE；rollback 复制目标 snapshot 到新的递增 revision，不回退 head | migration manifest 106；revision/history 是 PostgreSQL authority，通知只是 hint。clean acceptance DB 由 105 精确前进到 106/current；默认 `hc_data` 保持 99 且无新表，既存 annotation drift/missing 未触碰 |
| API、capability 与 P19 | 正式/runtime OpenAPI 新增 current、history、publish、rollback 四个 operation并重生成 client。GET 仅 exact `platform.operations.read`/`platform.admin`；mutation 仅 exact `platform.release.operate`。审计包含 actor/request/capability、environment、schema/revision、changed key 名和 reason 已记录标志，不复制配置值或 reason 正文；安全矩阵枚举全部 261 paths/294 operations | anonymous/错 capability、unknown/sensitive key、非法值、stale CAS、missing target 的 401/403/422/409/404 均稳定脱敏；运行配置变更进入 P19，职责不被 `platform.admin` 通配 |
| API/Worker 应用 | staging/production API、main Worker、media Worker 启动同步器；API 本地 state 和 Worker preview GC/storage inventory 每轮下一次 sleep 读取新 snapshot。PostgreSQL `LISTEN/NOTIFY` 触发立即 refresh，29 秒 authoritative poll 为丢通知后的数据库读留出 1 秒预算。所有 process heartbeat 报告 applied revision；local/test 使用 release baseline + in-memory adapter，旧本地节点目录显式投影 revision 0 | 低风险配置真正被 consumer 使用，不只是保存/展示；staging/production 缺 migration/列会 fail closed，local default DB 不因开发热重载被隐式升级 |
| 真实通知丢失与多进程证据 | PostgreSQL 16.10 disposable DB 上，一个 listener follower 与一个完全不接收通知的 polling follower 从 revision 0 收敛到同一 revision/digest；即时节点断言 `<2s`，丢通知节点使用生产 29 秒 poll 并硬断言从 publish 开始 `<=30.0s`。随后两个独立 Linux OS process 重演相同分支并各自写入不同 instance heartbeat，数据库精确显示两行 `applied_config_revision=1`。首轮 `spawn` harness 因仓库 tests 非 package 在写入前 bootstrap 失败，保留失败后改为 Linux `fork`，最终 multiprocess `1 passed/1 deselected in 31.20s` | 通知丢失、独立 process、同 revision/digest 和节点报告四项验收关闭；没有把缩短 poll 的单元测试冒充真实 30 秒证据。Secret key 尝试产生 0 新 revision/event；历史 UPDATE 被 immutable trigger 拒绝 |
| 回归、清理与基线 | affected 52 passed/2 explicit migration-harness skips；runtime-config PostgreSQL 两项真实集成分别通过；首次全量仅因 BE23 新路径未枚举为 1 failed/1134 passed，补齐安全矩阵后定向 12 passed，最终标准全后端 `1135 passed, 118 explicit external skips, 2 known xfailed`。Ruff format 515、lint、mypy 252、OpenAPI/client、TypeScript、diff clean。disposable runtime-config DB 残留 0；shared API/3 Worker 和 provider services healthy | HA4-05 完成；external skip 与既有 BE12-001/008 XFAIL 未记为 PASS。HA4-02 生产容量、RST3-04 5 TB/RPO 0 和 RST3-05 独立人员门禁保持红色 |

下一可独立实施切片进入 `OBS5-01` 统一 JSON logging、redaction 和 request→workflow→operation 关联；HA4-02 的生产容量 artifact、RST3-04 真实 5 TB planned migration 与 RST3-05 独立值班演练继续保持外部门禁。

### 2026-08-29｜Stage 5 checkpoint：OBS5-01 structured logging / redaction

| 项目 | 当前事实 | 结论 |
| --- | --- | --- |
| 固定 JSON 合同 | 新增 `runtime-logging-contract.yaml` 与 fail-closed Python formatter/filter。API、main Worker、media Worker、Frontend Nginx、Compose gateway 统一提供 `hc-runtime-log/v1` 和计划要求的 15 个字段；缺值保留 JSON `null`。Python message/args/traceback/exception text/unknown extra 在 stdout 或 OTel handler 前清除，只有 event code 与 allowlisted class/数值/route template 可出站；异常或 Secret-like locator 只保留 `id-sha256` | 运行日志不再依赖自由文本；P19 audit 继续独立 append-only，中心存储/retention 不由应用日志冒充 |
| 请求、工作流和操作关联 | 外层 HTTP middleware 验证或生成 UUID request ID、记录 framework route template/方法/状态/耗时；Temporal start event 继承 request ID，并以 durable workflow job ID 同时填充 operation/workflow ID；Activity 从 Temporal context 继续传播两者。活动外直接执行保持 null-safe；active OTel span 只接受合法 32 位 lowercase trace ID | 单个 `WORKFLOW.STARTED` 可直接连接 request→operation→workflow，Worker event 沿 operation/workflow join；不记录 project/user/object locator 高基数 scope |
| Gateway 与真实输出 | Frontend entrypoint 原子生成 Nginx `log_format`，Frontend 和 Compose gateway 只记录固定 `/`、`/api/`、健康路径，不引用 URI/query/header/cookie/body；gateway 优先使用 API response `X-Request-ID`。两份配置在实际 Frontend image 内 `nginx -t` 成功；disposable live Frontend 对带 query sentinel 的真实 HTTP request 仅输出一条可解析 JSON、sentinel zero match 后容器清零。独立 API process 对 query+Cookie sentinel 输出 release/access/httpx 三条 JSON，sentinel zero match | Frontend/gateway/backend stdout 生产者验收关闭；Nginx 固定 error stream 不被当作 access/event payload。节点丢失后中心可查仍属于 OBS5-02 |
| Redaction verifier 与回归 | `verify_logs.py` 改为要求固定 schema/字段并拒绝 unknown/sensitive key、Bearer/JWT、signed URL、DSN 和 email-shaped PII；敌意 exception/password/token/Cookie/object body/DSN/query 单元测试全部 zero match。首次全量因 load harness 假设 stdout 只有结果而失败，保留生产日志并把 harness result 改成最终单行 JSON；第二次全量发现旧 verifier fixture，迁移固定 schema 后最终 `1141 passed, 118 explicit external skips, 2 known xfailed`。BE12-002 contextual log XFAIL 已成为普通 PASS；Ruff 554、mypy 253、diff clean | OBS5-01 完成。两项剩余 XFAIL 仍为 preview activity dependency 与真实 5 TB/day+30% capacity，不属于日志验收；外部 skip 不记为 PASS |
| 可复验证据 | structured logger SHA `30dd7923b14151cad5229e2bfdb1695eb90bf80bb64be4308f21ed3333fd6b3a`；machine contract SHA `111b095ebd243577b61bff653b3435cfb98388ddc5443f459beef10d203b16e2`；Frontend entrypoint/Nginx SHA `622b9153f57fd1c6ba61bea4fa21d98ec102a8c963e748625800ba496fe5a8a1` / `8ec9602236d9d103835b4c874082420b24f570170f0ea705d33052724d2f018c`；Compose gateway SHA `c4a38720cafefbf96d7504cd2346ce4b7c411909aed3756c1327c4258a454f29`；operator procedure 固化在 `observability-runtime-logging.md` | 证据只证明 producer/schema/redaction/correlation；Collector/Loki/dashboard/alert delivery 和 kill-node searchability 不在本 checkpoint claim 内 |

下一可独立实施切片进入 `OBS5-02` OTel Collector、中心日志后端、dashboard 与 alerts，并执行 kill-node 后日志查询和关键告警端到端送达；HA4-02/RST3-04 的真实容量及 RST3-05 独立人员门禁继续保持红色。

### 2026-08-29｜Stage 5 checkpoint：OBS5-02 central observability / node-loss retention / alert delivery

| 项目 | 当前事实 | 结论 |
| --- | --- | --- |
| 有界指标与部署合同 | Python 指标移除 project/user/object/resource/workflow/request 等高基数 label，只保留 route template、method、status class/code 及有界 outcome/kind/profile/format/view mode。Helm 默认关闭；启用后部署 2 个 digest-pinned OTel gateway、Frontend file-log agent DaemonSet、PDB、实际 config checksum rollout、native OTLP Loki、PrometheusOperator rule/ServiceMonitor 和可选 managed Grafana；production 强制外部持久 Loki，内嵌 Loki 只允许 non-production 且默认 PVC | request/workflow/object 等 correlation 只进入已脱敏日志/trace，不进入 metric labels；生产不会静默使用单机临时日志盘或 chart 内明文 Grafana admin credential |
| Dashboard 与告警面 | `HC Data Platform Operations` dashboard UID `hc-data-platform-operations` 在真实 Grafana 13.1.0 API 可查；18 条 promtool-valid 规则覆盖 HTTP 5xx/429、workflow/outbox/Temporal、backup freshness/failure/deep verify、instance/version/config drift、migration/object replication/audit/collector/db/disk 和 synthetic critical canary；runbook 为每个规则提供精确响应锚点 | dashboard 模板仅 service/release/event 等有界维度；告警处置和复演入口已固化，不把 OBS5-03 的 append-only P19 审计归入日志后端 |
| 关键告警真实送达 | 三节点 Kind v1.32.2 中，`HcObservabilityDeliveryCanary` 于 `03:00:36.797Z` 进入 firing，真实 Alertmanager receiver 于 `03:00:38.542195Z` 收到；fingerprint `acf02160f46e5faf`，labels 精确为 critical/observability/synthetic。撤销 canary 后 alert 于 `03:01:24.797Z` 结束，receiver 于 `03:01:27.042893Z` 收到 resolved | firing 与 resolved 两个方向均完成端到端送达，不以规则静态校验或 Prometheus pending 状态冒充通知成功 |
| 节点丢失后中心查询 | 日志 producer 位于 `obs502-worker2`，观测后端位于 `obs502-worker`。真实停止 producer node 后，其 Kubernetes 状态达到 `Unknown/NodeStatusUnknown`；Loki 对 request `fa8fe9d9d5e9561d59a64330aad254e1` 的查询在停止前和 Unknown 期间均返回同一条 `HTTP.REQUEST_COMPLETED`、同一 timestamp ns `1787973022843678264`，结果 byte-equivalent；Grafana health/dashboard 在另一节点持续可用 | kill-node 历史日志可查验收关闭；证据证明已摄取记录的中心持久可查，不声称 trace retention、生产跨区 Loki SLA 或无限保留 |
| 可复验证据、回归与清理 | retained machine evidence 为 `obs5-02-drill-summary.json`；Collector/agent/dashboard/rules/Helm manifest SHA 分别为 `bbeb66ef…3733`、`97df844f…384dc`、`468ec256…241d`、`a17e9019…b290`、`13bcf3f…5468`。精确 OTel 0.157 config validation、promtool 18 rules、amtool、Loki verify、Grafana live provisioning、Helm lint/render、Ruff 全源通过；最终标准后端 `1145 passed, 118 explicit external skips, 2 known xfailed`。演练 Kind/Secret/临时文件全部清零；默认 `hc_data` 99 applied/drift 未迁移；local API/main/media Worker 改为不启用数据库 writer fencing、三 Worker 最终 healthy，staging/production 仍 PostgreSQL fail closed | OBS5-02 完成。两项 XFAIL 仍为 production activity dependency 与 5 TB/day+30% capacity；外部 skip、生产 Loki/provider SLA、HA4-02/RST3-04/RST3-05 均未伪造 PASS |

下一可独立实施切片进入 `OBS5-03`，将 backup/restore/release 事件接入 P19 append-only 审计并验证 capability 脱敏导出；HA4-02/RST3-04 的真实容量及 RST3-05 独立人员门禁继续保持红色。

### 2026-08-29｜Stage 5 checkpoint：OBS5-03 global P19 audit integrity / redacted export

| 项目 | 当前事实 | 结论 |
| --- | --- | --- |
| 全局 append-only hash chain | 第 107 个 forward-only migration `platform/006_platform_audit_integrity.sql` 为 `scope_kind=PLATFORM` 建立单一 sequence/predecessor/SHA-256 chain、head、确定性历史 backfill 和 AFTER INSERT 原子触发器；source audit 原有 UPDATE/DELETE trigger 与新增 integrity entry UPDATE/DELETE trigger 均 fail closed。server-side verifier 重算 source canonical hash、sequence、predecessor 与 head，不信任存储的结论 | backup/restore/release 继续在各自业务事务内写原全局 P19 source；不伪造 tenant project，平台事件拥有独立可验证链。head mismatch、缺 entry 或 hash drift 均返回 `FAILED`，导出自动 409 |
| API、职责与脱敏 | 正式/runtime OpenAPI 新增 list/integrity/export 三个 GET，并重生成 generated client。list 只允许 exact `platform.operations.read` 或只读兼容 `platform.admin`；verify/export 只允许 exact `platform.maintenance.verify`，release/backup/restore/break-glass operator 不继承导出权。list 使用 snapshot + `(occurred_at,event_id)` signed cursor，cursor 绑定 timezone-aware `[from,to)` filter；limit≤200，export≤10,000。actor/resource 只出 HMAC ref，`safe_details` 值永不出站，只保留 allowlist key 名，response/JSONL 均 `private, no-store` | 普通平台查看者可查脱敏页但不能导出；审计 verifier 与业务 operator 职责分离。request ID 仅作受保护 correlation；Secret、URL、repository/environment/object locator 与原 actor/resource ID 不出站 |
| 真实 PostgreSQL 业务路径 | fresh task-owned PostgreSQL 16.10 精确应用 107/107 migration。测试调用签名 golden manifest 的真实 `BackupCatalogService.rebuild_from_repository_artifacts`，以及真实 `PostgresMaintenanceRepository.request_operation` 的 RESTORE/RELEASE 路径；三个业务事件被分类为 BACKUP/RESTORE/RELEASE，首轮 integrity 为 PASS、checked events=3/chain=1。source UPDATE 与 integrity entry UPDATE 均被数据库拒绝；随后仅在 disposable DB 篡改 head，integrity 变为 FAILED 且 export 409 | 不是直接插入模拟业务事件；同事务生产路径接入与篡改响应已验收。测试数据库最终精确删除，默认 `hc_data` 仍为既存 99 migrations、drift 未迁移 |
| 导出与可复验证据 | verifier 导出当时 snapshot 的 5 条 JSONL，SHA-256 `042d791e9ccf48da250894b0cb9c4efdd75fe55df7f031a470ba51a19a86d931`；actor/resource、`backup-repository-cn`、`production-cn-east`、URL/object/secret marker 全部 zero match。错误 release capability 为 403；篡改后导出为 409。machine evidence 固化于 `backend/tests/system/results/obs5-03-audit-summary.json`，operator procedure 固化于 `deploy/runbooks/platform-audit-integrity-export.md` | 导出 payload 与链校验绑定，完整性失败不生成 artifact；runbook 禁止生产内改 head、重建链或直查数据库绕过 capability |
| 指纹、回归与清理 | migration/manifest/OpenAPI/generated operations client SHA 分别为 `bed69e95…a584`、`a52d9411…a4c`、`27d9468c…6a9`、`98bf2430…65d`；release compatibility baseline 更新为 107。真实集成单独 `1 passed`；格式后 focused `18 passed/1 explicit external skip`；最终标准后端 `1152 passed, 119 explicit external skips, 2 known xfailed`（319.66 秒）；Ruff 521 files、mypy 254 source、OpenAPI/client drift、Frontend typecheck、`609 passed` 和 3,773-module production build 全通过。task DB/临时日志残留零，dev API/Frontend/三 Worker 与 provider healthy | OBS5-03 完成。119 个标准 skip 不记为 PASS，其中真实 OBS5-03 PostgreSQL 项已用独立 DSN 执行；两项 XFAIL 仍为 production activity dependency 与 5 TB/day+30% capacity。HA4-02/RST3-04/RST3-05 外部门禁不受影响 |

下一可独立实施切片进入 `UI5-01`，实现版本、节点、备份、日志和升级状态 UI，并以 Mock-off E2E 验证普通管理员不见 Secret/主机敏感细节；HA4-02/RST3-04 的真实容量及 RST3-05 独立人员门禁继续保持红色。

### 2026-08-29｜Stage 5/6/7 checkpoint：UI5-01、REL6 实现与 DR7 签名门禁

| 项目 | 当前事实 | 结论 |
| --- | --- | --- |
| UI5-01 运维页 | 新增 `/settings/platform-operations`，并行加载 host-redacted version/node/backup/preflight、受限 Loki 日志和 release history；严格 generated API + Zod runtime contract。状态 rail 现在独立显示 release identity、四角色节点/config、当前备份、`RESTORE_VERIFIED` 恢复演练和中心日志；升级历史显示 append-only steps。只有 exact `platform.release.operate` 才显示批准动作，普通 `platform.admin` 不通过前端 capability implication 获得按钮 | Mock-off network 测试覆盖真实 GET filter 与批准 POST；fixture/response 不含 hostname、Pod/node locator、repository/environment、Secret 或 cluster credential。UI5-01 实现完成；真实浏览器仍不执行集群 mutation |
| SYS1-01 Frontend identity | Frontend Pod 增加最小 `hc-frontend-heartbeat` same-Pod sidecar：固定 localhost health、30 秒 PostgreSQL heartbeat、Frontend 精确镜像 digest、Pod UID/identity Downward API；只挂 PostgreSQL DSN，不挂 ServiceAccount token、对象/KMS/应用 Secret。平台节点目录所需角色变为 frontend/api/worker/media-worker 四类 | 所有组件的统一 identity surface 实现关闭；新 sidecar 的真实 Kubernetes replacement/同 Pod 验收尚未执行，因此 SYS1-01 仍以外部验收项保留，不把 Helm render 冒充 K8s PASS |
| REL6-01/05 发布控制面 | Ed25519 pinned-key monotonic signed feed、canonical digest、降级/伪造/不兼容拒绝；正式 preflight/approve/history/transition API；PostgreSQL durable CAS state 和 immutable event；请求者与批准者四眼；actor 仅 HMAC ref；P19 route/security matrix。浏览器只做人工批准，controller transition 覆盖 EXPAND/CANARY/ROLLOUT/CONTRACT/rollback/failure | focused HTTP 证明伪造 signer 不落 history、自批准拒绝、distinct approval 与 1→6 controller progression；一次性 PostgreSQL 16.10 空库实跑持久状态最终 `COMPLETED:6`，验收库已删除，默认 `hc_data` 仍 99 |
| REL6-02 数据库阶段 | 新增严格 `phases.json`；108 个当前 migration 全属 expand，contract 列表为空。`upgrade-expand`/`upgrade-contract`/phase status 共用 session advisory lock 和 checksum ledger；contract 在连接前要求 signed approval digest，且不会 replay expand 的 repeatable security DDL。Helm expand 是 pre-upgrade hook；contract 是独立、默认关闭、非 hook Job | 一次性 PostgreSQL 空库 `108/108 current`，contract `0/0 current`；Helm 无批准或 digest 非法均拒绝。真实 0.1.0+0.1.1 双版本 schema 执行必须等 0.1.1 不可变制品，REL6-02 保持逐 release 外部门禁 |
| REL6-03/04 canary 与 Worker routing | `hc-release-controller canary` 要求 API+Frontend 各 ≥300 秒/1,000 request、5xx≤1%、p95≤500ms、全 ready、target digest；样本不足 HOLD，回归返回 exit 30 与四组件 exact source images。Temporal controller 对 main/media 固定 task queue 安装 target compatible-default，Worker 从 release identity 取 Build ID并保留 source rollback window | 注入 5xx/延迟测试得到 exact rollback payload，credential-free CLI 无 kubeconfig/cluster-admin；routing 两队列全覆盖。真实 target canary、old/new history replay 和 production Temporal version 未执行，继续由矩阵 blocker 与 DR7-05 拦截 |
| 恢复/DR 发布门禁 | Upgrade preflight 不再把 `INTEGRITY_VERIFIED` 冒充恢复演练：新增独立 `VERIFIED_RESTORE`，没有当前 release `RESTORE_VERIFIED` 即 BLOCKED。新增 `hc-platform-dr-evidence-bundle/v1`：精确包含 DR7-01～06、同 release digest、三职责互斥、无 exclusion、cleanup=true、≤31 天；Ed25519 pinned signer 验证，target signed manifest 再绑定 bundle SHA。新增 rolling upgrade 与 DR7 总 runbook | 篡改、错 release、过期、缺场景、职责复用和 exclusion 全拒绝。该机器门禁只验证 envelope/身份/职责/时效，不把本地/合成演练转成 production PASS；DR7-01/02/04/05/06 真实执行仍为红色 |
| 指纹与门禁 | compatibility baseline 更新为 migration 108、当前 OpenAPI/Helm SHA 和 Worker routing `ENABLED`；0.1.1 edge 仍为 `DESIGN_ONLY_TARGET_ARTIFACTS_NOT_BUILT`，明确保留 target 未构建签名、production Temporal version、5 TB/day+30%、backup/restore、签名 DR7 bundle 和 adjacent bidirectional execution blockers | REL6 实现不能解除真实发布阻断；未构建 0.1.1、未执行生产等价演练时绝不写 `READY_FOR_CANARY` 或 `RELEASED` |
| 最终回归与清理 | 标准 backend `1190 passed, 120 explicit external skips, 2 known xfailed`（305.87 秒）；release PostgreSQL 专用临时库另行 `1 passed`。Frontend `107 files/615 passed`，P22 targeted 3 passed，ESLint、typecheck、generated client、4,025-module production build通过。Ruff 576、mypy 263、OpenAPI、Helm strict lint/render、Compose dev/test config 与 `git diff --check` clean。临时 PG 验收库不存在；默认 `hc_data` 精确仍为既存 99 | 两项 XFAIL 仍是 production activity dependency 与真实 5 TB/day+30%；120 个外部 skip 未计为 PASS。回归证明当前实现收敛，不改变外部红门禁 |

本 checkpoint 已完成所有可在当前工作区实现的 UI、发布控制面、迁移分相、canary/routing、签名 DR evidence 和 operator procedure。计划整体仍未达到 Definition of Done：RST3-04/HA4-02 的真实容量、RST3-05 独立值班演练、真实 0.1.1 双版本/Temporal history、DR7-01/02/04/05/06 生产等价执行以及连续三次恢复演练均需要外部环境、目标制品或独立人员，不能由本地代码代做。SYS1-01/03 的 Kubernetes 缺口已由下方 Stage 1 最终 checkpoint 关闭。

### 2026-08-29｜Stage 1 最终 checkpoint：SYS1-01 / SYS1-03 Kubernetes 验收

| 项目 | 当前事实 | 结论 |
| --- | --- | --- |
| SYS1-01 same-Pod identity | 三节点 Kind v1.32.2 中 Frontend 与最小 heartbeat sidecar 分别使用精确 digest，Pod label/annotation、`/config.js`、JSON access log 和 PostgreSQL node directory 绑定同一 release；两个容器均无 ServiceAccount token。cordon 原节点并删除 Pod 后，新 Pod 落到另一节点并取得新 Pod UID，旧 UID 由 PostgreSQL 时钟判定 stale，新 UID ready，二者 restart 均为 0 | `backend/tests/system/results/sys1-01-kind-frontend-identity-summary.json` 为机器证据；SYS1-01 关闭 |
| SYS1-03 singleton task lease | 新增第 109 个 forward-only migration，按 `(environment_id, task_id)` 保存数据库时钟 lease、不可复用 owner/lease UUID、单调 token 和 immutable event；storage inventory 每 scope 使用 hash identity，Worker 以 Pod/instance UUID 竞争并在提交事务内同时复核 task lease 与 writer permit | 本地真实 PostgreSQL 证明 active owner 互斥、过期接管、read-only 拒绝、旧 owner 未提交事务回滚和 event 不可改 |
| 多节点 disconnect/reconnect | owner A 在 zone-c 成功提交并连续续租后，仅其 Pod→PostgreSQL 流量被 node firewall REJECT；zone-b 的 owner B 等待 database-clock expiry 后以新 lease UUID 和 token `2 → 31` 接管并提交。移除 rule 后，A 的旧 lease 续租和提交都返回 `PLATFORM_TASK_LEASE_STALE` | 数据库最终只有 A 断连前与 B 接管后的两条事实，无 A 恢复后写入或重复副作用；`backend/tests/system/results/sys1-03-kind-task-lease-summary.json` 为机器证据；SYS1-03 关闭 |
| 迁移、发布、回归与清理 | Helm revision 2 只追加 `platform/008_platform_task_leases.sql`，PostgreSQL 16.10 为 109/109 current；probe 三 Pod 使用同一 API digest、不同节点、restart 0、无 SA token。focused 68 passed；最终 backend 1195 passed/121 explicit external skips/1 known XFAIL，Frontend 107 files/615 passed，Ruff541/mypy264/OpenAPI/client/Helm/Compose/diff clean。fault rule、三节点 cluster、容器/network/context、三个 task image tag 和单个临时 values 文件均精确清理，残留为 0 | Stage 1 四项任务全部关闭；唯一 XFAIL 是不可伪造的 30 分钟 production-like 5 TB/day+30% 容量证据；本地 Kind 证据不外推为 production release 或其余 DR7 PASS |

Stage 1 关闭不改变计划整体红门禁：RST3-04/HA4-02 仍需真实 5 TB/6.5 TB 和 5 TB/day+30% 容量环境，RST3-05 仍需未参与开发的值班人员签字，REL6-02/04 仍需真实签名 0.1.1 制品与 production Temporal history，DR7-01/02/04/05/06 和连续三次恢复仍须在生产等价外部故障域执行。

### 2026-08-31｜RST3-04 固定容量 blocker supersession 与双 Compose 真实迁移

本 checkpoint 是当前有效结论，显式取代上文历史 checkpoint 中“RST3-04 必须达到 5 TB/6.5 TB 才能关闭”的产品口径；历史记录和原始失败证据不改写。HA4-02 的 5 TB/day+30% 持续吞吐基准仍是独立性能门禁，不能用本次 100 MB 功能演练替代。

| 项目 | 当前事实 | 结论 |
| --- | --- | --- |
| 产品容量合同 | `reuse_external`、`copy_referenced`、`portable` 三种对象迁移模式进入 strict plan/CLI/schema/Helm 合同；容量来自已认证 inventory 的实际引用字节并按配置 headroom 计算。`reuse_external` 必须保持签名 bucket/prefix 和精确 VersionId，可用对象容量要求为 0；另外两种模式按实际复制/携带字节计算。无固定最小数据量 | RST3-04 功能正确性不再被 5 TB 门槛阻断；100 MB 仅为本次 reference rehearsal |
| 双 Compose 隔离 | `compose.dev.yaml` 的 project、九个 host port、环境 identity 均可参数化且默认开发行为不变。合同测试同时 render `hc-migration-src-*`/`hc-migration-dst-*`，证明 ports/networks/volumes 不相交、目标只连接自己的 PostgreSQL/Temporal、两侧 release/component image identity 相同；目标对象 endpoint 可显式复用源外部对象服务 | 未创建、停止、删除或复用 `hc-data-platform-dev` 的 container/volume；清理只接受 exact project/run label |
| 正式 CLI 路径 | runner 通过 `hc-platform backup create`、`restore plan`、带独立 Ed25519 approval 的 `restore execute`（重复执行 checkpoint 幂等）和无 approval 的 `restore reconcile`。Compose 功能 profile 只替换运行依赖为 run-bound owner-only file repository/key、pinned PG16 client container 和 exact Compose runtime；production 默认 S3 Object Lock/Vault/Kubernetes profile 未放宽 | 没有以手工 `pg_dump`/`pg_restore` 或直接复制业务库替代正式平台流程；Temporal 内部库复制仅模拟 provider-owned cluster recovery，正式 backup adapter 仍不导出 Temporal 内部表 |
| 真实 `live0831u` 证据 | 源 API 真实注册/登录/bootstrap；MinIO 写入一个非 sparse、versioned、随机 100,000,000-byte 对象，并在 PostgreSQL 保存 key/version/size/SHA-256 引用。备份仓库 1,064,109 bytes、8 objects，显著小于业务对象；restore object capacity 为 0。目标同名 `hc_data` 在独立实例恢复，source/target aggregate 完全一致；对象按原 VersionId 流式复核；9/9 项 FULL reconciliation 均 PASS，Temporal/read-only runtime 结论为 `RECONCILIATION_PASSED` | 证明动态容量、外部对象复用、同 release 恢复和全域 reconciliation；不宣称 5 TB/day 或任何性能外推 |
| 切换与隔离 | 写暂停 75.131 秒，目标 1,800 秒内；证据记录 `rpo_zero=true`。显式 write-enable 后，恢复的旧 session 可查询，目标新增一条受控账号写；源对应账号行仍为 0、业务 facts 不变。源唯一 post-snapshot drift 是正式 backup catalog 的一条预期审计事件。目标 API/Frontend/Gateway/Worker/Media Worker 全部运行，两侧五类组件 image ID 分别一致；源 PostgreSQL/API/Worker/Media Worker/Temporal 在切换验证后停止，外部 MinIO 保留至精确清理 | RPO 0、停写目标、目标可读写和 source/target 写隔离通过；目标产生新写后不把旧源作为直接回切目标 |
| 证据、回归与清理 | 机器证据：`backend/tests/system/results/rst3-04-compose-server-migration-summary.json`；产品决策：`backend/tests/system/results/rst3-04-fixed-capacity-superseded-summary.json`。runner 记录 18/18 条关键命令 PASS；38 个 run-bound 产物文件、21 份容器日志和机器证据的禁止 Secret 扫描为零命中。PASS 前记录两侧 exact containers/volumes/networks，随后全部为空；Vault/temp 也按 run identity 清理。backup/migration/Compose/runbook 边界最终 `219 passed, 23 explicit external skips`，另在 fresh disposable PostgreSQL 16.10 上执行 takeover fence 集成 `1 passed`；任务文件 Ruff format/lint、mypy 26 sources，backup schema/runtime OpenAPI 与 generated client drift check、Helm strict lint/render、Compose render 均通过。Frontend typecheck、107 files/611 tests、production build 通过。`performance_benchmark_claimed=false` | RST3-04 标记完成；DR7-02 的本地双服务器功能演练完成。生产等价季度复演仍按当次实际数据量/拓扑留作持续门禁，而不是恢复固定 5 TB blocker。全仓标准 suite 的两个既存并行工作树失败、22 个非本任务 formatter drift 和 6 个非本任务模块的 23 个 mypy error 仍如实保留，未冒充本任务失败或被覆盖 |

当前剩余红门禁不再包含“RST3-04 固定 5 TB”：RST3-05/DR7-01 仍需未参与开发的独立值班人员，REL6-02/04/DR7-05 仍需真实签名相邻 release，HA4-02 仍需其独立持续吞吐容量 artifact，DR7-04/06 和连续三次生产等价恢复仍需外部故障域执行。
