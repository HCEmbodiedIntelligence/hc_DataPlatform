# 前端 UI 重构实施计划（最终工程收口）

> 更新时间：2026-08-13 00:12 CST
> 状态：P01–P19 最终视觉、a11y、工程门禁与证据汇总已完成
> 范围：React 前端、公共 Shell/UI、前端可观察合同与验收
> 总体计划：[`plan/FINAL-IMPLEMENTATION-PLAN.md`](../plan/FINAL-IMPLEMENTATION-PLAN.md)

## 1. 当前结果

- P01–P19 已完成公共 UI 迁移且最终主证据已齐；UI-012A3A 九页已由主调度关闭，专属视觉/四断点/键盘/200% zoom 11/11，后续全量 200+、lint/typecheck/build 通过。正式参考、1672×941 运行证据、目视结论及保留差异见 `docs/FRONTEND-UI-BASELINE.md`。
- Shell/主题已统一为 218px 桌面侧栏、64px 顶栏、青绿/中性色、线性图标、低圆角、细边界、紧凑密度和一致的交互反馈。
- 标准列表页、详情页、管理三栏页与 P08/P11 工作台都保留现有路由、Schema、Adapter、Query、权限和状态机；视觉任务没有改变业务合同。
- UI-012B1/B2/B3/B4/B5/B6/B7 已完成 lint/孤儿清单、route chunk/Bundle、跨页巡检、16 组精确 a11y 修复及安全清理；UI-012B 已汇总最终全量门禁并关闭。
- 所有完成项均未独立复检。用户已明确不再安排复检；执行者按独占范围完成门禁后直接关闭任务。

## 2. 不得改变的边界

### 范围与真实能力

- 不创建或恢复 `backend/**`，不定义数据库、Worker、Outbox、producer 或服务端内部实现。
- 不用 Mock、Fixture、截图或合同测试宣称真实后端已经联调或生产可用。
- 未确认的媒体、趋势、导出、策略写入、权限写入、3D 资源和候选接口保持 `feature-unavailable` 或 fail closed。
- P01 真实 API 四个聚合合同未确认前保持 unavailable，不由 UI 猜测或拼装数字。

### 路由、HTTP 与数据

- P01–P19 页面 ID、活动路由和 Navigation Manifest Owner 保持稳定；不可变资源只使用固定 ID，不接受 `latest/current` 身份。
- Query 由目标页 codec 解析、规范化和构造；筛选变化清除失效游标，跨页入口使用 typed route builder。
- 页面不直接 `fetch` 或手拼 URL；请求/响应先过运行时 Schema 和 Adapter，MSW 与真实 API 复用同一合同链。
- wire 使用 `snake_case`，ViewModel 使用 `camelCase`；int64/bytes/纳秒/大计数保持十进制字符串，缺失、`null`、零和 UNKNOWN 不合并。

### 权限、写操作与分页

- 可操作范围始终是 capability、服务端 `allowed_actions`、资源状态和本地有效性的交集；授权未知或过期立即 fail closed。
- 危险写操作保留 preflight、影响摘要、精确确认、幂等和并发前提；失败不得乐观伪造资源或版本。
- 业务表继续使用服务端筛选、稳定排序与 `after/before` 游标，不退回浏览器 offset 或虚假总页数。
- RHF/Zod 继续作为表单值、dirty、校验和服务端错误事实源；`SecureUploadPicker` 只选择本地文件，不自行请求。

## 3. 已落地的 UI 体系

- Ant Design 中文 Provider、统一主题 Token、Lucide 线性图标与平台 Shell。
- `StandardPageScaffold`、`DetailPageScaffold`、`WorkbenchScaffold`、`PageHeader`、`FilterToolbar`。
- `DataTable`、`CursorPager`、`MetricCard`、`StatusTag`、`EntityDrawer`、`DangerConfirmModal`、`PageState`。
- RHF/Zod 受控适配器、`SecureUploadPicker`、CSS Modules 和公共主题层。
- 稀疏真实数据使用常驻事实检视、窗口摘要、结构化 unavailable 和合理容器层级；禁止假行、假曲线、假媒体、假动作或机械拉高单卡。

## 4. 阶段状态

| 阶段                                                  | 状态   | 最终交付                                                            |
| ----------------------------------------------------- | ------ | ------------------------------------------------------------------- |
| 0 基线/ADR 与前端接口需求                             | 已完成 | DOC-UI-001、API-001/API-002；19 页可观察接口合同和当前边界          |
| 1 主题/Shell/公共 UI                                  | 已完成 | Provider、Shell、Scaffold、数据/状态/表单组件；UI-012S 最终壳层证据 |
| 2 试点页 P02/P05/P12                                  | 已完成 | 共享 UI 迁移、稀疏态平衡及 UI-012A3A 最终证据                       |
| 3 标准/详情页 P01/P03/P04/P06/P07/P09/P10/P13/P18/P19 | 已完成 | UI-012A1/A2/A3A 最终逐页证据                                        |
| 4 管理页 P14–P17                                      | 已完成 | 三栏/常驻事实检视与 UI-012A3A 最终证据                              |
| 5 工作台 P08/P11                                      | 已完成 | 安全媒体缺省、时间带、Inspector 与 UI-012A3B 最终证据               |
| 6 证据汇总                                            | 已完成 | UI-012A4 已同步 19 页最终索引、运行证据、目视结论与保留差异         |
| 7 全站工程收口                                        | 已完成 | B1–B7 与 UI-012B 已完成 lazy chunk、a11y、孤儿清理与最终门禁汇总    |

## 5. 当前领取与依赖顺序

1. UI-012A4 已关闭：19 份正式参考、19 张 1672×941 证据与逐页保留差异已同步。
2. UI-012B1/B2 已关闭：lint 阻断已清除，P02/P03/P04 已形成独立 route chunk，生产入口约减少 34%。
3. UI-012B3 已关闭：95 组画布巡检定位了 16 组基础 a11y 发现，未修改产品代码或既有截图。
4. UI-012B4/B5/B6 已关闭：Tabs/Search accessible name 与 P17 重复 ID 均已修复，最终跨页巡检 22/22 转绿。
5. UI-012B7 与 UI-012B 已关闭：6 个零引用旧文件已安全删除，串行 Vitest 42/207、lint、typecheck、生产 build 与 diff-check 全绿。

## 6. 最终工程门禁

UI-012B 已完成以下最终门禁汇总：

- `typecheck`、全量 Vitest、仓库 ESLint、生产 build 和 `git diff --check`。
- P01–P19 跨页 E2E，覆盖主导航、唯一主标题、页级横向溢出、键盘焦点、Escape/焦点归还和 200% zoom。
- route lazy chunk 与主要生产 Bundle 记录；大 chunk 警告按实际依赖和风险说明，不为追求数字破坏页面边界。
- 被删除旧 CSS/组件的精确路径、无消费者证据和可恢复性；不得删除仍被动态导入、测试或路由引用的文件。
- `docs/FRONTEND-UI-BASELINE.md` 中 19 张证据存在、均为 1672×941，且没有批量覆盖既有截图基线。
- UI-012B3 遗留已复跑转绿：P06/P07/P10@390 的 Tabs more、P14/P15/P17@1672/1440/1024/768 的 Search，以及 P17 五档与 200% zoom 的重复 `#tab-compatibility` 均通过。

## 7. 完成定义

全站前端完成要求：

- 页面视觉与正式参考属于同一成熟产品体系，且稀疏数据/能力缺失被诚实表达。
- Route、Query、HTTP、Schema、Adapter、Query Key、权限、状态机、分页、表单和上传安全边界不回归。
- 1440/1024/768/390、200% zoom、键盘、焦点、Escape 和基础 a11y 通过最终巡检。
- 旧实现仅在无消费者证据成立时删除，Bundle/route chunk 和全量门禁结果被如实记录。
- 所有 Mock/feature-unavailable、`draft / user-confirmation-required` 与真实后端未联调能力继续明确列出。

完成上述工作仍只代表当前前端制作与工程门禁直接关闭（未独立复检），不代表后端实现、真实联调或生产验收。
