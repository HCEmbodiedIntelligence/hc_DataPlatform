# 两终端功能保留与消费核对

源 main `ccc47b7143845450f5dad0e4e5741c8ba5881fed`，A 分支
`integration/platform-two-a-20261008`。本轮独立候选项目
`hc-platform-two-20261008`，不改 48088、原业务数据或机器人。
交接与逐次证据在 `diagnostics/platform-decoupling-two-terminals-20261008`。

## 实时基线与交接

- `feat/chayi-g1` 的 `2840554b` 及 `cb00f3e7` 已在 main；不是功能验收证明。
- `feat/chayi-resume-20261008` 基于同一 main，仍有未提交工作。
  用户明确将两个集合发布后端文件及三个 P06 前端文件交给 A/B 接续，保留原工作区。
  精确源文件 SHA-256、只读快照及后端 patch 记在外部 `handoff.json`。
- A 只在自有树应用后端 diff；B 拥有前端。部署脚本、旧功能进度文件不随该 diff 引入。
- 实时复核原 48088 的 API/frontend 挂载 resume；worker 已退出且挂载 merge。
  本轮建立新 DB/MinIO/Temporal/API/frontend/worker 和新卷，不恢复旧 worker。

## 功能矩阵

| 功能 | 原/当前入口 | 新实现和实际消费者 | 本轮证据与未完成部分 |
| --- | --- | --- | --- |
| 原生/MCAP/LeRobot 摄取、失败原件下载 | 上传、robot-ingest、recordings | runtime resolver 冻结 Episode 计划，workflow commit；raw 授权独立 | 代码保留；统一 worker 的完整摄取回归待执行 |
| RGB-D 与 RGB-only、单位/掩码/映射、官方预览 | P06 EpisodeViewerShell | package reader、preview、官方 Colorizer | 代码保留；当前候选媒体/离线 reader 待复验；无采集深度不补造 |
| 固定数据人工审核、QC、独立标注审核 | P06/P08 | data_reviews、quality、annotation immutable binding | 保留版本和权限；本轮发布测试验证冻结审核及审核变化冲突 |
| P06 固定详情/文件/范围/完整包 | overview/files/authorize 与 package delivery | episode detail feature → episode_store | 实际代码消费存在；候选浏览器、下载读回待验证 |
| P08 区间、标注交付、Tag 派生与独立 QC | AnnotationDeliveryPanel/TagEpisodePanel | annotation delivery 和 Tag outbox handlers | 代码保留；必须验证真实 worker 完成、重试/取消及新条目 |
| P21 单包/批量/集合/RGB 子集/标注/原件 | PackageBatchPanel 与原件入口 | episode-deliveries；历史业务发布格式仍用 publishing exporter registry | 两种业务语义保留；worker 完成及官方读回待验证 |
| 历史读取、迁移回退与保留下载 | 显式历史版本/迁移记录 | legacy reader、migration inventory/batches | 仅保留兼容和隔离合成回归；不恢复历史批量迁移 |
| 集合发布 | main P06 仍走旧 Lance publisher；B 接续新 dialog | collection-releases preflight → publish → GET 固定 version_id | 本批 20 个真实 PG/MinIO/HTTP 与选择契约测试通过；B 页面接入/浏览器待验证，不算功能完成 |

集合发布新增显式 `episode_ids`（null 为旧行为，空数组明确选零项），未选择项返回
`NOT_SELECTED`；预检提供固定数据已审核的 `annotation_task_options`，供重新选择。
提交必须携带预检哈希；空集合、审核变化、旧哈希、同名不同选择不得发布。
省略选择字段的旧请求保持原幂等身份形状，已发布版本不重算历史哈希。
新集合发布不回退到 Lance 写入。

## 第一批验证

- `check-services.py --with-api tests/episode_store/test_collection_releases_services.py tests/episode_store/test_release_selection.py -q --junitxml=/results/collection-release-selection.xml`：20 passed，0 skipped。
- 覆盖预检无写、显式选择/空选/越界/重选、历史请求身份、三处中断、并发、审核变化、工作版本回退、名称碰撞、不可变数据库行、真实会话/撤权/401/403。
- runtime OpenAPI 重新生成，聚合检查、Ruff、502 文件架构门禁通过。
- 以上不是完整 P06/P08/P21 浏览器验收、真实传感器验收或 60 项架构完成证明。
