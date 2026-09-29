# G1 固定 Episode 详情与文件接口（2026-09-29）

本阶段补充 W4 的聚合读取和 W5 文件入口契约。新增固定数据版本的 `overview`、
分页 `files` 和按清单文件 ID 授权下载；原详情、历史 Lance 读取和 Tag 工作流保留。
这是接口阶段，统一详情页面尚待接入，不作为 UI-01 或完整 G1 验收。

## 绑定与状态

- 自动 QC 从当前包的冻结报告读取，人工数据审核按完整 DataVersionRef 读取，
  标注任务、当前草稿、历史提交、历史批准分别返回明确身份。数据库单条查询保持
  可变任务指针与不可变提交/审核记录一致；不会用另一数据版本的最新记录填充。
- 同一数据版本在多个集合下有多个标注任务时返回 `SELECTION_REQUIRED` 和候选，
  调用方必须传 `annotation_task_id`。不自动选取最新任务；没有标注读取权限时返回
  `FORBIDDEN`，不泄露任务、修订或交付。只有显式选择交付任务才显示该分段轨道的
  subtask 数量，不把所有层级标签数量当成分段数。
- 返回当前帧数、时长、实际视频流数量、来源窗口、预览状态、逐路深度 profile、
  实际源 dtype/scale、掩码/帧映射引用、缺帧/无效/饱和比例及两类误差证据。
  纳秒扩展字段保持十进制字符串。没有记录的父录制时长、任务要求和物理相机配对
  明确 `NOT_RECORDED`；这些字段仍需后续入口生产者补充，不能从通道名猜测相机数。
- 深度预览沿用官方 RealSense SDK。overview 只核对既有收据、来源和对象大小，
  不生成预览或重新解码整个显示视频；播放授权仍执行原有完整验证。

## 文件与授权

文件列表仅来自已提交 manifest，按数值、RGB、深度、标注、说明、报告分类；每页
最多 100 项，游标必须属于当前包。每项含路径、大小、哈希、HEAD 可用状态及权限
动作。`MANIFEST_BOUND` 不表示本次列表已重新校验所有远端字节。默认 overview 首
页为 50 项；完整包下载仍自行验证全清单，不能把首屏状态当成全包重新扫描结果。
自引用的 manifest/commit 收据不在此文件页中，完整 ZIP 仍包含它们。

单文件下载重新授权组织/项目/地域/版本与 export.download，校验内容哈希，再打开
受缓存租约保护的文件描述符后流式输出；客户端变慢不长期持有缓存锁。记录授权
审计，不接受任意对象路径。撤权后即使本地有缓存也拒绝新请求。GET 无生成副作用。

## 实际验证

- [后端 XML](episode-detail-backend-tests.xml)：11 项通过，使用隔离 PostgreSQL、
  MinIO、实际 HTTP 网关和普通会话；覆盖三类审核独立变化、返修后历史提交不变、
  跨数据版本隔离、多集合下显式任务选择、缺文件与缺预览可区分、不在 GET 生成、
  文件分页及逐文件哈希、未登录/跨组织/撤权拒绝、分段轨道选择及原读取回归。
  缺对象场景用 HEAD 故障注入，没有删除任何已提交对象。第二集合实际提交另一
  Episode 后创建，没有绕过集合成员数据库约束。保留一项已有 Starlette 弃用警告。
- [普通 HTTP 记录](episode-detail-http.json)：完整清单逐文件大小和 SHA-256 核对，
  当前账号撤销下载能力后热缓存仍拒绝。测试权限通过隔离数据库引导，HTTP 无替身。
- [三入口聚合记录](episode-detail-entrances.json)：读取此前真实服务链路输出，
  MCAP 30 帧/3 路/23 文件，连续录制 6 帧/6 路/31 文件，原生 LeRobot 6 帧/2 路/
  18 文件；深度 profile、源单位和目标帧数一致，既有预览 READY。输入均为软件
  夹具，不是真实采集、真人、浏览器或新增离线 reader 验收。
- Ruff、TypeScript、生成客户端检查通过；正式与运行时 OpenAPI 同步，runtime
  SHA-256 `d599441f084fc7622ce537085d9d49e9de30ea585b8f428358014526ad239243`。

复现：

```bash
bash deploy/chayi/compose.sh run --rm test tests/episode_store/test_detail_services.py tests/episode_store/test_preview_services.py tests/episode_store/test_readers_services.py -q --junitxml=.g1-artifacts/episode-detail.xml
bash deploy/chayi/compose.sh run --rm -e PYTHONPATH=/app/src:/app --entrypoint python test tools/capture_episode_detail.py --entrances /app/.g1-artifacts/qc-rule-entrances.json --output /app/.g1-artifacts/episode-detail-entrances.json
```

本阶段无新增迁移，隔离 API/worker 健康，未改 8088、旧存储或机器人。将来 G2
需要匹配契约的 API/前端及已有 146 项迁移；应用回退保留新旧数据与扩展迁移。
尚未执行 G2/G3。统一页面、来源补全、完整/批量交付、Tag 新 Episode、W7/W8
继续，17 项 P0 保持 Pending；reader 可读不代表未指定模型已支持深度训练。
