# G1 集合排队、授权下载与 P21 批量入口（2026-09-29）

W6 的重组生成器现已接入真实 API、持久队列与 P21 页面。独立标准包批量 ZIP、
LeRobot RGB-D 集合和 RGB 子集分别可选；本阶段不代表整个 G1 或真实采集验收完成。
深度预览继续使用官方 RealSense SDK Colorizer，本次没有新增着色算法或主视频编码。

## 行为和契约

- `POST /projects/{project}/regions/{region}/episode-deliveries/collections` 接收
  1–100 条固定数据引用、明确的 rgb/rgbd 模态及每条可选的固定标注修订/分段轨道。
  只接受同组织/项目/地域且每个 Episode 一个版本；固定父 manifest、profile 哈希、
  相机/数值契约、标注文件哈希、审核和版本。兼容性失败返回具体字段，不隐式转换。
- 重用现有交付表、租约及事务 outbox；请求的排序不影响内容身份。同一幂等键在
  响应中断后返回原冻结结果，不随最新审核变化重新选择。取消/重试具有代次约束；
  重试沿用选定版本、归档哈希和首次生成的实际总量。原始录制、主包和标注不改写。
- 文件数/大小在集合投影完成前为 null，页面明确显示尚未确定；生成后在有效 worker
  租约内冻结真实数值，随后报告归档文件/字节、上传字节。ZIP 内为 `collection/`
  官方数据集根与外层 `delivery.json`；媒体字节复用，数值索引/任务/统计重建。
- RGB 子集在创建前显示排除深度视频、像素掩码及深度特征。RGB-D 保留已采集深度
  视频/profile/掩码/源帧；独立包批量模式保留每条完整主包。未声明没有采集的深度
  已存在。集合是交付派生物，不是新的主数据版本，也不写 Lance。
- 同一状态/缓存/下载接口重新检查当前范围与权限；含标注的集合还要求标注读取。
  无标注权限时先在 SQL 查询中排除含标注任务，避免它们影响普通任务分页。签发
  300 秒对象下载 URL；已签发链接在到期前仍有效，新授权在权限撤销后拒绝。
- P21 默认标准包入口，固定集合快照分页、跨数据集逐条选择、固定修订/轨道、
  状态/重试/取消/下载均已接通。带标注模式必须逐条选择；重叠的轨道区间阻止提交，
  未批准修订须显式选择。历史格式与原始文件入口保留。当前“任务所属数据集”
  只收窄数据集范围，并明确提示再逐条选择 Episode；不是精确任务成员筛选。
- `GET .../episode-deliveries/candidates` 读取真实集合快照的标准包引用，后续页
  固定 collection_version_id；历史条目另计数量，不伪造 Lance 版本或绕过 READY。
  P06 的完整包列表显式限定 independent 类型，避免把集合误解析为单包。

## 已执行的检查

- [后端回归](collection-delivery-backend.xml)：41 项通过，包含新集合任务 4 项、
  既有完整包/标注交付回归和迁移测试。隔离 PostgreSQL/MinIO，受控故障期间暂停
  隔离 worker；之后已恢复。覆盖并发请求、上传故障/重试、幂等缓存、固定标注、
  撤权和不可变归档总量。帧率 `30` 与 `30.0` 规范化为同一契约，避免跨入口误拒绝。
- [普通 HTTP 与常驻 worker](collection-delivery-http.xml)：2 项通过，分别为
  新集合和既有完整包链路。普通注册会话发命令、worker 生成、真实对象下载核对
  哈希、撤销权限后拒绝新下载授权；[集合链路结果](collection-delivery-http.json)。
- [P21/客户端测试](collection-delivery-ui.xml)：23 项通过，包含 10 项响应契约/
  范围/标注/下载校验、4 项组件行为、9 项页面测试。默认标准包入口提交明确 RGB
  子集，未走历史 Lance 资格接口；明确排除提示与重试键保留通过。
  [既有 P06/P21 回归](collection-delivery-legacy-ui.xml)另有 20 项通过，其中
  8 项 P21 与本次 23 项重叠，不能计为 43 个独立用例。组件测试不是浏览器验收。
- [断网官方 reader](collection-delivery-offline-reader.json)：直接使用队列产出
  和普通 HTTP 实际下载的四份 ZIP，先核对外层任务/计划/完整 ZIP 清单和所有文件
  哈希，再用 LeRobot 0.6.1 与跨 Episode DataLoader 读取。各 2 Episode、8 帧，
  RGB/RGB-D/带标注集合均通过；独立应用 HC mask，核对 RGB、物理深度、帧映射、
  标注及统计。[冻结任务记录](collection-delivery-jobs.json)保留可复查的计划和哈希。
  官方 reader 对额外 HC metadata 给出忽略字段提示；HC 掩码由独立检查代码应用。
- [隔离迁移状态](collection-delivery-migrations.json)：148/148，无缺失/漂移。
  OpenAPI 静态片段、运行契约与前端生成客户端同步；类型、lint、格式及 diff 检查
  随代码提交保存。新增 SQL 为 `episode_store/0014_collection_deliveries.sql`。

这些输入仍是短软件夹具，未将其当成真实录制、长录制容量/吞吐、浏览器 1440/1024
或真人验收。没有声明任一模型支持深度训练。上传中途硬杀后的孤儿分片治理仍缺
专项证据；此次验证的是可控上传失败与既有恢复机制。未完成的来源字段、Tag 新
Episode、W7 清单/断点/回退演练、真实样本及 W8/17 项 P0 继续保留。

## 复现和 G2 注意事项（本次未部署）

```bash
bash deploy/chayi/compose.sh run --rm test tests/episode_store/test_collection_delivery_services.py tests/episode_store/test_complete_delivery_services.py tests/episode_store/test_annotation_delivery_jobs_services.py tests/core/test_migrations.py -k 'not normal_http' -q
bash deploy/chayi/compose.sh run --rm test tests/episode_store/test_collection_delivery_services.py tests/episode_store/test_complete_delivery_services.py -k normal_http -q
bash deploy/chayi/compose.sh run --rm reader /app/tools/read_collection_export.py /evidence/collection-delivery-offline-reader.json <rgbd-zip> <rgb-zip> <annotated-zip> <http-zip>
```

第一组受控故障测试应在专用隔离服务并暂停其 worker，第二组需要常驻 worker；
reader 的 ZIP 同目录须有测试保存的 READY `job.json` 或 HTTP `result.json`。
Reader 使用固定离线依赖、network=none，无平台 import。

未来 G2 需一起发布 API/worker/前端及 0014 迁移，保持相同 processor/archive/reader
版本；先确认临时盘容量、上传限制和实际 profile 兼容性。迁移放宽“生成前总量未知”
但保留 READY、范围和不可变约束。旧 worker/API 不理解新集合 plan，不能把仍有集合
任务的库直接接回旧代码。回退应先停止新命令并排空/保存任务，按 W7 最终演练的
兼容版本/指针方案操作；不删除新旧主包、原始录制或已冻结标注。此次仅更新独立
工作树和隔离库，未操作 8088 业务数据、日常机器人安装、机器人运动或 G2/G3。
