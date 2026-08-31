# ADR-0002：`hc-platform-backup/v1` 格式、加密、签名与信任边界

- 状态：已接受
- 决策日期：2026-08-28
- 实施任务：DR0-02；BAK2-01（manifest 签名边界与可重建 catalog）；BAK2-02（PostgreSQL logical/physical/PITR adapter）；BAK2-03（对象 inventory/snapshot/portable adapter）；BAK2-04（配置/Secret dependency 与精确 provider version preflight）；BAK2-05（Temporal provider recovery policy 与 inventory adapter）；BAK2-06（整站创建/复验、锁定仓库与 Kubernetes Job）；RST3-01（只读恢复计划与空目标/容量/兼容预检）；RST3-02（checkpointed payload restore 与只读服务启动）；RST3-03（九域只读 reconciliation 与恢复报告）
- 持久化实施：BAK2-01～06 与 RST3-01～03 已在工作树和对应 disposable 真实依赖上验收；已形成状态最高为 `INTEGRITY_VERIFIED` 的完整签名备份集，能在签名计划和独立职责批准下把对象、PostgreSQL 与 Temporal readiness 恢复到隔离目标，并对严格 `READ_ONLY_READY` checkpoint 执行九域只读 reconciliation。尚未接入生产 KMS/HSM、生产 WAL archive provider、外部托管 Temporal HA/backup provider，也尚未执行双 Compose 真实对象 planned migration、写入开放、`RESTORE_VERIFIED` 或 DR7
- 前置决策：[`ADR-0001`](ADR-0001-platform-state-boundaries-and-recovery-objectives.md)
- 机器可读合同：
  - [`hc-platform-backup-v1.schema.json`](contracts/hc-platform-backup-v1.schema.json)
  - [`hc-platform-signature-v1.schema.json`](contracts/hc-platform-signature-v1.schema.json)
  - [`hc-platform-restore-target-v1.schema.json`](contracts/hc-platform-restore-target-v1.schema.json)
  - [`hc-whole-backup-plan-v1.schema.json`](contracts/hc-whole-backup-plan-v1.schema.json)
  - [`hc-platform-checksums-v1.schema.json`](contracts/hc-platform-checksums-v1.schema.json)
  - [`hc-platform-restore-plan-request-v1.schema.json`](contracts/hc-platform-restore-plan-request-v1.schema.json)
  - [`hc-platform-restore-plan-v1.schema.json`](contracts/hc-platform-restore-plan-v1.schema.json)
  - [`hc-platform-restore-approval-v1.schema.json`](contracts/hc-platform-restore-approval-v1.schema.json)
  - [`hc-platform-restore-checkpoint-v1.schema.json`](contracts/hc-platform-restore-checkpoint-v1.schema.json)
  - [`hc-platform-restore-reconciliation-report-v1.schema.json`](contracts/hc-platform-restore-reconciliation-report-v1.schema.json)
- 可执行实现：`hc_data_platform.backup.contracts`、`hc_data_platform.backup.catalog`、`hc_data_platform.backup.repository`、`hc_data_platform.backup.signing`、`hc_data_platform.backup.postgresql`、`hc_data_platform.backup.postgresql_cli`、`hc_data_platform.backup.objects`、`hc_data_platform.backup.object_cli`、`hc_data_platform.backup.dependencies`、`hc_data_platform.backup.dependency_cli`、`hc_data_platform.backup.temporal`、`hc_data_platform.backup.temporal_cli`、`hc_data_platform.backup.whole`、`hc_data_platform.backup.whole_cli`、`hc_data_platform.backup.restore`、`hc_data_platform.backup.restore_execution`、`hc_data_platform.backup.restore_steps`、`hc_data_platform.backup.restore_runtime`、`hc_data_platform.backup.restore_reconciliation`、`hc_data_platform.backup.restore_reconciliation_adapters`、`hc_data_platform.backup.restore_cli`
- 取代方式：只允许新 ADR 和新 `format_version`；V1 解析器不得猜测未知字段或版本

## 背景与范围

ADR-0001 已冻结全站状态域、恢复目标和生产 Temporal 模式。本决策冻结可恢复备份集的清单、备份库、加密、密钥引用、签名和目标绑定。BAK2-01～05 分别实现签名/catalog、PostgreSQL、对象、配置依赖和 Temporal metadata-only 子域；BAK2-06 已把它们整合为可续跑、幂等的 `backup create/list/verify`。RST3-01 实现 `restore plan`，验证计划输入、签名清单、空目标、精确 release/KMS/Temporal/依赖和 30% 容量裕量；RST3-02 在独立职责签名批准下执行固定顺序的 payload 恢复，并只启动数据库栅栏保护的只读 API。reconciliation、写入开放、恢复事实和灾备演练仍分别由 RST3-03～05 和 DR7 实施。

领域数据集导出、PostgreSQL dump、对象 bucket 副本或主库中的 backup catalog 单独存在时都不是 `hc-platform-backup/v1`。只有通过本合同认证并绑定目标的完整备份集才可进入恢复流程。

## 决策

### 1. 备份集与清单

备份集逻辑布局固定为：

```text
backup-<backup_id>/
├── manifest.json
├── db/platform.dump
├── objects/inventory.jsonl.zst
├── objects/parts/*.tar.zst.age
├── temporal/policy.json
├── config/public.yaml
├── secrets/envelope.json
├── checksums.sha256
└── signature.sig
```

路径是逻辑角色，是否加密由清单中的 `client_side_encryption` 决定，不能根据扩展名猜测。Portable 模式下 PostgreSQL dump、对象 inventory/part、Temporal policy 和 Secret envelope 的内容都必须使用 `age_x25519_v1` 客户端加密；即使历史逻辑路径没有 `.age` 后缀也不代表明文。公共有效配置和校验和可作为 `public_integrity_metadata`，但仍受备份库 KMS 加密、对象锁和签名保护。Snapshot 模式不携带对象 part，敏感 artifact 必须至少使用备份库 KMS 加密。

`manifest.json` 是严格对象：所有层级拒绝未知字段，所有 ADR-0001 状态域都是具名必填字段，artifact 路径唯一且引用的路径/hash 必须闭合。核心域只能采用 ADR-0001 冻结的 `included` 或 `external` disposition，不能用 `not_applicable` 绕过。清单还必须记录生成它的备份工具 name/version/Git commit/image digest，以及具名、去重、带 verifier identity 和 evidence artifact 的 verification facts；`INTEGRITY_VERIFIED` 至少需要一个通过的 integrity fact，`RESTORE_VERIFIED` 还需要一个通过的 restore fact。

Catalog 中可记录 `CREATING/CREATED/INTEGRITY_VERIFIED/RESTORE_VERIFIED/FAILED/CORRUPT/EXPIRED` 的追加状态事实；交给 restore verifier 的不可变 manifest 只接受 `INTEGRITY_VERIFIED` 或 `RESTORE_VERIFIED`。失败或未完成的 catalog 状态不能被重写为可恢复清单。

### 2. JSON 编码和签名

V1 签名算法固定为 Ed25519，签名私钥必须位于 KMS/HSM 或由其保护的独立签名服务中，API/Worker 和备份包都不得持有私钥。清单记录不可变 KMS key reference 和 Ed25519 原始公钥的 SHA-256 指纹；恢复目标必须预先 pin 该指纹。

签名输入采用 `hc-json-c14n/v1`：

1. JSON 必须是 UTF-8 顶层对象，重复 member name 直接拒绝；
2. 清单 schema 只使用 JSON 字符串、整数、布尔、null、数组和对象，不允许 NaN/Infinity；
3. 对象 key 按 Unicode code point 排序，使用 `,`/`:` 分隔且不添加空白，非 ASCII 字符直接按 UTF-8 编码；
4. Ed25519 直接签名上述 canonical bytes；`manifest_sha256` 是同一 bytes 的小写 SHA-256；
5. `signature.sig` 是严格的 `hc-platform-signature/v1` JSON envelope，signature 使用无 padding 的 base64url。

Verifier 必须同时验证 envelope 与 manifest 的算法、canonicalization、key reference、公钥指纹和 manifest hash，再执行 Ed25519 验签。`checksums.sha256` 校验 artifact bytes；manifest 签名认证每个 artifact 的预期 path/size/hash。因此攻击者不能只替换 checksums 或 artifact 而保持备份有效。

### 3. KMS、包络加密与轮换

所有备份库对象必须采用 provider KMS 支持的 AES-256-GCM 服务端加密。备份库 key、portable age recipient key 和签名 key 都使用 provider-neutral 的不可变引用：

```text
kms://<authority>/<key-purpose>/versions/v<positive-integer>
```

`latest`、alias、零版本或省略 `/versions/` 的引用不符合 schema。Provider adapter 可以把该稳定逻辑版本映射到 AWS KMS、GCP KMS、Azure Key Vault 或 Vault Transit 的不可变 key/version identity，但 manifest 中不得出现访问凭据、明文 data-encryption key、private key 或可解密 Secret 的值。

Portable 模式使用 age X25519 v1 recipient。recipient private key 独立保存在 Secret Manager/KMS 受控域，备份工具只取得公钥进行加密；恢复进程通过 break-glass 身份在内存中解密。`secrets/envelope.json` 仅记录 Secret 名称、key 名称、不可变版本、指纹和 KMS/recipient metadata。数据库内已加密的数据源凭据仍依赖原始 `HC_DATA_SOURCE_CREDENTIAL_KEY` 版本，不允许恢复时临时生成替代 key。

轮换创建新版本和新备份，不重写旧清单。旧 key 在最后一个受保留备份到期且完成恢复演练前不得销毁。公钥指纹或 key version 不在目标 trust policy/KMS inventory 中时，预检失败。

### 4. 独立备份库

备份库不得与业务对象 bucket 或业务 PostgreSQL 共用唯一故障域。V1 要求：

- 所有对象 KMS 加密；
- Compliance object lock 和显式 `retention_until`；
- 异站 replica reference；
- manifest、checksums 和 signature 与 payload 一起保存；
- 主库 catalog 明确声明 `catalog_is_only_copy=false`；
- 读、写、删除、缩短保留和 KMS decrypt 权限分离，常规 API/Worker 无备份库写删权限。

备份库不可用、保留期限早于备份完成时间、对象锁未启用、异站引用缺失或 key 版本不可用都必须中止。仅有主库 catalog 记录不能降级为成功。

#### BAK2-01 实施证据与保留边界

- `ManifestSigningPort` 把私钥留在外部 KMS/HSM 或独立签名服务；平台只接收签名结果并用 manifest pin 的 Ed25519 公钥自验证。生产代码没有本地私钥 adapter，测试 golden fixture 也只提交公钥、canonical manifest 和 detached envelope。
- `platform.backup_catalog_entries` 只保存逻辑 repository URI 与 digest/identity；`platform.backup_catalog_status_facts` 只追加状态和 evidence reference。entry/fact 的 UPDATE/DELETE 以及失败后回退到成功均由 PostgreSQL trigger 拒绝，当前状态由 view 从最新事实派生。
- catalog rebuild 必须从独立 S3 adapter 读取严格的 manifest/signature pair；adapter 要求 bucket versioning、COMPLIANCE object lock、未来 retain-until、非空 VersionId 和有界对象大小，再用 pinned signer/repository 完成验签与 identity 绑定。物理 bucket/key 和 KMS reference 不进入 catalog audit。
- disposable MinIO 只证明 S3 versioning/object-lock/VersionId 和真实对象读取，不能证明 provider KMS/HSM、异站复制或生产 IAM 分权。BAK2-04 已关闭 Secret dependency 的 Vault KV v2/Transit version adapter；独立仓库 SSE-KMS preflight、Ed25519 provider 签名与生产 IAM 分权仍必须由 BAK2-06 关闭。本切片也没有生成 dump、inventory、portable part 或可恢复备份。

#### BAK2-02 PostgreSQL 实施证据与保留边界

- 外部 `hc-postgres-backup` CLI 只从结构化环境读取 endpoint、credentials 和 maintenance lease；连接密码只进入 owner-only `.pgpass`，不出现在 DSN、argv、receipt、stdout/stderr 或 artifact 中。创建前必须精确匹配 `BACKUP/EXECUTING`、owner UUID、fencing token、数据库时钟有效租约，以及环境 `READ_ONLY_MAINTENANCE`/fence token。
- 逻辑 adapter 要求客户端与服务端 PostgreSQL major 精确一致，在 read-only repeatable-read transaction 中导出 snapshot，并用同一 snapshot 生成确定性数据库证据和 custom-format dump。恢复只接受显式命名、由 `template0` 创建的空目标，使用 single transaction/exit-on-error，保留 owner 与 ACL；随后逐项比较 migration ledger/checksum、schema definition/owner/semantic ACL、constraint、sequence、每表行数和排序 JSONB 流 hash。
- 真实 PostgreSQL 16.10 演练把 104 个 migration、4,103 条 schema fact、1,614 个 constraint、7 个 sequence、152 张表和 238 行恢复到全新空库并精确匹配；同一目标的第二次恢复在 mutation 前因非空而拒绝。旧 token 在 artifact 创建前失败，dump/receipt 均为 `0600`，明文 Secret 扫描为零。
- 物理 adapter 使用 exact-major `pg_basebackup` plain/streamed-WAL 基线，逐文件计算 hash，并以 `pg_verifybackup` 和 `pg_controldata` 绑定 manifest、system identifier、timeline 和 WAL 范围。`PostgresWalAnchor` 本身固定为 `physical_recovery_ready=false`；只有校验 system/timeline、无 gap、未过期的外部 WAL archive receipt 后才可声明物理恢复输入完整。
- 专用 PostgreSQL 16.10 primary 的真实基线包含 2,174 个文件、约 60 MB；恢复实例从基线回放 WAL，在目标 LSN `0/A000860` 精确停止并提升到新 timeline，包含基线后目标事务且排除更晚事务，104 个 migration 和 system identifier 保持一致。该结果证明 PostgreSQL PITR adapter/坐标，不是整站 `RESTORE_VERIFIED` 演练。
- 独立 `postgres-maintenance` 镜像以 UID/GID 65532 运行，固定 PostgreSQL 16.10 的 `pg_dump`、`pg_restore`、`pg_basebackup`、`pg_verifybackup`、`pg_controldata` 和 `pg_waldump`；本地最终 image digest 为 `sha256:292810aab7d2d6965d8fee48590a995a9d442fe185e9c3073aca1c965bb4c4ec`。API/Worker 镜像不携带这套维护工具。
- 截至 BAK2-02 checkpoint 尚没有对象 inventory/portable parts、Secret/KMS、Temporal policy、签名整站 manifest、生产不可变 WAL archive provider/receipt 或 Kubernetes Job execution 证据，因此当时不得标记 `INTEGRITY_VERIFIED`/`RESTORE_VERIFIED`，也不得把本地 PostgreSQL 演练解释为整站备份或 DR7；对象子域随后由下述 BAK2-03 关闭。

#### BAK2-03 对象实施证据与保留边界

- `S3ObjectBackupAdapter` 只接受启用 versioning 的 source bucket，并在围栏窗口前后枚举当前精确 `VersionId` 集合。它排除当前 delete marker，拒绝 `null` VersionId、越出请求 prefix 的 key、naive 时间、布尔 size、异常 ETag/分页响应和版本集变化；每个对象都用显式 `VersionId` 读取并记录实际 SHA-256、ETag、UTC LastModified 和字节数。
- Snapshot 模式只生成 `objects/inventory.jsonl.zst` 和精确版本引用，不复制对象 payload。该本地 inventory 是 owner-only `0600` staging artifact，合同标记为 `repository_kms_only`；在 BAK2-06 真正写入备份库前仍必须证明 provider SSE-KMS、不可变 key version、对象锁和权限分离，不能把本地文件解释为已有 KMS 保护。
- Portable 模式按确定性 object/segment 顺序把每个精确版本流式切为单成员 PAX tar，经 zstd checksum 压缩后交给官方 age 1.3.1 X25519 recipient 加密。实际 ciphertext size 在提交前执行硬上限检查；owner-only checkpoint 绑定版本集 coordinate、62 字符 age recipient 指纹、part plan 和已完成 part hash，重启只复用完全匹配且重新验 hash 的 part，source/recipient/路径不符均 fail closed。
- `ObjectBackupVerifier` 先闭合 inventory/part path、size 和 SHA-256，再使用外部 owner-only age identity 流式解密、校验 zstd/tar 安全元数据和 segment hash，并重建每个原对象的完整 SHA-256；缺 part、篡改 part、错顺序、错 member、错 segment 或错对象 hash 均拒绝，不把“文件存在”当作完整性验证。
- 外部 `hc-object-backup snapshot-create/portable-create` 只从结构化环境读取 S3/PostgreSQL/lease/recipient 配置，argv 无 endpoint、DSN、credential 或 identity。创建前精确验证 `BACKUP/EXECUTING`、owner/token、DB-clock lease 和环境 read-only fence；receipt/checkpoint/inventory/part 均为 `0600`，stdout/stderr 只含稳定脱敏 code/摘要。
- 真实 disposable MinIO 上验证了 current/superseded/delete-marker 版本语义、显式 VersionId 读取、真实 PostgreSQL fence，以及官方 age 1.3.1 加密/解密。最终外部 Job 对 2 个对象、480,000 bytes 生成 snapshot inventory 和 5 个 portable part，完整解密重建与 Secret scan zero；最终 non-root 镜像 UID/GID 65532，digest `sha256:d131c6076e272a9ef2a09385f34e9c4dadb3607302568752d34de644861cd62b`，大小 128,613,821 bytes。
- incomplete multipart fail-closed 合同有确定性单元测试，但本次固定 MinIO `RELEASE.2024-11-07T00-52-20Z` 在已上传 6 MiB part 后仍未从 `list_multipart_uploads` 返回该 upload；因此这里不声称真实 provider 的 incomplete-multipart 可观测性证据。生产 provider/版本兼容矩阵和对应故障注入必须在 BAK2-06/DR7 另行关闭。
- 截至 BAK2-03 checkpoint 当时仍没有 Secret/KMS dependency export、Temporal policy、完整签名 manifest/checksums、独立备份库 payload 上传、整站 reconciliation、Kubernetes Job 或 DR7 证据；Secret/KMS dependency 随后由下述 BAK2-04 关闭，其余缺口仍不得标记 `INTEGRITY_VERIFIED`/`RESTORE_VERIFIED`。

#### BAK2-04 配置/Secret dependency 实施证据与保留边界

- `HelmRenderedDependencyAdapter` 对有界 UTF-8 multi-document YAML 使用拒绝 duplicate key 的严格解析，只接受具名 ConfigMap 引用、literal/field env 和精确 `secretKeyRef`。optional/conflicting Secret reference、未知 env/valueFrom key、重复 env、secret-shaped plaintext 均 fail closed；当前真实 chart render 提取 1 个公共 ConfigMap、23 个公共 literal env、12 个 runtime field 和 7 个必需 Secret dependency，并绑定精确 Helm render SHA-256。
- 七个核心依赖固定为 PostgreSQL DSN、对象存储 access/secret key、cursor secret、data-source credential key、abuse HMAC secret 和 Turnstile secret。公共 `config/public.yaml` 不含 Secret 值；严格 envelope 只记录 Secret path/key、KV v2 version/created time/key fingerprint、workload consumer，以及 Transit key/version/HMAC digest。Vault endpoint/token、原值和可解密材料不得进入 artifact、receipt 或错误。
- `VaultKvTransitProvider` 拒绝非 loopback HTTP、生产非 HTTPS、redirect、越界响应和不匹配的 KV/Transit identity；capture 读取 KV v2 latest 后固定精确历史 version，restore preflight 再读取该 version、created time 和 key fingerprint，并核对精确 Transit key version。缺失、soft-deleted、destroyed Secret version，或 Transit 轮换后禁用被 pin 的版本，都在任何 restore write 前用稳定脱敏 code 拒绝。
- `ConfigurationBackupAdapter` 在 provider capture/preflight 前后分别验证 PostgreSQL maintenance fence。Snapshot 输出 `public.yaml` 与仅含引用的 `envelope.json`；portable 输出使用 age 1.3.1 X25519 的 `envelope.json.age`。owner-only staging/receipt、`0600` 文件、no-overwrite publish、失败回滚和 artifact hash 闭合均由 verifier 验证；snapshot 的 `repository_kms_only` 标记只是交给 BAK2-06 的仓库加密要求，不是本地文件已有 SSE-KMS 的证据。
- 外部 `hc-configuration-backup snapshot-create/portable-create/verify` 只从结构化环境读取 PostgreSQL/Vault credential、age recipient/identity，敏感值不进入 argv。最终 non-root 镜像 UID/GID 65532，固定 age 1.3.1，digest `sha256:3187e7c81fe5c07227704b575fea6f4fc92cb4d449e3537624b87a0fe6854b5d`，大小 128,564,118 bytes；API/Worker 镜像不携带该 CLI。
- 真实 Vault v2.0.3 dev server 证明 KV v2 精确旧 version 在 latest 更新后仍可校验、delete 后拒绝并可 undelete、destroy 后永久拒绝，以及 Transit rotate 后 pin v1 仍可用、提高 `min_encryption_version=2` 后 pin v1 拒绝。最终 Docker Job 使用只含目标 KV read、Transit key read/HMAC update 的最小 policy；其他 Secret path 和 `sys/policies` 均被拒绝。Snapshot/portable 共用 coordinate `secret-dependency-set/v1:sha256:5bb7a261236ba0a1ce009747a6a1024ce23b31935b5eecd0a6471f58ebde343c`，所有产物 `0600`，对七个 Secret 原值、PostgreSQL password 和一次性 Vault token 的 byte scan 为零。
- Vault dev mode 只是在 disposable loopback/network namespace 中验证真实 API/ACL/version 语义，不是生产 HA、TLS、auto-unseal/HSM、Kubernetes auth 或 Enterprise namespace 证据；Transit HMAC 也不是 V1 manifest 的 Ed25519 签名。没有 Kubernetes context，因此外部 Docker Job 不冒充 Kubernetes Job。Temporal policy 随后由下述 BAK2-05 关闭；独立仓库 payload/SSE-KMS、Ed25519 provider 签名、完整 manifest/checksums 和 reconciliation 仍由 BAK2-06 关闭。

#### BAK2-05 Temporal 实施证据与保留边界

- `TemporalConnectionConfig` 把生产 V1 固定为非 loopback TLS 的 `external_managed`，且必须有 API key 或成对 mTLS 身份；未经后续 ADR 授权的 `external_self_hosted` 稳定拒绝，`development_only` 只允许无凭据 loopback plaintext probe，不能生成 production artifact。
- `TemporalSdkAdminAdapter` 只使用受支持的 Namespace/Cluster/System、Schedule 和 Visibility API。它稳定读取 namespace/cluster identity SHA-256、服务版本、retention、paused schedule 的 whitelisted action metadata 与确定性 spec hash，以及 `ExecutionStatus = "Running"` 的 open workflow identity metadata；不读取 history、payload、memo、search attribute 或 Temporal 内部数据库。双重稳定读取、有界重试、schedule/open workflow 数量硬上限和“生产 schedule 必须全部暂停”均 fail closed。
- 严格 `hc-temporal-provider-recovery/v1` 合同绑定 cluster reference、namespace、精确 identity/version、Temporal Operations owner、provider 官方 HA/backup 与 namespace/history restore 方法、RPO ≤ 900 秒、RTO ≤ 7200 秒、保护点、evidence ref/hash、restore runbook、verified/valid timestamps。引用携带 userinfo/query/fragment、证据过期、保护点超 RPO 或 live identity 不符均在创建/恢复 preflight 拒绝。
- `hc-temporal-backup-policy/v1` 固定 `internal_database_in_business_dump=false`、`workflow_histories_exported_by_platform=false`，并嵌入 schedule/open inventory hash 与 consistency coordinate。`TemporalBackupAdapter` 以 PostgreSQL maintenance fence 包围 capture/publish；snapshot 是待 BAK2-06 仓库 SSE-KMS 保护的 `repository_kms_only`，portable 使用 age 1.3.1 X25519。verifier 严格检查 owner-only `0600` artifact/receipt、no-overwrite publish、hash、age decrypt、provider live identity、当前 paused schedules 和 provider evidence freshness。
- 外部 `hc-temporal-backup snapshot-create/portable-create/verify/probe` 的 target、credential/API key、TLS private key、provider policy、PostgreSQL password 和 age identity 都只从结构化环境读取，并使用稳定脱敏错误。最终 non-root 镜像 UID/GID 65532，digest `sha256:6dc7859b78d2f4ae561b0913c1d0178514a43046115dd6543b58807cc2d64632`、128,622,842 bytes，固定 age 1.3.1 且不包含 `pg_dump`。
- 本地真实 Temporal 1.25.2 / Python SDK 1.31.0 集成创建一个携带 Secret sentinel 的 paused schedule 和一个 open workflow；支持 API inventory 分别精确增加 1，payload/history/internal DB 导出标记保持 false，artifact/probe sentinel scan zero，精确清理后返回基线。该测试只证明 development/protocol 行为；`development_probe_only` 和测试注入 managed-contract path 都不是外部托管 provider 的 HA、backup、SLA 或恢复证据。
- 截至 BAK2-05 checkpoint 仍没有完整签名 manifest/checksums、独立仓库 payload/SSE-KMS、真实 Ed25519 provider 调用、Kubernetes Job、整站 reconciliation 或 DR7 证据；这些缺口不得标记 `INTEGRITY_VERIFIED`/`RESTORE_VERIFIED`，下一切片由 BAK2-06 关闭可关闭的备份编排与仓库边界。

#### BAK2-06 整站备份实施证据与保留边界

- `hc-whole-backup-plan/v1` 把 operation/backup/source identity、snapshot/portable 模式、创建/完成时间、精确 release/tool identity、PostgreSQL/Object/Temporal/配置策略、repository/KMS/signing key version 和外部 evidence hash 固定为 owner-only 输入。`hc-platform-checksums/v1` 对 artifact path/size/hash/client-side encryption 及 ADR-0001 九个状态域的 receipt/verification hash 做排序、唯一和全集约束；两份合同与 V1 manifest/signature/restore-target 一样由 Draft 2020-12 schema 机械生成并执行 drift gate。
- `assemble_whole_backup` 只接受已由 BAK2-02～05 verifier 关闭且与同一 plan/mode/migration hash 一致的 receipt。Snapshot PostgreSQL dump 标记 `repository_kms_only`；portable 先用 age 1.3.1 X25519 加密并做外部 identity round-trip。existing receipt/artifact 必须 byte/hash/plan 完全相同才可续跑，任何缺域、路径越界、证据 identity/time 不符或签名前篡改都 fail closed。
- `S3LockedBackupRepository` 的 preflight 精确检查 versioning、Object Lock、未来整秒 COMPLIANCE retention 和目标 replication destination。所有对象要求 SSE `aws:kms` 及 provider 观测 key identity、非空 VersionId、metadata/hash；multipart checkpoint 记录 upload ID、part number/size/SHA-256/ETag，重启只复用重新校验的 part。发布后 verifier 从仓库独立下载 manifest/signature/checksums 和全部 artifact，再做 Ed25519、policy、size/hash 验证。
- `VaultTransitEd25519Signer` 只允许非 loopback HTTPS（disposable loopback 可 HTTP），要求 exact versioned key reference、Ed25519、`supports_signing`、non-exportable、禁止 plaintext backup，并拒绝低于 provider minimum version 的 key。签名请求明确指定 key version，返回 `vault:vN` 必须与请求一致，平台再用 provider public key 本地自验签；private key 从不进入进程或备份。
- catalog 的第 105 个 forward-only migration 允许对已有 `INTEGRITY_VERIFIED` 追加新的同状态独立验证 fact，并加入 environment/status/completed/backup-id keyset 索引。`publish_whole_backup` 只有在独立仓库验证成功后才原子创建 `CREATING → CREATED → INTEGRITY_VERIFIED`；`verify_published_whole_backup` 和 `hc-platform backup verify` 只追加 integrity fact。相同 operation 完全一致时幂等，任意冲突拒绝，BAK2-06 不存在产生 `RESTORE_VERIFIED` 的代码路径。
- `GET /api/v1/platform/backups` 强制当前 environment，只允许 exact `platform.operations.read` 或兼容只读 `platform.admin`，支持 status + 有界 signed keyset cursor，响应不含 manifest URI、signature hash、KMS、物理 bucket 或 provider endpoint。成功/拒绝写脱敏 P19 audit；正式 OpenAPI、runtime OpenAPI、生成客户端和 BE23 公网矩阵同步。
- 独立 `backup-maintenance` 镜像以 UID/GID 65532 运行，包含 age 1.3.1、PostgreSQL 16.10 `pg_dump/pg_restore` 和四个领域 adapter，最终 digest `sha256:13381e1091983bc6112cc5c50c027882a5dd9bbf8f8598be38ac4639f7774c76`、129,551,449 bytes。Helm Job 默认关闭，要求 digest image、显式 service account、plan Secret、公共 ConfigMap、credential Secret 和 staging PVC；禁用 service-account token、使用 read-only root/non-root security context，age identity 只复制到独立 `emptyDir` 而不进入 staging PVC。
- 真实 MinIO `RELEASE.2025-07-23T15-54-02Z` 以 static KMS key 验证了 SSE-KMS identity、Object Lock、5 MiB multipart/续跑、独立下载和实际异站复制；真实 Vault 1.20.3 验证 nonexportable Ed25519、最小 read/sign ACL、rotate 后 exact v1 和 minimum version 拒绝；全新 105 migration PostgreSQL catalog 验证重复 publish/list/verify、篡改拒绝且 catalog 不变。一次性 kind v1.32.2 中同一个 Helm `backup verify` Job 经 TLS Vault endpoint 连续成功两次，Pod 为 non-root、无 service-account token，catalog operation fact 仍为一条。
- 上述 MinIO static KMS 不是生产 KES/HSM，Vault dev 不是 HA/auto-unseal/Kubernetes auth，单节点 kind 不是生产多节点/CSI，integration 使用管理员 MinIO credential 也不证明生产 IAM 分权。运行日志和外部依赖是由外部 evidence hash 绑定的输入，真正 evidence producer 留给 OBS5/DR7。BAK2-06 证明完整备份可达到 `INTEGRITY_VERIFIED`，不证明任何恢复、reconciliation、RPO/RTO、5 TB 吞吐或 `RESTORE_VERIFIED`。

#### RST3-01 只读恢复计划实施证据与保留边界

- 新备份在 `PostgreSQLBackupV1` 中签名记录源数据库精确名称和 `pg_database_size()`；旧 V1 清单仍可验签，但缺少任一 RST3 容量/源 identity 时 `restore plan` 稳定拒绝，绝不使用压缩 dump 大小猜测恢复空间。PostgreSQL、对象数据和 staging 分别用纯整数计算 `source_bytes × 130%`，保持 ADR-0001 的 30% 裕量。
- `S3LockedBackupRepository.verify_manifest()` 只读取、校验 KMS/Object Lock/version metadata，并验签 `manifest.json` 与 `signature.sig`；不会下载 checksums、dump、对象 part 或其他 payload。只有签名、backup/source/target、精确 release、PostgreSQL major、signer trust 和所有 KMS exact version 绑定成功后，才调用只读目标探针。
- `PostgreSQLRestoreTargetAdapter` 只在 `REPEATABLE READ, READ ONLY, DEFERRABLE` transaction 中读取具名数据库 identity、major 和用户对象计数；`S3RestoreTargetAdapter` 只读取目标 bucket versioning 并以 `MaxKeys=1` 检查具名非 root prefix；staging 只做 owner-only 权限和 `statvfs` 可用空间检查。源/目标数据库或 bucket/prefix 完全碰撞、声明非空、实际非空、错 major/versioning 均 fail closed。
- `hc-platform-restore-plan-request/v1` 把 request/operation/backup/source/target、实际部署 release、writes-disabled/Worker-zero fence、可用 KMS exact versions、Temporal cluster/namespace/version、Secret dependency version/fingerprint、域名/证书 readiness、provider capacity evidence 和有效期绑定成 owner-only 输入。成功输出 `hc-platform-restore-plan/v1`：固定 `dry_run=true`、`writes_enabled=false`、`payloads_downloaded=false`，包含 12 项通过检查、三域容量、稳定 plan/checkpoint hash，下一步只能是 `restore_execute_requires_signed_approval`。
- disposable PostgreSQL 16.10 的 `template0` 空库与 versioned MinIO bucket 完成真实验收：成功计划、错 release、错 KMS、实际非空表、实际非空对象前缀和空间不足路径全部按稳定 code 结束；测试前后数据库用户对象数和目标 prefix 对象数均为 0，非空哨兵在拒绝后仍保持原 VersionId/内容，再由测试精确清理。RST3 聚焦 `14 passed, 0 skipped`。
- 更新后的 non-root `backup-maintenance` 镜像继续使用 UID/GID 65532、owner-only `/backup-staging`、age 1.3.1 与 PostgreSQL 16.10 client；镜像 ID `sha256:79d25520068516156c192671fe2cd0d74c96333bdea20e25efb241c1e78260c1`、129,589,192 bytes，真实 `hc-platform restore plan --help` smoke 通过。
- RST3-01 没有 restore execute port、payload download、数据库/object mutation、Temporal history restore、服务启动、流量切换、reconciliation、restore fact 或 catalog `RESTORE_VERIFIED` 路径。readiness/capacity 是外部 provider evidence 的精确 hash 输入；本轮 disposable 资源与管理员 MinIO credential 不证明生产配额 API、KMS/IAM 分权、托管 Temporal SLA、5 TB 容量或 DR7。

#### RST3-02 恢复执行实施证据与保留边界

- `PostgreSQLBackupV1.content_evidence` 把 migration、schema、constraint、sequence 与逐表 row/digest aggregate 纳入整份 Ed25519 签名 manifest；restore plan 同时绑定 backup signer fingerprint、目标数据库和对象 bucket/prefix。旧 V1 manifest 仍可验签，但缺确定性 content evidence 时禁止执行恢复。
- mutation 只接受单独 Ed25519 approver key 签名的 `hc-platform-restore-approval/v1`；approval 精确绑定 plan bytes/checkpoint、operation、backup、target、有效期，并固定 `allow_write_enable=false`、`allow_restore_verified=false`。backup signer key 与 approval key 相同会因职责冲突拒绝。当前 checkpoint 的 owner/fencing token、approval hash、前驱 hash、固定五步 prefix 和每步 fence/evidence receipt 必须完全一致，owner-only `0600` 文件冲突或篡改均 fail closed。
- 执行顺序固定为 `payload_verified → objects_restored → postgresql_restored → temporal_ready → services_read_only`。仓库恢复只续用与签名 size/hash 完全相同的本地文件；snapshot 使用源 `VersionId` 读取精确 bytes，portable 先验 inventory/part/decrypt hash；目标已有相同 bytes/version metadata 时幂等续跑，冲突不覆盖。PostgreSQL 只恢复到具名空库，重试必须与签名 content evidence 完全一致；Temporal 继续使用官方 API identity/paused-schedule/provider-evidence preflight，不读取或热 dump 内部表。
- `PostgresRestoreTargetFence` 在只读 API 启动前安装目标 `READ_ONLY_MAINTENANCE`；`KubernetesRestoreRuntime` 每步读取 Deployment 与数据库 fence，要求所有 Worker/Media Worker 的 spec/available/ready 均为 0，并只把具名 API 扩到批准副本数。Helm restore Job 默认关闭，使用 digest image、显式 ServiceAccount、最小 Deployment/scale RBAC、可配置 audience 的 projected token、owner-only PVC、Secret/ConfigMap 分离、Temporal TLS Secret、read-only root/non-root security context；token、CA/client key 只进入 private `emptyDir`，不进入 checkpoint PVC。
- 真实验收使用 PostgreSQL 16.10（fresh 105 migrations）、versioned MinIO source/target、SSE-KMS/Object-Lock 仓库及异站副本、Vault 1.20.3 nonexportable Transit Ed25519 v1、Temporal 1.25.2 supported API protocol 和 kind v1.32.2。故障注入在对象与 PostgreSQL 真恢复后、Temporal checkpoint 前中断，状态精确停在 `POSTGRESQL_RESTORED`，writes/reconciliation/restore-verified 全为 false；重复对象/数据库步骤未产生新 version 或内容漂移，随后从该安全点到 `READ_ONLY_READY`。
- fresh backup `rst302-real-backup-195117` 的外部 Helm Job 输出五步完成，API 1/1 ready、两个 Worker deployment 为 0；PostgreSQL rows/fence 和两个对象 bytes/version 精确一致。相同 Job/PVC/approval 再执行后 checkpoint SHA-256 `19449504405693c4c7a78025dbaf8a54ad7b060d98c765f8dbf71c64fbc3f00c`、数据库确定性 evidence 和对象 version tuple 均不变。早先超过 frozen 900 秒保护点的 provider evidence 在 Temporal 步稳定拒绝，证明恢复不会绕过 RPO freshness。
- 该验收中的 MinIO static KMS、Vault dev、管理员 object credential、单节点 kind 和本地 Temporal managed-protocol adapter 不证明生产 KES/HSM/IAM、Vault HA/Kubernetes auth、多节点 CSI 或外部托管 Temporal backup/SLA。RST3-02 不执行 DB→object/audit/Outbox/workflow/permission reconciliation，不启动 Worker、不开放写入、不写 catalog restore fact，也没有 `RESTORE_VERIFIED` 路径；这些门禁保留给 RST3-03、HA4、REL6 和 DR7。

#### RST3-03 只读 reconciliation 实施证据与保留边界

- `hc-platform-restore-reconciliation-report/v1` 固定九项、有序、`coverage=FULL` 的检查：完成的 `READ_ONLY_READY` checkpoint、数据库业务内容、对象 inventory、DB→object 引用、全局审计链、Outbox、workflow/Temporal、权限/RLS 与只读 runtime。报告精确绑定 plan/checkpoint/operation/backup/target/verifier/time，owner-only `0600` 原子 no-overwrite 发布；相同 bytes 可 exact replay，不同 bytes 冲突。`writes_enabled`、`workers_enabled`、`restore_verified` 永远为 false，报告不修改 checkpoint 或 catalog。
- PostgreSQL inspector 只在一个 `REPEATABLE READ, READ ONLY, DEFERRABLE` snapshot 内工作：重算所有非 `platform` 业务表的签名内容证据，验证 migration/schema/constraint、所有已提交对象引用、P19 sequence/predecessor/hash/head、Outbox claim/scope、运行 workflow/reconciliation、账号/组织/项目 membership、grant、平台职责分离和 scope RLS policy。目标本地 `platform` maintenance 行不冒充源业务内容相等；所有引用仍逐项匹配签名 inventory 的映射 key、size/hash。
- 对象 inspector 对每个 inventory record 读取目标当前精确 immutable VersionId，验证 restore metadata/source version/hash/size，并流式重算完整 bytes；没有 put/copy/overwrite port。Temporal inspector 读取 live namespace/cluster/version/schedule/open-workflow inventory 并与数据库事实对应；runtime inspector 只 GET 具名 Deployment 和数据库 fence，不 scale 资源。任一 inspector exception 生成稳定脱敏 FAIL check 并保留报告；不完整或错 plan 的 checkpoint 在 inspector 前 fail closed。
- `hc-platform restore reconcile` 独立重新验签锁定仓库 manifest、校验 authenticated payload/inventory 并发布报告；FAIL 退出非零但不丢报告。Helm `backend.restoreJob.operation` 明确区分 `execute|reconcile`，reconcile 不挂载或消费 mutation approval，仍使用 digest-pinned non-root image、显式 SA/PVC、private projected Kubernetes token/CA 与 Temporal TLS。
- 真实 RST3-02 中断/续跑 integration 的 checkpoint SHA 为 `ea3b68e2eb02fc187fda0ea33def6bda28ebc56bb752304a42442f05372c8edd`，direct reconciliation PASS report SHA 为 `5cc6564dea55261acecf00ec84ebe9bd6cde1ce242116939c189d0ff75b41740`。kind v1.32.2 external Helm Job 在 live PG16.10/105、MinIO、Vault Transit 和 Temporal 1.25.2 上生成 PASS report SHA `f89ed25908a6f6422dde47c8fe1568c58fbdb752dc775be72433f0faeae45bf1`；相同 manifest/timestamp/PVC replay 后 report/checkpoint/各项 evidence SHA 完全不变，API 1/1 且 Workers 仍为 0。
- disposable Temporal TLS 错配首次只把 workflow check 标红为 `RESTORE_RECONCILIATION_INSPECTOR_ERROR`，其余八项 PASS，FAIL report 与三个 false flag 被保留；修正独立 CA leaf、HTTP/2 ALPN 和跨 Docker network bridge 后 live inventory 通过。这个失败证明 fail-closed report path，不是生产网络拓扑证据。最终镜像 digest `sha256:31e2d349738cf5b15d7348bc0b09f189936a932107699b6e4906dff0e2e0bb5e`；所有 disposable 资源和身份已永久清理，镜像保留。
- RST3-03 PASS 仍不证明生产 KES/HSM/IAM、Vault HA/Kubernetes auth、多节点 CSI、外部托管 Temporal SLA、双 Compose RPO 0 迁移、值班 runbook、HA/canary 或 DR7，也没有写入开放和 `RESTORE_VERIFIED` 路径。

#### RST3-04 planned migration 实现与未关闭验收

- `hc-platform-planned-migration/v1` 把同 release 的 distinct source/target、RPO 0、最多 1,800 秒停写和三种对象语义固化：默认 `reuse_external` 不复制 OSS；`copy_referenced` 只复制数据库/manifest 引用版本；显式 `portable` 生成加密分片。容量按 `actual_referenced_bytes` 和 headroom 动态计算，复用外部 OSS 时只计算数据库、临时文件和日志；容量 evidence 继续拒绝 sparse/synthetic bytes 冒充真实对象。
- cutover approval 与 live observation 分别由不同 Ed25519 key 签名；migration Job 只接收公钥 pin。严格 observation 在最多 300 秒内读取 PG system/timeline/LSN/recovery、对象 inventory/pending、Temporal inventory、source/target fence/Worker/writer/upload grant、只读 reconciliation、route/DNS、pause 和 retention。任何 identity、签名或阶段顺序漂移都不能创建 checkpoint。
- 七阶段 checkpoint 以 owner/fencing token/plan/approval/predecessor CAS 绑定且 exact replay。traffic switch 前持续复核 final-sync 和 target verification；target promote 后 timeline 必须前进，source 永久只读，checkpoint 固定禁止 direct rollback 并要求 reverse sync。基础功能 PASS 必须执行实际引用对象的端到端迁移；100 MB 真实对象足够用于功能门禁。独立 5 TB/day 性能基准不能由该功能演练代替。
- `hc-platform migration plan/advance/status`、默认关闭的 digest-pinned Helm Job 和 planned-cutover runbook 已实现；当前主机容量不足且没有外部 provider/DNS 容量环境，故没有生成虚假 PASS 或开放目标写入，RST3-04 仍为进行中。

### 5. Restore target 绑定

任何会读取 payload 或写目标的 restore 实现必须先消费 `hc-platform-restore-target/v1`：

- operation 明确绑定 `backup_id`、来源 `platform_id/environment_id` 和目标 instance；
- 目标必须为空，或带有显式临时恢复授权；
- `writes_enabled` 固定为 false；
- 目标必须选择备份记录的精确 Git/Chart/image/migration/release identity；
- PostgreSQL major 必须相同，对象存储必须启用版本控制；
- signer fingerprint 必须受信任，所有精确 KMS key version 必须可用；
- Temporal namespace 和必需外部依赖必须处于 ready。

来源 identity、backup ID、release、PostgreSQL major、trust pin 或 KMS 版本任一不符都 fail closed。验证通过只授予“可制定恢复计划”，RST3-02 还要求独立职责签名批准；即使恢复执行成功也不授予打开写入，RST3-03 reconciliation 仍是独立门禁。
V1 target 合同保留“具名临时恢复授权”的设计表达能力，但 RST3-01 的可执行 planner 更严格：只接受 `target_is_empty=true` 且没有临时覆盖授权的隔离目标。任何非空目标都不得进入 RST3-02。

## 威胁模型

### 受保护资产

- PostgreSQL 中的业务、账号权限、P19 审计链、Outbox、迁移账本和加密凭据；
- 对象存储中的 Raw/Lance/发布/预览对象及其版本和名称；
- Temporal namespace、schedule 和 workflow inventory；
- Secret/KMS 依赖、精确发布 identity、备份完整性与保留期；
- 恢复目标在验证完成前保持空、只读和未启动 Worker 的安全状态。

### 信任边界与攻击者

1. 业务 API/Worker 或业务数据库被攻陷：攻击者不应取得备份库写删、签名或 KMS decrypt 权限。
2. 备份传输/仓库被读取：repository KMS 和 portable age 防止 payload 泄露；公开 metadata 只保留恢复所需最小字段。
3. 备份仓库对象被替换、截断、回滚或混装：严格 schema、artifact hash、canonical manifest hash、Ed25519 和显式 backup/source identity 检出。
4. 运维误选备份、环境、release、PostgreSQL major 或 KMS key：restore-target 绑定在任何 mutation 前拒绝。
5. 恶意或误配置导出 Secret：严格字段和递归 plaintext scanner 拒绝 secret-shaped key、私钥 PEM、带凭据 URI 和 AWS access-key-shaped 值。
6. 清单静默漏掉状态域：具名必填域和固定 disposition 拒绝；外部域也必须有 owner 和 evidence artifact。
7. JSON parser differential/重复 key：重复 member、未知字段、未知格式和非规范数字直接拒绝，签名只覆盖唯一 canonical 表示。
8. signer key 泄露：目标 pin 公钥 fingerprint，key 版本独立撤销；事件后新 trust policy 拒绝受损版本，旧备份不被悄悄重签。

不在 V1 单独解决的威胁：同时控制已授权签名 key、备份库、目标 trust policy 和 KMS decrypt policy 的恶意管理员仍可伪造恢复输入。生产控制必须通过独立角色、双人审批、不可变审计和 DR7 证据降低风险。DoS、provider 大范围不可用和 KMS 永久丢失属于 HA/异站复制/密钥托管问题，不能由 manifest 签名消除。

## 强制验证顺序

Verifier 固定按以下顺序执行，任何失败都不得读取 payload、创建 schema、导入对象或启动 Worker：

1. 限制大小后解析 JSON，拒绝非 UTF-8、非 object 和重复 key；
2. 执行 plaintext Secret scan；
3. 检查精确 `format_version` 并按严格 schema 解析；
4. 检查 signature envelope、key reference、公钥 fingerprint、manifest hash 和 Ed25519；
5. 绑定 backup/source/target identity、精确 release、PostgreSQL major、KMS versions 和 target fencing；
6. RST3-01 在不下载 payload 的情况下验证源容量、目标空性/空间和外部 readiness evidence；RST3-02 只有在 exact plan 和独立职责签名 approval 绑定成功后，才按 checkpoint 顺序下载、逐项验证 artifact bytes 并恢复到只读目标；
7. RST3-03 只接受精确完成的 `READ_ONLY_READY` checkpoint，重新验签仓库与 payload 后运行固定九项只读 inspector 并发布非授权报告；任一 FAIL 都不得推进 checkpoint、启动 Worker、开放写入或写 `RESTORE_VERIFIED`。

错误必须使用稳定、可审计且不包含 Secret 的 code。未知版本不得尝试兼容解析；旧备份先用其记录的精确 release 恢复并验证，再执行前向升级。

## 后果与当前证据边界

- 十四份 JSON Schema 由严格 Pydantic 合同机械生成，生成器 `--check` 防止文档和运行时漂移。
- 合同测试使用真实 Ed25519 签名验证篡改、错格式、错 release、错来源目标、错 key 和明文 Secret 均 fail closed。
- 本 ADR 声称已有本地真实 PostgreSQL 逻辑恢复/PITR、精确对象版本/age portable、Vault KV v2/Transit exact-version、Temporal metadata-only probe、完整 `INTEGRITY_VERIFIED` backup set、只读 restore plan，以及 disposable PostgreSQL/MinIO/Temporal/Vault/kind 上可中断续跑的整站 payload restore和九域只读 reconciliation。它不声称生产 KMS/HSM/IAM、生产 WAL archive、外部托管 Temporal HA/backup、双 Compose RPO 0 planned migration、写入开放或 `RESTORE_VERIFIED`；这些必须在 RST3-04～05/HA4/REL6/DR7 举证。
- 5 TB/日 + 30% 容量门禁继续为 `NOT_PASSED`，不能因设计合同完成而解除。
