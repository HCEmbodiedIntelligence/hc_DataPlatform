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

## 故障处理

- `MULTIPART_PARTS_MISMATCH`：重新列出分片，并使用权威 ETag 列表完成上传。
- 签名 URL 已过期：调用 `:renew`，不要创建第二个会话。
- `OBJECT_SIZE_MISMATCH`、`CRC64_MISMATCH` 或 `SHA256_MISMATCH`：会话会被标记为失败，
  且不会写入清单标记。
- `ROLLOUT_CONTENT_CONFLICT`：该 rollout ID 已指向不同字节；应分配正确的 rollout 标识，
  不要覆盖原始数据键。
- 客户端中断时可调用 `:pause`，随后调用 `:resume` 和 `GET .../parts`。取消操作会中止
  分片上传，并且具有幂等性。

签名 URL 和 Bearer 令牌绝不能写入日志。运行日志只应包含请求 ID、项目 ID、区域、会话 ID、
rollout ID、状态和稳定错误码。
