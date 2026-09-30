# Tag 创建独立 Episode：持久任务、页面与隔离验证

2026-09-30，继生成层 `93034838`、产品登记 `22e39dd5` 后，接通普通用户显式
创建命令、事务 outbox、常驻 worker、P08 页面和新条目跳转。本阶段是 G1 软件
进展，输入为合成 RGB-D 夹具，不是实际录制、真人操作或完整 P0 验收。

后续已接通新计划的[独立区间 QC](tag-quality.md)。本文 UNKNOWN、旧 runtime
哈希及截图保留当时实测状态，不能当作新 QC 浏览器验收；旧冻结计划仍维持原语义。

## 当前行为

- P08「标注文件与派生」选择已保存的修订与一个 Tag，显示半开帧区间和深度
  包含范围；使用未审核修订需要明确勾选。未保存草稿不参与此次创建。
  普通保存 Tag 仍走原编辑/提交/审核流程，不会触发派生。
- 请求冻结父数据与集合版本、标注 JSON 哈希、Tag 区间、schema 和编码计划；
  返回明确的新 Episode/data version。公开响应中的纳秒是字符串，内部冻结
  计划的整数和哈希契约不变，不用浮点数传递时间。
- 创建/重试/取消要求 `upload.manage`、`dataset_version.read`、
  `annotation_task.read`、`export.download`；读取要求后三项。每次请求重新
  授权，包括已缓存幂等结果。租户、项目、地域、父任务和数据引用均校验。
- 数据库保存请求幂等键、固定计划、代次、租约及已提交收据；出站事件与任务
  同事务产生。重试沿用原计划；已提交数据再次逐文件校验并继续预览/产品登记/
  标注任务，不再次编码或量化。新任务只有完整登记及自己的标注任务均完成才
  READY。READY 是创建完成，不是 QC 或审核通过。
- 仅可取消从未执行的 PENDING 任务。提交之后失败会明确显示「独立数据包已
  保存」，不通过取消隐藏数据。崩溃租约过期后可恢复，旧 worker 不能继续写状态。
- 新条目有独立空标注修订 0，不继承父审核。当前自动 QC 仍 UNKNOWN，原因是
  `DERIVED_INTERVAL_REQUIRES_OWN_QC`；需要后续接通独立执行，不能视为通过。
- 详情完整来源位于 `source.document.derivation`，保留父版本、标注哈希与区间；
  深度显示继续调用既有官方 `pyrealsense2.colorizer`。本阶段未改调色算法或
  主深度 profile；gray12le 实际编码 profile 保持如实记录。

## 已运行的证据

- [36 项数据库/任务/迁移测试](tag-jobs-tests.xml)：11 项任务用例与参数组合、
  25 项迁移检查。覆盖四路并发、提交前故障、提交响应丢失、预览/产品事务/
  标注创建/完成响应故障、主包提交后进程崩溃、租约抢占与旧 worker 隔离、
  取消条件、逐项能力撤销、跨租户/项目/地域及 SQL 不可变约束。
- [1 项实际 HTTP 验证](tag-jobs-http.xml)及[结果](tag-jobs-http-results.json)：
  普通注册会话 → 隔离网关 → API → 常驻 worker → 新 Episode → 详情/官方 PNG/
  米值读取 → 完整 ZIP。逐个文件大小与 SHA-256 一致；缓存命令和列表撤权后拒绝。
  后端共 37 项通过，无跳过，保留一个既有 Starlette/httpx 弃用提示。
- [71 项前端验证](tag-jobs-ui.xml)：11 项客户端身份/时间/响应绑定，6 项创建面板，
  5 项既有下载面板，5 项 TaskPage，44 项工作台回归。包括明确未审核选择、
  网络失败保留幂等键、发布未完成时重试、取消限制、权限及原 Tag 编辑/提交/
  审核行为。工作台保留既有 React `flushSync` 测试提示。
- [禁网官方 reader 结果](tag-jobs-offline-reader.json)：直接读取实际 HTTP 下载
  的 3 帧标准包，LeRobot 0.6.1 reader/DataLoader 读取 RGB-D，独立应用 HC mask，
  验证全部文件、父标注/帧/纳秒映射。读取过程未导入平台包，深度没有重新量化。
  官方 reader 对未知 HC 元数据字段的提示已观察到；HC 掩码由独立代码处理。
  没有作出任何模型已支持深度训练的声明。
- 实际 Firefox + 48088 网关/API/常驻 worker，没有请求替身：在 1440 宽创建
  [1,4) 子区间，1024 宽再创建 [0,2) 区间；均完成并得到独立新条目。
  视口为 1440×1014 / 1024×1014，文档无横向溢出。查看页两路视频
  `readyState=4`、无解码错误；深度媒体路径是官方 SDK 生成的预览。
  1024 下显示官方深度 PNG，点击得到像素 (1,0) `0.999551 m`。
  新标注任务在两种宽度均显示新 rollout、0 个 Tag，详情为修订 0/未审核。
  浏览器测试账号没有 Tag 编辑能力，因此此轮不宣称真人编辑/审核流程验收。
  截图与 API 收据见下方；登录使用真实注册会话恢复，未将登录表单列为本轮验收。

| 证据 | 内容 |
| --- | --- |
| [1440 页面](tag-browser/1440-two-tag-episodes.png) | 已创建任务、固定来源、子条目链接 |
| [1024 页面](tag-browser/1024-two-tag-episodes.png) | 窄宽度创建第二个条目并显示两个结果 |
| [子 Episode 回放](tag-browser/1024-child-viewer.png) | RGB 与官方深度预览实际解码 |
| [官方深度及取值](tag-browser/1024-official-depth-pixel.png) | SDK 着色帧和物理米值 |
| [1440 新标注任务](tag-browser/1440-independent-annotation.png) / [1024](tag-browser/1024-independent-annotation.png) | 新 rollout、独立空标注 |
| [只读 API 收据](tag-browser/receipts.json) | 两条真实浏览器创建结果、来源、独立审核状态；无令牌或签名链接 |

浏览器发现并修复：新接口带显式项目/地域路径，初版客户端误用旧标注短路径，
被正式合同校验拦截；修正后端点路径并补前端断言，重跑成功。验证脚本的详情
来源与预览字段名也按真实模型修正，没有放松接口校验。最初旧测试会话过期，
重新取得普通会话；Firefox 独立 profile 放在工作树，避免系统 Firefox 看不到
临时目录。脚本只退出自己启动的浏览器。

## 复现与部署约束

```bash
# 隔离常驻 worker 暂停后执行确定性故障注入，完成后恢复。
bash deploy/chayi/compose.sh stop worker
bash deploy/chayi/compose.sh run --rm --no-deps test tests/episode_store/test_tag_jobs_services.py tests/core/test_migrations.py --fail-on-skipped --fail-on-xfailed -q
bash deploy/chayi/compose.sh up -d --no-deps --wait --wait-timeout 30 worker
bash deploy/chayi/compose.sh run --rm --no-deps test tests/episode_store/test_tag_jobs_http.py --fail-on-skipped --fail-on-xfailed -q
bash deploy/chayi/compose.sh run --rm --no-deps frontend-test vitest run src/pages/p08-data-annotation/tag-episodes.test.ts src/pages/p08-data-annotation/TagEpisodePanel.test.tsx src/pages/p08-data-annotation/AnnotationDeliveryPanel.test.tsx src/pages/p08-data-annotation/AnnotationTaskPage.test.tsx src/pages/p08-data-annotation/AnnotationWorkbenchView.test.tsx
bash deploy/chayi/compose.sh run --rm --no-deps --entrypoint python test -m hc_data_platform.core.migrations status
```

迁移 `episode_store/0015_tag_derivations.sql` 已且仅在隔离库执行；最终状态
149/149，无校验和漂移、缺失或未知项。TypeScript、ESLint、Ruff/格式、diff
及生成客户端一致性检查通过。runtime OpenAPI SHA-256：
`0ea892822355da9f1237090920115a376b45385e630d06eb651dd19a8186f6ce`。

未来 G2 需要先执行该 expand 迁移，再配套发布 API、worker 与前端，并保留当前
官方 SDK 镜像依赖。回退前通过维护流程停止新的派生命令，处理或隔离未终态任务
及对应 outbox 后再回退消费者/页面；保留任务表、收据、新旧数据和标注，不回滚
已发布主包，不删除旧存储。这是本阶段部署注意事项，不能替代最终 G2 runbook。
本轮未执行 G2/G3，8088 业务数据、日常机器人配置和运动均未操作。

独立 QC、全无效/缺失深度区间语义、实际录制/规模/真人、全三入口浏览器、来源
缺失字段及 W7/W8 仍继续。此阶段没有把 G1 或任一完整 P0 标成全部完成。
