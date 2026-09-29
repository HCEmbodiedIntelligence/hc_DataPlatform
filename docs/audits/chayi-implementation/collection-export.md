# G1 重组 LeRobot RGB/RGB-D 集合生成与离线读取（2026-09-29）

新增 `episode_store/collection_export.py`，将固定版本的独立 Episode 重组成单个
官方 LeRobot v3 数据集根目录。它是 W6 的生成层，使用已授权的 package/revision
输入；本阶段未把集合选择接到任务 API 或 P21 页面，不据此声称批量交付闭环完成。
上一阶段的独立包批量 ZIP 与这里的重组集合仍是两种明确不同的产物。

## 最终行为

- 选择 1–100 条同租户/项目/地域的独立 Episode，每条只能选一个数据版本；按固定
  数据版本排序，重建全局 `index/episode_index/task_index`、任务表、逐 Episode
  元数据、全局/逐 Episode 统计和相机映射。数值按原 Arrow 类型写入，不做统一
  float32 降精度；`hc.timestamp_ns`、源帧和有效性信息保留。
- 每个 Episode 的每路视频仍是独立 MP4，复用原文件全部字节；集合元数据更新
  chunk/file 索引和视频起止偏移。各文件保持原来从零开始的 PTS。没有重复编码、
  量化、重采样或写新 Lance。原始录制、已提交主包和历史标注均不修改。
- 对齐频率、特征 dtype/shape/units、机器人类型、相机映射、结构化数值约定和
  深度 profile/version/unit 必须兼容。拒绝时给出具体字段，例如
  `features.action.units` 或 `depth_profile.observation.images.head_depth.version`，
  不隐式转换或按名称凑合并。缺失源单位/标定仍是原包缺失状态，未推断补造。
- RGB-D 保留深度视频、profile、有效掩码、源映射及误差报告。RGB 子集在集合
  清单逐条列出 `excluded_depth_features`，排除深度视频/像素证据和当前数据表的
  深度相关特征；来源附件中的父清单、原始 metadata 仍保留，用于追溯已排除内容。
  用户界面的排除提示待任务/页面接入，不能把清单字段当作 UI 验收。
- 来源附件按 `hc/episodes/{episode_index}/` 分开，保留原 HC 路径、标定和来源窗口；
  `hc/collection_frames.parquet` 逐帧记录全局索引、Episode、父数据版本/帧号和原
  纳秒时间。顶层清单冻结源 manifest 哈希、所选修订/轨道/审核、媒体哈希、相机
  映射与模态选择；不把导出物冒充新的主数据版本。
- 每条可显式选择自己的固定标注修订和分段轨道，复用现有标注派生逻辑保留全部
  标签/关系/属性和原 JSON 字节。集合 `subtask_index` 重新全局编号，同时保存
  原局部编号；空隙与未选标注的 Episode 为 -1。清洗排除保留显式 mask，不删帧。
- 数值统计流式重算；RGB 统计池化原视频矩；深度按有效像素数加权池化均值/方差，
  避免有效像素数不同时误用帧数权重，官方 `count` 仍记录帧数。未宣称提供分位数。
  官方深度显示仍调用 RealSense SDK，集合写入没有新增显示算法。

## 实際验证与边界

- [12 项测试通过](collection-export-tests.xml)：9 项集合生成/兼容性测试、2 项
  原单包 writer 测试及 1 项既有标注派生回归。使用真实隔离 PostgreSQL/MinIO 和
  标注文件服务。核对两个内容不同的视频、全局索引/任务/统计、不同输入顺序下
  相同产物哈希、RGB 深度排除、主包字节不变、单/双修订及全局分段编号。
- [5 项兼容性复查通过](collection-compatibility-tests.xml)：在完善字段级错误后
  再次测试频率、单位、维度、相机、profile 拒绝。属于上述用例的复跑，不是另
  5 项不同测试；拒绝路径注入 metadata 差异，不冒充实际坏包入库。
- [生成层结果](collection-export.json)记录原数据引用与 RGB/RGB-D 清单内容哈希。
  测试期间禁止调用原量化入口，视频与源哈希相等；重复生成结果相同。尚不代表
  任务缓存命中，因为集合任务队列仍需接入。
- [离线官方 reader 报告](collection-export-offline-reader.json)：LeRobot 0.6.1、
  网络禁用、无平台 import，对 RGB-D、RGB 子集、单修订、双修订四份集合分别
  验证。每份有 2 个 Episode、8 帧、2 个任务；batch_size=5，第一批实际跨越
  Episode 边界。逐帧核对数值、任务、RGB 解码值、深度物理值、索引/视频偏移、
  标签区间与全局分段编号；另外独立应用 HC mask 和核对父帧/源时间。
- 3 份 RGB-D 集合分别处理 8 个无效像素；RGB 子集没有深度流或深度 mask 验证
  声明。深度统计与有效物理像素一致。官方毫米取整最大差约 0.4491 mm，这项
  检查不代替采集深度真值或量化前误差验收。Ruff 与格式、diff 检查通过。

输入均为 4 帧/条的软件夹具，不是实际采集或长录制验收；真人与模型深度训练
支持均未验证。源量化和视频码值无损仍使用各自独立证据。未完成集合排队/缓存/
下载 API、P21 批量选择、RGB 子集页面提示、真实 1440/1024 浏览器、真实样本、
Tag 派生 Episode、来源生产者补全及 W7/W8 剩余项；17 项 P0 保留整体 Pending。

复现（测试会生成具有新数据身份的本地证据目录，reader 参数使用对应实际根目录）：

```bash
bash deploy/chayi/compose.sh run --rm test tests/episode_store/test_collection_export_services.py tests/episode_store/test_writer.py tests/episode_store/test_annotated_delivery_services.py -q --junitxml=.g1-artifacts/collection-export-tests.xml
bash deploy/chayi/compose.sh run --rm reader /app/tools/read_collection_export.py /evidence/collection-export-offline-reader.json <rgbd-root> <rgb-root> <annotated-root> <two-revisions-root>
```

本阶段没有新增数据库迁移或公开 API；隔离数据库仍使用先前 147 项迁移。未来
G2 需包含这个生成器、匹配队列/API/UI 接入和固定 reader 依赖，并用实际样本复查
契约与磁盘容量；本次产物不是完整可部署候选的最终验收。回退保留所有主包、
标注和导出证据，不能删除旧存储。仅在独立分支与隔离服务实施，未操作 8088
业务数据、机器人运动或日常安装，未执行 G2/G3。
