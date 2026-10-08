# W7 历史共享视频窗口迁移

2026-09-30，G1 隔离数据库和 MinIO 副本。本阶段处理的是原平台已有数据，
不是新生成的合成输入。源数据库、源对象和机器人未写入；G2/G3 尚未开始。
副本来源和完整库存见 [首次演练](historical-native.md)。

## 实际结果

[机器报告](historical-native-window-rehearsal.json)、
[81 项迁移测试](historical-native-window-tests.xml)、
[8 项原录制路径回归](historical-native-window-regression.xml)、
[禁网官方 reader](historical-native-window-offline-reader-torchcodec.json)。

选择原生 LeRobot 第 104 条 Episode：920 帧、30 Hz、四路 480×640 RGB，
原视频是 AV1，四个共享流分别有 12423、9113、12254、14761 帧。
完整执行 plan → stage → batch PREPARED → PUBLISHED → ROLLED_BACK。
新集合为真实 `cv_*` 身份，版本 156，1 条新包及 153 条旧引用；没有新 Lance。
批次为 `mb_4654fa6817704fa422e9cf4c5b210530a6840a781c5a4f8aed1934fd8d70ff10`。

完整旧行重建 SHA-256 为
`de8a46a607ca5d56224b5f924a93af1c43525e6a0523a7c64c57ee7090a3aa5a`；
数值、整数时间、有效性、重复状态及原媒体引用全部相等。
源 Parquet 使用 float32 秒；源末帧的整数纳秒与 MP4 有理数时间相差 127 ns。
工具分别核对实际 Parquet 来源时间及 MP4 的固定帧网格，再按历史 frame_index
选择像素，保留旧引用，不用 MP4 舍入值改写历史时间轴。

从隔离 MinIO 下载完整 18 文件、1028840074 字节，逐文件校验 manifest/commit。
禁网、无平台模块导入的 LeRobot 0.6.1 reader/DataLoader 使用官方 TorchCodec，
读取全部 920 帧。独立工具又读取四个完整原视频的哈希，解码选定源帧，逐流
比较新包全部 RGB 像素哈希；全部相同。reader 的 action/state/timestamp tensor
为 float32；存储数值和 HC 整数时间的精度另由完整旧逻辑哈希验证，不能用
reader 的 tensor dtype 声称源时间被无损转换为 float32。

同一包的 [官方 PyAV 全帧检查](historical-native-window-offline-reader.json) 随后完成，
全部 920 帧及四流原始选帧像素比较通过；原报告创建时仍在运行的状态保留在提交历史中。
已安装官方实现每次 seek 会解码并转换前一关键帧至目标帧；当前 RGB 编码的
GOP 会带来重复工作。TorchCodec 验证设置 `torch.set_num_threads(1)`，仍使用
官方 reader/默认数据内容；没有自写视频 reader 或减去帧数。后续保留 PyAV
结果及性能观察，不将本次观察作为完整 G3 性能验收。

发布后及回退后重新清点全部旧行：154 个原标注任务、156 个修订、2 次提交和
1 次审核的行哈希不变；旧 Lance 的 154 个版本不变。回退后集合头及两个新包
当前指针均为空，旧读取恢复，新包、预览、归档集合和产品版本全部保留。

## 代码行为和代价

`migration_native.py` 将冻结的 DB/Raw/meta Episode 窗口、源视频哈希、实际
Parquet 来源时间及旧逐帧引用绑定；`migration_rgb.py` 负责明确选帧、重复帧和
恒定目标网格。整流且网格相同的输入仍逐字节复用，H.265 RGB 不受深度灰度
限制。需要截取时用已有 `TrainingVideo` 的可选 libx264rgb/crf=0 路径，编码后
再次全帧解码比较像素。原录制默认 libx264/yuv420p/crf=18 行为保持，8 项回归
通过。转换报告记录编码库/选项/profile、源与目标哈希、帧索引哈希和像素哈希。

实际窗口输出为 H.264 High 4:4:4 Predictive/gbrp。四路视频合计
1027312404 字节；这是保持已解码 RGB 像素完全一致的存储代价，比原 AV1 的
相应窗口明显增大。浏览器使用已准备的普通 H.264/yuv420p 预览；随后完成该真实
窗口的 HTTP/1440/1024 标注、映射和完整交付流程，见
[后续实际证据](historical-annotation-migrations.md)。

81 项测试包含真实 H.265 RGB 整流复用、选帧/重复帧的无损往返，以及源哈希、
大小、网格、帧序、尺寸、float32 来源时间、PTS、原窗口及冻结收据篡改拒绝；
无 skip/xfail。初次缺失 fps 的夹具失败 XML 留在私有目录，最终报告独立保存。
Ruff、格式和 diff 检查通过。

## 标注等价与未完成项

原已批准任务绑定 Lance 132，而迁移来源固定为 Lance 154。实际读取两版全部
920 行后逐行及 fragment 哈希相等。原修订 1 的两个半开 Tag 区间为
`[152,245)`、`[251,287)`；原 ID/层级/属性保留，旧 APPROVE 没有被复制到新任务。
此比较随后用于持久映射、新包独立 Tag 编辑/提交/审核和完整包交付，见
[历史标注演练](historical-annotation-migrations.md)；旧批准未转移。
调查脚本首次仅改逻辑版本却保留物理 Lance 154，被固定引用校验正确拒绝；
随后改用 inventory 中完整的旧 Lance 132 引用才通过，未放宽固定引用规则。

这条来源没有采集深度，报告深度流为 0、掩码不适用。它不替代实采 RGB-D、
量化误差/码值无损/掩码验收，也不证明任何模型已支持深度训练。
全部 154 条历史批次、MCAP 深度证据、三入口完整实际样本、P06 新集合业务发布、
W8 与真人/真机条件仍继续。goal 保持 G1→G2→G3 顺序授权。
