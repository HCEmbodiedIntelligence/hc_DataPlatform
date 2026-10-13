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

## 直接查看文件及容量

用系统文件管理器打开 `/home/hc_op/workspace/data-platform-files`。
这个目录通过 rclone/FUSE 直接读取 MinIO，是同一份数据的只读视图，**不额外复制整份数据**。
新上传和处理产物通常在 5 秒目录缓存更新后可见，未完成上传的分片不会伪装成完整文件。
可以直接查看、播放和复制；没有启用 OSS，也没有把 MinIO 当成可删除的中转缓存。

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
