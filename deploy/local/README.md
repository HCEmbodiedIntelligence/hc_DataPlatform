# 本机平台启动入口

平台源码统一使用 `git@github.com:HCEmbodiedIntelligence/hc_DataPlatform.git` 的
`main` 分支。本机工作目录为 `/home/hc_op/workspace/hc_DataPlatform`。
前后端均读取当前 Git 工作目录；修改完成后提交并推送到该仓库。

本机日常只运行原平台 **8088**，数据库和对象文件继续使用原 Docker 数据卷。
38088 集成测试环境已停止，测试数据不合并到 8088；原测试卷和迁移备份暂留。
测试环境入口仅用于以后按需恢复，不应在日常启动时同时运行：

| 入口 | Compose project | 默认页面端口 | 数据卷前缀 |
| --- | --- | --- | --- |
| `original` | `hc-data-platform-restore-test` | 8088 | `hc-data-platform-restore-test_` |
| `openarm` | `openarm-e` | 38088 | `openarm-e_` |

在仓库根目录执行：

```bash
bash deploy/local/compose.sh original up -d --no-build --pull never --wait
bash deploy/local/compose.sh original ps
bash deploy/local/compose.sh original logs --tail=100 api worker
bash deploy/local/compose.sh original stop

# 以下仅用于以后需要恢复 OpenArm 测试环境时；日常无需运行
bash deploy/local/compose.sh openarm up -d --no-build --pull never --wait
bash deploy/local/compose.sh openarm ps
bash deploy/local/compose.sh openarm stop
```

`original` 使用 `deploy/local/original.env`，迁移时保留原离线部署的
`deployment.env` 内容；该文件被 Git 忽略。示例只用于新环境，不应覆盖现有配置。
`openarm` 沿用 `deploy/openarm-e/E.env` 以及其受限 `secrets/` 目录。
两个入口都按自己的配置加载端口、公开地址和数据库连接，脚本自动记录当前提交号。

本机保留已安装的 `hc-offline/*`、`openarm-e-backend:integration` 和前端依赖镜像。
这些名称只是 Docker 镜像标签，运行不再依赖原离线包目录或其中的 `images.tar.gz`。
后端镜像包含当前源码所需的 PyAV 15.1.0。上述入口面向已迁移的本机环境；
在新机器上需先按仓库部署说明准备依赖镜像，再提供该机器的配置。

保留数据时使用 `stop`；`down -v` 会删除数据卷。
源码更新涉及数据库迁移时，先备份并验证迁移，再启动新版本。
迁移备份和本机验收记录保存在工作区 `diagnostics/platform-repository-migration-20260928/`，
其中私有配置备份和业务数据不得提交到 Git。
