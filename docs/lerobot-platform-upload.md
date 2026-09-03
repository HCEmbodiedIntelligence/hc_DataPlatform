# LeRobot 本地服务器上传验收

> 状态：里程碑目标，尚未完成。

本指南属于“本地服务器验证”里程碑。目标是让网页和上传脚本只连接本地平台 API，不要求
操作者配置 OSS AccessKey、Bucket CORS 或浏览器签名直传。完整范围与问题清单见
[`plan/LOCAL-SERVER-VALIDATION-MILESTONE.md`](../plan/LOCAL-SERVER-VALIDATION-MILESTONE.md)。

## 目标链路

```text
本地 LeRobot（Parquet + MP4）
  -> 向本地平台创建原生 LeRobot Raw 上传会话
  -> 将文件正文发送到本地平台 API
  -> 平台在本地运行时中持久化文件并提交完成
  -> 本地 Worker 登记 Raw Source、Episode 和处理任务
  -> 通过本地页面与 API 验证质检、对齐、可视化和标注结果
```

本地验证模式下，客户端、API 和 Worker 都不得持有 OSS 凭据或访问 OSS endpoint。源文件、
上传会话、处理产物和预览读取全部使用本机存储。生产环境可以保留对象存储能力，但它不能被
本地模式调用，也不能成为本地验收的隐式前置条件。

## 当前限制

当前代码尚未达到目标状态：

- 网页客户端仍保留 `direct` 和 `proxy` 两种模式，并优先尝试签名 URL；
- `compose.dev.yaml` 仍固定使用 OSS provider；
- 还没有一条无云凭据的本地端到端测试覆盖上传、Worker 处理和结果读取。

在这些问题关闭前，只能验证单元级上传会话、分片恢复和队列行为，不能宣称本地服务器上传
已经验收通过。

## 计划中的运行方式

里程碑完成后，在仓库根目录启动完整本地栈，并运行：

```bash
backend/.venv/bin/python scripts/upload_lerobot_via_platform.py \
  --source-dir /absolute/path/to/lerobot-dataset \
  --api-base-url http://127.0.0.1:8000
```

脚本应只需要：

1. 本地 LeRobot 文件夹；
2. 本地平台地址；
3. Organization ID；
4. 平台登录凭据或 Bearer Token；
5. 项目、区域、数据集、采集任务和机器人绑定。

Token 通过环境变量传入，避免出现在命令行和进程列表：

```bash
export HC_DATA_ACCESS_TOKEN='平台 Bearer Token'
export HC_ORGANIZATION_ID='所属 Organization ID'
```

Windows 路径需要继续支持转换为 WSL 的 `/mnt/<盘符>/...`。原始 Parquet、MP4 和元数据必须
保持相对路径，暂停、恢复、刷新和重复提交必须复用同一个服务端上传会话。

## 验收证据

里程碑完成时至少保存以下证据：

- 完整本地栈的服务状态与 readiness；
- 浏览器、API 和 Worker 的网络记录，证明整个验收过程没有访问 OSS；
- 最小 LeRobot 样例的上传、暂停、恢复、提交和重复提交结果；
- Worker 完成处理后的 Raw Source、Episode 与任务记录；
- 页面能打开结果，并能读取用于预览的本地资源；
- 前后端全量 CI 门禁通过。
