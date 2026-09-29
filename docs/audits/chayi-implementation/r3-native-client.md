# G1 R3：独立上传器进入原生 RGB-D 标准包

2026-09-29，执行工作树 `hc_DataPlatform-chayi-work` / `feat/chayi-g1`。
这是 R3 / W2 的部分软件证据，G1 和 17 项 P0 仍未全部验收。

## 本阶段变更

- 机器人 PASS 收据支持真实 `CommitReceipt`，核验 COMMITTED 包、精确集合版本、
  租户/项目/区域/数据集/Episode 和输出样本数。新格式 `lance_version=null`；历史
  Lance 收据继续读取。机器人公开 `openarm-processing-result/v1` 结构不变。
- 人工查询机器人 Episode 结果增加可选 `data_ref`，已重新生成运行时 API 客户端。
- 0008 迁移区分源 `frame_count` 和输出 `sample_count`，继续保留 0007 的完整来源、
  收据、集合、授权约束及禁止清除绑定的规则。仅隔离数据库应用：141/141 current，
  missing / unknown / checksum_drift 均为空。
- 修复 public recording fields 的视频特征名与内部 topic 混用；新格式保留可逆
  映射，历史 topic 身份不变。`source.timestamp_ns` 使用合法内部身份并恢复原特征名。
- 新处理器 `native-episode-package/2` 用整数最近纳秒网格，与原生 reader 的固定
  帧时钟一致；旧处理器继续向下取整。版本参与对齐/包身份计算。不放宽深度时间检查，
  不修改源 HC 时间、profile 或掩码，不重新量化；解决 30 fps 的 1 ns 取整差异。
- manager 和平台 public field 元数据都读取实际源 profile 比例，缺失单位时为 null，
  不再默认写 0.001。机器人阶段提交 `f101a9d`，仅执行工作树，未安装到日常机器人环境。
- 深度显示继续调用固定版本的 RealSense 官方 `rs.colorizer`；参见
  [平台显示](realsense-preview.md) 和 [相机证据显示](camera-official-colorizer.md)。

## 实际链路与证据

独立安装的 uploader 1.3.0（Python 3.10、无平台/ROS 依赖）通过真实 48000 API，
用正常签发的 robot Bearer 令牌上传、分片对账和提交；运行中的 outbox、Temporal、
worker、PostgreSQL、MinIO 完成处理。没有 TestClient、伪造 READY 或直接插入包。
预置仅包括隔离租户/任务/合成机器人身份、凭据、已发布 Tag Schema。

输入明确为 6 帧、16×16、30 fps 合成数据，不是传感器录制或完整验收样本。
来源比例 0.00025 m/unit；RGB 和单通道 gray12le 深度各一条 HEVC MP4。
实际编码 profile 为 **Rext**，按用户后续选择记录实测值，不将其伪称 Main 12。
编码 `lossless=1`，解码码值哈希一致。对数量化参数仍为 0.01–10 m、shift=3.5 m；
该输入最大未饱和量化误差约 0.665 mm，与视频码值无损是分开的两项检查。

同一上传 `riu-f0e417718e5a52198b9c732d0987ee44` 首次因时间网格失败，修复版本后
通过 uploader 的 `retry --processing` 恢复成功。Raw、源 Episode 和上传 UUID 保持
不变。最终 `READY/PASS`，不是“已审核发布”。Lance lineage 为零，15 个 Raw 资产
逐字节不变；完整包的 RGB、深度视频和全部深度 HC 证据与输入字节一致。数值 Parquet
与源 action/state/额外矩阵逐值一致，标注任务绑定该数据版本。

| 验证 | 结果 | 证据 |
| --- | --- | --- |
| 收据、对齐、入口/API/原生映射回归 | 94 passed，0 skipped/xfail | [XML](r3-receipt-regression.xml) |
| 原生包实际服务成功/缺掩码/错 profile/错映射 | 4 passed；含 0008 正负约束 | [XML](r3-native-services.xml) |
| manager 实际源单位/未知单位元数据 | 6 passed | [XML](r3-recording-fields.xml) |
| 独立上传器真实 HTTP 最终回执 | READY/PASS，原协议 | [JSON](r3-uploader-ready.json) |
| 持久化收据/Raw/包/视频/标注审计 | 精确绑定、零 Lance、原件不变 | [JSON](r3-native-receipt.json) |
| 禁网官方 reader / DataLoader / 独立 HC 掩码 | LeRobot 0.6.1，6 帧 RGB-D，无平台 import | [JSON](r3-native-offline-reader.json) |

完整包由授权 package service 的 ZIP 路径生成、校验后交给禁网 reader；这次不计作
人工登录后的 HTTP 下载或浏览器验证。官方 reader 不自动应用 HC 无效掩码；独立应用
后检查物理深度，实际最大读回误差 1 mm（包含官方毫米输出取整）。float64 额外矩阵
的官方 Tensor 为 float32，最大转换误差约 1.19e-8，Parquet 源精度保持。
`model_depth_training_support=NOT_VALIDATED`。

Ruff、正式/运行时 OpenAPI 检查通过；客户端运行时契约 SHA-256
`fc38b1e0b2497b6f805b9a066411c5eaa40f25dbb428e218dccb1ee52a878eab`。
服务测试暂时停止的 G1 worker 已恢复健康；原 8088、原始四份文档和机器人主工作树
未修改。没有启动运动或真实采集，没有删除旧存储，没有执行 G2/G3。

## 复现入口

`backend/tools/robot_native_client_probe.py` 只允许指定的 G1 namespace/数据库/凭据
配置。`prepare --run <新名字> --host-backend <执行工作树/backend>` 创建明确合成输入、
正常身份与 0600 凭据文件、独立上传配置。随后运行安装的 `humanoid-upload` 的
`enqueue`、`step`；发生可重试处理失败时使用原任务的 `retry --processing`。
本机须对 127.0.0.1/localhost 设置 NO_PROXY，防止系统代理把本机 API 请求转成 502。
`inspect` 校验真实结果并导出包；`diagnose-plan` 是显式诊断正常幂等计划构建，不用于
声称上传/处理成功。源码清单、完整 Temporal 历史和私有配置留在忽略目录
`backend/.g1-artifacts/robot-native/r3-native-20260929d/`，凭据不纳入交付或日志。

```bash
bash deploy/chayi/compose.sh run --rm --entrypoint python -e PYTHONPATH=/app:/app/src -e HC_DATA_SOURCE_CREDENTIAL_KEY=isolated-chayi-g1-source test -m tools.robot_native_client_probe inspect --run r3-native-20260929d --host-backend /home/hc_op/workspace/hc_DataPlatform-chayi-work/backend
bash deploy/chayi/compose.sh run --rm reader /app/tools/read_native_rgbd_package.py --reference /app/.g1-artifacts/robot-native/r3-native-20260929d/receipt-audit.json --output /evidence/r3-native-offline-reader.json
```

## 未完成与 G2 交付条件

- 第一个输入 `r3-native-20260929c` 的 topic 错误被旧处理器记为不可重试。现有 API
  正确拒绝该状态的重试；失败记录和 Raw 保留，没有手工修改状态。代码修复后的
  受控永久失败恢复流程仍待补齐，不能以之后的新测试成功代替该项。
- 原上传器 source-mapping 契约固定 `time_base=[1,30]`，本阶段沿用；这不是任意
  帧率上传器验收。30 fps 测试不能代替长录制、多 Episode 共享视频/重采样。
- MCAP/连续录制新包分派、真实采集来源/标定、R1–R4 全范围、subtask、独立审核、
  批量交付、1440/1024 浏览器、真人角色、W7 迁移/回退演练仍须继续；17 项 P0 Pending。
- G2 前同时交付理解 package receipt、DataVersionRef 和 v2 时间网格的 API/worker，
  以及 0006/0007/0008 expand 迁移，先验证再启用新任务。该阶段没有执行部署切换。
  回退停止创建新格式任务，保留兼容 reader/worker 恢复已提交任务；不删除表、收据、
  Raw、标准包或旧 Lance，不降级解释新数据身份。完整 G2 指南仍待 W7 全流程证据。
