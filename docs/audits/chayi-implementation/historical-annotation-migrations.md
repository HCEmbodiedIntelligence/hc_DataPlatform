# 历史标注映射与独立审核演练

2026-09-30 至 2026-10-08，G1 历史数据库副本和隔离对象存储。输入是平台已有的
920 帧、30 Hz、四路 RGB 原生 LeRobot Episode，深度流为 0。源平台 8088、源对象
和机器人没有写入；G2/G3 尚未开始。这是实际历史数据的软件演练，不是真人验收。

## 结果与可查证证据

- [冻结映射、浏览器下载、全列核对及旧行审计](historical-annotation-migration-rehearsal.json)
- [禁网官方 LeRobot reader/DataLoader：全部 920 帧](historical-annotation-offline-reader.json)
- [迁移服务 18 项](historical-annotation-migration-tests.xml)、
  [详情/实际会话 HTTP 3 项](historical-annotation-detail-http-tests.xml)、
  [原详情和批次 CLI 回归 7 项](historical-annotation-detail-regression.xml)
- [前端协议/详情 21 项](historical-annotation-detail-frontend.xml)、
  [标准包关节读取/旧读取回归 9 项](historical-annotation-joint-window.xml)
- [最后下载响应复查](historical-annotation-final-route.xml) 是上述 HTTP 用例的重复执行，
  不重复计数；独立用例总数为后端 28、前端 30，全部通过，无 skip/xfail。

旧任务 `04ba1d3d-34cb-50b4-8a22-d4d332a92155` 固定 Lance 132、标注 r1，已有
提交和 APPROVE。与迁移来源 Lance 154 的全部 920 行及 fragment 哈希逐项相等，
保留完整旧引用；没有只替换逻辑版本来绕过固定引用检查。

映射 `am_d2f8e73b112e7ae9efc2cf44ac4d5bfde02ca167cb58107c99fe61fe90b94200`
先 PREPARED，后原子 APPLIED，重试返回同一收据。冻结完整旧任务、修订、操作、
提交、审核、幂等记录和 Schema；映射 JSON 10715 字节，SHA-256 为
`cb0a516818d36679ca529df58cf513cdfd896840585a946596a214239419bc20`。
新任务绑定真实 Episode `dv_2a1d2dce71faea009074a5415ed4fe6a34c1c21e69519c60de26bbcbb7887889`
及归档集合版本 156，`base_lance_version=NULL`。r0 为空，r1 恢复原 Tag ID、层级、
属性和半开区间 `[152,245)`、`[251,287)`；初态为未分配 DRAFT，旧批准不转移。

## 实际页面与完整交付

普通隔离账号经过真实网关/API，未模拟请求。1440 页面读取四路 RGB 和 29 个
具名关节，认领新草稿，将第一个 Tag 名称改为“1 · 隔离演练”，保存为 r2，再
执行预检查和提交。另一个普通隔离审核账号在 1024 页面独立批准，评论明确为
“G1 隔离软件演练，非真人标注验收。”该决定只属于新任务 r2。

实际页面证据包括 [1440 保存](history-browser/1440-native-tag-edited.png)、
[提交](history-browser/1440-native-annotation-submitted.png)、
[1024 审核](history-browser/1024-native-tag-review.png)、
[独立批准](history-browser/1024-native-annotation-approved.png)、
[1440 历史映射](history-browser/1440-native-mapping-history.png)、
[1024 历史映射](history-browser/1024-native-mapping-history.png) 和
[完整包已就绪](history-browser/1440-native-delivery-ready.png)。两个宽度的详情同时
显示当前标注通过、自动 QC 无报告、人工数据未审核，旧批准在来源卡片中单独显示。
[1440](history-browser/1440-native-selected-subtasks.png) 和
[1024](history-browser/1024-native-selected-subtasks.png) 已选交付轨道为 2 段，
无横向溢出。源 robot_id、物理相机数量和配置证据不足，页面如实显示具体原因，
没有伪造 3D 姿态或标定。

浏览器实际保存已审核 r2 JSON、两个宽度的历史映射 JSON 和完整 ZIP。完整标注
包为 25 文件、1028885407 字节；浏览器文件、普通 HTTP 下载和任务收据的 SHA-256
均为 `cdf7049aaf36930d3155120a43204e1b9caa69c4aa7e4264ca2cee4b3a06fbae`。
输出保留全部父数据列、源帧表和四路视频字节，仅增加 `subtask_index` 和
`hc.annotation.excluded`；两个分段为 93/36 帧，其余 791 帧编号为 -1。
禁网 LeRobot 0.6.1 官方 reader/DataLoader 用 TorchCodec 读取全部帧，未导入平台模块。

本历史 API 不附加通用 worker，避免恢复副本的旧队列触碰未镜像历史资产。此次
仅取新任务的实际已存 outbox 信封，调用生产修订文件/标注交付 handler；没有排空
原历史队列，也没有把 dispatcher 的全流程验收算入本证据。普通 G1 常驻 worker 的
队列链路证据另见 [标注交付](annotation-delivery.md)。

## 实现和复现入口

`backend/tools/episode_migration_annotations.py` 只接受显式操作员作用域与端点：
`prepare` 要求批次、旧任务、修订和完整 `LegacyEpisodeRef` JSON；`apply/status`
要求映射 ID；输出文件不覆盖。端点配置沿用 `episode_migration_batch.py` 的
`HC_EPISODE_MIGRATION_*`，无现网默认连接或 HTTP 操作员写入口。

源历史变更、完整数据不等价、未知 Tag 字段、Schema/提交哈希不符会拒绝。中断、
丢响应、并发 apply 均恢复同一映射和任务。SQL 0017 只允许 PREPARED→APPLIED，
要求实际已核验批次/COMMITTED 包和同事务独立草稿，拒绝改写/删除已应用收据。
集合回退保留新包、映射和任务，不重新激活成员指针。

详情下载路由要求 `annotation_task.read` 与 `export.download`，绑定完整数据引用，
核对对象大小/SHA 与数据库收据；撤权、跨组织、错版本、缺失/同大小损坏 JSON
全部拒绝。响应为 JSON attachment、private/no-store、nosniff。OpenAPI、客户端、
TS 编译和 Ruff 检查均通过。浏览器发现的标准包关节 422 已修正：标准包只请求
manifest 的实际列名，历史 Lance 仍使用原列名候选。

最后再次核验：154 个原任务、156 个旧修订、2 次原提交、1 次原审核及 154 个旧
Lance 版本保持原行哈希；新增标注行仅属于映射任务。集合头仍为空，两个新包
current 指针仍为空，旧读取及归档集合可访问。

## 未验收范围

此来源没有深度，因此不替代实际 RGB-D、深度源单位/profile、量化误差、码值
无损、HC 掩码、真实标定/同步或任何模型的深度训练验收。全部历史批次、三入口
完整实际样本、P06 业务发布、W8 综合台账和真人/真机条件仍继续；G1 尚未完成。
