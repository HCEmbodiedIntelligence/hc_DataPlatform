# 持久化预览迁移

`preview/0001_durable_preview_artifacts.sql` 创建跨 API 副本、Temporal media-worker
和 Pod 重启共享的控制面状态：

- `preview.artifacts`：不可变 HLS 产物身份、Lance 血缘、对象清单、大小、TTL、LRU、
  引用计数和保留/冻结保护；只允许 `REBUILDABLE_DERIVATIVE`。
- `preview.jobs`：幂等生成任务。部分唯一索引保证同一租户作用域和 `artifact_key`
  同时最多一个 `QUEUED/RUNNING` 任务。
- `preview.sessions`：短 TTL 媒体授权会话，不保存签名 URL。

三个表均包含 `organization_id/project_id/region_code`，启用并强制 RLS。媒体成员存放在
MinIO/S3；`object_manifest` 保存每一个对象的 key、大小、SHA-256、ETag 和媒体类型，GC
只能按该清单精确删除，不能执行宽泛前缀删除。
