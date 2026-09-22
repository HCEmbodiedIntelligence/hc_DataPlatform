# B / OpenArm LeRobot v1 交付

日期：2026-09-22。工作分支：`feat/openarm-lerobot-v1`。

实现、测试及往返证据交付 SHA：**`4709ff6c38892c9fda1b5375ca68f43a4f85cd21`**。
基线：`2f6022202728f7c18852c0afd0fbd8df4244f362`。
本记录在上述实现提交之后单独提交；不改变实现与测试产物。
未 push、未部署，未修改原离线平台或原数据库。所有平台执行均走 B Compose。

## 完成内容

- PROCESS 按原生 feature schema / names / episode metadata 解析，支持不同维数和相机数；OpenArm 使用明确的 `openarmx-v1` profile，保留原 robot_type。G1 原解析、QC profile 和对齐参数保持兼容。
- state/action 保留 float32 数值、轴名和单位；14 轴 rad、2 夹爪单指位移 m。相机 feature 到 canonical topic 显式映射。原生帧网格精确对齐，action 不线性插值，导出为数值向量。
- 实际上传 API → 原始存储 → Temporal → QC → alignment/media/Lance → 预览 API → 标注 → 独立审核人通过 → 发布 → 签名下载往返已通过。
- capture context、原始 task/outcome/source episode、源帧/时钟映射、模型与标定引用保留；平台审核标签独立保存。拒绝 qualified episode 与无效源区间重叠、未来/过期命令、跨 clock epoch 等异常。
- 伴随资产通过现有原始文件下载接口提供，导出元数据带 SHA/size inventory。逐文件签名下载校验 byte equality 与 SHA256，A 的 20 个文件全部通过，包括 depth/pointcloud MCAP、SQLite 索引及 source trace。
- 旧 CLI 先处理机器人身份入口，再走统一原生 schema 校验；OpenArm/generic 机器人身份回归通过。
- 前端上传确认支持通用 profile；曲线保留逐轴 rad/m。模型包生成工具走现有机器人模型资产机制，验证 16 个映射、prismatic 单指位移和 mimic，已生成 24 文件 / 22 mesh 的模型包。

实现说明、C 完整调用示例及 E 装配要求见 [docs/openarm-lerobot-v1.md](../../docs/openarm-lerobot-v1.md)。

## 测试命令与结果

以下 `G0=/home/hc_op/workspace/openarm-data-integration-plan/g0`，工作目录为 B/platform。

| 命令 / 检查 | 结果 | 证据 |
|---|---|---|
| `bash "$G0/scripts/compose.sh" B config --quiet` | PASS | `compose-config.log` |
| `bash "$G0/scripts/compose.sh" B build migration` | 已执行，Docker Hub pinned Python manifest 网络超时 | `baseline-build.log` |
| `bash "$G0/scripts/compose.sh" B -f artifacts/g0/offline.compose.yaml build migration` | PASS，本机已有离线基础镜像 | `offline-build.log` |
| `bash "$G0/scripts/compose.sh" B run --rm --no-deps migration` | PASS，B 数据库 128 个迁移 | `migration.log` |
| `bash "$G0/scripts/test-platform.sh" B` | **102 passed / 16 skipped**，默认未设置真实基础设施变量 | `regression.log` |
| 下方完整基础设施回归 | **118 passed / 0 skipped** | `full-infrastructure.log` |
| 三类样本真实往返，含实际 QC PASS、原始时间/映射精确比较 | **3 passed**，另已包含在完整回归 | `roundtrip.log`、`roundtrip/*.evidence.json` |
| generic/OpenArm/A 源读取和所有异常样本 | **20 passed**，另已包含在完整回归 | `clock-regression.log` |
| B Compose 中 `pnpm run typecheck` | PASS | `frontend-tests.log` |
| B Compose 中下方 Vitest 选择集 | **103 passed / 13 files** | `frontend-tests.log` |
| `bash scripts/check_openarm_reader_b.sh` | **6/6 PASS**，`pip check` PASS | `reader-verification.log`、`reader-*.json` |
| Ruff | PASS | `lint.log`、`lint-final.log` |
| 代码 `git diff --check` | PASS；排除原始日志和 E 的故意损坏 fixture | E fixture 的 106 文件逐字节一致 |

完整回归命令（所有服务均为 B 的隔离 PostgreSQL/MinIO/Temporal）：

```bash
bash /home/hc_op/workspace/openarm-data-integration-plan/g0/scripts/compose.sh B run \
  --rm --no-deps --user 1000:1000 --entrypoint python \
  -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 -e OPENARM_REAL_INFRA=1 \
  -e HC_TEST_POSTGRES_DSN=postgresql://hc:hc@postgres:5432/hc_data \
  -e HC_MINIO_ENDPOINT=http://minio:9000 -e HC_MINIO_BUCKET=openarm-b-tests \
  -e HC_MINIO_ACCESS_KEY=minio -e HC_MINIO_SECRET_KEY=minio-local-only \
  migration -m pytest tests/lerobot_imports tests/publishing \
  tests/tools/test_lerobot_platform_upload.py -q \
  -p pytest_asyncio.plugin -p no:cacheprovider
```

为持久保存新往返产物，额外传入
`-v /home/hc_op/workspace/openarm-integration/B/platform/artifacts/g0:/delivery`
和 `-e OPENARM_ROUNDTRIP_OUTPUT=/delivery/roundtrip`。本次已保存产物与 reader 结果匹配，SHA 见下表。

前端使用 `.frontend-env` 中 Node 22.18.0 / pnpm 10.14.0，依赖按原 `pnpm-lock.yaml`
执行 frozen install；挂载到 B migration 容器的 `/tool` 和 `/frontend` 后运行：

```bash
node /tool/node_modules/pnpm/bin/pnpm.cjs run typecheck
node /tool/node_modules/pnpm/bin/pnpm.cjs exec vitest run \
  src/pages/p03-upload-jobs \
  src/pages/p06-dataset-detail/lance-window-source.test.ts \
  src/features/viewer/EpisodeWorkbenchCore.test.tsx \
  src/pages/p15-robots/urdf-import.test.ts
```

容器 PATH 前置 `/tool/node_modules/node/bin`。前端锁文件未改变。
真实 PostgreSQL 回归暴露的旧测试 fixture 缺少 display_name、引用已移除的 dashboard convenience
方法已修复；迁移测试直接验证仍存在的 SQL contract，没有更改公共迁移。

## 往返产物与真实 reader

全部样本均为 2 episode / 12 frame，frame 0–5 后进入下一个 episode；源区间
`[0,6)` 与 `[15,21)` 的间隔保留在 source mapping，未补造成训练帧。

| 样本 | action/state batch | 视频 batch | 全部原资产下载 | ZIP SHA256 |
|---|---|---|---:|---|
| valid-openarm | `[8,16]` | 3 × `[8,3,480,640]` | 12 | `9f5241d14a9950a40e815ef43345bba8123ada86571dc3e66f396569a3de1e17` |
| valid-generic | `[8,2]` | 1 × `[8,3,240,320]` | 10 | `9b1a4f400cf808789297ebefaa4de84a93ffe607857d097af15a158047b2badf` |
| a-actual | `[8,16]` | 3 × `[8,3,480,640]` | 20 | `33f68a038e02361fff7adcafb3c1013a9093ff813c03163c5ce902a21fc3af0c` |

ZIP、解压目录和 QC/source marker 回执位于 [roundtrip](roundtrip)。
汇总见 [verification-summary.json](verification-summary.json)。每个源包及对应导出包均通过真实
`LeRobotDataset` 逐帧读取和跨 episode 的 DataLoader batch；float32 action/state 精确相等，
时间/episode/frame/task 以及源映射精确一致。视频逐相机对照源 reader，最大逐帧平均绝对误差
分别为 0.007805、0.004558、0.003956，均小于 8/255 的重编码阈值。

A 产物来自实际 exporter（显式 synthetic，不是真机动作采集）：

- A manager SHA：`0efe2ff0aabfb123e0ccb4d64b0f95092d67661d`。
- 原始 bundle：`/home/hc_op/workspace/openarm-integration/A/robot/artifacts/g0/openarm-capture-v1-final/bundle`。
- content SHA256：`e678e557da2b36bd2582a6cf541615d7098b30dd04ed9df33f5e1954b4ef2043`。
- export_id：`04242113-b0cb-4f4e-9681-74334c2c5819`。

Reader 为 `.reader-compose` 独立 venv，`include-system-site-packages=false`：
Python **3.12.8**、LeRobot **0.6.1**、Torch **2.11.0+cpu**、TorchVision **0.26.0+cpu**、
PyAV **15.1.0**。完整 **64 项**依赖见 [reader-requirements.lock](reader-requirements.lock)，
系统与 PyAV 内置 FFmpeg 库版本见 [reader-environment.json](reader-environment.json)。
锁文件 SHA256：`3c47fa7f61424556725e3c140def146d3abc3a04b80206a4ebdf436438f2a93c`。
`bash scripts/check_openarm_reader_b.sh --install` 可从锁文件重建并读取全部六包。

Reader 按原样加载数据，无 monkeypatch、无替代 reader，读取时 HF 网络关闭。
它对 `hc.*` info 扩展发出忽略提示；来源/标签需显式读取 `meta/annotations.json`。
初次容器运行因 UID 无 passwd 名称触发 Torch 缓存错误，设置
`TORCHINDUCTOR_CACHE_DIR=/tmp/torch` 后通过；该设置已进入复现脚本。
主机上最初的缓慢重复 reader 下载已停止，未作为验证环境。

## C 可调用接口

模块：`hc_data_platform.lerobot_imports.committed`。

```python
manifest = normalize_committed_manifest(raw, verified_assets)
# C 将 manifest 持久化到 raw.manifest_key；Raw 必须已 COMMITTED。
result = discover_committed_source(storage, raw)
# result.schema_version == "committed-lerobot-discovery/v1"
# result.plan: LeRobotImportPlanV1
# result.episodes: episode metadata + source_episode_id/outcome
# result.source_manifest_sha256: 当前不可变 manifest 的 SHA256
```

`verified_assets` 含 `path/object_key/size/sha256` 和可选 `crc64`，来自 C 已验证的资产事实。
必须在 Raw 对应租户/项目/区域的 worker context 内调用，不接受机器人本地路径。
支持 C 的 `raw-<32 hex>` Raw ID；多次发现返回相同 task ID/计划。源对象可使用完全不连续的
opaque object_key，测试已覆盖。发现成功不代表 QC/READY。

启动现有 `LeRobotImportWorkflow`，输入
`LeRobotImportWorkflowInput(task=result.plan.episode_tasks[0], episode_count=len(result.episodes))`，
使用现有 Pydantic Temporal converter、配置的 task queue 及 C/E 的 durable idempotency 策略。
已有 READY episode 由现有 pipeline 复用；显式重处理通过既有 attempt ID。
完整示例和逐 episode activity 接法见仓库 `docs/openarm-lerobot-v1.md`。

## E 挂接需求

1. 将 C 的 COMMITTED LEROBOT_V3 分支接到上述 manifest 规范化/发现入口，再调度既有原生处理工作流；
   C 从真实 episode/QC 结果聚合 processing result。B 不修改 C 核心实现。
2. 保留现有 runtime 对 `LeRobotPipeline` 的注入及 native workflow/activity 注册，统一普通/media
   task queue。B 没有修改 `runtime.py`、公共 worker、路由总装配或迁移注册表，无新增迁移。
3. 为目标数据集配置标签 schema、ACTIVE 采集任务和 enabled robot 数据源。自定义 QC 可通过
   `quality_profile_factory` 注入；改变阈值应使用新的 profile 版本/身份。
4. 按现有模型资产 API 上传/绑定 OpenArm 模型；本地已生成 `artifacts/g0/openarm-model`，校验回执
   为 `model-verification.json`。夹爪映射至 finger_joint1，finger_joint2 由 URDF mimic 控制。
   模型包身份是模板，绑定具体机器人时填写目标身份，不把 A 的文件系统路径传给平台。
5. 统一发布时从正常 pinned base image 重建，保留原离线部署与数据库，再安排 U4。

## 未执行项与已知限制

- **未部署、未执行 U4、未执行跨电脑浏览器验收、未进行真机任务语义或物理同步验收。**
- **C/E 总装端到端尚待他们合并挂接。** B 已完成独立资产发现接口、确定性计划及真实 pipeline 验证。
- 真实平台往返使用测试 principal 注入和测试目标选择；生产认证/目标授权未由该往返测试覆盖。
  测试从已提交源显式启动真实 Temporal 工作流，使用 UnsandboxedWorkflowRunner；
  标注/审核/发布调用生产服务，未覆盖其完整浏览器交互及生产自动排队总装。
  PostgreSQL、MinIO、Temporal、QC、Lance、媒体、标注、审核、发布与下载均为真实实现。
- 默认 Docker 构建执行过但受 Docker Hub 网络超时阻塞；使用现有
  `hc-offline/backend:71eaa560` 作为 B 专用基础镜像并挂载本次源代码完成测试。
  依赖实录见 `platform-environment.json`，fallback 文件已提交。原镜像和数据库未覆盖。
- 原生 PROCESS 范围为 LeRobot v3、具名 float32 一维 state/action、整数 1–240 FPS、HWC RGB video；
  未声明支持任意 feature dtype、非固定帧率或任意多维训练特征。
- 伴随 MCAP/SQLite/trace 通过原始资产下载保留；训练 ZIP 不重复嵌入其字节，内含校验清单与来源引用。
  视频重编码有损；原始 MP4 下载逐字节保持一致。
- SDK 忽略 `hc.*` info 扩展，训练端若需采集上下文和人工标签应读取 annotations sidecar。
- E 共同 fixture 未改动；`wrong-hash/export.json` 的空白是故意造成 hash 错误的字节，未自动格式化。
  测试日志保留工具原始输出空白。其余代码 whitespace 检查通过。
