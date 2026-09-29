# G1 完整 RGB-D 包下载任务与独立包批量 ZIP（2026-09-29）

固定 Episode 数据版本现在可以从 P06 详情准备完整下载，查看归档/上传进度、
取消、重试及下载已有归档。后端还支持一次冻结 1–100 条同租户/项目/地域数据
引用，生成每条均可独立读取的批量 ZIP。本阶段覆盖 W5/W6 的完整包交付部分，
批量选择页面和可跨 Episode 直接训练读取的重组集合尚未接入。

## 交付与授权

- 单包根目录为 `episode/`，批量为 `episodes/000000/` 等；外层 `delivery.json`
  明示 `is_reassembled_collection=false`。每条包含数值、全部已采集 RGB/深度视频、
  官方元数据、HC profile/掩码/源帧/误差、原 package manifest 与 commit。
  逐文件核对 SHA-256 和大小后逐字节归档，没有重复量化、转码或写入 Lance。
- 计划冻结完整 `DataVersionRef`、manifest 哈希、归档格式与处理器/reader 版本，
  按数据版本排序生成内容身份。同一选择改顺序或使用新的请求键仍复用相同归档；
  同请求键改选择冲突。这里保留标准包自身文件；指定标注修订仍走 P08 标注交付，
  不自动拼接“最新标注”，不混淆单包、独立包批量与重组集合。
- PostgreSQL 命令、请求幂等记录、审计与 outbox 同事务提交。worker 按 generation
  和租约执行；取消使旧 worker 的后续写入失效，失败/取消后可并发安全地重试。
  已完整上传但未写 READY 的任务，在租约恢复时核对已有归档后完成，不重建归档。
- ZIP64 归档采用有界读缓冲；分片上传使用内存上限和临时文件。任务分别报告归档
  文件/字节与上传字节。API 为创建、列表、状态、取消、重试和下载授权提供正式契约。
  P06 使用下载授权链接直接交给浏览器，不把 ZIP 整体读入前端内存。
- 每次 API 访问重新检查精确范围及 `dataset_version.read`、`export.download`；
  创建/取消/重试还要求 `export.create`。后台从已持久化且范围一致的 outbox 命令
  执行。READY 缓存复用和下载前校验归档哈希；下载授权有效 300 秒。撤权后拒绝
  新授权，已签发链接仍可能有效至到期，不声称即时撤销。
- 深度显示继续使用官方 RealSense SDK Colorizer。保存的 gray12le HEVC、实际
  profile 和物理像素读取不变；下载深度不是浏览器着色视频的替代文件。

## 实际验证

| 检查 | 结果和证据 | 范围 |
| --- | --- | --- |
| 完整交付及标注交付回归 | [13 项通过](complete-delivery-backend-tests.xml) | 真实隔离 PostgreSQL/MinIO；完整包 7 项、既有标注交付 6 项 |
| 迁移清单 | [25 项通过](complete-delivery-migration-tests.xml) | 0013 为追加迁移；隔离库 147/147 current，无漂移 |
| 页面/API 回归 | [18 项通过](complete-delivery-frontend-tests.xml) | jsdom/接口替身；不算浏览器流程 |
| 普通账号 HTTP + 常驻 worker | [实际交付记录](complete-delivery-http.json) | 普通登录、网关创建、常驻 worker READY、签名对象实际下载、撤权拒绝 |
| HTTP 下载 ZIP 离线读取 | [官方 reader 报告](complete-delivery-http-reader.json) | LeRobot 0.6.1、DataLoader、网络禁用、无平台 import |
| 两个独立 Episode 批量 ZIP | [官方 reader 报告](complete-delivery-batch-reader.json) | 每个根独立读取并核对 HC 掩码；不是重组集合验证 |

服务测试包括每个归档成员的内容核对、禁止重复量化、重复/乱序选择缓存复用、
四路并发创建/重试、取消对旧租约的隔离、上传错误、完整上传后崩溃、租约过期、
伪造数据引用/outbox 拒绝、跨租户/撤权拒绝、损坏归档拒绝。
故障注入在真实存储服务外围进行，不冒充真实采集故障。

实际 HTTP 归档 SHA-256：
`db891f561f2eb7050e0faae358f671b012c52b2d82c3579f8c2fd4710326afd5`。
两条独立包的批量归档 SHA-256：
`9b5867a6ec9cb028d3d2459bccdaeadb4577c55e66cb420b4817728d446edb38`。
这些输入每条为 4 帧合成软件夹具；RGB/深度/数值可读且包文件哈希一致。reader
结果独立应用 HC mask，无效值置 NaN，核对源映射；官方整毫米输出与 profile
反量化结果最大差约 0.4491 mm。此值验证 reader 取整，不是源传感器量化误差。
原量化误差与视频码值无损检查保留其各自证据，未合并为一个“无损”结论。

TypeScript、ESLint、Ruff、Prettier 和生成客户端一致性检查通过。先前类型检查
发现可选 scope 字段未缩窄，已改用经过范围核对的固定身份字段并复查通过。
runtime OpenAPI SHA-256：
`2ad89bc178e6e0891dd39e2f590f6fadd3872f3bd5d0d96f38bfc1106da7bd14`。

复现命令使用 `deploy/chayi/compose.sh` 指向专用服务；故障测试应独占对应 fixture，
有意控制租约的测试不与其他测试 worker 竞争；HTTP 用例必须运行常驻 worker。

```bash
bash deploy/chayi/compose.sh run --rm test tests/episode_store/test_complete_delivery_services.py tests/episode_store/test_annotation_delivery_jobs_services.py -q --junitxml=.g1-artifacts/complete-delivery.xml
bash deploy/chayi/compose.sh run --rm test tests/core/test_migrations.py -q --junitxml=.g1-artifacts/complete-delivery-migrations.xml
bash deploy/chayi/compose.sh run --rm frontend-test vitest run src/pages/p06-dataset-detail/complete-deliveries.test.ts src/pages/p06-dataset-detail/CompleteDeliveryPanel.test.tsx src/pages/p06-dataset-detail/EpisodeDetailPanel.test.tsx src/pages/p06-dataset-detail/ViewerShell.test.tsx --reporter=default --reporter=junit --outputFile.junit=.g1-artifacts/complete-delivery-ui-tests.xml
bash deploy/chayi/compose.sh run --rm reader /app/tools/read_complete_delivery.py /app/.g1-artifacts/complete-delivery-http/delivery.zip /evidence/complete-delivery-http-reader.json
```

## 未验收与后续部署条件

尚未完成重组集合、P21 批量选择、RGB 子集排除深度提示、真实 1440/1024 浏览器、
真实录制/真人角色、Tag 派生 Episode 身份、来源生产者补全及 W7/W8 剩余工作。
大包容量/长时间压力未验收；进程在分片上传中途硬退出的孤立分片清理/续传未验收，
已验证的是普通异常清理和完整对象已上传后的恢复。损坏 READY 缓存会拒绝下载，
不自动修复不可变归档，需要先恢复对象。列表 API 可分页，当前详情页显示最近
25 项关联任务。17 项 P0 保留整体 Pending，模型深度训练支持为 NOT_VALIDATED。

未来 G2 必须一起部署匹配的 API、worker、前端和 147 项迁移，以及既有官方 SDK
镜像/固定 reader 依赖；worker 需注册 `episode.delivery.requested.v1`。先验证普通
账号范围/能力和对象下载入口，观察 outbox、租约及归档进度。回退时先停止新任务
入口并处理在途任务，使用能识别新事件的 worker 完成或取消；保留新表、归档和
标准包，不能让不认识该事件的旧 worker 直接消费。旧同步完整下载接口继续兼容。
本阶段仅修改独立分支及隔离服务；未操作 8088 业务数据、旧存储或机器人，未执行
G2/G3。这是可审阅的阶段实现，不是整个 G1 可部署候选已经验收的声明。
