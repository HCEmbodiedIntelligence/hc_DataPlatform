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

`0002`/`0003` 是同一历史 HLS 运行时的发布租约和全局容量扩展，继续保持不可变。
`0004_canonical_aligned_media.sql` 是前向收敛迁移：它新建独立的 `aligned_media` schema，
其中 MP4 artifact 在所有相机完成并且 Lance Dataset 版本提交后才带
`dataset_committed_at`。旧 `preview` 表只保留迁移历史，当前运行时代码不再依赖。

`0005_commit_fence_and_media_retirement.sql` 区分 READY 后的发布保护期与真正执行中的
Lance commit，并为已提交 Dataset 版本增加 `retired_at → exact receipt delete → deleted_at`
两阶段回收。对象清单作为审计收据保留，播放器在 `retired_at` 写入后立即停止授权。
