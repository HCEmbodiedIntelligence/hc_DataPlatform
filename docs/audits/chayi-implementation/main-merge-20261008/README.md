# 2026-10-08 本地主分支合并记录

用户先暂停整个 G1 → G2 → G3，再单独授权“合并进去吧”。本记录对应已有
平台/机器人代码的合并及必要修复，不恢复剩余开发、部署、真人或真机验收。
G1 仍未完成，G2/G3 没有执行。历史不再批量迁移；未采集深度保持 RGB-only。

## 合并基线与阶段提交

| 仓库 | 合并前 main | 待合代码/结果 |
| --- | --- | --- |
| hc_DataPlatform | 313f4fef | 既有 G1 至 cb00f3e7 已在 main；新增 2840554b 的集合发布 API 合并 |
| humanoid_manager | ba16c3e | main 已快进至 f101a9d |
| humanoid_camera | 0c0282a | main 已快进至 6918b4c |
| humanoid_motion_server | dce1eae | 无待合 G1 差异 |
| hc_teleop_recv | 506212d | 无待合 G1 差异 |

平台使用独立工作树 `/home/hc_op/workspace/hc_DataPlatform-chayi-merge-work`，
分支 `integration/chayi-g1-main-20261008`。执行输入三份文档均存在且与执行
工作树原件字节相同。合并保留 main 的解耦、原始录制和摄像头导入变更。
主分支发布投影接口已经迁入 dataset_registry；集合发布改用其正式版本 ID
函数及事务内 promote_current_release 方法，未恢复 publishing 的旧私有接口。
两个生成 TypeScript 文件有冲突，依据合并后的运行时 OpenAPI 重新生成解决。
新集合发布完成 HTTP/服务/契约，P06 集合发布按钮尚未接入，详见
[阶段范围](../collection-releases.md)。

## 实际验证

- 合并后后端 73 项通过，无失败、跳过或预期失败：集合发布 13 项、提交、集合、
  独立标注绑定、旧发布组合和架构约束。[XML](main-merge-backend-regression.xml)。
- 合并后前端 15 项通过：P06 页面、媒体源和数据集查询。
  TypeScript 无错误，API 生成文件 check 通过。[XML](main-merge-frontend-regression.xml)。
- 机器人录制/源深度单位/字段/官方 Colorizer/诊断 83 项通过。
  main 与已测试候选树完全一致。[XML](main-merge-robot-regression.xml)。
- 上传器最终完整回归 68 项通过，无跳过，[最终 XML](main-merge-uploader-complete.xml)。
  首次完整运行 67 项通过、1 项失败，
  原因为缺少测试 wheel 目录。补充独立 1.3.0 wheel 和固定依赖后隔离安装
  通过；wheel 包内容与候选源码逐文件字节核对一致。
  初次原始 [XML](main-merge-uploader-regression.xml) 和
  [补充隔离安装 XML](main-merge-uploader-isolation.xml) 保留。
- Ruff、源码及生成文件 diff 检查、架构检查通过。初次失败 XML 内 pytest
  的空白原样保留，源码 whitespace 检查排除此类原始 XML 附件。
- 测试只连接 G1 的独立 PostgreSQL/MinIO/gateway。合并候选隔离服务
  `/health/ready` 返回 200，[响应](isolated-readiness.json)。

## 运行环境及边界

8088 API 使用只读 bind mount 指向原主工作区，并运行 uvicorn --reload；因此
更新主分支工作区会被 API 自动加载。这不代表完成 G2 的数据库/机器人部署。
合并前 `/health/ready` 已返回 503，[原响应](8088-readiness-before.json)，
原数据库只记录 129 项迁移，与现有 main 所需迁移已不一致。
此次仅在 G1 隔离数据库应用 episode_store/0018_collection_releases.sql，
不修改 8088 数据库业务记录、迁移、旧存储或对象，不安装日常机器人，不启动运动。
后续正式部署须恢复目标后按 G2 核对和备份恢复流程实施。

合并只更新本地 main；没有 git push。候选和合并分支保留供追溯。
真实新录制 RGB-D、未接入的 P06 浏览器发布、W8 17 项 P0 缺少的真实/
真人条件仍未据此验收；可读性不代表未指定模型已支持深度训练。
