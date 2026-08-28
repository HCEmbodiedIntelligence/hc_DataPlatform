# 持久化预览模块

预览拆成两个明确边界：

- `PreviewControlPlaneService` 只在 API 进程中查询 PostgreSQL、创建短 TTL session、
  幂等创建 job，并返回 201 READY 或 202 PENDING。它没有 encoder 依赖。
- `PreviewGenerationService` 只由独立 media Temporal queue 调用，按批从 Lance 读取、把 JPEG
  迭代流送入单个 FFmpeg `image2pipe` 进程，再将 HLS 成员上传至 MinIO/S3。playlist 最后
  上传且全部对象校验后，先持久化精确 publication receipt，再把 `preview.artifacts` 标记为
  READY。Temporal 取消会传入线程安全信号，终止 FFmpeg 并清理该任务的 staging。

客户端只能提交 allowlist `profile_id`；宽高、码率、codec、preset 和 FFmpeg threads 均由服务端
配置。ORIGINAL `artifact_key` 不包含 annotation revision，EDITED/COMPARE 默认复用 ORIGINAL
媒体并由时间线/前端 overlay 表达排除区间。

产物使用 `derived/previews/{project_id}/{artifact_key}/`，数据库保存每个对象的精确 checksum
清单。GC 仅清理无活动引用、无 legal/governance/retention hold 的
`REBUILDABLE_DERIVATIVE`，绝不扫描或删除 RAW、MANIFEST、PUBLISHED_MANIFEST。
失败 publication 也保留精确清单并立即进入 orphan GC；`DELETING` 不能被并发授权请求复活。
维护循环逐 scope 执行 TTL/项目配额，再跨配置的 RLS scope 汇总全局高低水位并选择全局 LRU。
