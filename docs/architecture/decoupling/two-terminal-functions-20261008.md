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
| RGB-D 与 RGB-only、单位/掩码/映射、官方预览 | P06 EpisodeViewerShell | package reader、preview、官方 Colorizer | 真实 preview 服务与离线官方 LeRobot 0.6.1 读回通过；无采集深度不补造；输入为合成数据 |
| 固定数据人工审核、QC、独立标注审核 | P06/P08 | data_reviews、quality、annotation immutable binding | 保留版本和权限；本轮发布测试验证冻结审核及审核变化冲突 |
| P06 固定详情/文件/范围/完整包 | overview/files/authorize 与 package delivery | episode detail feature → episode_store | 实际 HTTP/持久 worker 完整包与集合交付、官方 reader 读回通过；B 浏览器下载仍待验证 |
| P08 区间、标注交付、Tag 派生与独立 QC | AnnotationDeliveryPanel/TagEpisodePanel | annotation delivery 和 Tag outbox handlers | 实际 HTTP/持久 worker Tag 派生、独立 QC、交付、撤权回归通过；B 浏览器仍待完成 |
| P21 单包/批量/集合/RGB 子集/标注/原件 | PackageBatchPanel 与原件入口 | episode-deliveries；历史业务发布格式仍用 publishing exporter registry | 两种业务语义保留；持久 worker 与完整包/Tag/集合官方离线读回通过；浏览器仍待验证 |
| 历史读取、迁移回退与保留下载 | 显式历史版本/迁移记录 | legacy reader、migration inventory/batches | 仅保留兼容和隔离合成回归；不恢复历史批量迁移 |
| 集合发布 | P06 CollectionReleaseDialog（B 提交 3df9fc78） | collection-releases preflight → publish → GET 固定 version_id | 20 项服务/选择测试通过；普通账号浏览器预检、取消条目、201 发布、固定读取、刷新、同名幂等通过；浏览器审核变化/撤权仍待完成 |

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


## 已复核的第一批总集成

集成提交 `604bef6b4668978b6e7889004e511b9dbed6d652` 合并 A 后端
`2b68d4b7` 与 B 前端 `3df9fc78`。新增能力的生产消费者如下：

| 能力 | 实际消费链 | 验证边界 |
| --- | --- | --- |
| 不可变流写入 | Episode 提交、完整包和集合交付 → ImmutableObjectWriter | source hash/size、并发赢家、失败中止与真实 MinIO 读回；其他领域适配仍待迁移 |
| 应用实例隔离 | Episode 三组 router → request.app.state.episode_packages | 无本实例服务时 503；其他领域全局装配尚未全部拆除 |
| 历史 QC 规则 | quality evidence 和 Episode quality → 版本冻结 required rule set | v1/v2/v3 金样本和扩展枚举不改旧 JSON/hash；可插拔规则执行仍待实现 |
| 标注冻结 | annotation/collection delivery、Tag → AnnotationSelectionPort | 当前授权、固定修订/审核文件及内容校验；真实 API/worker 回归 |
| Tag 装配 | runtime 统一工厂注入 projection/QC/commit/publish/derive | 移除服务内跨域仓储创建；目录投影私有耦合仍待拆 |

该集成树重新执行：后端 168 passed，真实 PostgreSQL/MinIO/HTTP/Temporal
持久 worker 42 passed，前端 Vitest 1004 passed；均 0 skipped。
前端 typecheck、gen:api --check、54 项 architecture 和 mock=off 的 production
build:container 通过；后端 OpenAPI 聚合检查通过，runtime SHA256
`125732c98e2578f581e198ef81c338c892e34ef54dccc089c8d165e62f375206`。
原始结果在协调目录 `results/A-services/integration-batch-one-*`。
浏览器证据在 `evidence/B/p06-*`；58088 当前仍固定于 `3df9fc78`，与本地
集成树明确区分，不将本地合并视为运行版本升级。剩余浏览器验收、60 项中的
未完成项及真实传感器条件不在这些通过结果中。
