# 后端数据主链路 12 人并行开发计划

## 1. 计划文件与总体目标

后端代码统一放入 `backend/`，不修改现有前端代码和前端计划。

一期交付：

```text
MCAP 上传
→ Raw 完整性校验
→ 数据质量评估
→ 30 Hz 多模态对齐
→ Lance Dataset
→ HLS 预览
→ 人工标注与非破坏性清洗
→ 审核
→ 数据集版本发布
→ Lance / LeRobot v3 导出
```

按 12 名开发人员拆分为 `BE-01` 至 `BE-12`。每个人拥有独立代码目录、数据库迁移目录、OpenAPI Fragment 和测试目录，完成验收后可直接进入主分支 Merge Queue。

## 2. 全员统一开发规则

### 2.1 目录所有权

每个业务模块统一包含：

```text
backend/src/hc_data_platform/<module>/
backend/openapi/<module>.yaml
backend/migrations/<module>/
backend/tests/<module>/
```

规则：

- 负责人只能直接修改自己拥有的目录。
- `core/`、根 `pyproject.toml`、锁文件、CI 和 Compose 仅由 BE-01 修改。
- 新增第三方依赖先提交依赖申请，由 BE-01 单独更新锁文件。
- 跨模块类型由提供方拥有，消费方只能依赖，不能复制或修改。
- 公共合同发生变化时，先合并独立 Contract PR，再修改业务代码。
- 各模块通过自动发现注册，不允许多人共同编辑中央 Router 或 Worker 注册表。
- 各负责人必须提供 Fake Port，使上下游未完成时仍能独立开发和验收。

### 2.2 公共完成标准

每个人的代码只有同时满足以下条件才算完成：

- 模块 API、Port、事件和数据表均有版本化定义。
- 正常、失败、重试、幂等、权限和并发场景有自动化测试。
- `ruff format --check`、`ruff check`、`mypy` 和模块 pytest 全部通过。
- 数据库迁移能从空库执行，重复执行不产生副作用。
- 日志包含 `request_id`、`project_id`、业务资源 ID 和错误码，不记录 JWT、签名 URL 或敏感数据。
- 提供模块 README、运行方法、配置项和故障处理说明。
- 未完成能力必须关闭或返回 `FEATURE_DISABLED`，不能伪造成功状态。
- 不修改其他负责人目录，不修改前端。

## 3. 每个人的任务、边界与验收

### BE-01：后端基础工程与公共合同

**负责范围**

- Python/uv 工程、FastAPI 应用、配置、模块自动发现和健康检查。
- 公共错误、请求上下文、游标分页、ETag、幂等规范和 OpenAPI 聚合。
- PostgreSQL、Temporal、MinIO 本地 Compose、CI 和 Helm 基础模板。

**对外提供**

- `RequestContext`、`ProblemDetails`、`PageInfo`、`ModuleRouter`、`DomainEventEnvelope`。

**明确边界**

- 不实现上传、MCAP、QC、Lance、标注或发布业务。
- 不定义业务模块自己的状态机和数据库表。

**验收**

- 一条命令启动 API、PostgreSQL、Temporal 和 MinIO。
- `/health/live`、`/health/ready` 和模块自动发现可用。
- OpenAPI 聚合稳定，CI 能阻止不兼容合同。

### BE-02：数据库、鉴权、Scope 与审计底座

**负责范围**

- PostgreSQL、Unit of Work、Repository、OIDC/JWT、项目角色、Scope/RLS。
- 幂等记录、审计、Outbox、资源版本和 ETag。

**对外提供**

- `AuthContext`、`ScopedUnitOfWork`、`IdempotencyStore`、`AuditSink`、`OutboxPublisher`、`ResourceVersion`。

**明确边界**

- 不定义领域表，不访问 OSS，不启动 Workflow，不决定业务状态。

**验收**

- JWT、跨项目隔离、100 并发幂等、过期 ETag、Outbox 原子性和审计测试通过。

### BE-03：上传会话、OSS/MinIO 与离线导入

**负责范围**

- Collection Job、Rollout、Upload Session、OSS/MinIO Port、Multipart、Raw Key。
- CRC64、对象大小、SHA-256、Manifest 最后提交和离线导入 CLI。

**对外提供**

- `ObjectStoragePort`、`UploadSessionService`、`RolloutManifestV1`、`RawObjectCommittedV1`。

**明确边界**

- 不解析 MCAP，不执行 QC、对齐或 Lance 写入。

**验收**

- 断点续传、校验、Manifest 提交、相同哈希幂等、不同哈希冲突和 20 GB 直传测试通过。

### BE-04：Temporal 工作流与统一任务中心

**负责范围**

- Temporal Client/Worker、Ingest/QC/Derive/Preview/Publish/Export Workflow。
- Activity 重试、超时、Heartbeat、取消、Job API 和跨存储对账。

**对外提供**

- `WorkflowLauncher`、`JobStatusPort`、`IngestRolloutWorkflow`、`DatasetWriterWorkflow`、`PublishDatasetWorkflow`。

**明确边界**

- 不实现 Activity 内部算法，不吞掉质量失败，不直接修改业务状态。

**验收**

- Replay、确定性 ID、崩溃恢复、幂等副作用、取消和任务状态测试通过。

### BE-05：MCAP 文件完整性校验

**负责范围**

- MCAP Header/Footer/Summary/索引、CRC、遍历、Topic、Schema 和解码检查。
- 生成不可变 `raw_verification_report.json`。

**对外提供**

- `RawVerificationPort`、`RawVerificationReportV1`、`RawVerifiedV1`。

**明确边界**

- 不重复传输校验，不评价业务质量，不对齐，不修改 MCAP。

**验收**

- 正常、截断、缺 Footer、损坏索引、错误 CRC、缺 Topic、无法解码和流式内存测试通过。

### BE-06：数据质量与时序质检引擎

**负责范围**

- `quality_profile`、覆盖率、频率、间隔分位数、缺帧、视觉异常、关节/Action/点云异常和模态偏差。
- `PASS`、`RISK`、`REJECT` 和 `qc_report.json`。

**对外提供**

- `QualityEvaluationPort`、`QualityProfileV1`、`QcReportV1`、`QualityCompletedV1`。

**明确边界**

- 不校验 MCAP 容器，不正式插值，不升级 RISK，不写 Lance。

**验收**

- 30 Hz PASS、28 Hz RISK、时间倒退 REJECT 和全 Finding 可追溯测试通过。

### BE-07：30 Hz 多模态对齐引擎

**负责范围**

- `alignment_profile`、统一时间轴、图像/点云最近帧、关节插值、Action/状态选择、聚合和有效性字段。
- 生成确定性的 Arrow/Lance 暂存分片。

**对外提供**

- `AlignmentPort`、`AlignmentProfileV1`、`AlignedFragmentManifestV1`、`AlignedFragmentReadyV1`。

**明确边界**

- 不提交共享 Lance，不决定 QC，不生成视频，不修改 MCAP。

**验收**

- 60 秒约 1800 Step、各模态策略、容差失效、重复帧标记、确定性哈希和受限内存测试通过。

### BE-08：Lance Dataset 与数据目录

**负责范围**

- Schema Snapshot、共享 Lance、暂存验证、单写入提交、版本和血缘。
- Dataset/Version/Rollout/Step Window API 和跨存储对账。

**对外提供**

- `LanceCatalogPort`、`StepReaderPort`、`DatasetVersionRef`、`DerivedReadyV1`。

**明确边界**

- 不生成对齐内容，不执行标注或视频，不暴露物理行号，不并发直写共享 Dataset。

**验收**

- 20 Rollout 并行提交、幂等、Schema 拒绝、对账恢复、稳定 Step 身份和 `DERIVED_READY` 测试通过。

### BE-09：人工标注、非破坏性清洗与审核

**负责范围**

- Task、Draft、Revision、Operation、Review、排除/恢复、编辑冲突和 Reviewer 权限。
- 仅定义 `AutoAnnotationProvider`，不实现 VLM。

**对外提供**

- `AnnotationReadPort`、`AnnotationWritePort`、`EffectiveExclusionPort`、`AnnotationApprovedV1`。

**明确边界**

- 不修改 Raw/Lance，不生成 HLS，不发布数据，不实现 VLM。

**验收**

- 半开区间、多模态统一排除、恢复留历史、412 冲突、角色权限和 VLM 关闭测试通过。

### BE-10：HLS/fMP4 临时预览服务

**负责范围**

- `original`、`edited`、`compare` 模式、FFmpeg HLS/fMP4、占位帧、缓存、签名和时间映射。

**对外提供**

- `PreviewService`、`PreviewDescriptorV1`、`TimelineMappingV1`。

**明确边界**

- 不修改标注，不生成永久训练资产，不把缓存作为训练来源。

**验收**

- 三种模式、排除映射、无效帧、版本化缓存、24 小时 TTL 和 15 分钟签名测试通过。

### BE-11：数据集版本发布与训练导出

**负责范围**

- 发布预检、不可变版本、`annotations.lance`、训练 Manifest、Lance Snapshot 和 LeRobot v3 导出。

**对外提供**

- `DatasetPublisher`、`ExporterPort`、`PublishedDatasetManifestV1`、`DatasetVersionPublishedV1`。

**明确边界**

- 不修改基础数据，不实现 HDF5/Parquet/VLM，一期不要求永久 MP4。

**验收**

- 资格过滤、排除 Step、完整血缘、确定性哈希、不可变版本、LeRobot 读取和半成品隔离测试通过。

### BE-12：系统测试、可观测性与试点部署

**负责范围**

- Golden MCAP、合同/集成/E2E、故障注入、容量测试、OpenTelemetry、Dashboard、告警和 Kubernetes 试点部署。

**明确边界**

- 不替业务负责人修改业务逻辑和合同，不拥有业务状态机。

**验收**

- 完整链路、异常 Fixture、崩溃恢复、50 并发、5 TB/日容量、Dashboard、Runbook 和 Helm 回滚通过。

## 4. 跨人员接口和依赖关系

```text
BE-03 RawObjectCommitted
          ↓
BE-04 编排调用 BE-05
          ↓
BE-05 RawVerified
          ↓
BE-04 编排调用 BE-06
          ↓
BE-06 QualityCompleted(PASS)
          ↓
BE-04 编排调用 BE-07
          ↓
BE-07 AlignedFragmentReady
          ↓
BE-08 DerivedReady
          ├── BE-09 标注与审核
          └── BE-10 临时预览
                    ↓
          BE-11 发布与导出
```

- BE-04 只负责编排和恢复，不拥有阶段结果。
- RISK 数据可以进入隔离预览，但默认不触发正式派生提交。
- BE-08 的 `StepReaderPort` 供标注、预览和发布使用。
- BE-09 的审核结果是 BE-11 发布输入。
- BE-12 从第一周开始建立测试。

## 5. 合并顺序

1. BE-01、BE-02 合并公共基线并冻结合同。
2. BE-03 至 BE-07、BE-12 使用 Fake Port 独立并行合并。
3. BE-08 至 BE-11 按冻结合同并行实现并合并。
4. BE-12 持续补齐完整 E2E 和试点验收。

## 6. 最终联合验收

- Raw MCAP 不可修改、不可覆盖、可校验和追溯。
- 重试、重复消息和 Worker 崩溃不产生重复业务数据。
- PASS/RISK/REJECT 语义明确，RISK 默认不进入训练集。
- 对齐保留来源时间、误差和有效性，缺失数据不静默伪造。
- Lance 增量提交安全，跨存储不一致可修复。
- 标注清洗版本化，不修改 Raw 和基础 Lance。
- 预览仅生成临时 HLS，不成为训练来源。
- 发布不可变、可复现，LeRobot v3 可加载。
- JWT、角色和 Scope 隔离通过。
- 20 GB Rollout、50 并发上传和 5 TB/日试点基线通过。

## 7. 一期明确不做

- 不开发前端，不实现完整 P01-P19 后端。
- 不实现机器人资产、标定、存储生命周期和完整 Schema Registry。
- 不实现独立人工问题/EDL 清洗系统。
- VLM 只保留接口。
- 不实现 HDF5、Parquet 导出。
- 派生阶段不生成永久 MP4。
