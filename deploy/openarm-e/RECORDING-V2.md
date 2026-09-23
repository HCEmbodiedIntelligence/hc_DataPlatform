# E 未切片录制接入

机器人 D 用现有 Bearer robot-ingest API 提交 CAPTURE_BUNDLE v2 / CONTINUOUS。提交事务写 `robot.recording.register.requested.v1` outbox，Worker 校验不可变标记、身份、对象回执和来源配置后，将同一对象登记为 continuous-recording/v2，不重传对象、不提前生成 episode。机器人所有权 API `/uploads/{upload_id}/recording` 返回等待登记/等待切片/已切片和页面路径。

平台 `/recordings/:recordingId/slice` 复用视频、传感器与 EDL 工作台。OpenArm 30 Hz 帧取整在保存时归一，Worker 切片按帧编号重新建立局部网格；既有来源字节不变。QC 检查实际 state/action、动作有效性和视频；对齐仍缺必需样本时拒绝写入训练数据。

Dataset episode ID 由完整 recording:episode 身份生成；一个录制的多个切片共享来源包溯源，不把来源包数量当 episode 数量。发布门禁使用真实 continuous QC、Lance 和媒体回执。导出从冻结切片取得任务描述，并包含来源录制/采集上下文、标注和视频；物化导出 revision 升至 lerobot-materialized-v3，保留原 v2 对象。

Lance 已提交但后续投影/回执失败时，commit adapter 可从历史版本恢复，不要求最新版本等于失败版本，也不依赖已清理的 staging。恢复验证源 hash、converter、schema、真实行数/有效性和媒体回执，不重复追加 Lance。已经失败退出的旧 Workflow 需要运维重放失败提交点；`recover-recording-v2.py` 是本次 E 的严格限定恢复记录，不能直接用于其他租户或机器人。

验收程序使用真实 E 服务及数据库；平台操作员由脚本注入 AuthContext，机器人上传走真实凭据认证。这不替代外部浏览器登录及手工切片验收。详细路径、数值和人工剩余项见计划目录 `integration/20260923/E-WORKFLOW-REVISION.md`。
