# 机器人身份直传 Raw

机器人采集端使用组织级平台身份，不绑定固定项目。每次上传只把
`collection_task_id` 作为归属入口；服务端解析任务后得到唯一的组织、项目、数据集、
Region 和处理配置。客户端传入的 `organization_id`、`project_id` 仅用于一致性检查，
不能改变任务归属。

## 管理身份与凭据

在“数据源 → 机器人接入 → 接入机器人”中选择或创建机器人，设置规则后生成上传凭据，
再由采集端验证连接。系统复用同组织已有实例、身份和有效凭据；重复提交不会自动更换
凭据或恢复已停用身份。一个组织内同一 `robot_id` 只有一个有效上传身份；身份管理收在
机器人接入详情中。管理员可以：

- 启用或停用身份；
- 限制 HTTPS 传输、允许的数据格式和批次/资产/分片策略；
- 签发、再次查看/复制、轮换和撤销凭据；删除已撤销或已过期的凭据；
- 查看最近认证、心跳、上传、失败尝试以及 Raw/Episode/QC 统计。

新凭据以 `hcri_...` 形式返回。服务端保留用于认证的 HMAC 摘要，同时加密保存完整凭据。
在机器人详情的凭据列表点击“查看凭据”，具有 `ingest_source.manage` 权限的管理员可
再次查看和复制；关闭弹窗或刷新后仍可查看。普通列表只返回前缀和状态，不返回明文；
专用查看接口返回 `Cache-Control: no-store`，前端不把明文放入本地/会话存储。
查看不会轮换凭据、启用已停用身份或刷新设备在线时间。

升级前只有摘要的旧凭据无法直接解密，仍可正常使用。设备下一次成功认证时，平台会
加密补存原凭据；之后可以查看。未补存前页面明确提示该限制，不会自动生成替代凭据。

已撤销、已过期的凭据可以确认删除，从列表移除并清除密文。数据库保留原 ID、摘要及
历史关联所需的最小记录，历史上传和签发请求回执不会丢失。重复删除安全，重试旧签发
请求也不会恢复已删除凭据。仍有效的凭据须先撤销；停用身份和撤销凭据是分别判断的。

本功能需要迁移 `ingest/023_robot_credential_vault.sql`。加密密钥由现有
`HC_DATA_SOURCE_CREDENTIAL_KEY` 经独立用途派生，不新增配置；升级和重启时保持原密钥，
不要替换。把凭据写入机器人的安全存储，例如：

```bash
export HC_ROBOT_INGEST_TOKEN='hcri_...'
```

## 设备通信与连接验证

采集端使用同一上传 Bearer 凭据调用 `POST /api/v1/robot-ingest/heartbeat`，请求体为
`{"activity":"IDLE"}`；采集或上传时可分别报告 `COLLECTING`、`UPLOADING`。
建议每 60 秒发送一次。身份、Robot ID 和组织只从有效凭据解析，不由请求体指定。
旧采集端不必更换凭据：上传协议中的有效认证仍会刷新最近通信时间。

平台按服务端观测时间判断连接：最近有效认证或心跳距今不足 300 秒为在线，达到
300 秒为离线；新设备没有通信是未验证，旧身份缺少可靠观测是未知。签发凭据和
凭据库检查均不能证明设备在线。设备报告活动后，在 900 秒活动窗口内发生的通信
超时会进入待处理故障，直到有效设备通信恢复；正常空闲离线不会因此成为故障。

“今日成功上传量”位于上传记录页及机器人详情，使用同一个
`GET /api/v1/projects/{project_id}/robot-ingest/successful-volume` 接口。
它按当前项目、`X-Region-Code` 与页面显示的 `Asia/Shanghai` 自然日，对成功 Raw
的原始字节量去重聚合；归属时间取成功提交时间，不取会话创建时间。可通过 `day`、
`tz`、`robot_id` 查询有权限的明细，查询失败不会伪装成 0。

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
