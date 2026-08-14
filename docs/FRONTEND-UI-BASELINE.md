# 前端 UI 最终视觉基线

> 更新时间：2026-08-13 00:12 CST
> 实施仓库：`/home/czy/hc_DataPlatform/frontend`
> 总体计划：[`plan/FINAL-IMPLEMENTATION-PLAN.md`](../plan/FINAL-IMPLEMENTATION-PLAN.md)

## 1. 当前结论

- P01–P19 已逐页形成正式参考与 1672×941 运行证据的一一索引；页面证据均来自实际 MSW/Fixture 或明确的安全缺省态，没有用设计图数值、假行、假曲线或假媒体填充。
- 最终页面共同采用 218px 桌面侧栏、64px 顶栏、青绿/中性色、线性图标、低圆角、细边界、紧凑表格与常驻事实检视；Shell 的三张跨页证据历史路径为 `frontend/test-results/ui-012s/`，当前源码归档不再捆绑运行输出。
- 页面任务已覆盖 1440/1024/768/390、键盘、焦点、Escape、200% zoom 与基础 a11y；UI-012B3 的 16 组精确 a11y 发现已由 B4/B5/B6 转绿，UI-012B 最终跨页巡检 22/22 通过。
- 下表结论均未独立复检；UI-012A3A 九页已由主调度关闭，九张主证据逐页目视通过，专属视觉/四断点/键盘/200% zoom 为 11/11，后续全量 200+、lint/typecheck/build 通过。用户已明确不再安排复检；这不代表真实后端联调、生产验收或未确认产品能力已经可用。
- 真实 API 模式下没有确认合同的能力继续 `feature-unavailable` 或 fail closed。尤其 P01 四个聚合接口、媒体 descriptor、导出、策略写入、权限写入和部分 3D 资源不得从 Mock 截图推断为可用。

## 2. P01–P19 最终证据索引

原始视觉参考已从精简后的计划目录移出；其历史文件名和运行证据路径仍记录在下表，但当前源码归档不再捆绑 `frontend/test-results/`。

| 页面               | 正式参考 PNG                                                        | 最终 1672×941 运行证据                         | 目视结论（均未独立复检）                                                               | 因真实数据/能力边界保留的差异                                                      |
| ------------------ | ------------------------------------------------------------------- | ---------------------------------------------- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| P01 Dashboard      | `01-dashboard.png`                                                  | `ui-012a1/01-dashboard-1672x941.png`           | 指标、上传/存储/可用性图表、覆盖矩阵和待办形成完整首屏；坐标、图例与成功率百分比已收敛 | Fixture 只有单时点/单月和较少待办，不伪造 24 小时曲线；真实 API 聚合仍 unavailable |
| P02 数据源         | `02-data-sources.png`                                               | `ui-012a3a/02-data-sources-1672x941.png`       | 指标、双行筛选、列表与右侧常驻检视保持正式图三段结构                                   | 仅 1 个真实数据源而非设计图 6 行，列表下方留白如实保留                             |
| P03 上传任务       | `03-upload-jobs.png`                                                | `ui-012a1/03-upload-jobs-1672x941.png`         | 状态页签、指标、筛选、高密表格和聚焦会话事实区层级完整                                 | 仅 2 个上传会话而非 10 行；使用现有字段补充事实区，不增加假任务                    |
| P04 上传详情       | `04-upload-detail.png`                                              | `ui-012a1/04-upload-detail-1672x941.png`       | 概要、对象表、校验流水线、隔离与审计摘要形成稳定主/侧栏                                | 当前代表态为 1 个隔离对象，而非设计图 9 个上传对象；状态与操作以真实 Fixture 为准  |
| P05 数据集         | `05-datasets.png`                                                   | `ui-012a3a/05-datasets-1672x941.png`           | 双行筛选、汇总、紧凑列表和常驻选中摘要平衡单条数据                                     | 当前窗口仅 1 个数据集；摘要只消费 Ready/Episode/复核/退回/草稿等现有事实           |
| P06 数据集详情     | `06-dataset-detail.png`；revision `06b-episode-viewer-readonly.png` | `ui-012a3a/06-dataset-detail-1672x941.png`     | 数据集指标、Episode 筛选/列表、窗口事实和常驻选中检视已稳定；只读 Viewer 路径保留      | 仅 1 个 Episode，未伪造设计图的 10 行；Viewer 媒体取决于已授权 descriptor          |
| P07 版本详情       | `07-version-detail.png`                                             | `ui-012a3a/07-version-detail-1672x941.png`     | 固定版本概要、Episode/Revision 表和右侧不可变事实检视清晰                              | 仅 1 条 Episode/Revision；Manifest、Schema 与导出仍按能力和固定快照加载            |
| P08 数据标注       | revision `08-data-annotation.png`                                   | `ui-012a3b/08-data-annotation-1672x941.png`    | 任务、双模态 Viewer、标签 Inspector、关节事实与多轨时间带组成成熟三栏工作台            | Bootstrap 未返回授权媒体 descriptor，媒体格明确 unavailable；不伪造机器人图像/视频 |
| P09 人工问题       | `09-manual-issues.png`                                              | `ui-012a2/09-manual-issues-1672x941.png`       | 状态页签、筛选、3 行问题表与无遮挡详情检视结构稳定                                     | 详情没有安全媒体预览，只显示固定范围与 Viewer 路径；不伪造缩略图                   |
| P10 清洗草稿       | `10-cleaning-drafts.png`                                            | `ui-012a2/10-cleaning-drafts-1672x941.png`     | 五轴状态汇总、筛选、2 行列表和来源/状态常驻详情完成                                    | 当前只有 2 条活跃草稿；Preview/Commit 队列为 0 时按真实状态显示                    |
| P11 手动清洗       | `11-manual-cleaning.png`                                            | `ui-012a3b/11-manual-cleaning-1672x941.png`    | 操作列表、Source/Cleaned A/B、安全检视、事实侧栏与 EDL 时间带比例完整                  | Source/Preview 未携带授权媒体资源，保留安全缺省；不伪造图像或写入完成态            |
| P12 存储总览       | `12-storage-overview.png`                                           | `ui-012a3a/12-storage-overview-1672x941.png`   | 六指标、容量趋势、层级环图和 Inventory 对账占满首屏核心区域                            | 只有 2 个月趋势而非 6 个月；费用与 Inventory 继续为服务端只读事实                  |
| P13 生命周期       | `13-storage-lifecycle.png`                                          | `ui-012a2/13-storage-lifecycle-1672x941.png`   | 风险提示、策略表、影响概览和执行/恢复说明形成正式图信息层级                            | 仅 1 条策略且尚未运行 Simulation，不伪造 12 月预测或执行成功                       |
| P14 机器人模型资产 | `14-robot-model-assets.png`                                         | `ui-012a3a/14-robot-model-assets-1672x941.png` | 指标、资产表与右侧模型事实/资源状态常驻检视完成                                        | 仅 1 个模型；无固定版本资源 URL 时显示结构化 3D unavailable，不伪造模型预览        |
| P15 机器人与组件   | `15-robots-components.png`                                          | `ui-012a3a/15-robots-components-1672x941.png`  | 机器人列表、拓扑面板和详情面板保持三栏比例与键盘边界                                   | 只有 1 台机器人且组件未加载；拓扑以授权 Bootstrap 状态为准，不伪造组件树           |
| P16 标定管理       | `16-calibrations.png`                                               | `ui-012a3a/16-calibrations-1672x941.png`       | 标定集、固定 Frame Graph、版本摘要和变换详情形成三栏管理页                             | 只有 1 个标定集；3D 预览与报告缺少已确认资源时禁用，不伪造矩阵行                   |
| P17 数据 Schema    | `17-data-schemas.png`                                               | `ui-012a3a/17-data-schemas-1672x941.png`       | 类别、Registry 表和固定 Schema 详情/字段定义保持正式图信息架构                         | 仅 1 个 Schema；未选固定版本时兼容性/引用动作保持安全提示或禁用                    |
| P18 用户权限       | `18-access-control.png`                                             | `ui-012a2/18-access-control-1672x941.png`      | 三角色能力上限、成员表、角色边界和成员事实常驻检视清晰                                 | 当前仅 1 个成员；权限写入 API 未确认，邀请/角色/ScopeGrant 写入口明确 unavailable  |
| P19 审计日志       | `19-audit-log.png`                                                  | `ui-012a1/19-audit-log-1672x941.png`           | 指标、密集筛选、审计表、窗口事实与无遮挡常驻 Inspector 已完成                          | 当前窗口 2 条事件；`precise_ip` 等字段按策略省略，导出能力缺失时保持禁用           |

## 3. 跨页视觉与交互结论

- Shell：`ui-012s/{p01-dashboard-shell,p08-annotation-shell,p17-schema-shell}-1672x941.png` 覆盖标准页、工作台和管理页；项目/Region、任务、通知、用户与权限导航使用真实快照，搜索/通知无接口时不伪造结果。
- 稀疏态：标准页优先使用常驻事实检视、窗口摘要、结构化 unavailable 和只读影响概览；没有机械拉高单卡，也没有用设计图行数填满表格。
- 响应式：各页任务已经验证 1440/1024/768/390 和 200% zoom；宽表在区域内滚动，桌面 Inspector 不遮挡主体，窄屏切换为可关闭抽屉或纵向流。
- 可访问性：页面保持唯一主标题、语义区域、可见焦点、键盘打开/关闭、Escape 和焦点归还；状态不只依赖颜色。
- 数据边界：所有 ID、状态、数量、时间、bytes、hash、媒体可用性和 `allowed_actions` 均来自 Schema/Adapter/Fixture；UNKNOWN 与合同不匹配继续 fail closed。

## 4. 最终工程收口

- UI-012B1 已直接关闭：仓库 lint、typecheck、全量 200/200、build 与定向 E2E 5/5 通过；高置信零引用候选及两项仅合同消费候选已交 UI-012B 逐项验证，不因“看似孤儿”提前删除。
- UI-012B2 已直接关闭：P02/P03/P04 改为动态页面 import，生产入口约从 955.3 kB 降至 632.44 kB（约 34%）；DataTable、Mock browser 和按需 analytics 大 chunk 如实保留。
- UI-012B3 初始巡检定位的 P06/P07/P10 Tabs more、P14/P15/P17 Search 和 P17 重复 ID 已由 B4/B5/B6 精确修复；最终 19 页 × 五档加代表性 200% zoom、键盘与 Escape 巡检为 22/22 通过，全部 16 组红灯转绿。
- UI-012B7 已删除 6 个生产零引用旧文件（833 行），保留仍被全局主题导入的 `shared/ui/styles.css` 与有合同消费者的二级 region-state；未改业务、API/Mock 或后端。
- UI-012B 已完成全量收口：串行 Vitest 42 files / 207 tests、仓库 ESLint、typecheck、生产 build（4532 modules）及 diff-check 全通过；入口维持 632.44 kB，P02/P03/P04 继续为独立 lazy route chunk。门禁摘要原位于 `frontend/test-results/ui-012b/final-gate-summary.md`，当前源码归档不再捆绑该运行输出。

以上工程收口不会改变本文的业务边界：后端实现、数据库、Worker、Outbox、producer、真实 API 联调和生产验收不在当前前端完成声明中。
