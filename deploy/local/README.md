# 本机平台启动入口

平台只保留 `/home/hc_op/workspace/hc_DataPlatform` 一个代码目录，使用当前 `main`
工作目录和 `compose.dev.yaml`。机器人只保留 `/home/hc_op/workspace/teleop_ws`。

2026-10-09 按要求删除了旧平台容器与数据卷，使用当前源码重建镜像并初始化空白平台。
Lance 相关代码暂时保留；新上传处理沿用 Episode 文件包，旧 Lance 数据不会导入新环境。

```bash
cd /home/hc_op/workspace/hc_DataPlatform
bash deploy/local/compose.sh current up --build -d
bash deploy/local/compose.sh current ps
bash deploy/local/compose.sh current logs --tail=100 api worker
bash deploy/local/compose.sh current stop
```

入口为 <http://127.0.0.1:8088>，配置在根目录 `.env`。
局域网机器人接入时，将 `.env` 中的 `HC_PUBLIC_ORIGIN` 设置为机器人可达的平台
IP 或域名（例如 `http://192.168.1.10:8088`），并设置 `HC_GATEWAY_BIND_ADDRESS=0.0.0.0`。
该地址同时用于机器人连接链接和对象存储上传/下载地址；即使操作员从
`127.0.0.1` 打开平台，复制出的机器人连接链接也使用这个对外地址。
修改后执行 `bash deploy/local/compose.sh current up -d --no-deps api worker frontend gateway`
使环境配置生效。没有配置对外地址时，连接链接使用浏览器 API 地址，但拒绝生成
指向 `localhost`、`127.*`、`::1` 或监听通配地址的链接。独立前端或 Helm 部署通过
`VITE_ROBOT_PLATFORM_BASE_URL` / `frontend.runtimeConfig.robotPlatformBaseUrl` 配置相同地址。
首次打开页面并注册账号：第一个成功注册的账号自动获得平台管理员权限，
后续注册账号为普通账号。新环境不预置管理员用户名或密码。
前后端镜像由当前源码构建；本机数据库、对象存储和 Temporal 使用已安装的基础镜像。
本机覆盖配置在 `current.compose.yaml`，统一入口会自动加载。
`original` 是 `current` 的命令别名，均使用 `hc-data-platform-dev` Compose project；
不再启动独立 OpenArm、Chayi 或双终端测试平台。

原始上传文件及处理后的 Parquet、视频、JSON 由本机 MinIO 管理；MinIO 内部文件保存在
`/home/hc_op/workspace/data-platform-storage`（`.env` 的 `HC_MINIO_DATA_DIRECTORY`）。
账号、项目、索引、任务、模型资源和版本关系存入 PostgreSQL。
`stop` 保留数据；`down -v` 会删除 PostgreSQL 等命名卷，但不会清空上述宿主机绑定目录。

## Cloudflare 临时外网访问

本机已安装官方 `cloudflared`，位于工作区 `.data-platform-tools/cloudflared`。
平台运行后，可使用以下命令管理临时 HTTPS 地址，无需 Cloudflare 账号或域名：

```bash
cd /home/hc_op/workspace/hc_DataPlatform
python3 deploy/local/cloudflare.py start
python3 deploy/local/cloudflare.py status
python3 deploy/local/cloudflare.py logs
python3 deploy/local/cloudflare.py build
python3 deploy/local/cloudflare.py stop
```

隧道由当前用户的 `hc-platform-cloudflare` systemd 服务在后台运行，进程失败会自动重试。
这是本次开机的临时服务，机器重启后需要重新执行 `start`。`status` 显示当前访问地址和连接状态。
首次连接或隧道进程重启后，脚本自动将 `.env` 的 `HC_PUBLIC_ORIGIN` 和
`HC_TUNNEL_HOSTNAME` 更新为新地址，并重建 API、Worker 和前端容器使配置生效；
这几项服务会短暂重启。视频、下载及机器人连接链接随后使用临时 HTTPS 地址。
`stop` 关闭隧道并恢复之前的内网地址；期间手动修改的地址配置会保留。
运行状态和日志在工作区 `.data-platform-tools/cloudflare/`，不进入 Git。

外网域名使用 `frontend-public` 提供的打包前端，启用压缩、按页面加载和带哈希资源的长期缓存。
内网 IP 入口继续使用 Vite 热更新。首次启动隧道时若没有构建产物，会复用正在运行的
前端容器及其依赖完成 TypeScript 检查和生产构建，无需重新安装依赖。
修改前端代码后，执行 `python3 deploy/local/cloudflare.py build` 更新外网页面；
此命令只更新外网前端和网关，不重启隧道，当前访问地址继续有效。
构建失败时保留之前的页面，发布时先写入资源再切换首页，并保留旧哈希资源供已打开的标签页使用。
`stop` 同时停止外网静态前端服务。构建产物位于工作区 `.data-platform-tools/cloudflare/frontend/`。

外部访问使用平台原有账号登录。临时域名每次新建隧道都会变化，需要重新分享链接；
旧的下载链接或机器人接入链接也需要重新生成。
[Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
不保证可用性、不支持 SSE，且限制为 200 个同时在途请求；平台任务状态已有轮询回退。
需要长期固定地址时，改为 Cloudflare 账号下的命名隧道和自有域名。

## 直接查看文件及容量

用系统文件管理器打开 `/home/hc_op/workspace/data-platform-files`。
目录按“项目名 → 任务名和任务号 → 录制时间与机器人”展示，可直接双击文件、复制粘贴
单个文件或整个录制文件夹。复制出来的是普通文件，可脱离平台使用；源目录只读。

```text
openarmx（项目01）/
  pick and place（任务00000014）/
    2026-10-20_14-19-08_X1-001（录制8位编号）/
      录制说明.txt
      原始文件/
      处理后的文件/
      切片/片段001_00分00.000秒至00分09.300秒/版本001/
```

日期和时间使用北京时间，缺少采集时间的批次明确标注“导入于”。短编号稳定区分同秒录制；
名称中的路径分隔符等字符会替换为下划线。原始文件及 LeRobot 包内的文件名、相对路径保持
原样，切片范围来自该版本冻结的来源记录，不随后续切片编辑改变。

这是同一份 MinIO 数据的只读视图，**不额外复制整份数据**。索引每 10 秒自动刷新，目录缓存
为 5 秒；通常 15 秒内可见已提交的新文件。未完成上传或未提交的处理包不会展示成完整文件。
少量历史记录若缺少清单，会在 `目录状态说明.txt` 列明；数据库暂不可达时保留上次完整索引并
显示 `目录刷新状态.txt`。不启用 OSS，不把 MinIO 当成可删除的中转缓存。

```bash
# 全部文件数量、内容总大小；也可追加 raw/ 等实际前缀
bash deploy/local/data.sh usage
bash deploy/local/data.sh list
bash deploy/local/data.sh status

# 平台已登记的对象、状态及允许的操作（默认选择唯一项目）
bash deploy/local/data.sh objects
# 多项目时追加 --organization 组织ID --project 项目ID
bash deploy/local/data.sh trash 对象ID --reason '清理不再使用的派生数据'
bash deploy/local/data.sh restore 对象ID --reason '恢复误回收的派生数据'
```

本机管理命令通过 API 容器和临时管理员会话执行，与网页使用相同的权限、引用检查及审计；
结束后撤销该会话。多个管理员时用 `--username` 明确指定。不会重置密码或中断现有会话。
原始文件、清单、仍有引用或处于保留期的对象不会被强制删除。回收站仍占磁盘空间，
最终清理由平台生命周期规则控制；不能把回收等同于立即释放空间。
`usage` 是完整对象内容的合计，不含未完成分片、MinIO 内部元数据及 PostgreSQL。

浏览目录仅支持查看/复制。不要直接修改 `data-platform-storage` 中的 `xl.meta`、分片等内部文件。
目录读取需要 MinIO 和挂载服务运行；临时离线副本可通过以下命令导出到一个空目录：

```bash
bash deploy/local/storage.sh export-files /你的空导出目录
# 迁移 MinIO 底层存储目录：暂停写入、复制并校验后重启，不自动删除旧目录
bash deploy/local/storage.sh set /新的绝对目录
```

### 挂载服务

本机使用官方 rclone v1.75.1，二进制及只读凭据位于工作区 `.data-platform-tools/`，不提交 Git。
安装包 SHA-256：`982b5aa772841168f8e380f139e9e787b2a105403e32b94da8676a0e1c0a13ab`。
新机器从 [官方发行目录](https://downloads.rclone.org/v1.75.1/) 下载对应 Linux amd64 ZIP，
核对 `SHA256SUMS` 后将其中 `rclone` 安装到该位置。需要系统 FUSE3 和 `/dev/fuse`。

```bash
bash deploy/local/files.sh setup
bash deploy/local/files.sh install-service
systemctl --user status hc-platform-files.service
# 用户登录时自动启动；手工停止/恢复
systemctl --user stop hc-platform-files.service
systemctl --user start hc-platform-files.service
```

`HC_LOCAL_FILES_DIRECTORY` 可以指定一个独立的空目录。更改后先停止上述服务，重新执行
`setup` 和 `install-service`，并重建 API/Worker 容器以更新页面路径。挂载账号只有目标桶
的读取权限，没有覆盖或删除权限。不会启用整桶镜像、双向同步或额外的 OSS 同步。

服务内部将对象挂载在 `.data-platform-tools/objects`，通过仅监听回环地址、带随机凭据的
只读目录服务提供业务名称，再挂载到 `data-platform-files`。网页“存储位置”使用
`HC_OBJECT_STORE_RAW_BROWSER_PATH` 指向内部对象挂载；平台展示的浏览入口仍为可读目录。
`local_file_catalog` 在 API 容器内使用只读数据库事务及已提交清单构建索引，不写业务数据。
若更改 API 容器名，可在 `.env` 设置 `HC_LOCAL_FILES_API_CONTAINER`。
平台管理员统计命令 `usage/list` 仍按对象存储实际前缀统计，包含历史对象，口径与业务目录不同。

旧开发副本的未提交修改、独有文件及提交索引保存在
`/home/hc_op/workspace/diagnostics/workspace-consolidation-20261009/`。
这些资料没有导入新平台。

## 当前 OpenArmX 接入

组织/项目 `01 / 01` 中已发布当前部署的 OpenArmX 双臂模型，绑定 `openarmx_qingshan`，
并配置 **OpenArmX 日常采集**（任务号 `00000001`）。模型包括 URDF、网格和 16 轴映射；
URDF mimic 从动手指不要求额外录制通道。
`teleop_ws` 已配置独立后台上传服务，新录制停止后自动封存、上传原始文件。
收到整段录制后，在平台完成切片确认及后续处理；上传完成不等于训练数据已就绪。
具体目录、身份、服务命令和真机验证边界见
[机器人端接入说明](../../../teleop_ws/src/humanoid_manager/docs/platform-upload-8088.md)。
