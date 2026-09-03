# Hugging Face Unitree G1 四相机数据导入

`hc_data_platform.tools.hf_unitree_g1_to_mcap` 将 Unitree Robotics 官方账号发布的
G1 全身操作 LeRobot v3 数据转换成 HC 数据平台可直接选择文件夹上传的数据包。

固定数据源：

- 数据集：`unitreerobotics/G1_WBT_Dex1_Put_Clothes_into_Washing_Machine`
- revision：`6d698e2641cc4bb765cd738835fe3a4ecc0fe2c7`
- 格式：LeRobotDataset v3.0
- 原始频率：30 Hz
- 相机：头部双目、左腕、右腕，共四路 640×480 AV1 视频
- 机器人状态：根位置 3 + 根四元数 4 + G1 29 个关节

对应机器人模型来自 Unitree 官方 `unitree_ros`，不是转换器生成的模型：

- 仓库：`unitreerobotics/unitree_ros`
- revision：`4ddbf6df0aa5bf8c8789d3edfa83e5e3ca45fe48`
- URDF：`robots/g1_description/g1_29dof_mode_15_with_dex1_1.urdf`
- 37 个 URDF 引用的 STL mesh 保持原始字节不变

## 先转换 episode 0

在仓库根目录运行：

```bash
PYTHONPATH=backend/src backend/.venv/bin/python \
  -m hc_data_platform.tools.hf_unitree_g1_to_mcap \
  --project-id be22-hf-g1-video-20260819-02-p1 \
  --collection-task-id 14d16ba1-d95a-5ee3-aaa7-7b7d78091b52 \
  --robot-id robot-d1a17126-b495-59b8-bf48-0ccce0a6ffe7 \
  --episode 0
```

首次运行会下载固定 revision 的源 Parquet、episode 元数据、episode 所在的四个视频
分片和 Unitree 官方模型。下载支持 `.part` 断点续传。输出结构为：

```text
artifacts/hf-unitree-g1-mcap/
├── _robot_model/
│   └── unitree_g1_mode15_dex1/
│       ├── g1_29dof_mode_15_with_dex1_1.urdf
│       ├── robot.config.json
│       ├── meshes/
│       ├── UNITREE_LICENSE.txt
│       └── MODEL_PROVENANCE.md
└── hf-g1-package-000000-<稳定摘要>/
    ├── recording-config.json
    ├── recording.mcap
    └── rollout_manifest.json
```

机器人资产页面手动选择 `_robot_model/unitree_g1_mode15_dex1` 文件夹。数据上传页面
选择单个 `hf-g1-package-*` 文件夹，或选择总输出目录发现多个 episode。

当前平台已发布并绑定这一官方模型：机器人
`robot-d1a17126-b495-59b8-bf48-0ccce0a6ffe7`，模型固定版本
`version-9aaa7507-541d-4a27-9a72-2ead93838ed1`。生成当前任务的数据包时必须传入上面的
`--robot-id`，否则 Manifest 会引用默认的离线来源标识，而不是平台机器人资产。

## 关节与时间语义

`observation.state.robot_q_current[0:7]` 原样保留为根位置 `xyz` 和四元数
`wxyz`；`[7:36]` 严格按照 Unitree SDK 的 29 电机顺序命名，并发布到
`/robot/joint_states`。动作中的 `action.robot_q_desired` 用相同切片规则处理。

MCAP 的相对时间来自每行 source `timestamp`，视频切片边界来自 LeRobot v3 的
episode metadata。源数据没有记录绝对采集日期，`recording-config.json` 会明确标记
`NOT_PRESENT_IN_DATASET`；Manifest 使用的 UTC 起点只是平台时间窗口的确定性锚点，
不会被描述成真实录制时间。

Dex1 每侧只提供一个 `5.5 → 0.0` 的控制器开合值，而官方 URDF 每侧包含两个单位为
米的 prismatic 关节。源数据没有给出控制器单位到米的标定。因此脚本将原始 Dex1
数值保存在独立 Topic 和曲线中，但不驱动四个 URDF 手指关节；配置文件用
`UNMAPPED_NO_PUBLISHED_CONTROLLER_TO_METRE_CALIBRATION` 明确标识，避免伪造换算。

## 前 10 条批量转换

episode 0 验收后运行：

```bash
PYTHONPATH=backend/src backend/.venv/bin/python \
  -m hc_data_platform.tools.hf_unitree_g1_to_mcap \
  --project-id be22-hf-g1-video-20260819-02-p1 \
  --collection-task-id 14d16ba1-d95a-5ee3-aaa7-7b7d78091b52 \
  --robot-id robot-d1a17126-b495-59b8-bf48-0ccce0a6ffe7 \
  --all-episodes \
  --episode-count 10
```

每个 episode 生成一个独立 Manifest/MCAP 文件夹；已通过校验的输出会复用，视频
分片也只下载一次。每个数据包发布前都会执行 Manifest 模型、MCAP CRC/Schema/Topic
和辅助配置文件校验。本数据集的 AV1/Parquet 分片会由多个 episode 共用，因此转换
10 条或更多 episode 时应保留默认缓存，避免删除后又下载同一分片。`--prune-source-cache` 只适合
转换单条，或能够接受后续 episode 重新下载共享分片的低磁盘空间场景；全部完成后可
统一删除独立的转换缓存目录。

如果先上传了 episode 0，之后再选择包含 0–9 的批量目录，平台会按稳定的
`data_package_id` 找到既有上传。已提交包返回 `ALREADY_COMMITTED` 并直接在网页队列中
标记完成，不会再次传输原 MCAP；未完成会话则继续断点续传。

## 已完成的首条端到端验收

- 数据包：`hf-g1-package-000000-302f84889700310c`
- MCAP：958 帧、31.93 秒、四路相机、29 个 G1 身体关节
- 质检：`PASS`
- 数据集：`dataset_hf_unitree_g1_multicam_30hz`，Lance v1
- 标注任务：`a3665597-89eb-5429-a74a-8ca19ec8559a`
- 标注保存：修订 r1，`操作` 区间为 0–957 步

采集任务 `00000094` 已绑定到这个 30 Hz 数据集，本次 Windows 手动导入包为 episode 0–9 共 10 条。先前用于验证的
DROID 15 Hz 数据集仍被保留，但已解除与当前任务的绑定。
