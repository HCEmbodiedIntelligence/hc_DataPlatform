# 数据采集上传会话

该模块负责采集任务与 rollout 注册、可恢复分片上传的控制面操作、不可变原始数据键、
传输/内容完整性检查以及清单提交标记。它刻意不解析 MCAP 容器，也不启动质量检测或工作流处理。

## 运行时装配

`UploadSessionService` 必须传入 `ObjectStoragePort`，并接受一个
`IngestPersistencePort`。单元测试使用 `InMemoryObjectStorage` 和
`InMemoryIngestPersistence`。生产环境应注入：

- 使用 boto3 客户端的 `S3ObjectStorage`，用于本地 MinIO/S3；或
- 使用 `oss2.Bucket` 的 `OssObjectStorage`，用于阿里云 OSS；以及
- 基于 `migrations/ingest/001_ingest.sql` 实现的 PostgreSQL 仓库。

MinIO 客户端必须使用 SigV4（boto3 使用 `Config(signature_version="s3v4")`）。

分片 URL 默认在 15 分钟后过期，并且只返回给已认证的上传者。客户端将字节直接 PUT 到
对象存储，调用 `GET .../parts`，再把已排序的 `part_number`/`etag` 列表发送到
`:complete`。API 进程绝不接收原始文件正文。

存储桶属于不可变数据存储。请启用存储桶版本控制/对象锁，并拒绝 API 身份拥有常规的
`DeleteObject`/覆盖权限。适配器还会检查键是否已存在，清单写入则采用条件式创建。
原始数据键包含项目、UTC 日期、机器人、采集任务、rollout 序号/ID 和 SHA-256。

## 离线导入

CLI 会校验清单语法、文件大小、与 OSS 兼容的 CRC64 和 SHA-256，但不会解析 MCAP
头部/尾部。随后，它会使用与机器人上传端相同的创建、续期、列出分片、完成和提交清单 HTTP 端点：

```bash
hc-offline-import recording.mcap rollout_manifest.json \
  --api-base-url https://data-api.example \
  --region-code cn-hangzhou \
  --idempotency-key import-2026-08-14-001
```

请设置 `HC_DATA_ACCESS_TOKEN`，不要把 Bearer 令牌写入 Shell 历史记录。
使用 `--validate-only` 可以生成本地完整性报告。

`hc-offline-import` 是大体积离线包的推荐入口。它不会把原始字节经过 API 进程；会根据
文件大小自动增大分片以满足 S3/OSS 的最多 10,000 分片限制（包括 5 TiB 上限），并在每次
网络中断、超时、5xx 或签名过期后先调用 `GET .../parts` 对账。已被对象存储接收、但客户端
没收到响应的分片不会重传；确认未接收的分片会记录一次失败、重新取得短期授权后指数退避重试。
进程被关闭或网络长时间不可用时，使用**同一个源 MCAP、同一个 Manifest 和同一个
`--idempotency-key`**重新运行命令即可从服务端已确认分片继续：

```bash
hc-offline-import recording.mcap rollout_manifest.json \
  --api-base-url https://data-api.example \
  --region-code cn-hangzhou \
  --idempotency-key import-2026-08-14-001 \
  --max-part-retries 5
```

重试次数有界，避免不受控地消耗对象存储请求和服务端重试额度。达到上限后命令会明确失败；
网络恢复后可用上述相同参数再次运行，已经确认的分片仍会被跳过。

一个接近 5 TiB 的**单一** S3 对象受 10,000 分片上限约束，每片至少约 524 MiB。部署时必须
让该大小的分片能在签名 URL 有效期内完成；默认 15 分钟授权适合稳定的数 MiB/s 上行，但不适合
长期低于约 5 Mbit/s 的链路。此类采集场景应在采集/落盘阶段将 recording 切成多个独立、各自有
Manifest 的 MCAP 包（而不是事后把一个已完成的 MCAP 任意按字节切断），或者由运维在风险评审后
将 `HC_INGEST_PART_AUTHORIZATION_TTL_SECONDS` 配置到最长 3600 秒。断点续传不能绕过单对象分片数量和签名有效期这两个
对象存储协议限制。

不要为了“压缩上传”而对已完成的 `.mcap` 文件再套 gzip/zstd：这会改变 Manifest 的
文件大小、CRC64 和 SHA-256，也会把容器语义变成一个不可直接消费的压缩包。应在采集或
离线打包阶段使用 MCAP/图像/视频编码器的原生压缩，并在 Manifest 的 `compression` 字段
如实记录；本导入器只传输并校验这些确定的原始字节。

## 浏览器采集目录批量导入

网页“选择采集文件夹（支持多级目录）”会递归读取浏览器提供的相对路径。每一个
Manifest 所在目录都是独立数据包根目录；它只会匹配该 Manifest 声明、且仍位于同一目录树
中的 `RAW_MCAP`。因此不同机器人、日期或班次目录中同名的 `recording.mcap` 不会被交叉配对。

目录中的每个有效数据包会先独立执行服务端 Manifest 预检，再按顺序建立独立、稳定幂等键的
上传会话。某一个 Manifest 无效、文件缺失或单包上传失败只会进入该包的失败清单，不会阻止
后续包；浏览器断网时停止当前包，联网后先按服务端已确认分片续传当前包，再继续剩余目录。
调度器有意一次只处理一个数据包，不会将整个目录的内容读入内存或为所有文件同时建立分片连接。

浏览器出于安全限制不能跨刷新保存本地硬盘文件句柄。刷新后只需重新选择同一个根目录，未完成
会话会按 `data_package_id` 自动重新绑定到相应的原始文件并顺序续传；不要逐条手工重新选择
`.mcap`。重新选择时仍会校验文件大小，服务端在提交时继续验证 SHA-256、CRC64 和对象大小。

## 故障处理

- `MULTIPART_PARTS_MISMATCH`：重新列出分片，并使用权威 ETag 列表完成上传。
- 签名 URL 已过期：CLI 会调用 `:renew`；不要创建第二个会话。
- 网络中断、超时或对象存储 5xx：CLI 先列出服务端分片。若该分片已落库则直接继续；否则
  调用 `:retry-parts` 取得新授权并自动重试。网络长期中断后直接以相同 idempotency key 重跑。
- `OBJECT_SIZE_MISMATCH`、`CRC64_MISMATCH` 或 `SHA256_MISMATCH`：会话会被标记为失败，
  且不会写入清单标记。
- `ROLLOUT_CONTENT_CONFLICT`：该 rollout ID 已指向不同字节；应分配正确的 rollout 标识，
  不要覆盖原始数据键。
- 客户端中断时可调用 `:pause`，随后调用 `:resume` 和 `GET .../parts`。取消操作会中止
  分片上传，并且具有幂等性。

签名 URL 和 Bearer 令牌绝不能写入日志。运行日志只应包含请求 ID、项目 ID、区域、会话 ID、
rollout ID、状态和稳定错误码。
