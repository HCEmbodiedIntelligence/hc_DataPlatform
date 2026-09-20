# 单机完整平台部署与迁移

平台保留：原始上传与存储、解析索引、自动质检、时间对齐、可视化、切片、标注、
问题处理、审核、版本发布、按需导出，以及账号权限、模型、标定、审计和备份。

## 数据与媒体

- 原文件存入 MinIO，业务关系、任务、质检与标注记录存入 PostgreSQL；不把视频放进数据库。
- G1 LeRobot v3 默认进行自动处理；可显式选择仅存档，稍后从原始记录启动处理。
- 后台质检逐帧解码原视频，内存只保留当前帧和小型统计；不保存 JPEG 帧目录。
- 对齐保存数值结果及原视频帧引用。播放授权包含原视频片段偏移，多路视频共用时间轴。
- 原视频引用产物只拥有自己的 JSON 索引；失败回滚、孤儿清理和版本退休都不删除原始视频。
- 带平台清单的 MCAP 沿用完整处理流程；图像消息需要生成可播放的媒体时，保留该必要功能。
- MCAP/ROS bag 无清单上传及通用 LeRobot 支持原样存档；任意机器人或自定义消息的处理、可视化
  仍需匹配适配器。这不是通用 ROSbag 解析器的实现声明。
- 历史 H.264、Arrow、Lance 和已完成标注仍可读取。原视频播放需要终端支持其编码。

## 六个常驻服务

| 服务 | 职责 | CPU 上限 | 内存上限 |
| --- | --- | ---: | ---: |
| web | 静态网页、API 与对象访问代理 | 0.5 | 128 MiB |
| api | 业务接口与鉴权 | 2 | 2 GiB |
| postgres | 业务及 Temporal 数据库 | 1 | 768 MiB |
| minio | 原始文件、数值结果及派生文件 | 1 | 1 GiB |
| temporal | 持久任务调度、进度及重试 | 1 | 1 GiB |
| worker | 同进程监听主处理与媒体队列 | 2 | 3 GiB |

API 和 Worker 使用同一份后端镜像。合并 Worker 保留两个队列，各允许一个并行 activity；
FFmpeg 单任务一个线程，媒体还有数据库全局并发门限。上限不是预分配或空闲占用。
Temporal 每服务数据库连接池上限为业务库 4、可见性库 2，空闲连接上限为 2/1，避免耗尽共享 PostgreSQL 的 80 个连接。
API 并发上限 48，日志每服务最多 3 × 10 MiB。LeRobot 本地源缓存最多 5 GiB，
闲置 24 小时清理，活跃目录通过文件锁保护。缓存和中间产物有独立生命周期。

数据库迁移、建桶初始化运行完成后退出。Temporal UI 不常驻，开发服务器与测试容器不参与部署。

## 启动

```bash
cp .env.single-server.example .env.single-server
# 设置凭据、HC_PUBLIC_ORIGIN、监听地址、原有卷名称
# 已有 .env.single-server 时保留原有密钥，不执行上面的覆盖操作
docker compose --env-file .env.single-server -f compose.single-server.yaml up -d --build
```

默认入口 `http://127.0.0.1:8088`。外部访问需要把 `HC_PUBLIC_ORIGIN` 改成实际入口，
保持对象代理的 Host、桶路径及签名查询参数。公开部署沿用项目已有 HTTPS 和生产身份配置。

当前部署复用 `hc-data-platform-dev_postgres-data` 和 `hc-data-platform-dev_minio-data`。
worker-cache 仅保存可重建文件。不要执行 `down -v` 或 `docker volume prune`。

## 迁移

1. 暂停接收写入，让 Worker 排空任务后停止；保留数据库与对象存储的一致时间点。
2. 备份 PostgreSQL 的 `hc_data`、`temporal`、`temporal_visibility` 三个数据库，以及数据库角色。
   迁移数据库容器也可以在完整停机后复制整个 PostgreSQL 数据卷，保持 PostgreSQL 主版本一致。
3. 停止 MinIO 后复制完整数据卷，或使用现有对象备份工具；不要边写入边直接复制文件系统。
4. 保存 `.env.single-server`、已有签名/加密密钥、源码或镜像版本。密钥和备份不提交 Git。
5. 在目标服务器恢复卷，设置 `HC_POSTGRES_VOLUME`、`HC_MINIO_VOLUME` 和
   `HC_VOLUMES_EXTERNAL=true`；更新入口地址，再启动 Compose。
6. 验证登录、原文件哈希、视频 Range、主/媒体队列轮询，以及一次质检、对齐、可视化、标注保存。

不迁移构建缓存、node_modules、旧容器可写层和可重建源缓存。历史派生数据有版本引用，
不能当作缓存批量删除。仅有 `hc_data` 的旧备份不足以迁移正在执行的 Temporal 工作流。

### 后台任务的项目范围

本地部署启用 `HC_LOCAL_SCOPE_DISCOVERY=true`，让新项目上传后的事件也能被处理。`HC_OUTBOX_SCOPES` 与 `HC_STORAGE_INVENTORY_SCOPES` 接受 JSON 数组，例如 `["组织ID/项目ID/区域代码"]`；当前服务器已按已有数据集配置这两个范围，供任务分发、维护和容量统计使用。迁移到生产环境时设置 `HC_ENVIRONMENT=production`、`HC_LOCAL_SCOPE_DISCOVERY=false`，并显式填写实际项目范围；新增项目也要更新范围并重建 worker。
