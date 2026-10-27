# HC Data Platform

具身智能数据平台：上传 → 质检与对齐 → 可视化与标注 → 审核发布 → 数据集导出。

## 一键启动 / 停止

在本机终端用 `hc_op` 用户执行：

```bash
cd /home/hc_op/workspace/hc_DataPlatform
bash start.sh
```

启动成功后打开 **<https://158722.xyz>**（也可用 <https://www.158722.xyz>）。
本机入口：<http://127.0.0.1:8088>，使用已有平台账号登录。

停止平台和隧道，保留数据库与上传文件：

```bash
cd /home/hc_op/workspace/hc_DataPlatform
bash stop.sh
```

脚本自动处理 Docker、平台容器、外网前端、固定域名隧道及健康检查；需要系统权限时会提示输入 sudo 密码。
重复启动或停止均可；日常启动复用已有镜像和外网页面，缺少页面产物时自动构建。
域名检查失败会提示错误，并保留已启动的本机服务供排查；恢复访问后可再次执行 `bash start.sh`。

## 更新代码后

内网开发页面与后端源码支持热更新；域名访问使用打包前端。
更新依赖、Dockerfile 或需要同步最新外网页面时，执行：

```bash
bash start.sh --build
```

这会重建应用镜像、更新前端依赖并发布外网页面，保留已有数据和固定域名。

## 固定域名配置

本机 `.env` 已配置如下，日常启停无需修改：

```dotenv
HC_GATEWAY_BIND_ADDRESS=0.0.0.0
HC_GATEWAY_HOST_PORT=8088
HC_PUBLIC_ORIGIN=https://158722.xyz
HC_TUNNEL_HOSTNAME=158722.xyz
```

固定隧道使用系统服务 `cloudflared.service`；Cloudflare 两个域名须关联此隧道，源站为 `http://127.0.0.1:8088`。
源站使用上述回环地址，避免机器重启、内网 IP 改变后域名失效；不要填写旧内网 IP。
修改位置：Cloudflare 控制台 → Networking → Tunnels → 当前隧道 → Routes → 编辑两条域名的 Service URL。
两条均保存为 `http://127.0.0.1:8088`，详见 [Cloudflare 路由说明](https://developers.cloudflare.com/tunnel/get-started/)。
改 `.env` 后执行 `bash start.sh` 生效。脚本会停止旧临时隧道，避免它覆盖固定域名。
不要执行 `deploy/local/cloudflare.py start/stop`，它们用于临时域名；一键脚本使用已安装的固定隧道。

## 查看状态 / 排查

```bash
bash deploy/local/compose.sh current --profile cloudflare ps -a
bash deploy/local/compose.sh current --profile cloudflare logs --tail=100 api worker gateway frontend-public
sudo journalctl -u cloudflared.service -n 80 --no-pager
```

`migration`、`minio-init`、`runtime-cache-init` 是一次性任务，`Exited (0)` 属于正常状态。
启动会检查数据库版本；已完成的业务迁移不会重复执行。迁移日志只在启动失败时显示。
本机不通先看容器日志；本机可访问而域名不通，再查隧道、DNS 和源站地址。

## 数据与更多说明

数据库和任务索引保存在 PostgreSQL 命名卷；MinIO 文件在 `/home/hc_op/workspace/data-platform-storage`。
可读文件目录为 `/home/hc_op/workspace/data-platform-files`，需要挂载服务 `hc-platform-files.service`。
`bash deploy/local/data.sh usage` 查看容量。不要使用 `down -v` 删除数据卷，也不要直接改 MinIO 内部文件。

- [本机存储与文件挂载](deploy/local/README.md)
- [LeRobot 上传](docs/lerobot-platform-upload.md) · [数据合并迁移](docs/data-merge.md)
- [静态单机部署](docs/single-server-storage.md) · [发布部署](deploy/README.md) · [后端开发](backend/README.md)
