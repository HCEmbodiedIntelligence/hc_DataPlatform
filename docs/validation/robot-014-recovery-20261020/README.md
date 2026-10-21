# 014 实际恢复与页面改版验证

2026-10-20；当前业务平台 `8088`，隔离平台 `5180`。

- `browser-ui.json`：真实业务浏览器只读验证。列表入口→配置导航→分页关节表→默认 v1→历史→上传记录→014 数据集→返回原记录。业务配置没有为浏览器测试新增版本。
- `browser-isolated-publish.json`：仅 synthetic 组织执行真实保存、实际 MCAP 验证、默认 v1/v2 发布及 v1 不变检查。
- `browser-upload-form.json`：真实网页选择本地 MCAP 并打开确认窗口，**未确认上传**；当前流程先选数据集存档，稍后处理时选择任务/机器人。
- `after-verification.json`：保护的成功 Episode 仍 READY / PASS / 114 帧 / version1，更新时间未变。两条旧失败处理快照未变；同源关联1条；任务上传2次、接收1次、成功1条、失败0次，QC通过1、待评估0。
- `014-inventory.json`、`014-approval.json`、`014-applied.json`：仅批准与执行1条已确认同源关联，历史配置候选执行0项。
- `published-config.json`：用户确认的19控制关节方案与真实样本验证证据，业务默认v1。
- `04` 列表入口，`05` 已填模板，`06` 两栏关节表，`07` 验证与发布，`08` 审计，`09` 上传，`10` 014数据集去重统计，`11` 返回原记录，`12–13` 隔离环境保存发布及不可变版本，`14–15` 实际手动上传入口与确认窗口。

后端本阶段24项通过，前端本阶段20个不同用例通过；最终配置/同源专项7项通过。完整 JUnit、SHA256 部署清单、此前原文件备份位于 `/home/hc_op/workspace/reports/robot-014-recovery-20261020` 和 `reports/robot-processing-config-20261020/recovery-014-*.xml`。业务数据库备份受限保存在报告目录，未加入文档仓库。完整18项验收及未验证边界见主实施记录。

操作步骤、部署范围及回退见 [操作指南](../../robot-014-processing-and-configuration-guide.md)。
