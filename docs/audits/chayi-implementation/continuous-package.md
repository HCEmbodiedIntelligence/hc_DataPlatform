# 连续录制切片 → 独立 RGB-D 标准包

新派发的连续 Episode 通过 `continuous-episode-package/1` 生成独立 LeRobot v3 包。
创建时机是平台确认切片、QC 通过并完成对齐之后；上传和 Raw 登记保留原录制，
尚不代表已经生成独立 Episode。包提交、官方 RealSense 预览、集合、Tag 任务和
详情投影完成后，既有数据库约束才允许 READY。

## 实现与边界

- 新 resolver 使用 Episode schema，既不申请 Lance reservation，也不提交 Lance。
  已持久化的历史 workflow 输入默认保持原 Lance 分支，历史 reader 兼容不变。
- 冻结计划包含全部原录制资产位置/大小/哈希、QC、配置、schema、切片网格和编码
  依赖版本。worker 读取输入前核验完整租户、已提交上传、切片账本和全部资产，
  不使用要求 READY 的公开下载授权，消除提交前读取与 READY 的循环。
- 已知 OpenArm `/3`、`/4` 派生使用其明确的生产者契约识别深度；一般录制需要
  `hc-recording-media/1` 的显式声明，不根据 H.265、文件后缀或相机名称猜测深度。
  `recording-media.json` 的 `cameras` 必须覆盖配置中的 camera_id，每项包含
  `kind: rgb|depth` 和 `video_path`；深度还需 `evidence_path`，其目录必须包含
  `profile.json`、`frames.parquet`、`report.json`、`validity/flags.parquet`。
  文件必须都在已提交资产清单里，profile/视频哈希/来源单位继续完整校验。
- 采用采集派生证据里的精确 `source_t0_ns`。本样本与录制墙钟起点相差
  **26,032,068 ns**，两者都记录，不能把微秒墙钟当成真实第一帧时间。
  一般窗口按绝对有理数位置选择源帧，避免先舍入窗口起点导致半帧偏移。
- 同网格整流字节复用；深度切片只选择已有 12 位码值、掩码和来源映射，随后无损
  编码，不再量化。窗口实现版本为 `hc-depth-window/2`，沿用
  `hc-hevc-gray12-lossless/2`。主深度为 MP4/gray12le，实测 profile **Rext**。
  原量化误差与这次码值编码无损分别记录。RGB 切片复用现有 H.264 编码实现；
  符合整流契约的 H.265 RGB 可直接复用，原录制始终保留。
- 源帧证据按窗口复制到 `hc/recording/source_frames.jsonl`；已声明标定文件原字节
  纳入包，未验证正确性显示 `PROVIDED_UNVERIFIED`，缺少标定显示 `UNKNOWN`。
- 深度预览调用官方 `rs.colorizer`，使用既有 SDK 2.58.4 Dynamic/Jet 配置；六路
  预览是独立显示产物，单通道深度主文件和参数不因可视化改变。
- 新工作流 ID 的 v2 身份覆盖组织/项目/地域/录制/Episode，修复跨组织同名冲突。
  保留旧持久化 ID；派发必须核对账本中的实际 ID。READY/QC_FAILED 后的重复事件
  按相同组织、切片版本和工作流终态确认完成，不重新处理，错误绑定拒绝。

## 已执行证据

1. **2 项实际服务测试通过**：一个直接服务场景，一个真实隔离 Temporal 场景。
   二者均经 RobotIngestService 分片上传/提交、Raw 桥接登记、真实切片草稿/确认、
   QC 和对齐服务生成 6 帧、3 RGB + 3 深度包。未使用 HTTP 或已安装 uploader；
   原始输入明确为软件合成，不能替代机器人上传或真人操作。
2. **追加 1 项 Temporal 派发负向测试通过**：读取实际 outbox 信封，生产 handler
   启动真实 workflow。提交结果丢失后 activity attempt 1→2 恢复，历史重放通过；
   完成后再次投递取回终态，错误组织和 finalized_revision 被拒绝。
3. 提交完成后禁止读取暂存 Arrow，三个并发重试仍取得相同包/集合/详情。篡改窗口、
   缺失资产拒绝。整个 Episode 处理阶段将深度 quantize/原深度 write 改为抛错，
   仍然成功，证明未重复量化。历史 catalog 在新路径中是禁止调用的断言对象。
4. **56 项回归通过**，覆盖窗口码值、掩码、缺帧/重复/饱和/误差、writer、连续录制
   与基础工作流。增加任意半帧起点和旧整数起点兼容性检查。
5. **7 项工作流 Temporal 回归、2 项历史连续录制流程通过**。后者使用测试
   Temporal 服务和内存目录，生成四路 60 秒 720p 视频，不是新 RGB-D 真实长录制。
   旧测试原断言要求第三次 catalog 写入，已改为核验第三次 activity 从已提交
   回执恢复，catalog 两次尝试（首次故障、第二次成功），无重复版本。
6. **断网官方 reader/DataLoader 通过**：LeRobot 0.6.1，未导入平台模块，三路 RGB
   和三路单通道深度全部读取；逐文件校验完整包，独立应用 HC 掩码，核对源/目标
   帧与单位。官方 reader 输出 mm；对独立反量化结果的舍入差最大
   **0.48777683 mm**。原量化报告最大误差 **0.0005244789 m**；本次编码码值无损。
   此切片没有无效像素/缺帧，不能据此声称覆盖这些场景；其故障证据来自独立窗口
   测试及之前的 [窗口验证](depth-window.md)。未验证任何模型支持深度训练。
7. 只读服务审计再次核验 **30 个包文件、14 个未变的原文件、6 个 READY 预览**；
   对象内容哈希一致，当前数据集实际 Lance 版本数 **0**。恢复的隔离 worker
   已确认该 outbox 投递，实际 workflow job 为 SUCCEEDED；审计含时间和 run_id。
   API/worker 健康，迁移 **143/143 current**、无 drift。早期中断的另一个合成
   用例保留 TECHNICAL_FAILED 记录，没有把该失败改写成成功或删除其原件。

证据：[服务及对象审计](continuous-package.json)、[离线读取](continuous-package-offline-reader.json)、
[服务 XML](continuous-package-services.xml)、[派发 XML](continuous-package-dispatch.xml)、
[回归 XML](continuous-package-regression.xml)、[工作流 XML](continuous-package-legacy-temporal.xml)、
[历史连续录制 XML](continuous-package-legacy-vertical.xml)。旧组合测试曾手动中断，
随后按两个完整文件分别运行成功；中断不计成功证据。Ruff 与 diff 检查通过。

```bash
# 服务测试由测试进程消费新切片，先暂停隔离 worker，完成后恢复。
bash deploy/chayi/compose.sh stop worker
bash deploy/chayi/compose.sh run --rm test tests/episode_store/test_recording_package_services.py -q
bash deploy/chayi/compose.sh start worker
# 使用服务测试产生的目录；reader 容器 network_mode=none。
bash deploy/chayi/compose.sh run --rm reader /app/tools/read_continuous_rgbd_package.py \
  /app/.g1-artifacts/continuous-package/raw-pin-a838e66279254a00bc3be7eb55026442
bash deploy/chayi/compose.sh run --rm --entrypoint python test tools/audit_continuous_package.py \
  /app/.g1-artifacts/continuous-package/raw-pin-a838e66279254a00bc3be7eb55026442
```

## 未验收与 G2 条件

本阶段接通的是连续录制新包的软件处理链，不代表整个 G1 完成。缺少符合单位和
有效性契约的真实设备录制、长录制多窗口的实际入口验证、此链路的 HTTP/独立
客户端贯通、1440/1024 浏览器及真人验收。MCAP 通用新包入口、原生共享视频/多
Episode、三类独立审核、subtask/批量交付、W7 演练等仍需继续。17 项 P0 仍 Pending。

G2 未执行。未来部署须同时包含既有 `episode_store/0009–0010`（共 143 个当前
迁移）、本次 resolver/activity/workflow 与已支持 data reference 的 API/UI，
以及可调用官方 RealSense SDK 的已固定镜像。先在隔离环境完成剩余 G1 验收，
清点历史运行任务，再决定现网切换；不能仅凭此阶段包可读就部署。

回退先停止新的派发并保留正在处理的冻结输入与历史，不改包/集合/标注/原件，
不把标准包引用转换成 Lance。已有 v2 工作流 ID 或新包任务后必须使用能理解其
身份与输入的兼容版本处理，不能直接退回不识别它们的旧 worker。本阶段无新迁移，
未改 8088 业务数据、未删除旧存储、未启动机器人运动或新采集。
