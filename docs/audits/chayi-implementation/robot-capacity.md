# G1：机器人隔离工作树、录制容量与状态阶段

2026-09-29；R1/R2/R4 的部分软件实现。R1–R4 尚未全部完成，17 项 P0 保持
Pending，未执行 R5/G2/G3，没有启动 ROS 节点、相机、机器人运动或真实采集。

## 工作树与安装事实

执行前重新核对四个原机器人仓库，均无源码改动，HEAD 与补充方案记录一致。
四个仓库分别建 `feat/chayi-g1`，位于 `/home/hc_op/workspace/teleop_ws-chayi-work/src`；
没有将 ROS 工作区根目录当作 Git 仓库。实际版本和路径见
[工作树记录](robot-worktrees.json)。

| 仓库 | 开始版本 | 本阶段候选 |
| --- | --- | --- |
| humanoid_manager | ba16c3ee | **1e3f3934c1271de9c1aa8fe245f6ec38af480a43** |
| humanoid_camera | 0c0282a7 | 保留基线，深度来源契约仍待修改 |
| humanoid_motion_server | dce1eae0 | 保留基线，无运动算法修改 |
| hc_teleop_recv | 506212d3 | 保留基线，无 VR 协议修改 |

独立 build/install/log/state/recordings/upload-queue/evidence 目录已建立。
`deploy/chayi/robot-env.sh` 固定测试 ROS_DOMAIN_ID=221、ROS_LOCALHOST_ONLY=1 和
独立状态路径，只设置环境，不启动节点。`robot-python-test.sh` 清除继承的 overlay，
只加载 `/opt/ros/humble` 消息类型；manager 从新源码导入，测试数据写独立/临时目录。
依赖测试解释器暂复用 D 工作区已有环境，未改动它；这不等于新的 manager/camera
已完成 colcon 构建安装。完整隔离运行配置和全部跨端字段矩阵仍待后续完成。

只读核对的日常安装：manager package 0.1.0，模块位于
`teleop_ws/install/humanoid_manager/local/lib/python3.10/dist-packages/humanoid_manager`；
camera package 1.2.0，适配器位于 `teleop_ws/install/humanoid_camera/lib/humanoid_camera/timestamp_adapter.py`；
motion_server 0.2.0、teleop_recv 0.1.0；原独立 uploader 的 distribution 版本是 1.2.0。
这些路径未被本阶段修改。

候选 uploader 1.3.0 从新工作树构建 wheel，以本地依赖 wheelhouse 离线安装到
`teleop_ws-chayi-work/install/uploader`。用该解释器 `-I` 检查模块和 distribution；
没有 hc_data_platform、SQLAlchemy、psycopg、Temporal 或 rclpy 依赖。
[安装路径与 wheel 哈希](robot-uploader-install.json)。

## 已实现的跨端契约

- raw 完成包最多 4095 个原始文件，加一个完成标记，总上传资产最多 4096。
  直接连续录制上传（没有额外 seal）允许 4096；完成包 v1 的 seal 校验仍预留一个
  位置。完成 schema、客户端校验、平台 raw/continuous 模型同步；没有调整分片
  授权批次限制或删除文件以凑额度。
- raw 封存超过总额时，在复制原件前拒绝，不留下完成标记。原始录制保留。
- 平台新建身份的策略默认值改为 4096；已有持久化的 256/其他额度保持原值，
  没有数据库批量提权或现网策略修改。
- 新增 `GET /api/v1/robot-ingest/uploads:capabilities`，使用现有真实机器人凭据
  验证并返回绑定机器人和该身份实际额度，响应 no-store。无凭据或用户 bearer
  不可调用；能力查询不替代创建上传时的 task/组织/项目授权。
- 新客户端仅在 CAPTURE_BUNDLE 总资产超过 256 时，先查询版本化能力。
  旧服务、不匹配身份、无效类型、额度不足时，在创建会话/传输前停止，原队列可恢复。
  小包保留旧请求方式；原 processing-result 严格 schema 未增加字段。
- JSON 文件/客户端响应和平台完成标记读取仍限制 16 MiB；隔离网关 API 请求上限
  为 600 MiB。本阶段容量测试使用正常短路径/小型元数据，不声称所有最大长度
  字段组合、超大元数据都在此额度内。
- SLICED 独立保存为 `sliced` 终态，管理页兼容历史 `awaiting_slice + SLICED`。
  AWAITING_SLICE 和 SLICED 都停止自动平台轮询；可显式重新查询，复用上传/Raw ID，
  不重传或请求重新处理。页面分别提示等待切片和已确认，标准包/QC/审核到平台查看。
- VR 的历史路由保持：`aligned` 进入 OpenArm raw Session，`raw` 进入旧诊断录制。
  启动提示改为原始录制，不把机器人录制说成已对齐。详细兼容/回退说明随
  manager 的 `docs/chayi-upload-capacity.md` 提交。

## 验证与实际边界

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 平台容量/API/策略、原接收和恢复 | 33 passed，0 skipped/xfail | [XML](robot-capacity-tests.xml) |
| 独立上传器传输/队列/分片故障回归 | 65 passed，1 个旧页面文案断言失败 | [原始 XML](robot-uploader-capacity-tests.xml) |
| 修正文案断言后，真实隔离安装 CLI 的管理页接口 | 1 passed，65 deselected | [XML](robot-uploader-web-hook-tests.xml) |
| v1、v2 两种超过 256 的完整清单/哈希与重复入队 | 2 passed，65 deselected | [XML](robot-uploader-large-inventory-tests.xml) |
| ROS 消息序列化、实际 Session/MP4/MCAP 与封存 | 4 passed，0 skipped | [XML](robot-raw-seal-tests.xml) |

上传器回归包含 kill/restart 期间已上传分片恢复，固定身份、错误任务、commit 响应
丢失、凭据/网络故障、显式处理重试；新增 v1/v2 大清单、旧 API/低额度/错误身份
能力拒绝和 SLICED 终态/重新查询。HTTP 服务为测试合同实现，不是实际平台服务。
原始录制测试使用合成消息，实际写盘与编码，但没有 ROS 发布、相机或运动。
没有把本阶段作为 R3 的独立 uploader→运行中平台链路证据。

初次在平台容器执行机器人测试缺少 jsonschema/ROS 消息类型，因此改用已有依赖
解释器和仅 ROS distribution overlay；没有模拟这些缺失依赖。初次宿主机 HTTP
测试被沙箱拒绝 socket，获准后仅运行本机随机端口测试。安装 wheel 首次命令遗漏
本地路径前缀，随后改为 `--no-index ./upload_client` 离线构建成功。

Ruff、修改 Python 解析、JS 语法、`git diff --check`、TypeScript 与正式/运行时
OpenAPI/客户端 generation 检查通过。运行时契约 SHA-256：
`350ebda5eb1574b358342631dc45a5b53f91ab4ab3de4485b6d8e1fd118a18ee`。
隔离八个服务运行，配置了健康检查的七个均 healthy；gateway 运行但无健康检查。

## 未完成及 G2 准备

R1 的完整字段样例/运行配置，R2 的源 depth_scale/单位/encoding/标定验证，R3 的
新包回执及连续录制提交，R4 实际独立客户端→平台、长录制多窗口、真实数据/硬件、
1440/1024 浏览器尚待完成。此处 304/314 文件软件清单验证不替代真实长录制吞吐
或大包实际服务上传；manager/camera 整体安装构建亦未完成。

G2 应服务端先行，随后核对旧 uploader 小包，再安装候选客户端并保留队列。
已有大包队列不能回退到只接受 256 的客户端；暂停新增上传并保留可恢复的软件。
任何凭据/策略变更继续服从授权，不因为容量预检成功就跳过上传/下载权限。
本阶段没有执行 G2，也没有启用真实机器人平台身份或录制。
