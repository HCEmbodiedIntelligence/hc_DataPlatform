# 共享采集方案与任务版本绑定交付

2026-10-21。实现位于 `implementation/shared-capture-schemes-20261020` 隔离分支，已带入原工作区既有未提交基线，再合入前端、处理、机器人三个终端的交付。原运行目录未覆盖，未部署到业务服务。

## 最终行为

- 组织共享方案支持草稿、完整样本验证、不可变发布、归档；实例只保存关联和自己的映射版本。方案库、接入、任务表单保留上下文返回。
- 任务固定发布版本和各参与实例映射；录制开始由服务端按期望修订冻结快照。GET 仅查询；并发换计划与开始录制受同一事务约束。合法在途任务不随新版本或默认项变化。
- 机器人任务、录制元数据、capture-context、封存描述、队列、上传及普通重试沿用同一快照。网页上传独立冻结服务端 `WEB_UPLOAD_PLAN` 来源。
- MCAP 与 LeRobot 均按完整实际文件校验结构，保留 15 Hz、167 帧、独立动作/状态轴及缺失语义。含图像 MCAP 的显式关节时钟在序列化后保留；旧默认 RGB 配置的序列化保持兼容。
- 数据发布和标注分开。合格数据可先入库、读取、导出，标注待配置/失败独立显示；单独重试标注不重复提交数据。网页、项目和机器人查询通过精确数据版本读取当前标注交付，不把数据可读推断为可训练。
- 旧 OpenArm 与新共享 capture-context 按明确版本分派，新文件上下文须与服务端快照一致；兼容读取旧回执和回放旧 Temporal 历史。
- 新增 027/028/029 向前迁移、旧配置到共享草稿的幂等迁移工具、来源引用和审计；不自动发布旧候选，不改历史归属或已成功成果。

## 验收

完整证据位于 `/home/hc_op/workspace/reports/shared-capture-schemes-20261020`。

| 检查 | 结果 |
| --- | --- |
| 主分支后端/真实数据库/架构/工作流回归 | 238 通过，0 跳过；基础设施处理用例另行运行 |
| MCAP 纯关节、带 RGB 完整处理及回归 | 6 通过；实际 PostgreSQL、MinIO、Temporal、读取、导出与回放 |
| LeRobot 完整处理 | 4 种代表性场景通过，见最初 5 项处理报告；标注当前状态补充场景通过 |
| 同实例关节与视觉方案、完整样本校验 | 6 通过 |
| 时钟/精确回执与工作流补充 | 23 通过 |
| 前端相关回归 | 主套件145、上传补充85、方案最终33通过；集合有重叠，不累加 |
| 机器人回归 | 172 通过、7 跳过 |
| 实际浏览器和机器人 HTTP 交接 | 均通过，输入边界见验收矩阵 |
| 静态检查 | 变更 Ruff/格式、新读投影 strict mypy、架构、OpenAPI、生成类型、前端类型与构建通过 |
| 隔离迁移账本 | 169/169，无缺失、未知迁移或 checksum drift |

逐项场景和限制见 [25 项验收矩阵](/home/hc_op/workspace/reports/shared-capture-schemes-20261020/acceptance-matrix.md)。机器人 3 项缺少 rclpy、4 项缺少官方 LeRobot writer 环境而跳过；实际交接中的官方 writer 输出由真实文件夹具提供，未执行机器人运动。额外旧 OpenArm roundtrip 3 项未启用，不计为通过。

## 历史数据与后续环境操作

6 条真实 Raw 的原文件已校验并完整解码，只读确认成功、丢弃和重复状态。任务 14/16 的新视觉样本为字节重复，且原文件缺少可信的完整单位依据；没有擅自补 rad、改归属或重试。

- [逐条盘点及恢复建议](/home/hc_op/workspace/reports/shared-capture-schemes-20261020/migration-and-recovery.md)
- [正式重新导入操作单](/home/hc_op/workspace/reports/shared-capture-schemes-20261020/historical-reimport-runbook.md)
- [发布、在途兼容和回退](/home/hc_op/workspace/reports/shared-capture-schemes-20261020/release-and-rollback.md)
- [架构未完成项审计](/home/hc_op/workspace/reports/architecture-audit-20261021/architecture-audit.md)

正式签名制品、业务部署、生产迁移 apply、真实设备官方 writer 验证和历史重新导入尚未执行。原架构清单的其他待办继续存在；共享方案功能完成不等于整个平台架构改造完成。
