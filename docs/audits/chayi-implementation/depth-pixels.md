# G1 官方着色帧与物理像素取值（2026-09-29）

本阶段补充 W5：P06 数据信息中选择已提交数据版本的深度相机和取值帧，在官方
RealSense 着色 PNG 上点击，或输入从 0 开始的原深度坐标，读取量化反解后的米值。
取值帧由用户明确选择，与播放器共享时钟的连续深度预览分别使用；打开卡片或换帧
不重置原回放。尚未实现点击正在播放的视频自动冻结该帧。

显示继续调用官方 `pyrealsense2.colorizer` Dynamic/Jet/直方图均衡，没有新增调色
算法。颜色按帧动态分布，页面说明距离以物理取值为准。PNG 的临时 Z16 输入只在
内存存在；没有重编码主 H.265、重做主量化或新增源精度主副本。

## 固定版本与坐标

- 新 `GET .../data-versions/{data_version_id}/depth/{feature}/{frame_index}/pixel?x=&y=&unit=`
  使用既有主包解码/反量化与 HC 掩码。返回完整 DataVersionRef、帧、原图尺寸、坐标、
  单位、有效性、上下饱和标记、量化 profile 和源帧映射。无效像素返回 JSON null，
  不用灰度码 0 推断无效。纳秒时间保持十进制字符串。
- 保留 m/mm 两种读取，页面请求 m；既有 mm 反量化包含取整，不能用它冒充更多小数
  精度。页面提示 12 位量化及工作距离，小数位数不表示传感器精度。
- 坐标明确为 `depth_native`。页面不做 RGB 空间注册，也不暗示同位置 RGB 像素对应
  同一空间点。图像按比例缩放，点击转换为原分辨率坐标，右/下外边界不接受。
- 每次读取要求 dataset_version.read 和既有组织/项目/区域/版本归属，no-store；
  越界拒绝。前端核对完整固定版本、帧、通道、坐标、单位和掩码，拒绝混用结果。
  预览 object URL 在换帧或卸载时释放；请求错误时不显示旧读取结果。

## 已执行的验证

- [后端 XML](depth-pixel-backend-tests.xml)：4 项通过，包含两项实际 PG/MinIO 读取、
  一项普通会话真实网关 HTTP、官方 SDK 色彩逐像素对照。实际验证顺序/逆序深度读取、
  像素与 NPZ 数组一致、无效 null、越界422、未登录401、跨组织403及撤权后热缓存403。
- [HTTP 记录](depth-pixel-http.json)：同一已提交 RGB-D 包完整下载逐文件哈希核对，
  重复下载字节一致；记录真实返回的 2 号帧 (1,1) 像素值、源映射和量化配置。
  测试普通账号初始权限用隔离数据库设置，实际请求无认证替身，结束已注销。
- [前端 XML](depth-pixel-frontend-tests.xml)：13 项通过，含8项坐标/协议、2项卡片和
  3项原 Viewer 回归。检查明确换帧、无权限不请求、缺帧/无效显示、blob URL 释放和
  版本/帧/单位/掩码不一致拒绝。组件和 API 响应使用替身，不是实际浏览器验收。
- TypeScript、ESLint、Ruff、Prettier、生成客户端检查通过。正式及 runtime 契约
  同步，runtime SHA-256 为 `9e0a928d2328528d40592c1a57bf9a702b8703ac3ff5e1ba839c30a8536e3730`。

实际服务输入为 4 帧 16×16 软件夹具，不是真实传感器录制。本次没有新增离线官方
reader/DataLoader、1440/1024 浏览器、真人或训练适配证据。后端只有读取接口，没有
新增数据库迁移。隔离 API/worker 已使用候选代码，8088 和机器人未操作。

复现命令：

```bash
bash deploy/chayi/compose.sh run --rm test tests/episode_store/test_readers_services.py tests/episode_store/test_http_services.py tests/episode_store/test_realsense_colorizer.py -q --junitxml=.g1-artifacts/depth-pixels.xml
bash deploy/chayi/compose.sh run --rm frontend-test vitest run src/pages/p06-dataset-detail/depth-pixels.test.ts src/pages/p06-dataset-detail/DepthPixelPanel.test.tsx src/pages/p06-dataset-detail/ViewerShell.test.tsx
```

将来 G2 需要匹配的 API/前端及已固定的官方 SDK 镜像；本阶段没有执行 G2/G3。
应用回退只移除新增入口，不改变已提交主包。此前数据库扩展继续保留。统一详情、
完整/批量下载任务、标签新 Episode、W7 和 W8 剩余工作继续，17 项 P0 仍为 Pending。
