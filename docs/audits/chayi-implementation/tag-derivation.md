# Tag 区间创建独立 Episode：生成层证据

本阶段实现 W3 的显式派生生成层。普通 Tag 编辑不触发该生成器，不修改父包、
原录制、旧修订或冻结切片账本。任务/API/P08、新条目产品登记与独立 QC 尚待接通；
不能据此将 SEG-02、G1 或真实样本验收标为完成。

## 数据契约

- 固定父数据版本、完整标注修订 JSON/哈希、指定 Tag、审核引用、半开帧区间、
  schema 与实际编码环境。身份和数据版本可重复计算；不同 Tag/修订/名称产生
  新身份，审批变化可以产生同身份的新数据版本，不覆盖旧版本。
- 数值 Parquet 保留父 Arrow 类型和选中值；重建局部索引、相对秒时间、任务表、
  统计和官方 v3 元数据。HC 纳秒时间及父帧映射保留原值，首帧的“重复上一帧”
  标记重置为 false。分批读取，不要求把整个父表载入内存。
- 深度整段选择逐字节复用；区间选择解码原 12 位码值并以现有 lossless HEVC
  重新封装，不重新量化。profile、量化误差、掩码、缺帧/饱和和源帧证据随包保留。
  区间新时间映射保留父纳秒值，避免重新起算造成 1ns 偏移。实际 profile 沿用
  gray12le/Rext 的既定记录，不伪称 Main 12。
- RGB 整段复用，切片使用已有 H.264 CRF18 编码，报告明确记为重编码；不声称
  RGB 切片无损。官方 RealSense SDK 生成的网页预览仍为独立显示副本。
- 完整父标注、父 manifest/commit、来源/标定及录制证据随包保存。已有原录制
  时钟/窗口按精确时间转换；缺失窗口保持缺失，末尾使用父记录的结束时间。
  子条目不冒充新的冻结 slice，不继承父 QC、人工数据审核或标注审批。
- 当前子包 QC 为 UNKNOWN / DERIVED_INTERVAL_REQUIRES_OWN_QC；全无效深度
  区间仍被既有统计/有效性检查拒绝，未伪造有效像素统计。这些条件需在服务闭环
  中明确处理。

## 实际执行的检查

隔离 PostgreSQL/MinIO 和真实编码器，输入均为合成软件夹具。

1. [40 项通过](tag-derivation-tests.xml)：Tag 生成/新身份/幂等提交，整段复用、
   旧修订保留，跨租户/变更参数/伪造窗口拒绝，精确父时间与 29.97fps、深度契约。
2. [14 项回归通过](tag-derivation-regression.xml)：既有窗口、录制、包生成、
   标注绑定与 openarm raw 路径。
3. 两份实际提交的子包在断网容器经官方 LeRobot 0.6.1 reader/DataLoader 读取
   RGB-D，并由独立脚本反量化、应用 HC 掩码；所有 manifest 文件 SHA 核对。
   [离线报告](tag-derivation-offline-reader.json)、[输入与产物收据](tag-derivation-results.json)。
   官方 reader 输出数值 tensor 为 float32；原 Arrow 类型/值不变由后端检查证明，
   不据此声称官方 tensor 保留全部 float64 精度。
4. 变更文件 Ruff、格式及 diff 检查通过。当前未新增数据库迁移（隔离库沿用
   148 项迁移）；未执行 G2/G3。

复现测试：

```bash
bash deploy/chayi/compose.sh run --rm test tests/episode_store/test_tag_derivation_services.py tests/episode_store/test_depth_window.py tests/publishing/test_depth_contract.py -q
bash deploy/chayi/compose.sh run --rm test tests/episode_store/test_depth_window_services.py tests/episode_store/test_recording_package_services.py tests/episode_store/test_writer.py tests/episode_store/test_annotation_bindings_services.py tests/continuous_recordings/test_openarm_raw.py -q
```

离线使用 `reader` 服务运行 `backend/tools/read_tag_derivation.py`，参数为报告路径
及一个或多个完整子包根目录。实际样本、HTTP/worker 派生命令、1440/1024 浏览器、
真人审核与模型深度训练均未由本阶段验证；剩余 G1、W7/W8 继续。
