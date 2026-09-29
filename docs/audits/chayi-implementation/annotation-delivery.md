# G1 带标注 RGB-D 下载任务阶段（2026-09-29）

本阶段在 `feat/chayi-g1` / `hc-chayi-g1` 接通上一阶段的分段包生成层、持久化任务、
授权 API、正式契约及 P08 页面入口。尚不代表 W3/W5/W6 或 17 项 P0 全部通过。

## 行为与边界

- `POST /api/v1/annotation-tasks/{task_id}/deliveries` 默认冻结当前已审核修订；未审核
  下载必须指定修订并显式允许。用户明确选择轨道名称及 annotation IDs，同轨重叠
  返回 422 和两个标签 ID，跨轨交叉标签仍完整保留。
- 请求同时要求 `annotation_task.read`、`export.download`、`export.create` 及精确
  组织/项目/区域/任务归属。列表、状态和新下载授权仍重新验证所属任务与下载权限。
  API 不接受对象路径或客户端声称的审核结果。
- `annotation.delivery_jobs` / `delivery_requests` 保存固定 plan、幂等键和派发记录，
  复用现有 `core.outbox_events` / worker。计划包含完整父数据引用和清单哈希（涵盖
  深度 profile/掩码/源帧）、修订/文件哈希、轨道、不可变 review/submission、
  `lerobot/0.6.1` reader 与 `hc-annotated-delivery-queue/1` 处理器版本。
  同键不同命令冲突；同内容的新请求共享任务/归档，不按文件名猜测命中。
- 页面显示等待、验证、生成分段、打包、保存校验、就绪、失败、取消；失败和取消
  可按所见 generation 重试。租约每 15 秒续期，取消立即使旧 worker 无法发布。
  租约丢失但未到终态时保留 outbox 重试，避免确认通知后任务永久滞留。
- ZIP64 使用固定排序/时间戳，逐文件校验；视频、profile、掩码、来源映射按字节
  复用，不量化、不转码。仅派生数值表添加 subtask_index / hc.annotation.excluded。
  完整父时间轴保留，清理区间以逐帧 bool 标记，不隐式丢帧。
- 随包有原始标注 JSON、完整审核/提交附件、分段表、源帧映射、父回执/清单和本次
  派生包清单。新草稿不替换固定审核修订。普通 Tag 保存不触发此命令。
- 归档 SHA/大小/清单 SHA 先持久化，再上传、回读验证，最后 CAS 标记 READY。
  上传后失去响应可复用完整归档；旧 generation、旧租约及重复通知不能覆盖新状态。
  每次缓存命中/下载重新验证归档哈希，再签发 300 秒下载地址；完整包包含已采集深度。
- P08 同页入口保留原 Tag 编辑/提交/审核及版本化 JSON 面板，默认已审核修订，未
  保存草稿提示明确，分段必须勾选，同轨重叠可见，取消/重试受权限限制，列表可分页。

这里生成的是“完整父 Episode + 固定修订”的交付派生物，仍不创建新的主数据版本。
“从 Tag 区间创建新的 Episode 身份”、完整基础包通用缓存/统一 P06 下载入口、批量
独立包 ZIP 与重组集合、1440/1024 真实账号浏览器流程继续实施，不能视为已完成。

## 已运行验证

1. [后端回归 XML](annotation-delivery-backend-tests.xml)：49 项通过，包括 6 项新
   PG/MinIO/outbox 服务测试、原分段/派生写入、版本化 JSON 和迁移检查。
   覆盖新草稿后仍下载审核 r1、完整深度字节复用、并发幂等、同键不同内容、取消与
   并发重试、旧租约/事件、上传失败、上传后中断、租约到期恢复、损坏缓存拒绝、
   缺省审核约束、同轨重叠、能力/组织隔离与伪造 outbox 拒绝。HTTP 测试使用
   FastAPI TestClient，注入测试 AuthContext，不能计作真实登录浏览器验收。
2. [前端 XML](annotation-delivery-frontend-tests.xml)：23 项通过，含新面板/API 13
   项与已有任务/JSON 面板 10 项。覆盖明确轨道/未审核选择、重叠、重试绑定、权限、
   网络失败复用命令键、响应版本和下载地址校验。这些组件测试采用测试替身。
3. [常驻 worker 证据](annotation-delivery-live.json)：测试命令生成真实待办后恢复
   隔离 worker，由未替换依赖的常驻派发循环完成。任务
   `ad_0c5b20c72eb7658e5fc0a63ef95db68a9f76d81c5c105a286bba4987893d63fa`
   READY、outbox 已确认、对象归档回读哈希一致。复现工具
   `backend/tools/capture_annotation_delivery_worker.py` 只读服务并复制本地证据。
4. [常驻输出离线 reader](annotation-delivery-live-offline-reader.json) 与
   [已审核输出离线 reader](annotation-delivery-approved-offline-reader.json)：各 4 帧，
   官方 LeRobot 0.6.1 / DataLoader 直接读取 RGB-D、分段和排除列，独立解码应用 HC
   掩码。各保留全部 2 个 Tag、1 条深度流，6 个媒体/深度证据文件哈希复用，4 个
   无效像素被掩码；网络关闭，不导入平台代码。已审核输出对应
   [冻结任务回执](annotation-delivery-approved.json)，父任务已有 r2，交付仍为 r1。
   reader 对 `hc.*` 非官方字段的忽略警告属预期，HC 掩码仍由独立逻辑显式应用。
5. Ruff、TypeScript、所改前端 ESLint、正式/runtime OpenAPI 对齐和生成客户端
   `--check` 均通过。修正旧迁移测试停留在 baseline 的 EXPECTED_MANIFEST，明确
   列出已存在的 14 个 G1 migration 与本次新增项，没有降低迁移校验。
6. 追加 [真实网关测试 XML](annotation-delivery-http-tests.xml) 1 项通过，见
   [HTTP 证据](annotation-delivery-http.json)：在隔离环境注册测试账号、建立普通
   登录会话，通过 gateway → API 创建任务，常驻 worker 生成归档；同键重复创建
   命中缓存，下载授权后实际对象网关 GET/Range 成功，逐成员哈希一致。无登录和
   外组织被拒绝；撤销账号下载 capability 后热缓存状态/新签名均被拒绝。
   仅测试账号组织成员及 capability 的初始授予使用隔离 DB 测试初始化，HTTP 请求
   没有替换认证依赖。会话在测试结束注销。此项为真实服务链路，仍不是浏览器验收。

所有输入在证据中明确标为合成故障夹具；数据可读不等于未指定模型已经支持深度
训练。没有真实长录制、真实标定/单位确认或真人操作证据的项目仍未验收。

## 运维与后续 G2 条件（未部署 G2）

- 新增 migration `annotation/0017_annotation_deliveries.sql` 为 additive expand，
  本轮只应用至隔离 PostgreSQL，状态 144/144，checksum drift / missing / unknown 均空。
- 将来获准 G2 时，先备份与核对隔离验收版本，迁移先于 API/worker；两者须使用同一
  代码/处理器版本，并包含 annotation.delivery.requested.v1 handler 与所选队列 scope。
  给交付角色分配现有 export.create/export.download，不能用服务身份代替用户授权。
- 任务过程使用本地临时标准包和 ZIP，需要磁盘容纳父包复制及归档；长录制容量/耗时
  尚未实测。失败 multipart 尝试可终止，原件、旧存储、已完成归档一律不删除。
- 回退应用代码前停止新的交付创建、让当前任务结束或取消，保留新增表、通知、对象与
  原标注记录；恢复本版本 worker 可继续同一 generation/plan。旧 worker 未配置的通知
  不能伪装成已成功交付。G1 W7 的完整 inventory/checkpoint/指针回退演练另行完成。
- 本阶段未更改 8088 业务数据、未操作机器人，深度显示仍为官方 RealSense SDK。
