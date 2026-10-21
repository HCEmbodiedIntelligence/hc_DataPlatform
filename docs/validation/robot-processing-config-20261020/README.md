# 机器人实例采集与解析配置：验证证据

实施日期：2026-10-20。功能、18 项验收、迁移清单与回退步骤见[实施记录](../../robot-instance-processing-config-implementation.md)。本目录是交付时的证据副本；完整原始记录、依赖快照和任务增量在 `/home/hc_op/workspace/reports/robot-processing-config-20261020`。所有写入验证均使用隔离数据。

## 结果与证据口径

| 验证 | 最终结果 | 证据 |
| --- | --- | --- |
| 后端及 PostgreSQL / MinIO / Temporal 集成 | 266 通过 | [汇总](verification-summary.json)、[逐例结果](backend-reconciled-final.xml) |
| 前端功能 | 178 通过 | [逐例结果](frontend-reconciled-final.xml) |
| 机器人端及原上传客户端 Python | 114 通过、3 跳过 | [逐例结果](robot-reconciled-final.xml) |
| 前端类型 / 架构 / API 生成 / 生产构建 | 通过；架构 54 项、生成 15 项 | `frontend-typecheck.txt`、`frontend-architecture.txt`、`frontend-api.txt`、`frontend-build.txt` |
| 机器人页面 Node 集成 | 1 通过；新默认 v2 与录制中 v1 正确显示 | `robot-ui-final.txt` |
| 本轮 Python 文件 Ruff | 通过 | `ruff.txt` |
| 全局安全门禁原工作区基线 | 3 通过、3 既有失败 | [原始结果](security-baseline.xml)；新配置接口权限专项单独通过 |
| Firefox 实际页面操作 | stage1–7 通过 | `browser-stage1.json` 至 `browser-stage7.json`、下列截图 |

`*-reconciled-final.xml` 按测试路径、名称及参数重复序号，保留每项最后一次执行结果，不删除初跑失败。`verification-summary.json` 列出组成汇总的全部原始 XML；它们也保存在本目录。汇总脚本为 `scripts/summarize-verification.py`。不能把 266 项理解为一次无中断全仓库测试，也不能把硬件跳过或未运行项目计为通过。

最初缺失 G0 清单、Temporal namespace、容器临时目录执行权限、worker 引导账号等环境问题已经在隔离环境补齐并补验。真实 ROS/相机/运动硬件、生产 Temporal 部署、外部硬件交接包和独立离线安装隔离测试尚未验证。机器人 `test_isolation.py` / `test_web_hook.py` 不在上述通过范围内。

## 浏览器操作与截图

使用真实 Firefox + geckodriver，连接隔离前端 `http://127.0.0.1:5180`，API `http://127.0.0.1:8006`。执行保存、验证、发布、选择版本重试、切片提交、错误跳转及返回；没有使用模拟 API 替换业务响应。截图为浏览器实际画面。

| 路径 / 操作 | 画面 |
| --- | --- |
| 机器人 → 实例详情 → 采集与解析配置；保存、样本验证、发布 v2 | [配置入口](01-instance-config.png)、[发布版本](03-joint-config-published.png) |
| 上传 → 失败 MCAP → 查看具体缺图像错误与原始通道 | [缺图像错误](02-missing-rgb.png)、[原始通道](05-original-channels.png) |
| 同批次 → 选择配置版本 → 核对差异 → 重试；旧错误继续可查 | [重试差异](06-retry-version-diff.png)、[历史记录](07-retry-preserves-history.png) |
| 机器人接入 → 已有设备详情 → 同一配置 | [最终合并后的接入详情](12-onboarding-shared-config.png) |
| 机器人 → 新建实例 → 型号/软件模板 → 离线草稿 | [模板草稿](13-new-instance-template-draft.png) |
| 切换项目 → 同一机器人实例 | [跨项目配置](14-cross-project-config.png) |
| 数据集 → 采集记录 → 失败详情 → 前往机器人配置 → 返回 | [数据集返回原包](18-dataset-return.png) |
| 采集任务 → 采集记录 → 失败详情 → 配置 → 返回 | [任务返回原包](18-task-return.png) |
| 上传记录 → 失败详情 → 配置 → 返回 | [上传返回原包](18-upload-return.png) |
| 首页 → 问题详情 → 配置 → 返回 | [首页返回原包](18-dashboard-return.png) |
| 纯关节录制 → 工作台 → 曲线与时间区间 → 提交切片 | [工作台](09-joint-only-workbench.png)、[时间切片](10-joint-time-slice.png)、[提交完成](11-joint-slice-finalized.png) |
| 纯关节切片 → 质检证据 → 已入库片段 | [图像检查不适用](19-joint-qc-not-applicable.png)、[最终关节片段](20-final-joint-workbench.png) |
| 采集任务 → 数据要求 → 必须有 RGB → 保存并刷新 | [任务视觉要求](21-task-visual-requirement.png) |

四个返回入口的最终断言见 [stage5](browser-stage5.json)；接入最终依赖合并后的断言见 [stage7](browser-stage7.json)。较早的 `17-dashboard-entry.png` 是定位问题时的画面，以 `18-dashboard-return.png` 为最终证据。

浏览器提交的同一纯关节切片为 0–0.300 秒、9 帧。真实处理服务产生 QC PASS（0 视频、36 传感器消息）、入库 Episode 和 LeRobot / MCAP / original 三种 READY 导出，见 [browser-pipeline.json](browser-pipeline.json)。后端参数化集成还验证了带/不带标注的六种导出组合、RGB 和 RGB-D 入口。

## 可复现环境与命令

保留的隔离网络名 `hc-robot-config-test`。`hc-robot-config-{pg,minio,api,front}` 提供预览；`hc-robot-config-{temporal,worker,worker-api,gateway}` 使用专用库 `hc_config_worker` 验证真实后台任务。PostgreSQL 为 tmpfs，删除容器会丢失测试库。交付前合并另一终端把平台面板移动到“上传管理”的布局与文案，保留配置摘要和固定版本显示；仅在隔离 worktree 合并并用机器人 Node 集成补验。所有公开测试端口只绑定 loopback，不连接业务库或业务 Temporal。

下面的核心回归命令使用已有隔离服务与本机预装镜像。密码只用于这一套一次性测试库。不要将测试 DSN 改为业务库。

```bash
config_repo=/home/hc_op/workspace/.worktrees/robot-processing-config-20261020/hc_DataPlatform
config_report=/home/hc_op/workspace/reports/robot-processing-config-20261020
docker run --rm --user 1000:1000 --network hc-robot-config-test \
  --tmpfs /tmp:rw,exec,size=1g,mode=1777 \
  -v "$config_repo/backend:/app:ro" -v "$config_report:/evidence" -w /app \
  -e PYTHONPATH=/app/src:/app \
  -e HC_POSTGRES_DSN=postgresql://hc:hc-test-only@postgres:5432/hc_config \
  -e HC_G1_TEST_POSTGRES_DSN=postgresql://hc:hc-test-only@postgres:5432/hc_config \
  -e ROBOT_CONFIG_TEST_DSN=postgresql://hc:hc-test-only@postgres:5432/hc_config \
  hc-data-platform-backend:dev python -m pytest -q \
  tests/robot_processing/test_configuration.py \
  tests/robot_processing/test_source_links.py \
  tests/continuous_recordings/test_mcap_native.py \
  tests/episode_store/test_native_mcap_pipeline_services.py \
  tests/episode_store/test_continuous_ready_services.py \
  --junitxml=/evidence/recheck-core.xml
```

其他回归范围的准确测试名见各原始 XML。补充环境条件：

- 原上传流程数据库测试还设置 `ROBOT_INGEST_POSTGRES_DSN`、`HC_TEST_POSTGRES_DSN` 为同一隔离 DSN；MinIO 使用隔离网络别名 `minio:9000`、桶 `hc-data-local`。
- 旧 LeRobot 工作流测试挂载 `/tmp/hc-config-g0-full:/g0:ro`。其固定二进制数据来自仓库 `backend/tests/fixtures/openarm-g0`，缺少的 manifest 使用 `scripts/build-g0-manifest.py` 从真实上传客户端生成。另一些 HTTP 示例是 synthetic，不冒称外部冻结交接资料。
- `test_processing_parallel.py` 的 Temporal 时间跳跃二进制需要 `/tmp` 带 `exec`；完整采集包工作流测试需要 `HC_TEMPORAL_TARGET=temporal:7233`、`HC_TEMPORAL_NAMESPACE=chayi-g1`，namespace 已在本轮隔离服务创建。
- `test_recording_registration_worker_services.py` 连接内部 `gateway:8080`，必须使用独立 `hc_config_worker` 库并运行配套 API/worker，不能与浏览器库共享后台扫描。引导管理员先建立，测试创建的普通用户才可真实验证撤销授权后的 403。
- 前端使用锁定依赖：`pnpm exec vitest run <测试文件>`、`pnpm typecheck`、`pnpm check:architecture`、`pnpm check:api`、`pnpm build`。只读挂载时为 `.vite`、`.vite-temp`、`.cache` 及构建输出提供可写临时目录。
- 机器人测试运行平台任务、预检及 `upload_client/tests` 中记录的十个测试模块；提供平台源码、jsonschema、aiohttp 和上述固定 G0 数据，硬件交接目录缺失的 3 项保持 skip。没有通过伪造硬件目录消除跳过项。

浏览器脚本在 `scripts/browser-stage*.mjs`，对应实际执行阶段与证据；它们使用 `browser-fixture.json` 中的测试对象。先运行后端 `python -m tests.robot_processing.browser_fixture` 建立新 fixture，再用隔离账号操作第一、二阶段；后端 `python -m tests.robot_processing.browser_pipeline prepare /evidence` 准备关节数据，第三阶段在浏览器创建切片，`python -m tests.robot_processing.browser_pipeline finish /evidence` 完成真实 QC / 入库 / 导出，后续阶段检查接入与往返路径。创建实例、发布和切片步骤会产生隔离数据，不能把全阶段脚本盲目重跑到已完成切片或业务库。

## 真实数据只读核对

[首次 dry-run](historical-dry-run.json) 与[最终 dry-run](historical-dry-run-final.json) 完全一致：1 个旧配置候选、0 个配置冲突、1 个已知同源关联候选。真实业务库尚未安装本轮配置表，也未执行 approval 清单。CLI 幂等、摘要不符拒绝、冲突候选保留和同源去重均在隔离库验证。

[X1 最终保护证据](x1-protected-final.json) 记录成功 Episode 仍为 READY / PASS / 114 帧，时间与数据版本未改。失败 MCAP 没有图像消息；保存配置不能恢复不存在的图像。发布步骤、待确认的单位/布局/模式、精确候选 ID 和回退办法见[实施记录](../../robot-instance-processing-config-implementation.md)。

## 合并交付包

完整实现保留在两个隔离 worktree；原工作区未被写入。报告目录的 `platform-task.patch` 与 `robot-task.patch` 仅包含相对 `dependency-delivery` 已保存依赖快照的本任务增量，并附文件 SHA256 和补丁核验结果。它们依赖当时已有的未提交接入、任务录制及凭据代码，不能直接套到裸 HEAD。

在独立发布 checkout 中先恢复/合并团队已确认的依赖，再 `git apply --check <任务补丁>`，人工审阅实际差异后应用。不要对正在热加载业务服务的原工作区自动应用整个 diff，也不要为消除冲突回退其他终端的改动。
