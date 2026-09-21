# 平台统一发布

`deploy/` 是本仓库唯一的部署来源。`deploy/helm/hc-data-platform` 直接包含前端、API、
Worker、运行时配置和仅向前迁移钩子；其中没有组件子 Chart。各组件镜像仍保持独立，
但每个环境都使用同一份发布清单中记录的准确摘要。

发布渲染器还会把发布清单自身的 SHA-256、Git commit 和 migration manifest SHA-256 写入
Helm values。API、Worker、Media Worker、Frontend Pod 共享该 release identity；组件镜像摘要
仍分别记录，不能把不同组件的 image digest 误当成整个平台的 release manifest digest。

验证自包含的 Chart：

```bash
helm lint deploy/helm/hc-data-platform -f deploy/helm/hc-data-platform/values-ci.yaml
helm template hc-data-platform deploy/helm/hc-data-platform \
  -f deploy/helm/hc-data-platform/values-ci.yaml
```

## 外部恢复 Job

`backend.restoreJob.enabled` 默认必须保持 `false`。它只用于已通过 `restore plan` 的具名隔离目标，
并要求精确的 backup ID、target environment、digest-pinned `backup-maintenance` 镜像、显式
ServiceAccount、输入 Secret、公共环境 ConfigMap、credential Secret 和 owner-only staging PVC。
`backend.restoreJob.operation` 只能是 `execute` 或 `reconcile`，默认 `execute`。两种模式的输入 Secret
都必须包含 whole plan、restore target/request 和 restore plan；只有 `execute` 还必须包含由独立职责
key 签名的 approval/envelope。`reconcile` 不挂载、读取或继承 mutation approval。credential 不得写入
这些文档或命令参数。

ServiceAccount 只应取得目标 namespace 中具名 API/Worker Deployment 与 `/scale` 的 read/patch/update
权限。Job 禁用默认 token automount，改用具名 audience 的 projected token；不同集群的 API issuer
不同时必须显式设置 `backend.restoreJob.kubernetesTokenAudience`。外部托管 Temporal 的 CA 或成对
mTLS certificate/private key 通过 `backend.restoreJob.temporalTlsSecret` 挂载，所有 token、CA 和私钥
只复制到 Pod 私有 `emptyDir`，不进入 checkpoint PVC。

成功 Job 的唯一 RST3-02 终态是 `READ_ONLY_READY`：API 可以按批准副本数启动，但数据库仍为
`READ_ONLY_MAINTENANCE`，Worker/Media Worker 必须保持 0，输出必须继续显示
`writes_enabled=false`、`reconciliation_completed=false` 和 `restore_verified=false`。后续必须由
RST3-03 reconciliation/report 独立放行；该 Job 没有打开写入或写入 `RESTORE_VERIFIED` 的路径。

RST3-03 使用 `operation: reconcile`，从同一 PVC 读取完成的 `restore-checkpoint.json`，并把
`restore-reconciliation-report.json` 以 owner-only `0600`、原子 no-overwrite 方式写回。Job 会重新
验签锁定仓库和 authenticated payload，再以全量只读检查验证数据库内容/对象引用、对象 immutable
version/hash、P19 审计链、Outbox、workflow/Temporal、权限/RLS 与 runtime fence。任一检查失败仍
保留脱敏 FAIL 报告并以非零退出；完全相同的 `reconciledAt` 和输入允许 byte-exact replay，不同报告
不得覆盖已有文件。即使报告 PASS，输出仍固定 `writes_enabled=false`、`workers_enabled=false`、
`restore_verified=false`，下一步只能进入独立 smoke/write-enable approval；本 Chart 当前没有据此
开放写入或写 `RESTORE_VERIFIED` 的路径。

完整的 RST3-05 值班 packet 位于 `deploy/runbooks/`：先读
`restore-operator-evidence.md`，新物理机/VM 从 `restore-new-bare-metal-server.md` 开始，已有隔离
Kubernetes 目标执行 `restore-kubernetes.md`，环境变量/Secret/file mount 按
`restore-environment-reference.md`。文档终态仍是 read-only reconciliation PASS，不增加 Chart
中不存在的 write-enable 或 `RESTORE_VERIFIED` 权限。

## 计划内迁移 Job

`backend.migrationJob.enabled` 默认保持 `false`，`operation` 只允许 `plan|advance|status`。
`plan` 只验证 owner-only migration plan；`advance` 必须同时挂载 exact plan、独立职责签名的
cutover approval、独立 observer 签名的 live observation，并通过 ConfigMap pin 两把不同的
Ed25519 公钥。私钥不得进入 Job、Secret、argv、环境变量或 checkpoint PVC。

Job 使用 digest image、显式 ServiceAccount、禁用默认 token automount，并由 init container 把
read-only Secret 输入复制为 PVC 中的 `0600` immutable staging 文件。每次执行最多推进
pre-copy、source fence、final sync、target verify、traffic switch、target write enable、rollback
retention 中的一个固定阶段；checkpoint 对 plan/approval/owner/fencing token/predecessor 做 CAS
绑定。目标写入后状态永久为 `direct_rollback_allowed=false`、`reverse_sync_required=true`。

该 Job 是 evidence gate，不拥有 PostgreSQL/Object/Temporal/DNS provider mutation credential。
完整操作顺序与失败处理见 `deploy/runbooks/planned-migration-cutover.md`。RST3-04 必须在真实
双环境完成实际引用清单的迁移、RPO 0 和不超过 1,800 秒停写。默认 `reuse_external` 复用原
OSS 且不复制对象；`copy_referenced` 只复制数据库/manifest 引用对象；显式 `portable` 才生成
加密对象分片。容量按实际引用字节和默认 30% headroom 动态计算，复用外部 OSS 时只计算数据库、
临时文件和日志。稀疏/合成对象不得冒充真实对象；独立 5 TB/day 性能基准继续作为生产容量门禁，
但不阻塞基础导出、恢复或服务器迁移功能。

### 同机双 Compose 演练

迁移演练显式叠加 `deploy/compose/compose.minio-test.yaml`，用一次性 MinIO 隔离故障注入；正常开发的
`compose.dev.yaml` 也包含本机 MinIO。演练项目名和全部宿主端口都可通过 env file
参数化，未提供时仍保持测试默认值。
示例变量见 `deploy/compose/migration.env.example`。source/target 必须使用不同 `-p` 项目名和互不重叠
端口，例如：

```bash
docker compose -p hc-migration-src-<run-id> --env-file <source-env> -f compose.dev.yaml -f deploy/compose/compose.minio-test.yaml up -d
docker compose -p hc-migration-dst-<run-id> --env-file <target-env> -f compose.dev.yaml -f deploy/compose/compose.minio-test.yaml up -d
```

测试 overlay 的 Compose 项目边界会分别命名 PostgreSQL、MinIO、alignment/media 卷和 default network，因此两个项目
不共享 PostgreSQL、Temporal、网络或命名卷。`reuse_external` 演练只允许 source 将本次 disposable
MinIO API 绑定到专用随机端口，target 通过 `host.docker.internal:host-gateway` 读取该端口；target 的
PostgreSQL DSN 和 Temporal target 仍使用其自身 project-scoped `postgres`/`temporal` 服务名。
不得对现有 `hc-data-platform-dev` 执行 stop/down，也不得用宽泛的 container/volume/network 删除命令。

## 多节点应用高可用

生产 values 必须启用 `highAvailability.enabled`，并为 Frontend、API、main Worker 和 media
Worker 各提供至少两个副本。Chart 会在 HA 模式下 fail closed：PDB 必须启用且
`1 <= minAvailable < replicas`，node/zone topology key 必须不同，两个 `minDomains` 至少为 2，
`maxSkew` 固定为 1，所有 drain/grace 时间必须为正且小于 Pod termination budget。

Frontend 和 API 在停止进程前等待 endpoint propagation；Worker 通过只写入专用
`/tmp/hc-runtime` `emptyDir` 的 readiness sentinel 摘流，停止本地 claim loop 后再调用 Temporal
graceful shutdown。readiness probe 禁止导入完整 runtime 或执行依赖工厂，以免探针本身争用
Temporal workflow CPU。真实 node-drain 验收步骤、零失败窗口和副作用证据见
`deploy/runbooks/ha-node-drain.md`。该应用层演练不替代 PostgreSQL、对象存储和 Temporal 的
HA4-04/DR7-04 provider failover。

API 与 main Worker 可分别启用 `autoscaling/v2` HPA；启用时 Chart 不再写 Deployment
`replicas`，并要求 `minReplicas>=2`、`maxReplicas>minReplicas`、合法 CPU target 和非空 CPU
request。PDB 的 HA 校验使用 HPA `minReplicas`。main Worker 默认把单 Pod workflow task/cache
上限固定为 4/16，HPA 承担横向扩容，禁止用无界单 Pod 并发替代容量规划。

生产示例同时启用 `backend.capacityGate`。它以 pre-install/pre-upgrade Helm hook 从外部 ConfigMap
只读挂载 `hc-capacity-evidence/v1`，先校验精确 SHA-256，再执行 5 TB/日 + 30%、至少 30 分钟、全链路、
故障恢复和清理合同。placeholder/错 digest/本地或不完整 evidence 都会在修改 Deployment 前失败。
证据生成、release 恢复和真实扩缩容验收见 `deploy/runbooks/ha-autoscaling-capacity.md`；本地 Kind
结果不能替代生产等价容量证据。

staging/production 正常进程必须使用 durable runtime backend；memory backend 只允许显式注入的
本地/测试 composition。生产 session、idempotency、upload control、workflow/job 分别以 PostgreSQL、
versioned S3 和 Temporal 为 authority。alignment/aligned-media/quality/export 的本地文件只允许位于
`/tmp/hc-data` 的有界 `emptyDir`，可由 activity retry 重建，不能作为跨 activity 或跨 Pod 唯一副本；
readiness sentinel 同样只是 process-local lifecycle 信号。完整 machine audit 位于
`docs/architecture/platform-runtime-state-audit.yaml`，真实 replacement 步骤见
`deploy/runbooks/ha-state-restart.md`。

共享 PostgreSQL、versioned HA S3 和 external-managed Temporal 必须分别向应用提供稳定 endpoint；
provider failover 期间不得修改应用 DSN/target、重启应用或临时轮换 credential。HA 模式还要求所有
backend workload 使用同一个非零 `HC_SECRET_BUNDLE_REVISION`，它只标识外部 Secret Manager 中的
immutable bundle 版本，不导出 secret 明文或可重用 fingerprint。三类 provider 的顺序故障切换、
旧 writer fencing、对象 quorum/multipart、Temporal in-flight workflow 和 Secret rotation 规则见
`docs/architecture/shared-provider-ha.yaml` 与 `deploy/runbooks/ha-shared-provider-failover.md`；本地
protocol drill 不替代 production provider SLA、跨 region 或相关故障证据。

运行时配置默认不可热加载。HA4-05 只开放 release matrix 中三个低风险 key，并以 PostgreSQL
monotonic revision、P19 audit、LISTEN/NOTIFY 和最长 30 秒 authoritative poll 同步；节点目录报告
`applied_config_revision`。DSN、Secret/credential、TLS、镜像和 schema identity 必须随 release
滚动，不能借用该通道修改。操作、通知丢失和 rollback 验收见
`deploy/runbooks/ha-runtime-config-revisions.md`。

## 安全滚动升级

Chart 的 `backend.migration` 默认运行 `upgrade-expand` pre-install/pre-upgrade hook，并以同一
PostgreSQL session advisory lock 串行化；`contract` 是默认关闭、无 Helm hook 的独立 Job，必须在
source Pod/build 退出、观察窗结束、备份/恢复有效且取得独立批准后提供 approval digest。任何路径都
没有数据库 down migration。

真实发布必须先通过 pinned Ed25519 monotonic feed、compatibility/current-platform preflight、四眼
批准和短期签名 DR7 bundle；浏览器不取得集群 credential。外部最小权限 controller 再执行 expand、
Temporal compatible build routing、API/Frontend canary 和滚动。canary 样本不足为 HOLD，5xx/延迟/
readiness/digest 回归返回四组件 exact source image rollback payload。完整操作与失败注入见
`deploy/runbooks/rolling-upgrade.md`；DR7-01～06 的 production evidence 合同见
`deploy/runbooks/disaster-recovery-gates.md`。

绝不能部署仓库默认配置中的全零摘要或 `example.invalid` 仓库地址。CI 会在推送全部三个镜像后
生成真实的发布清单。预发布和生产环境提升的是同一份清单，不会重新构建镜像。应用 rollback 必须
使用 preflight 保存的 exact source digests、保留 forward expand schema，并满足兼容矩阵；普通 Helm
revision 恢复不能冒充数据库或 Temporal rollback。

本地开发也只有一个入口：

```bash
docker compose -f compose.dev.yaml up --build
```

根目录的 `compose.dev.yaml` 负责开发环境所有服务，并在 `http://127.0.0.1:8088` 暴露统一网关；
`compose.single-server.yaml` 用于单机静态部署。测试、验收和迁移演练配置集中在
[`deploy/compose/`](compose/README.md)，各组件目录不包含自己的 Compose 文件。
