# 预览模块迁移

BE-10 没有持久化业务表。预览会话和媒体都是由注入的 `PreviewCachePort` 管理的 TTL 缓存
条目，因此本目录有意不包含任何 Alembic 迁移版本。
