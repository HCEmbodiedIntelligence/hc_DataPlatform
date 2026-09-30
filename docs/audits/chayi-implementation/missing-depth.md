# 缺帧与全无效深度区间：独立包、集合及浏览器

2026-09-30，G1 隔离服务。此阶段补充 W2/W3/W5/W6；所有输入均为合成 RGB-D
夹具，未新增真实采集、真人验收或模型深度训练证据。未修改 8088 业务数据。

## 行为与数据含义

- Tag 选定区间可以没有源深度帧，或有源帧但没有有效深度像素。完整独立包仍
  保留深度 MP4、实际 HEVC Rext/gray12le profile、掩码及源帧映射。分别记录
  `NO_SOURCE_FRAMES`、`NO_VALID_PIXELS`，不补造源序号、单位、物理值或误差。
- 码值零仍是占位值；官方 reader 返回的有限填充值不能视为测量。独立 HC 掩码
  应用后返回 NaN，像素 API 返回 `valid=false,value=null`。全缺帧的数值有效性
  列必须为 false。主包验证器核对实际掩码/帧映射，拒绝假造状态和统计。
- 无有效像素时不写该深度特征的 min/max/mean/std/count；视频元数据明确记录
  `hc.stats_valid_pixels=0` 与 `hc.stats_status=NO_VALID_PIXELS`。官方数据读取已
  验证；依赖归一化统计的下游必须处理缺测，不能把此次读取测试当作训练适配。
- 完整区间直接复用视频字节，裁剪继续复用已量化码值并做码值无损编码。未改变
  量化参数、实际 profile、官方 RealSense SDK Colorizer 或原始录制。
- 集合允许缺测与有效 Episode 共存，逐条保留缺测状态和全部媒体/证据。汇总
  深度统计只使用有效像素；全部缺测时不写深度统计。分 Episode Parquet 中
  缺测统计为 null，避免 Arrow 仅按第一条记录推断而丢掉后续有效统计。
- 独立 QC 继续既有八类风险策略：缺帧样例为 RISK；有帧但全部像素无效的样例
  可为 PASS。页面另行明确物理测量不可用；QC PASS 不表示深度有效、人工数据
  审核通过或标注审核通过。

## 实际执行证据

- 后端 **57 个唯一用例最近执行通过**，无 skip/xfail：
  [37 项基础/服务](missing-depth-tests.xml)、[4 项 HTTP/缺帧窗口](missing-depth-http-tests.xml)、
  [9 项完整性复查](missing-depth-integrity-tests.xml)、[11 项集合](missing-depth-collection-tests.xml)。
  前三份去重为 46 项，集合新增 11 项。保留既有 Starlette/httpx 弃用提示。
- [10 项前端回归](missing-depth-ui-tests.xml)、TypeScript、ESLint、Ruff/格式检查
  通过。此阶段没有新增 SQL 迁移或正式 OpenAPI 字段；深度详情扩展已有 JSON。
- [普通 HTTP/常驻 worker 证据](missing-depth-http-results.json)：固定 Tag 创建、
  独立 QC/空标注修订、官方 SDK 黑色 PNG、无效像素与完整 ZIP 均核验；撤权和
  跨作用域拒绝未放松。父包保持有效帧及原始统计，子包缺测不改变父包。
- 服务输出和实际 HTTP 下载分别通过禁网官方 LeRobot 0.6.1 reader/DataLoader，
  未导入平台包，独立应用 HC 掩码并核对全部文件哈希与来源：
  [服务包](missing-depth-offline-reader.json)、[HTTP 包](missing-depth-http-offline-reader.json)。
  每份 3 帧、16×16 深度中 768 像素均无效；官方未知 HC 字段提示保留。
- [集合离线证据](missing-depth-collection-offline-reader.json)：两份双 Episode、
  8 帧集合（全缺测/混合），官方跨 Episode batch、RGB 像素/边界、全部数值、
  重建统计、HC 掩码/来源和视频哈希核验。未重新编码或量化。

## 实际浏览器与完整包

独立 Firefox 普通账号，经隔离 48088 网关/API/常驻 worker，P08 创建缺帧 Tag
子 Episode，再打开 P06 统一详情。1440×1014 与 1024×1014 两种 viewport 均
无横向溢出；没有拦截请求或伪造响应。截图为实际页面，部分是完整页面截图。

- [创建](tag-browser/missing-1440-created.png)、[播放](tag-browser/missing-1440-child-viewer.png)：
  RGB 和官方深度预览均 readyState=4，无播放错误；深度为黑色。
- [1440 像素取值](tag-browser/missing-1440-depth-pixel.png)、
  [1024 像素取值](tag-browser/missing-1024-depth-pixel.png)：点击实际 SDK 生成
  的深度 PNG，像素 (8,7) 返回“无效，无物理测量值”“源帧缺失”。
- [1440 QC](tag-browser/missing-1440-qc.png)、[1024 QC](tag-browser/missing-1024-qc.png)：
  明确当前 Tag 已对齐区间的检查范围；RISK，pass 18/warn 3/fail 0/skip 5/info 12。
  数据和标注人工审核均未审核，子标注修订为 0。
- [1024 完整包准备](tag-browser/missing-1024-download-ready.png)：真实队列完成
  30/30 文件、150837/150837 原文件字节，下载按钮可用。没有执行浏览器保存
  ZIP 到操作系统的动作；随后用同一普通账号授权 HTTP 下载验证实际归档。
- [浏览器任务/下载收据](missing-depth-browser-receipt.json)与
  [该 ZIP 的禁网 reader 结果](missing-depth-browser-offline-reader.json)：ZIP
  156077 字节，SHA-256 `93bbd16a00d788036407db464caf7fb053ca84660cd6906af7a9309a124bb978`，
  全文件清单/profile/掩码/帧映射一致。收据不含会话令牌或签名 URL。

## 复现与后续

```bash
bash deploy/chayi/compose.sh run --rm --no-deps test tests/episode_store/test_missing_depth_services.py tests/episode_store/test_depth_window.py tests/episode_store/test_writer.py tests/publishing/test_depth_contract.py tests/episode_store/test_tag_jobs_http.py tests/episode_store/test_collection_export_services.py --fail-on-skipped --fail-on-xfailed -q
# 使用审计 JSON 中记录的实际输出目录；reader 容器禁网。
bash deploy/chayi/compose.sh run --rm --no-deps reader /app/tools/read_complete_delivery.py /app/.g1-artifacts/missing-depth-browser/complete.zip /evidence/missing-depth-browser-offline-reader.json
```

G2 必须同时保留认识缺测标记的主包验证器、集合生成器与页面；旧的“统计必有值”
实现不能承担这些包的完整回退功能。W7 工具/隔离副本、W8 汇总、真实样本/规模、
三入口完整浏览器及真人/真机条件继续。没有执行 G2/G3，没有把任何完整 P0 标为完成。
