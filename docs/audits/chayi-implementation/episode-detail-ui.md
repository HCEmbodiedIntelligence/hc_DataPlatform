# G1 统一详情页面与单文件授权（2026-09-29）

P06 现有回放的“数据信息”入口已接入固定 Episode 聚合 API，包含回放与分段、
数据文件、数据质检、标注与审核、版本与来源五个页签。原共享回放器位于详情
抽屉外，保留相机选择、共享时间轴及机器人场景；旧存储仍使用原信息展示。
本阶段属于 W4/W5 的页面接入，不代表完整 G1 或浏览器验收。

- 主信息显示任务、机器人、日期、数据版本/标注修订、三类独立审核、当前时长、
  帧数、视频流数、明确选定轨道的任务分段数。缺少物理相机配对、父录制时长和
  任务要求的旧包明确显示未记录；精确纳秒以字符串计算秒，完整来源可展开。
- 标注页明确区分当前草稿、最近提交、历史批准与当前有效批准；新草稿不会继承
  历史修订的审核显示。多个任务要求显式选择，选择后保留当前页签。原 P08 编辑、
  提交和审核入口继续可用。人工数据审核提交后刷新聚合状态。
- 文件页分页读取已提交清单，显示分类、路径、大小、哈希、可用状态和受限原因，
  失败可重试。新增单文件 POST authorize 重新检查权限、清单身份与文件可用性，
  校验后签发 300 秒链接，由浏览器直接下载，不把大视频整体加载到前端内存。
  授权与租户、完整数据版本、文件 ID/哈希/大小绑定，签名 URL 不写审计或报告。
  撤权拒绝新签发；已签链接在到期前仍可能使用，不声称即时撤销。
- 深度沿用官方 RealSense colorizer 的预览与既有物理像素读取；没有新增调色算法、
  主深度重编码或源精度副本。实际 profile、源单位、掩码和误差可展开查看。

## 验证与范围

- [后端 XML](episode-detail-ui-backend-tests.xml)：6 项通过，含普通账号真实网关
  签发、对象存储边缘实际下载与 SHA-256/attachment 检查、未登录/跨组织拒绝、
  撤权后的热缓存拒绝；其余聚合、不同集合标注任务、缺失文件等测试再次通过。
  测试容器通过内部 DNS 连接边缘，保留公共 URL 的签名 Host；首次直接访问容器
  回环地址失败，改正测试路由后全部通过。没有修改生产 URL 或放松授权。
- [前端 XML](episode-detail-ui-frontend-tests.xml)：17 项通过，覆盖固定引用与范围、
  交叉版本拒绝、签名文件身份、纳秒精度、页面文件授权、多任务显式选择及保留
  标注页签、新草稿与旧审核区分、缺失来源、无权限不请求、人工数据审核与原
  Viewer 回归。组件/API 使用替身，jsdom 补 ResizeObserver；不是实际浏览器证明。
- TypeScript、ESLint、Ruff、Prettier 和生成客户端检查通过。runtime OpenAPI
  SHA-256 `0be9bd9c48022d53ffa599a63e78408979e8e585f3b701750f04cf4789a03c24`。

本阶段没有新增真实录制、离线 reader、1440/1024 浏览器或真人证据。尚需完成
完整包就近下载的持久打包/缓存/进度入口、RGB 子集明确排除深度、批量集合、
来源生产者补全、跨入口跳转和 W7/W8 工作。单文件下载不能代替完整标准包交付。
默认 P06 时间轴仍是原回放；取值帧单独选择，点击播放视频自动冻结取值仍待实现。
17 项 P0 保持 Pending，未指定模型的深度训练支持仍未验收。

复现：

```bash
bash deploy/chayi/compose.sh run --rm test tests/episode_store/test_detail_services.py -q --junitxml=.g1-artifacts/episode-detail-ui-backend.xml
bash deploy/chayi/compose.sh run --rm frontend-test vitest run src/pages/p06-dataset-detail/episode-detail.test.ts src/pages/p06-dataset-detail/EpisodeDetailPanel.test.tsx src/pages/p06-dataset-detail/DataReviewPanel.test.tsx src/pages/p06-dataset-detail/ViewerShell.test.tsx --reporter=default --reporter=junit --outputFile.junit=.g1-artifacts/episode-detail-ui-tests.xml
```

无新增数据库迁移。未来 G2 使用匹配的 API/前端及既有官方 SDK 镜像、146 项迁移；
本阶段只在 hc-chayi-g1 隔离服务实施。回退页面/API不删除包、预览或扩展表。
8088 业务数据、旧存储和机器人未操作，未执行 G2/G3。
