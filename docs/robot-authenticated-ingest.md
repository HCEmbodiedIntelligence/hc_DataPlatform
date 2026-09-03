# 机器人身份直传 Raw

机器人采集端使用组织级平台身份，不绑定固定项目。每次上传只把
`collection_task_id` 作为归属入口；服务端解析任务后得到唯一的组织、项目、数据集、
Region 和处理配置。客户端传入的 `organization_id`、`project_id` 仅用于一致性检查，
不能改变任务归属。

## 管理身份与凭据

在“数据接入 / 数据源”的“机器人上传身份”区域创建身份。一个组织内同一
`robot_id` 只有一个身份。管理员可以：

- 启用或停用身份；
- 限制 HTTPS 传输、允许的数据格式和批次/资产/分片策略；
- 签发、轮换和撤销凭据；
- 查看最近认证、心跳、上传、失败尝试以及 Raw/Episode/QC 统计。

新凭据以 `hcri_...` 形式只展示一次。服务端只保存 HMAC 摘要和可公开的前缀，列表、
日志与 OpenAPI 管理响应都不会再次返回明文。把凭据写入机器人的安全存储，例如：

```bash
export HC_ROBOT_INGEST_TOKEN='hcri_...'
```

## 统一协议

所有格式走同一组接口：

1. `POST /api/v1/robot-ingest/uploads` 提交不可变 manifest；
2. `POST .../assets/{asset_id}:authorize-parts` 查询已完成分片并获取短期直传 URL；
3. 客户端直接向 OSS/S3 上传文件体；
4. `POST .../assets/{asset_id}:complete` 以完整分片清单完成资产并校验大小、SHA-256、
   OSS 兼容 CRC-64/XZ；
5. `POST .../{upload_id}:commit` 幂等提交 Raw，并启动对应 Adapter 的后续处理。

幂等键是机器人身份、任务 ID 和 `client_upload_id` 的组合。官方脚本把客户端 UUID
保存到 `.hc-robot-ingest-state.json`；该文件不含机器人凭据、临时 URL 或 OSS 密钥。
新 sidecar 以 `0600` 权限原子发布并在落盘后才创建平台会话，避免进程崩溃或并发启动产生
两个本地 UUID。
重跑相同命令会读取服务端的持久分片列表、跳过已上传部分，并在 URL 过期或 PUT 结果
不确定时先核对 OSS 状态再重新授权。

上传可调用 `:pause`、`:resume`、`:cancel`。取消会终止尚未提交的 multipart；已提交的
Raw、manifest 和归属事实不可覆盖。未提交会话按身份策略中的保留天数设置
`expires_at`；过期访问返回 410，并终止仍在进行的 multipart，但保留上传、资产和失败
尝试记录用于审计。

## 连续采集包

先用现有 `native_unitree_g1_recording` 工具生成完整 bundle，然后运行：

```bash
python -m hc_data_platform.tools.native_recording_upload \
  --bundle-dir /data/capture-bundle \
  --api-base-url https://platform.example.com
```

当 `HC_ROBOT_INGEST_TOKEN` 存在时，脚本自动使用统一机器人协议，`project-id` 和
`organization-id` 只是可选的一致性断言，`region-code` 不再由客户端选择。没有机器人
凭据时，脚本仍按原参数使用兼容的用户会话接口。

连续数据提交后先形成一个 Raw。Episode 数保持独立：`declared_episode_count`、
`verified_episode_count` 和后续切片产生的 `derived_episode_count` 不会混为一项。

## LeRobot v3

LeRobot 目录需要提供真实采集时间：

```bash
python -m hc_data_platform.tools.lerobot_platform_upload \
  --source-dir /data/lerobot-v3 \
  --api-base-url https://platform.example.com \
  --collection-task-id task-a \
  --robot-id robot-a \
  --capture-started-at 2026-09-01T00:00:00+08:00 \
  --capture-ended-at 2026-09-01T00:20:00+08:00
```

脚本上传原始 Parquet、MP4 和 metadata，不转换为 MCAP。未设置
`HC_ROBOT_INGEST_TOKEN` 时保留原来的平台用户登录流程。

## Adapter 边界

multipart 传输层不识别文件格式。格式差异只存在于 Adapter：LeRobot v3、连续采集包、
MCAP，以及带已批准 `adapter_name` 的自定义格式。Adapter 负责 manifest 预检、资产验证、
Episode 发现、相机实测指标、Raw 归一化和处理启动；相机声明值与验证值分别保存。
清单中的 `format_metadata` 始终是机器人声明，内置 Adapter 不会把其中的
`verified_*` 提示提升为平台验证事实；只有读取不可变 Raw 的受信处理任务可以回写这些字段。
统计总采集时长在已有相机实测结果时取同一 Raw 中最长的验证时长（多相机并行不累加），
尚未完成探测时才回退到清单的采集起止区间。既有 `collection_job_id` 还必须同时匹配任务、
认证机器人和任务权威 Region；身份、凭据、任务/Dataset 由复合外键约束，Job 权威关系由
数据库触发器守卫且在产生 Robot Upload 后不可改写。

管理员可在 P03 展开一条已提交记录，或调用
`GET /api/v1/projects/{project_id}/robot-ingest/uploads/{upload_id}/episodes` 查看每个
Episode 的处理状态、帧/样本数、Dataset/Lance 版本和 QC 报告。身份统计中的 QC 合格率
只以已评估 Episode 为分母，PASS、RISK、REJECT 分别计数，未评估项不会被当作合格项。

当前自动化测试用内存对象存储验证恢复算法，并用真实 PostgreSQL 验证身份、任务解析、
幂等上传和 Raw 提交。真实 OSS/S3 的端到端验证仍需在部署环境用实际存储适配器执行，
不能由内存适配器结果替代。
