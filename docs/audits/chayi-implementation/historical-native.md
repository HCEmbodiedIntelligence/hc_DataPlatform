# W7 首次历史副本迁移与回退演练

2026-09-30，仍在 G1 隔离阶段。实际读取现有 8088 平台历史记录并制作副本，
不是本轮生成的合成 Episode。机器人归属、传感器来源和真人验收尚未独立确认，
不能据此把全部真实采集条件标为通过。G2/G3 的顺序授权继续有效。

## 隔离范围与原件

原工作区已由外部工作推进至 `89df980da61062d8c9fe2c16db5d3a78d9fadf18`，
不是最初的 `4933200c`。当前四份输入文档仍未跟踪。G2 合并时必须保留此后原
工作区的修改，不能用本工作树覆盖服务端源码。

从现有 PostgreSQL 只读 `pg_dump` 获得 136389679 字节归档，SHA-256 为
`fd389bfee0be50fe0b51c8f3325924026ab7470f71132404829aa724d1eb0da8`；恢复到
**hc-chayi-g1 PostgreSQL 的独立数据库 `hc_chayi_history_fd389bfee0be`**。
恢复 exit 0，追加 21 项 G1 migration 后达到 150 项，无历史 migration 校验漂移。
常驻 API/worker 仍使用原 G1 数据库，副本 outbox 没有被 worker 消费。
这不是 G2 的整个平台备份恢复验收。

快照有 491 条 Episode，其中 330 条 MCAP、154 条原生 LeRobot、7 条既有
软件夹具。原生 LeRobot 数据集的原件为 50 文件、8482990322 字节，元数据
声明 154 条、123603 帧、四路 RGB，无深度；旧对齐存储为 123610 帧，不能把
二者混为同一计数。本工具保持旧对齐结果，并保留原始来源信息。

已复制这个原生数据集的全部 Raw 前缀、所有旧 Lance 文件和冻结媒体引用：
1765 对象、13832271695 字节。每个源对象以 ETag 条件读取，校验本地 SHA-256；
写入隔离 MinIO 时不覆盖已有对象，写后重新 GET 全量校验。原 DB/对象仅只读。
归档、原件和大清单均在被忽略的 `backend/.g1-artifacts/history-copy/`，不提交
至 Git；数据库归档和原始复制文件为受限权限文件。

数据库引用展开后核验 1283 个去重对象、13447915589 字节，全部已展开文件哈希
通过。另有两条历史媒体记录未 READY，报告仍为 **INCOMPLETE**；不能把这两条
失败记录消去后宣称整库通过。额外 Lance 文件由对象复制校验和所选 Episode 的
完整逻辑核验覆盖，不包含在这 1283 个原件/媒体对象计数中。

## 代码与命令

`episode_store/migration_native.py` 从明确 tenant/region 的原始 DB 行、Raw 清单、
已落库的产品 revision 和媒体 receipt 准备完整 RGB 视频。读取每条 Episode
全部旧行，默认最多 20000 帧（可显式设置至 100000）；校验每帧源引用和实际
解码 PTS、帧数、分辨率、流数量，再冻结转换计划。原始任务文字、机器人类型、
已有 HC 来源/标定声明进入新包；没有标定则保留 unknown，不制造质量或审核结论。

已支持旧清单的精确 `lerobot` 别名：旧源码 `40c0ad79` 写此字符串，但文件清单
摘要使用 `lerobot_v3`/`v3.0`。现在接受这一已确认的旧组合，同时仍验证原哈希，
不使用任意大小写/其他格式回退。不改写旧 manifest。

新增 `backend/tools/episode_migration_stage.py plan/stage`：

```bash
# 显式设置 HC_EPISODE_MIGRATION_DSN、*_OBJECT_ENDPOINT、*_OBJECT_BUCKET、
# *_OBJECT_ACCESS_KEY、*_OBJECT_SECRET_KEY、*_LANCE_ROOT、JSON *_LANCE_OPTIONS。
# 这些参数没有现网/应用配置默认值。
PYTHONPATH=backend/src python backend/tools/episode_migration_stage.py plan \
  --organization "$MIG_ORG" --project "$MIG_PROJECT" --region "$MIG_REGION" \
  --dataset "$MIG_DATASET" --episode "$MIG_EPISODE" --version "$MIG_VERSION" \
  --inventory "$MIG_INVENTORY" --directory "$MIG_DIRECTORY" --output "$MIG_PLAN"
# stage 使用相同 scope/Episode/version/inventory/directory，加 --plan "$MIG_PLAN"；
# --output 必须是新的报告路径。随后将报告中的 spool 交给 batch prepare/publish。
```

plan 只读取服务和写本地计划；stage 复核当前源行与固定计划，提交完整包但
`activate=False`。现有不可覆盖 checkpoint 用于重试。它不经过公开 READY
导出授权器，也不降低公开资产授权条件，没有提交前读取/READY 循环。

当前适配器有明确边界：共享原视频窗口须先完成窗口适配；声明深度的来源须由
具有实际 profile/源单位/掩码/帧映射证据的适配器处理。二者当前均拒绝，不能
静默丢弃深度或把整个共享视频当作一条 Episode。H.265 **RGB** 可按其 RGB
合同校验，不受深度单通道格式限制。

## 实际演练证据

[机器报告](historical-native-rehearsal.json)、[54 项定向测试](historical-native-tests.xml)、
[禁网官方读取](historical-native-offline-reader.json)。

- 选择旧逻辑/物理 Lance 154 中的第一条原生 Episode，958 帧、四路 RGB。
  plan → stage → batch PREPARED → PUBLISHED → ROLLED_BACK 实际完成。
  发布形成 1 条新标准包 + 153 条旧引用的混合集合，未伪造 Lance 版本。
- 全部旧数值、整数时间、稀疏来源/有效性和媒体引用重建后逻辑 SHA-256 为
  `634a67ba01cc2c23e82529635af063784953579c454a0e86e96b8254d24d2341`，与旧
  fragment 完全相同。四路 RGB MP4 逐字节复制，没有重编码。
- 从隔离 MinIO 下载 18 文件、53233261 字节的完整已提交包。禁网容器中的
  **LeRobot 0.6.1 reader/DataLoader 读取全部 958 帧**，独立重建全部旧逻辑哈希
  和视频哈希，未导入平台代码。官方关于忽略 HC 自定义 metadata 的提示保留；
  HC 由独立检查器验证。这条来源无深度，报告明确深度流 0、掩码不适用；
  不用 RGB 成功充当 RGB-D/深度训练验收。
- 发布后、回退后分别核对完整库存。全部旧行保留；154 项标注任务、156 项修订、
  2 次提交和 1 次 APPROVE 审核的原始行哈希全部不变。未对旧审核做自动迁移批准。
  回退后集合头为空、新 Episode 当前指针为空，读取返回旧存储；154 个旧 Lance
  版本及其引用未增加/删除/改写。新包、预览、归档集合/产品版本仍保留。
- 回退后再次执行 stage CLI，返回相同收据、同一包目录及等价报告；只有 1 个
  新标准包版本，集合头/当前 Episode 指针仍为空，未重复版本或重新激活。
- 定向测试包括错误格式/哈希、租户/区域范围、逐帧纳秒时间/PTS/版本/来源/窗口
  篡改、未就绪/退役媒体、实际 H.265 RGB 解码、帧数/尺寸错误及 gray 深度被
  错标为 RGB 的拒绝。54 项通过，无 skip/xfail。初次 25 项失败是测试夹具缺少
  frame-ref 三个必填状态字段；失败 XML 留在私有工件目录，最终文件独立保留。
- 报告工具修正了无深度包的掩码覆盖标记；修正后重新完整运行官方读取。
  最终 Ruff/format/diff 检查通过。首次物理清点因误配隔离 MinIO 密钥 403，
  修正连接后才得到上面的物理哈希结果；没有绕过认证。

## 深度来源调查与未验收条件

额外只读复制了一条历史 MCAP 与其媒体/清单，6 对象、23989276 字节。
原 MCAP 15584050 字节，SHA-256
`d170410ea8a2b63d8fc945219cedda0c62117f337e7fc73b05c8ad0e0e25b37a`。
官方 MCAP ROS2 解码确认其深度是 `sensor_msgs/CompressedImage`、`16UC1; png`、
uint16、400×640，首帧有 47952 个零值。文件包含 RGB/深度内参及外参声明；
未发现 channel `hc.depth_source`、MCAP Metadata 或所读取标定声明中明确的
深度像素比例。外参平移的 mm 不等于像素的 mm。尚不能按猜测的 0.001 生成
有效性/误差结论，也不能仅凭旧 HEVC 的 `depth_unit=mm` 反推实际源单位。
这条调查不算 MCAP 深度迁移完成，继续查找可绑定至该原件的驱动/采集配置证据。

仍需完成共享 AV1 原视频窗口（含有 APPROVE 标注的 Episode）、旧标注/审核与
新数据版本的完整等价映射、MCAP/已有深度证据恢复及原始 MKV/数组适配、全部
选定历史批次/规模与实际 HTTP/1440/1024 浏览器验收、P06 新集合业务发布和 W8。
本轮不是 G1 整体验收，也不是 G2 现网迁移/机器人安装；没有执行 G3。
模型深度训练支持仍为 NOT_VALIDATED。
