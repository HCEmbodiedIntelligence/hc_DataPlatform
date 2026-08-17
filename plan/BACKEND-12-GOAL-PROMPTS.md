# 后端 12 个 Codex `/goal` 并行提示词

> 使用方法：在 12 个独立 Codex 终端中，每个终端进入
> `/home/czy/hc_DataPlatform`，分别复制一个代码块。12 个终端共享同一个工作区时，
> 不要创建/切换分支，不要执行 commit、reset、checkout、clean、stash、rebase 或 cherry-pick。
> 每个终端只能修改提示词中列出的独占范围。

## 终端 1：BE-01 公共工程与合同

```text
/goal 完成 HC Data Platform 后端 BE-01 公共工程、公共合同和 CI 基线，并使该工作包达到可直接验收状态。

仓库：/home/czy/hc_DataPlatform
先阅读 plan/BACKEND-DATA-PIPELINE-IMPLEMENTATION-PLAN.md、git status 和现有 backend 实现。工作区已有其他人的前端改动和部分后端实现，必须保留；不要推倒重写已经正确的代码。

你的独占写入范围：
- backend/pyproject.toml、backend/uv.lock、backend/README.md、backend/.env.example、backend/Dockerfile、backend/compose.yaml、backend/Makefile
- backend/src/hc_data_platform/core/**
- backend/openapi/core.yaml、backend/openapi.generated.yaml
- backend/tests/core/**
- .github/workflows/backend-ci.yml
可以读取所有后端代码，但禁止修改 security、ingest、workflow、verification、quality、alignment、lance_catalog、annotation、preview、publishing、frontend 和 backend/deploy。

任务：
1. 完成 FastAPI app factory、配置校验、ProblemDetails、RequestContext、Cursor、ETag 公共约定和模块自动发现。
2. /health/live 只表示进程存活；/health/ready 必须通过可注入探针检查 PostgreSQL、Temporal、对象存储，失败时返回非 2xx 和具体依赖状态。
3. OpenAPI Fragment 聚合必须确定性、检测重复 path/schema，并提供兼容性检查；生成文件必须可重复。
4. 收集 backend/docs/dep-requests/BE-*.md 中的依赖申请，统一维护 pyproject 和锁文件；其他终端不能修改依赖。
5. 完成本地 Compose、API Docker 镜像和 CI。CI 执行 ruff format --check、ruff check、mypy、pytest、OpenAPI 生成/差异检查，且不触碰 frontend。
6. 修复公共代码中的类型、格式、Python 3.10/3.12 兼容问题，但不得替业务模块修改代码。

验收：
- 全新虚拟环境可安装；API 能启动并生成 OpenAPI。
- core tests 覆盖游标篡改、重复 OpenAPI 路由、配置错误、探针失败和模块自动发现。
- 同一 OpenAPI 输入生成相同 SHA-256；不兼容合同会让 CI 失败。
- 只运行全量测试进行只读检查；发现业务模块失败时写入 backend/docs/integration-findings/BE-01.md，不越界修复。
- 仅当自身范围 ruff、mypy、pytest、构建全部通过时才把 goal 标记完成。完成汇报必须列出改动文件、命令、结果和仍需其他负责人的事项。
```

## 终端 2：BE-02 数据库、安全与审计

```text
/goal 完成 HC Data Platform 后端 BE-02 PostgreSQL、JWT、Scope、幂等、审计和 Outbox 工作包。

仓库：/home/czy/hc_DataPlatform
先读 plan/BACKEND-DATA-PIPELINE-IMPLEMENTATION-PLAN.md 和现有 backend/src/hc_data_platform/security。保留现有实现，基于测试补完。

独占写入范围：
- backend/src/hc_data_platform/security/**
- backend/migrations/security/**
- backend/openapi/security.yaml
- backend/tests/security/**
- 如需新依赖，只能写 backend/docs/dep-requests/BE-02.md
禁止修改 core、其他业务模块、pyproject、锁文件、CI、deploy 和 frontend。

任务：
1. 实现 OIDC/JWT 验签，强制 exp、iat、sub、issuer、audience 和算法白名单；禁止 alg=none。
2. 实现 uploader、annotator、reviewer、publisher、admin 权限和 Project/Region Scope。
3. 提供 SQLAlchemy/PostgreSQL ScopedUnitOfWork、Repository、事务边界和 RLS session context；保留内存 Fake。
4. 实现 PostgreSQL IdempotencyStore：相同 scope+key+相同请求只执行一次，不同请求返回 409；并发下依赖唯一约束和事务。
5. 实现 ResourceVersion/ETag、AuditSink、OutboxPublisher，确保业务写入、审计和 Outbox 原子提交。
6. 迁移使用模块独立目录，可从空库重复执行；所有项目域表启用 RLS，并提供 worker 显式 scope 方式。

边界：不定义 Rollout/QC/Annotation 等领域状态，不访问 OSS，不启动 Temporal，不修改其他模块表。

验收：
- 有效、过期、错误签名、错误 issuer/audience、未知角色测试通过。
- 跨 Project/Region 读取和写入均被拒绝；service identity 缺少显式 scope 也被拒绝。
- 100 并发相同幂等键只发生一次副作用；同键不同 body 返回 409。
- 旧 ETag 返回 412；事务回滚后业务、审计、Outbox 均不留数据。
- PostgreSQL 集成测试可用 test container/本地 Compose 运行；无 PostgreSQL 时单测仍可用 Fake 完成。
- 只运行自己的格式、类型、单测和 integration marker；完成汇报列出命令和结果。
```

## 终端 3：BE-03 上传、OSS/MinIO 与离线导入

```text
/goal 完成 HC Data Platform 后端 BE-03 Upload Session、OSS/MinIO Multipart、Manifest 提交和离线导入工作包。

仓库：/home/czy/hc_DataPlatform
先读后端计划和现有 ingest 代码；不要替换已通过的内存实现，补齐生产适配器与合同。

独占写入范围：
- backend/src/hc_data_platform/ingest/**
- backend/migrations/ingest/**
- backend/openapi/ingest.yaml
- backend/tests/ingest/**
- 如需依赖，只写 backend/docs/dep-requests/BE-03.md
禁止修改 security/core/workflow/其他模块、pyproject、锁文件、deploy 和 frontend。

任务：
1. 完成 CollectionJob、Rollout、UploadSession、UploadObject、UploadPart 状态和持久化 Port/Fake。
2. 完成 ObjectStoragePort，以及阿里云 OSS 生产适配器和 MinIO/S3 本地适配器。数据正文必须客户端直传，API 只签发短期分片授权。
3. 实现 create、renew、list parts、complete、pause、resume、cancel；complete 必须接收排序后的 part_number+ETag，不能依赖进程内 upload_id→key 映射。
4. 实现 raw/v1/project/date/robot/job/rollout/sha256 Key；禁止覆盖。
5. 完成对象大小、OSS CRC64 和服务端流式 SHA-256 校验。Manifest 只能最后提交并作为 commit marker。
6. 相同 rollout+sha 幂等；相同 rollout 不同 sha 返回 ROLLOUT_CONTENT_CONFLICT。
7. 离线 CLI 校验 MCAP/Manifest 后调用同一 HTTP 协议，不单独创造命名和状态规则。
8. API 接入 BE-02 AuthContext/ScopeGuard，但只消费接口，不修改 security。

边界：不解析 MCAP Header/Footer，不执行 QC、对齐、Lance 或视频。

验收：
- MinIO 集成测试覆盖中断续传、授权续期、乱序/缺失 part、完成和取消。
- Manifest 在对象未完成、size/CRC64/SHA 错误时不能提交。
- 同 hash 重试返回同一资源，不同 hash 409。
- 流式 SHA 测试证明不会把大文件整体加载进内存；稀疏 20GB 场景只验证控制面和直传语义。
- 离线 CLI 与机器人上传得到相同 Key 和 Manifest。
- 自身 ruff、mypy、pytest 和 OpenAPI 合同测试通过后完成。
```

## 终端 4：BE-04 Temporal 编排与任务中心

```text
/goal 完成 HC Data Platform 后端 BE-04 Temporal 工作流、Activity 编排、任务中心和恢复语义。

仓库：/home/czy/hc_DataPlatform
先读计划、现有 workflow 代码及各阶段公开 Port。保留纯内存参考实现，并补齐真实 Temporal Python SDK 适配器。

独占写入范围：
- backend/src/hc_data_platform/workflow/**
- backend/migrations/workflow/**
- backend/openapi/workflow.yaml
- backend/tests/workflow/**
- 如需依赖，只写 backend/docs/dep-requests/BE-04.md
禁止修改任何阶段算法模块、core、pyproject、deploy 和 frontend。

任务：
1. 实现 Temporal IngestRolloutWorkflow、DatasetWriterWorkflow、PreviewWorkflow、PublishDatasetWorkflow、ExportWorkflow。
2. 使用结构化 Activity 输入输出调用 verification、quality、alignment、lance_catalog、preview、publishing 的 Port，不在 workflow 内复制业务算法。
3. Workflow ID 必须确定：kind+version+project+resource；重复启动返回现有运行。
4. 实现 RetryPolicy、Heartbeat、Start-to-close、Schedule-to-close、取消和 non-retryable 业务错误分类。
5. Quality PASS 才进入正式 derive；RISK 进入隔离预览状态且 training_eligible=false；REJECT 终止派生但保留 Raw。
6. Worker 自动注册真实 Workflow/Activity；当前 worker.py 不得再以空注册启动。
7. Job API 区分 PENDING/RUNNING/SUCCEEDED/TECHNICAL_FAILED/QUALITY_RISK/QUALITY_REJECTED/CANCELLED。
8. 提供 Lance 已提交但 DB 未登记、发布临时资产已生成但事务失败等对账 Workflow。

边界：不修改质量结论，不修改 MCAP/Lance 内容，不吞掉业务错误。

验收：
- Temporal replay tests 全部通过。
- 相同 Workflow ID 并发启动只有一个执行。
- Activity 在副作用后崩溃，重试没有重复 Step/版本。
- 网络错误退避重试，校验失败/QC REJECT 不重试。
- 取消停止后续活动但不删除 Raw。
- API/worker 重启可恢复；自身 ruff、mypy、pytest 通过后完成。
```

## 终端 5：BE-05 MCAP 完整性校验

```text
/goal 完成 HC Data Platform 后端 BE-05 MCAP 流式完整性校验工作包，并修正现有实现的兼容与合同问题。

仓库：/home/czy/hc_DataPlatform
先读计划和 verification 现有代码/测试。已有自研有限 MCAP Parser，不要盲目推倒；先用 Golden MCAP 验证，再决定是否用官方 mcap SDK 作为生产适配器。

独占写入范围：
- backend/src/hc_data_platform/verification/**
- backend/migrations/verification/**
- backend/openapi/verification.yaml
- backend/tests/verification/**
- 如需依赖，只写 backend/docs/dep-requests/BE-05.md
禁止修改 ingest、quality、alignment、core、pyproject、deploy 和 frontend。

任务：
1. 校验 Magic、Header、Footer、DataEnd、Summary/索引、Chunk size/CRC、消息遍历和正常关闭。
2. 生成 Schema/Channel/Topic/消息数/时间范围清单。
3. 检查必需 Topic，并通过 DecoderProbe 对每个 Topic 至少抽检一条消息；未知可选 Topic 只 Warning。
4. 从可流式读取的 ObjectStorage Reader 工作，不整体加载 20GB 文件。
5. 生成确定性的 RawVerificationReportV1 和稳定错误码；输入 Raw 不得修改。
6. 检查现有 Python 3.10 Enum/BinaryIO 兼容修正，补齐 OpenAPI、README 和迁移。

边界：不重复 OSS CRC64/SHA 传输校验，不评价帧率/黑帧/关节质量，不执行对齐。

验收：
- Golden MCAP 正常通过；截断、缺 Footer、损坏索引、CRC 错、未知压缩、缺 Topic、解码失败分别得到明确错误码。
- 相同输入报告哈希一致。
- 大对象读取峰值内存受 chunk 上限控制。
- Fake 和生产 Reader/Decoder 合同测试通过。
- 自身 ruff、mypy、pytest 全通过后完成。
```

## 终端 6：BE-06 数据质量与时序质检

```text
/goal 完成 HC Data Platform 后端 BE-06 数据质量、时序指标、规则 Profile 和 PASS/RISK/REJECT 工作包。

仓库：/home/czy/hc_DataPlatform
先读计划和现有 quality 实现。保持规则引擎确定性，补齐缺失测试、持久化端口和合同。

独占写入范围：
- backend/src/hc_data_platform/quality/**
- backend/migrations/quality/**
- backend/openapi/quality.yaml
- backend/tests/quality/**
- 如需依赖，只写 backend/docs/dep-requests/BE-06.md
禁止修改 verification、alignment、lance_catalog、core、pyproject、deploy 和 frontend。

任务：
1. 完成版本化 QualityProfile，所有频率、时长、覆盖、容差和质量阈值来自 Profile。
2. 计算实际频率、唯一帧、重复/倒退时间戳、P50/P95/P99、最大 gap、连续缺帧和覆盖率。
3. 检测黑帧、重复帧、损坏图像接口；关节越界、Action 缺失/跳变、空点云/点数异常、模态偏差和完整 Step 比例。
4. 硬失败→REJECT，软偏差→RISK，无 Finding→PASS；不能自动升级 RISK。
5. 完整 qc_report 写不可变 ReportSink，摘要写 MetadataSink；技术失败与质量结论分离。
6. 保证报告内容和哈希确定性，Finding 包含规则、阈值、观测值、Topic 和影响区间。

边界：不解析 MCAP 容器，不执行正式插值，不写 Lance。

验收：
- 30Hz 正常样本 PASS；28Hz 按 Profile RISK；时间倒退/必需 Topic 大段缺失 REJECT。
- 多相机覆盖差、黑帧、空点云、Action 跳变和关节越界各有测试。
- 同输入+Profile+engine version 报告完全一致。
- ReportSink 失败不伪造成功状态。
- 自身 ruff、mypy、pytest 和 OpenAPI 测试通过后完成。
```

## 终端 7：BE-07 30Hz 多模态对齐

```text
/goal 完成 HC Data Platform 后端 BE-07 30Hz 多模态对齐、暂存分片和确定性哈希工作包。

仓库：/home/czy/hc_DataPlatform
先读计划和现有 alignment 实现/测试。当前 tests/alignment 可能存在 rows 调用顺序错误和 test_alignment.py.rej，先在自己的范围内清理并修复。

独占写入范围：
- backend/src/hc_data_platform/alignment/**
- backend/migrations/alignment/**
- backend/openapi/alignment.yaml
- backend/tests/alignment/**
- 如需依赖，只写 backend/docs/dep-requests/BE-07.md
禁止修改 quality、lance_catalog、core、pyproject、deploy 和 frontend。

任务：
1. 用整数秒生成 [start,end) 标准时间轴，默认30Hz，避免浮点累计漂移。
2. 图像/点云 nearest、连续关节 linear、Action causal previous、离散状态 recent、IMU/力矩 window aggregate。
3. 容差来自 AlignmentProfile；超限写 value=None、valid=false，不能静默伪造。
4. 保存 source_timestamps_ns、time_error_ns、valid、repeated、sample_valid；图片复用必须 repeated=true。
5. FragmentWriter 逐行写 attempt 隔离暂存，失败只 abort 当前 attempt。
6. schema_sha256/content_sha256 不包含 attempt_id 等非内容字段；相同输入/Profile/converter 结果一致。
7. 不提交共享 Lance，只输出 AlignedFragmentManifestV1。

验收：
- 60秒30Hz生成1800个连续 Step。
- 各模态策略、嵌套连续值插值、窗口均值、因果不读未来值均有测试。
- 容差失效和 repeated 来源时间测试通过。
- 相同内容不同 attempt 哈希一致；写入失败不留 committed fragment。
- 删除补丁残留 .rej；自身 ruff、mypy、pytest 全通过后完成。
```

## 终端 8：BE-08 Lance Dataset 与目录

```text
/goal 完成 HC Data Platform 后端 BE-08 Lance Dataset、单写者提交、目录索引和跨存储对账工作包。

仓库：/home/czy/hc_DataPlatform
先读计划和现有 lance_catalog 实现/测试。保留现有内存 Fake；检查并删除或补全仅有占位注释的 adapters.py。

独占写入范围：
- backend/src/hc_data_platform/lance_catalog/**
- backend/migrations/lance_catalog/**
- backend/openapi/lance_catalog.yaml
- backend/tests/lance_catalog/**
- 如需依赖，只写 backend/docs/dep-requests/BE-08.md
禁止修改 alignment、annotation、preview、publishing、core、pyproject、deploy 和 frontend。

任务：
1. Dataset Schema Snapshot 编译为稳定 Arrow/Lance Schema；不兼容 Schema 拒绝写同一 Dataset。
2. 每个 project+dataset+schema_snapshot+frequency 共享一个 aligned_steps.lance。
3. 验证暂存 Manifest、step_index 连续、schema/content hash，并实现 per-dataset 单写者。
4. 幂等键 rollout_id+source_sha256+converter_version；重试不得重复 Step。
5. 真实 LanceAdapter 放在 Port 后；保留内存 Fake。先写 attempt staging，提交成功后登记 PostgreSQL version/lineage。
6. Lance 成功、DB 失败时记录 pending reconciliation；retry/reconciler 能恢复，不能再次 append。
7. StepReader 使用 rollout_id+step_index，不暴露物理行号；提供 window、lineage、dataset version snapshot。

边界：不生成对齐内容，不修改 Step，不执行标注或媒体编码。

验收：
- 20 Rollout 并发转换结果经单写者全部提交且无重复。
- 同幂等键重放返回同一版本；不同 Schema 被拒绝。
- 注入目录事务失败后 reconcile 恢复一致性。
- compaction/重写后外部 Step 身份不变。
- 真实 Lance integration marker 和内存单测通过；自身 ruff、mypy、pytest 通过后完成。
```

## 终端 9：BE-09 标注、清洗与审核

```text
/goal 完成 HC Data Platform 后端 BE-09 人工标注、非破坏性区间操作、Revision 和 Reviewer 审核工作包。

仓库：/home/czy/hc_DataPlatform
先读计划和现有 annotation 实现/测试，基于不可变 Revision 模型补齐 API、持久化和权限。

独占写入范围：
- backend/src/hc_data_platform/annotation/**
- backend/migrations/annotation/**
- backend/openapi/annotation.yaml
- backend/tests/annotation/**
- 如需依赖，只写 backend/docs/dep-requests/BE-09.md
禁止修改 lance_catalog、preview、publishing、security、core、pyproject、deploy 和 frontend。

任务：
1. 实现 AnnotationTask、Draft、Current、Revision、Operation、Review 的 PostgreSQL Port/Fake。
2. 支持 claim、保存 draft、submit、review、needs_revision/reject 和历史查询。
3. EXCLUDE/RESTORE 使用半开区间 [start_step,end_step)，服务端规范化重叠区间；restore 新增操作，不删除历史。
4. 保存要求 expected_revision、If-Match、client_mutation_id；相同 mutation 幂等，不同内容冲突。
5. Annotator/Reviewer/Publisher 权限消费 BE-02；禁止无 Scope、越权、自审和读取别项目。
6. APPROVED 必须绑定确切 Revision；后续编辑产生新 Revision 并失去旧发布资格。
7. 只保留 AutoAnnotationProvider Protocol 和 Disabled Provider；VLM capability=false，无任务表和假结果。

边界：不修改 Raw/Lance，不编码视频，不直接发布数据集。

验收：
- [300,450) 精确排除300..449并作用于所有模态语义。
- restore 后有效区间正确且历史完整。
- 两人并发保存旧 revision 返回412；mutation重放不重复。
- 自审、跨项目、错误角色均被拒绝。
- approved revision 快照可供发布读取；VLM 返回 FEATURE_DISABLED。
- 自身 ruff、mypy、pytest、迁移和 OpenAPI 测试通过后完成。
```

## 终端 10：BE-10 临时 HLS 预览

```text
/goal 完成 HC Data Platform 后端 BE-10 Lance 图像到临时 HLS/fMP4 的预览服务工作包。

仓库：/home/czy/hc_DataPlatform
先读计划和现有 preview 实现/测试。保留内存 Fake，补齐 BE-08/09 消费适配器和真实 FFmpeg Adapter。

独占写入范围：
- backend/src/hc_data_platform/preview/**
- backend/migrations/preview/**
- backend/openapi/preview.yaml
- backend/tests/preview/**
- 如需依赖，只写 backend/docs/dep-requests/BE-10.md
禁止修改 lance_catalog、annotation、publishing、core、pyproject、deploy 和 frontend。

任务：
1. 在 preview/adapters.py 内适配 StepReaderPort.read_steps 和 EffectiveExclusionPort；只消费，不修改提供方。
2. original 保持源 Step 顺序；edited 应用 half-open exclude 并压缩播放时间；compare 保留完整帧并标记排除。
3. 返回 presentation time↔source step 的区间映射，edited 不允许前端猜映射。
4. 无效图像生成带 invalid_reason 的占位帧，不删除或伪装。
5. 实现 FFmpeg HLS+CMAF/fMP4 Encoder Port，使用安全参数数组、不拼 shell；分段输出先写临时目录，成功后原子提交缓存。
6. Cache Key 包含 rollout、lance_version、annotation_revision、camera、view_mode、range和编码profile。
7. 默认缓存24h、签名15min；revision变化不命中旧缓存；缓存不是训练资产。

边界：不修改 Annotation，不生成永久资产，不发布数据集。

验收：
- 三种模式和映射测试通过。
- invalid frame 占位测试通过。
- 缓存命中不重复读取/转码，过期和 revision 变化正确失效。
- FFmpeg 集成测试生成可探测的 m3u8/fMP4；失败不提交半成品。
- 自身 ruff、mypy、pytest、OpenAPI 测试通过后完成。
```

## 终端 11：BE-11 发布与 LeRobot v3 导出

```text
/goal 完成 HC Data Platform 后端 BE-11 不可变数据集发布、训练 Manifest、Lance Snapshot 和 LeRobot v3 导出工作包。

仓库：/home/czy/hc_DataPlatform
先读计划和现有 publishing 实现/测试。保留确定性内存实现，补齐 BE-08/09 适配器和真实导出器边界。

独占写入范围：
- backend/src/hc_data_platform/publishing/**
- backend/migrations/publishing/**
- backend/openapi/publishing.yaml
- backend/tests/publishing/**
- 如需依赖，只写 backend/docs/dep-requests/BE-11.md
禁止修改 lance_catalog、annotation、preview、core、pyproject、deploy 和 frontend。

任务：
1. 在 publishing/adapters.py 适配 Catalog Snapshot、Step Reader 和 Approved Annotation Snapshot。
2. 发布预检：默认只包含 quality=PASS、derived=DERIVED_READY、annotation=APPROVED 的 Rollout。
3. 使用有效 exclusion 求 included_step_ranges；RISK、REJECT和排除 Step 默认不进入训练集。
4. 冻结 base_lance_version、annotation_revision、quality/alignment profile、source_mcap_sha256、converter_version。
5. 生成确定性的 annotations.lance 逻辑资产和 training manifest；同输入得到同 content hash。
6. Published Version 不可原地修改；内容变化必须创建新版本。
7. 实现 ExporterPort、Lance Snapshot Exporter 和 LeRobot v3 Exporter；失败输出放 attempt staging，校验后才发布下载授权。
8. 一期不实现 HDF5/Parquet/VLM/永久MP4。

验收：
- 资格过滤、exclude范围、完整血缘和确定性hash测试通过。
- 相同version id不同内容返回冲突；已发布内容不可覆盖。
- LeRobot v3导出可由真实读取器重新加载，所有模态Step同步。
- 失败不留下可下载半成品，重试幂等。
- 自身 ruff、mypy、pytest、OpenAPI和integration marker通过后完成。
```

## 终端 12：BE-12 系统测试、可观测性与试点部署

```text
/goal 完成 HC Data Platform 后端 BE-12 跨模块系统测试、故障注入、可观测性、容量验证和试点部署工作包。

仓库：/home/czy/hc_DataPlatform
先读完整后端计划和全部公开接口。你是验收负责人，不替业务负责人改业务代码。

独占写入范围：
- backend/tests/system/**、backend/tests/fixtures/**、backend/tests/load/**
- backend/observability/**
- backend/deploy/**
- backend/runbooks/**
- 如需依赖，只写 backend/docs/dep-requests/BE-12.md
禁止修改任何 src/hc_data_platform 业务模块、pyproject、锁文件、CI 和 frontend。发现缺陷写 backend/docs/integration-findings/BE-12.md，包含复现命令和负责模块。

任务：
1. 建立合法、截断、坏CRC、缺Topic、28Hz、时间倒退、黑帧、空点云等 Golden MCAP/结构化 Fixture。
2. 完整 E2E：upload→manifest→verify→QC→align→Lance→preview→annotation→review→publish→LeRobot export。
3. 故障注入：SHA、QC、Lance提交、DB登记、转码和导出阶段杀Worker/断网络/重复消息，验证恢复无重复数据。
4. OpenTelemetry/Prometheus 指标覆盖上传积压、workflow失败、QC分布、Lance提交、转码和导出；日志验证无token/签名泄漏。
5. 完成 Helm API/Worker、配置、Secret引用、探针、资源、滚动升级和回滚；不把密钥写入 values。
6. 负载方案验证20GB Rollout控制面、50并发上传会话；在明确试点硬件上测量吞吐并形成5TB/日+30%余量报告，不能凭空宣称通过。

边界：不能为了让测试通过修改合同或业务逻辑；不能删除Raw/Lance/Published数据。

验收：
- E2E及所有异常Fixture得到预期状态。
- 重复/崩溃/恢复不产生重复Rollout、Step、Revision或Version。
- Dashboard和Alert都含project/resource/workflow定位信息与Runbook链接。
- Helm lint/template、升级和回滚演练通过。
- 容量报告写清硬件、数据、命令、P50/P95/P99、瓶颈和未达标项。
- 仅当系统测试和部署范围全部通过时完成；业务失败必须报告给对应BE负责人，不能越界修复。
```

## 最后集成规则

12 个 Goal 全部结束后，由 BE-01 终端只读汇总全量检查，BE-12 运行系统验收。
如果出现跨模块合同不匹配，消费方先在自己的 adapter 中适配；只有无法兼容时，才由提供方提交合同变更。
任何终端都不得通过修改其他人的测试、降低阈值或删除失败场景来宣称完成。
