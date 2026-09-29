# 深度窗口与编码回归：G1 阶段证据

2026-09-29，`feat/chayi-g1`，仅 `hc-chayi-g1` 隔离环境。
这是 W1/W2 的阶段交付，**不是 G1 完成，也不是 MCAP／连续录制入口已经接通**。
编码修复提交：`49bff69b`。窗口实现和本报告另作阶段提交。

## 显示与存储

按用户要求，深度显示继续直接调用固定的官方 `pyrealsense2.colorizer`
2.58.4.10922，使用 Dynamic／Jet，未添加自定义调色板。
依据是 [RealSense 官方实现](https://github.com/realsenseai/librealsense/blob/master/src/proc/colorizer.cpp)；
显示不是主深度编码，也不用于测量。原有逐像素 SDK 对比测试本次再次通过。

主深度仍为 MP4／HEVC／gray12le 单通道，0～4095；量化 profile `hc-depth/1`
仍为 log、0.01～10 m、shift=3.5 m。源实际单位和 scale 保存在逐帧来源契约中。
实际解码 profile 为 `Rext`，不伪称 Main 12。RGB H.265 保留原字节；网页衍生
预览与主包分别记录。本次没有修改或替换任何已上传的原始录制。

## 发现并修复的编码问题

增加中间全零缺帧、重复源帧和饱和值序列后，旧编码参数在当前依赖下出现间歇性
码值不一致，原有逐码哈希检查正确拒绝生成完整深度证据。PyAV 和独立安装的
系统 FFmpeg 对失败文件解出了相同的错误码值，不能用可视化“看着正常”验收。
本次固定探针的旧参数 24 次中 12 次失败，集中在 16×16 和 32×32；该结果不表示
真实相机尺寸必然发生同样问题。关闭 weightp、关闭汇编、提高 rd 等尝试均未
稳定解决；没有把其中一次成功当作修复依据。

新编码 profile `hc-hevc-gray12-lossless/2` 增加 `keyint=1`，保持 `lossless=1`、
gray12le 和原量化参数。实际 codec profile、请求的编码参数、PyAV／FFmpeg
库版本与配置 SHA-256 写入报告。新配置同一探针 24 次均逐码一致；另有 7 种
尺寸、各 6 组序列的两个解码器回归，覆盖中间缺帧、4095 和随机 12 位码值。
上游 `openarm_raw/prepare.py` 升为 `openarm-raw-platform/4`，生成新的派生前缀。
参数含义参考 [x265 官方文档](https://x265.readthedocs.io/en/master/cli.html#cmdoption-lossless)。
修复依据是本环境实测；没有声称已定位上游库的具体源码缺陷。

代价是失去帧间压缩，文件可能更大。本探针 640×400、12 帧平坦序列由旧配置
11838 字节变为 40916 字节，不能外推为真实录制的压缩比。每个输出仍逐码验证，
不是只检查 `lossless=1` 参数。未来重新启用帧间压缩必须采用新编码版本并重新验证。
已编码、哈希／profile／时间一致的完整视频不因本次参数修改而被重编码。

## 窗口契约

`episode_store/depth_window.py` 接收已由内部处理链授权的 `PreparedVideo` 和
明确的帧窗口，不接受公开对象路径，不依赖或放宽 READY 下载授权。

- 完整验证源视频、profile、掩码、逐帧来源和误差报告的哈希及相互关系后才写输出。
- 整段且目标网格一致时原对象直接复用；整段只调整目标时间网格时复制视频字节，
  重建来源时间差。该复制保留原编码器版本，不能把当前验证进程冒充原编码器。
- 截取／重采样时选择原有 12 位码值，通过无损 HEVC 编码；不调用物理深度量化。
  使用有理数计算，选择规则为从首个指定源帧起最近邻、ties-to-even。
  目标时间明确选择 floor 或 nearest，调用方必须冻结共享时钟含义，不能猜测偏移。
- 保留源 seq、时间、实际 scale、source profile、PNG 掩码和原量化误差；重算
  缺帧／重复／时间超阈状态及汇总。记录父帧、父目标时间、父视频和每个证据文件
  的哈希，二次切片保留可追溯的父报告哈希。
- 量化误差标记为从已验证来源报告继承，编码无损通过重新解码另行比较。
  参数／父来源／编码配置共同产生窗口配置哈希；接入处理计划时必须将它绑定到
  新数据版本，不能仅按文件名或相机名复用。

包校验增加实际 codec profile、编码配置哈希、掩码像素统计、饱和／无效码值、
源时间差、重复状态和误差统计的一致性检查。它支持未携带新编码版本字段的旧
合法报告，不伪造其版本。原始量化前图像不在包内时，不能重新测量来源误差，
因此保留其来源声明及哈希，不声称重新完成原始传感器精度验收。

## 实际执行的验证

1. **55 passed**：窗口、双解码器、量化、writer、源单位、SDK 着色和实际包服务。
   包含篡改哈希／profile／时间／统计拒绝、跨 128 帧行组、二次切片及整段不编码。
2. **43 passed**：prepared bundle、预览授权、真实 Temporal 提交／恢复与重放、
   openarm raw、Raw 发布响应丢失及历史 materialized export 回归。与前一组有重叠，
   不把两组之和称作独立用例总数。
3. 真实 PostgreSQL／MinIO 的包服务接受选取的深度窗口，生成官方 SDK 预览和集合
   引用，无 Lance 字段；物理深度读取将缺帧标为 NaN，RGB H.265 字节保持不变。
   测试直接提供已准备的内部资产，**不是从上传入口或真人切片开始**。
4. 从该服务复制完整独立包，在 `network_mode: none` 的 LeRobot 0.6.1 环境用
   reader／DataLoader 读取 RGB-D，不导入平台模块；独立应用 HC 掩码，验证源映射。
   4 帧中 1 缺帧、1 重复，未饱和有效像素最大误差 0.25 mm（包含官方整数毫米
   输出舍入），来源量化报告最大误差约 0.000698704 m；两者独立报告。
5. 只读校验上一阶段通过真实 uploader 上传的旧包
   `dv_511feca78413fae01bf9f75fbff83b31d32218e69cc75c631fb0c220416c921f`，
   完整哈希不变。禁用编码器后仍整段复用，未重做量化。
6. 改动文件 Ruff 和 `git diff --check` 通过；本阶段无新增 SQL 或公开 API。

可复现命令（执行工作树根目录）：

```bash
bash deploy/chayi/compose.sh run --rm test \
  tests/episode_store/test_depth_window.py tests/publishing/test_depth_codec.py \
  tests/publishing/test_depth_contract.py tests/episode_store/test_realsense_colorizer.py \
  tests/episode_store/test_writer.py tests/publishing/test_depth_source.py \
  tests/episode_store/test_depth_window_services.py -q
bash deploy/chayi/compose.sh run --rm --entrypoint python test \
  tools/probe_depth_codec.py --output .g1-artifacts/depth-codec-comparison.json
bash deploy/chayi/compose.sh run --rm reader /app/tools/read_depth_window_package.py
```

证据：[单元／服务 XML](depth-window-unit.xml)、[扩大回归 XML](depth-window-regression.xml)、
[编码探针](depth-codec-comparison.json)、[离线 reader](depth-window-offline-reader.json)、
[旧包复用](depth-window-legacy-reuse.json)、[服务包引用](depth-window-package.json)。
全部输入为明确标识的合成序列，不能据此把真实录制、模型深度训练或 P0 整项标成通过。

## 尚未完成与 G2 条件

窗口代码尚未接入连续录制／MCAP 的 frozen plan 和派发；native 入口也仍保留
共享视频／多 Episode HC 来源限制。下一步仍需替换连续录制的 Lance 版本依赖，
将精确窗口和物理来源映射绑定到提交、授权及 READY 回执。

新 raw 派生采用 `/4`；后续阶段已修复跨版本重跑可能生成新派生并遇到不可变
recording identity 冲突的问题。桥接固定已登记版本及对象位置，实际服务并发／
缺失 receipt 拒绝测试见 [Raw 派生恢复](raw-derivation-recovery.md)。测试未运行
历史 `/3` 二进制，**仍不能当作真实旧资料跨版本部署已验收**；不能删除旧 receipt
或改 READY 条件绕过冲突。

G2 前还需真实分辨率／时长的存储与吞吐测量、三入口、浏览器 1440／1024、三类
独立审核、批量交付、迁移及回退演练等剩余 G1 项。回退保留原始录制、旧派生和
已提交标准包；新编码包仍是合法 gray12le HEVC，可由已验证的旧 reader 读取。
本阶段未执行 G2／G3、未操作 8088 业务数据、未启动相机或机器人运动。
