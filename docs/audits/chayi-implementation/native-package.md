# G1：原生 RGB-D 上传的新包提交阶段

2026-09-29；执行工作树 `hc_DataPlatform-chayi-work`，分支 `feat/chayi-g1`。
这是 W2 的部分软件证据，17 项 P0 仍为 Pending，G1 尚未完成。只使用隔离
`hc-chayi-g1` 服务；未修改 8088 业务数据、旧存储或机器人运动配置，未执行 G2/G3。

## 实现与约束

- 新建原生 LeRobot 导入任务显式选择 `lerobot_v3`。历史 Temporal 输入缺省保持
  原 Lance 分支；旧父子工作流的命令序列保留，并执行历史重放验证。
- 实际上传、原始清单提交、QC、Arrow 对齐之后冻结 native Episode 处理计划。
  计划包括原始对象哈希/大小/定位、完整作用域、逻辑 Schema、对齐结果、视频和
  深度证据。提交前从已提交 Raw 清单重新授权、校验资产，不依赖尚未存在的 READY
  数据版本，也不通过伪造 Lance 版本解除授权循环。
- 完整视频、频率、时间范围及 HC 深度契约一致时，RGB H.265 与深度 HEVC 视频
  逐字节复用。深度 profile、掩码、来源帧和误差报告一起校验、交付，不重新量化。
  缺失或不一致的证据使处理失败；原始文件保留且仍可授权下载。
- 提交使用标准包服务和产品投影，不调用 Lance 写入或预留 Lance 版本。恢复先校验
  已提交包，再恢复后续投影；丢失成功响应后复用已提交结果和已生成预览。
- 原生数值保留源 float32/float64 和额外矩阵特征。可逆特征名映射避免特殊字符冲突；
  深度解码后的数值帧不套用 RGB 黑帧/亮度规则。深度有效性来自 HC 证据。
- 新 READY 绑定完整 `DataVersionRef`，Lance 字段为空。迁移 0006/0007 约束精确的
  COMMITTED 收据、集合、帧数、原始来源哈希与录制关联；不能清除已有新包绑定。
  不把 SQL NULL 的 QC 状态当作有效通过条件。
- 完整下载包含 `hc/manifest.json` 和 `hc/commit.json`。离线校验允许这两个自排除
  收据，但校验其内容、清单哈希和数据身份，拒绝只存在一个或内容被篡改的情况。
- 标注任务绑定同一新数据引用，Tag 保存、提交和审核继续可用。
  深度预览调用已固定版本的 RealSense 官方 `colorizer`，主 H.265 文件不变；
  [显示方案及官方来源](realsense-preview.md)。不自行实现配色。

## 已运行验证

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| 原生导入、包服务、标注、workflow 回归 | 154 passed，0 skipped/xfail | [XML](native-package-regression.xml) |
| 成功、缺掩码、profile 错误、帧映射错误 | 4 passed | [XML](native-package-tests.xml) |
| 失败后逐个授权下载原始资产的追加检查 | 3 passed，1 deselected | [XML](native-package-negative-tests.xml) |
| 原有 Ingest 失败重试、重复启动及历史重放 | 1 passed | [XML](native-legacy-ingest-replay.xml) |
| 离线官方 LeRobot 0.6.1 / DataLoader | 6 帧 RGB-D，全包输入，禁网，无平台 import | [JSON](native-package-offline-reader.json) |

154 项回归运行早于负向参数扩展，不能相加解释为独立的 162 个测试。
保留既有 Starlette/httpx 弃用提示，没有禁用失败/跳过检查。

原生服务测试使用真实 PostgreSQL、MinIO 和隔离 Temporal；执行生产父/子 workflow
及活动，注入一次产品投影成功后的响应丢失。第二次提交恢复，只有两次预览编码
（一条 RGB、一条深度），父子历史可重放。直接拦截 Lance stage/commit 和目录
commit_fragment，同时核对 Lance lineage 为零。完整下载中的视频、Parquet 数值、
额外矩阵和 HC 文件经过核对；篡改提交收据被拒绝。结果见
[成功证据](native-package-roundtrip.json) 和 [历史哈希](native-package-history-hashes.json)。
完整 Temporal 历史保存在执行工作树 `backend/.g1-artifacts/native-package-*-history.json`，
可通过同一测试重新产生。

负向处理没有发布包或标注任务。失败后分别下载 11/12/12 个原始资产，均与上传前
字节相同：[缺掩码](native-package-missing-mask.json)、
[profile 不一致](native-package-profile-mismatch.json)、
[帧映射错误](native-package-frame-mismatch.json)。

离线 reader 使用成功测试实际下载的包，源 scale 为明确的 0.00025 m/unit。
官方读取器输出深度单位为 mm；先检查官方未自动应用 HC 掩码，再独立将无效像素
设为 NaN。各帧最大误差为 1/0.5/0/0.5/0/0.5 mm，包含官方整数毫米舍入。
数值 Parquet 保留 float64；固定版本官方 reader 的 HF transform 会构造 float32
Tensor，实测最大绝对转换误差记录在 JSON 中。不能将 Parquet 精度与 Tensor
输出精度混为一谈。`model_depth_training_support=NOT_VALIDATED`。

Ruff、`git diff --check`、正式/运行时 OpenAPI 及生成客户端检查通过；公开契约
未变化，运行时 SHA-256 为
`b167d926e12d30ba7534d356dfea5883c5da5696620ede52bc97a01a06e0a3f7`。
仅隔离数据库应用新迁移：140/140 current，missing/unknown/checksum_drift 全空。

## 可复现命令

在执行工作树中运行。故障测试需暂时停止隔离 worker，以免生产式 outbox 轮询与
测试专用队列竞争；结束后恢复隔离服务。不得将以下 helper 替换为现网 Compose。

```bash
bash deploy/chayi/compose.sh stop worker
bash deploy/chayi/compose.sh run --rm --no-deps -e OPENARM_REAL_INFRA=1 -e HC_TEST_POSTGRES_DSN=postgresql://hc:hc@postgres:5432/hc_data test tests/lerobot_imports/test_native_package_services.py --fail-on-skipped --fail-on-xfailed -q --junitxml=.g1-artifacts/native-package-tests.xml
bash deploy/chayi/compose.sh run --rm --no-deps reader /app/tools/read_native_rgbd_package.py
bash deploy/chayi/compose.sh up -d --no-deps --wait --wait-timeout 30 api worker
```

扩大回归的附加测试路径为 `test_generic_openarm.py`、`test_lerobot_adapter.py`、
`test_lerobot_web_upload.py`、`test_committed_source.py`、`test_pipeline_cache.py`、
`test_native_viewer_projection.py`（均位于 `tests/lerobot_imports`），以及
`tests/episode_store tests/workflow/test_workflow.py tests/annotation`。
同时设置 `HC_ANNOTATION_TEST_POSTGRES_DSN=postgresql://hc:hc@postgres:5432/hc_data`。
旧工作流重放测试为
`tests/workflow/test_temporal_workflows.py::test_temporal_ingest_gates_retries_duplicate_start_and_replays`；
本机使用只读挂载的 Temporal test-server 1.39.0 和 `temporal_local` 插件指定已有
二进制，未替换 workflow 行为。

## 未验收范围与后续条件

- 输入是明确标记的 6 帧、16×16、25 Hz 合成 RGB-D，API 测试主体为注入的授权
  principal。这不是实际传感器、完整网关登录/机器人 uploader 或真人角色验收。
- 当前 native 新分支仅接受完整视频与单 Episode 根级 HC 证据。共享 MP4、非零
  窗口、重采样及多 Episode 的深度证据需要后续按窗口处理和来源重映射；当前明确
  失败并保留原件，不能计为原生入口全部完成。长录制内存与性能仍待验证。
- MCAP 和连续录制的上层分派、R1–R4 机器人接入、真实 RGB-D 源单位/标定证据，
  subtask、独立审核、统一详情/下载和批量交付、1440/1024 浏览器与迁移回退演练
  继续推进。17 项 P0 不因本阶段测试通过而改为完成。

## G2 部署/回退条件（未执行）

0006/0007 是 additive expand 迁移，必须一起交付。先部署理解 NativeEpisodePlan、
新 DataVersionRef、自动标注绑定及官方 SDK 的兼容 worker，再允许新任务分派。
仍在处理的旧 Temporal 输入保留旧分支；不能给历史任务补写虚假的 Lance 值。
回退时停止创建新格式任务，保留能够完成或恢复已提交包的兼容 worker 和读取服务。
不删除新表/列、原始录制、标准包、旧 Lance 或 outbox，不清空 READY 绑定。
已提交包不能被旧版“未提交源”删除/重跑判断覆盖。完整 G2 指南仍需合并 W7 演练
和机器人 R5 安装/回退组合后交付，本阶段没有切换现网。
