# P01–P19 需要产品设计/业务确认的决策

> 审计日期：2026-08-17  
> 状态说明：本文件中的事项都没有从 Browser Mock 推断为正式规则。**未确认**表示尚未找到产品批准、正式合同或可执行验收测试。

## 1. 最优先确认的 10 项

| 决策编号 | 页面 | 具体问题 | 推荐选择 | 可选方案 | 不决定会阻塞 | 优先级 | 状态 |
|---|---|---|---|---|---|---|---|
| PD-01 | 全局/P18 | 一个用户的可见范围如何由组织、项目、区域和角色共同决定？是否允许同一用户在不同 scope 拥有不同角色？ | 采用 `principal × project × region × role` 显式绑定；服务端按最小权限求 capability，所有页面查询强制 scope | 仅项目级角色；或完全交给外部 IdP group | 真实 auth bootstrap、P01–P19 全部页面、越权测试 | P0 | 未确认 |
| PD-02 | P01/P05–P19 | 是否允许跨项目或跨区域聚合、搜索和导出？ | V1 只允许单 project + 单 region；跨区域另立受控接口并评估数据驻留 | 允许同组织多区域汇总；或管理员全局汇总 | P01 聚合合同、列表 scope、P19 审计可见范围 | P0 | 未确认 |
| PD-03 | P01/P03/P12/P19 | 默认时间范围、最大范围、时区和夏令时规则是什么？ | DB 存 UTC；项目配置 IANA timezone；默认 24h，常用 7d/30d，`from` 含、`to` 不含；限制最大范围 | 固定 Asia/Shanghai；或用户本地时区 | P01 activity、存储趋势、审计查询、缓存键 | P0 | 未确认 |
| PD-04 | P01 | “dashboard activity”是上传吞吐时间桶，还是用户/系统事件流？ | 把当前路径定义为上传活动时间序列；事件流另建 `/dashboard/events`，不混用 | 只做事件流；或一个响应同时返回两类数据 | activity OpenAPI、Repository、图表和测试 | P0 | 未确认 |
| PD-05 | P01/P03 | `accepted_unique_bytes`、上传成功/失败/terminal 的精确定义和去重边界是什么？ | 以 project+region 内最终 committed object identity 为唯一键；重传不重复计数；session 终态另作 session 指标 | 按上传请求累计；或按 content hash 全组织去重 | P01 activity/snapshot、P03 摘要、容量评估 | P0 | 未确认 |
| PD-06 | P01/P06 | uploaded、validated、viewable episode 分别如何定义；重跑、修订和多版本是否重复计数？ | 定义稳定 episode identity；按选定 dataset version 的最新有效 revision 计数；漏斗阶段单调且可追溯 | 按 rollout；或按物理记录/文件计数 | episode 投影、P01 snapshot、P06 episode 列表 | P0 | 未确认 |
| PD-07 | P01/P12 | RAW/DERIVED/PREVIEW/PUBLISHED 存储角色包含哪些对象；副本、临时文件、删除标记和去重如何计量？ | 建立 canonical inventory；按物理占用和逻辑唯一量分别报告，默认卡片显示物理占用 | 只显示对象存储账单；或只显示逻辑大小 | P01 storage、P12 概览/成本、库存迁移 | P0 | 未确认 |
| PD-08 | P01/P15 | coverage 的机器人组、任务全集和 denominator 从哪里来？未计划的组合是否算缺失？ | denominator 来自批准的 collection plan，机器人组和 task 为版本化目录；未计划组合不进入分母 | 所有 robot×task 笛卡尔积；或仅按历史出现组合 | P01 coverage、robot/task 目录、查询性能测试 | P0 | 未确认 |
| PD-09 | P01/P04/P07/P09–P13 | 工作台待办包含哪些来源、优先级、SLA、去重键、关闭条件和 action target？ | V1 只纳入有真实模型的 failed upload、QC risk/reject、annotation review、version review；每类定义稳定 source id、severity 和 deep link；缺失域后续加入 | 直接采用 Mock 六枚举；或展示所有 workflow failure | pending-items 合同、union 查询、分页、通知/跳转 | P0 | 未确认 |
| PD-10 | P01/全局 | 某个数据源超时/失败、空数据、无权限和陈旧数据分别如何展示；允许多旧？ | 响应返回 `as_of` 和 per-section status；单域失败显示部分结果+明确告警，不回退 fixture；默认新鲜度目标 5 分钟 | 任一失败整页失败；或静默使用缓存 | P01 Service/前端降级、错误码、SLO 和 E2E | P0 | 未确认 |

## 2. 其余页面级产品决策

| 决策编号 | 页面 | 背景与具体问题 | 推荐选择 | 可选方案 | 不决定会阻塞 | 优先级 | 状态 |
|---|---|---|---|---|---|---|---|
| PD-11 | P02 | 当前只有前端数据源 Mock，后端没有 connector/credential 模型。首批支持哪些数据源；凭据由谁拥有；测试连接能否访问任意地址；删除后历史如何处理？ | 首批只支持经 allowlist 的 S3/MinIO；保存 secret reference 而非明文；连接测试固定 egress policy；删除改为停用并保留历史引用 | 一次支持本地盘/HTTP/数据库；允许用户自定义 endpoint | source 模型、密钥集成、SSRF 防护、P02 API | P1 | 未确认 |
| PD-12 | P03/P04 | 创建上传需要哪些必填元数据；暂停/取消/重试在哪些状态允许；同一 manifest 重提如何处理？ | 采用服务端状态机和 Idempotency-Key；cancel 不物理删除已提交事实；只允许对明确失败阶段重试 | 客户端决定命令；重复 manifest 总是新 session | P03 创建合同、P04 命令、并发/幂等测试 | P0 | 未确认 |
| PD-13 | P05/P06 | dataset 的业务身份、名称唯一范围、owner、创建来源、归档和删除语义是什么？ | dataset id 不变、名称在 project+region 内唯一；owner 可转移；只允许 archive，不在 UI 直接硬删 | 名称全组织唯一；允许管理员硬删 | 数据集模型补充、列表/创建、权限和审计 | P1 | 未确认 |
| PD-14 | P07 | version review 由谁发起/批准；需要一人还是双人；哪些 checks 阻塞批准；退回后生成新版本还是修改原版本？ | 发布者发起、reviewer 批准且不得自审；阻塞 checks 必须全过；退回后创建新 revision，已批准版本不可变 | 管理员可自审；原地修复版本 | version 状态机、审批命令、并发和审计测试 | P0 | 未确认 |
| PD-15 | P08 | annotation task 如何分配、锁定和超时；annotator/reviewer 能否是同一人；区域是否强约束？ | 显式 assignee+租约；annotator 与 reviewer 分离；任务固定 project+region | 抢占式无锁；允许自审 | P08 合同适配、锁/冲突、权限测试 | P1 | 未确认 |
| PD-16 | P09 | manual issue 是独立业务对象还是 annotation/QC 的视图；分类、severity、owner、SLA、关闭和重开如何定义？ | 独立 issue，引用不可变 source evidence；状态 `OPEN→IN_PROGRESS→RESOLVED→CLOSED`，允许有权限者 reopen | 只做 QC report 过滤视图；或复用 annotation task | issue 表/API、P01 pending、统计口径 | P0 | 未确认 |
| PD-17 | P10/P11 | cleaning draft/EDL 的操作集合、基线版本、协作方式、冲突处理、提交和回滚语义是什么？ | 不可变 base version + 可重放 operation log；单 writer 乐观锁；preview 隔离；commit 生成新 dataset revision，失败可补偿 | 直接原地编辑 Lance；多人实时协作 | 草稿/操作模型、preview/apply Worker、数据安全验收 | P0 | 未确认 |
| PD-18 | P12 | 当前 page/routing/T2 已移除费用 Tab，但 API client、query 和 Mock handler 仍保留 cost-breakdown 草案。成本是否仍属于 P12 V1；若属于，来自云账单、库存估算还是固定单价？ | V1 与当前 UI 一致，只交付容量/Inventory/Multipart；将 cost 草案明确标为 deferred，待账单源、币种、税、延迟和分摊规则批准后再启用 | V1 用版本化单价做 estimated cost；或直接接云账单 | P12 正式范围、是否保留 cost OpenAPI/类型/Mock、财务准确性说明 | P1 | 未确认 |
| PD-19 | P13 | 生命周期策略的匹配优先级、保留期、legal hold、dry-run、审批、批次上限、恢复窗口和失败补偿是什么？ | 默认 dry-run；deny/delete 冲突时 legal hold 优先；双人批准后分批执行；保留 execution ledger；定义恢复窗口 | 管理员单击立即执行；只调用存储原生 lifecycle | policy/simulation/execute/restore 全链路 | P0 | 未确认 |
| PD-20 | P14/P15 | robot model、robot、component 的唯一标识、版本关系、资产格式、区域迁移、停用及历史引用规则是什么？ | 型号版本不可变；robot serial 在组织内唯一；component 有有效期；迁移保留历史；停用不删除 | 允许原地修改型号；serial 仅项目内唯一 | P14/P15 模型、P01 coverage、ingest 外键迁移 | P1 | 未确认 |
| PD-21 | P16 | calibration 类型、适用 robot/component、有效时间、覆盖优先级、批准、撤回和回滚规则是什么？ | calibration version 不可变；按 component+time 精确匹配；发布前格式/范围/兼容检查；撤回不删除历史 | 永远使用最新；允许覆盖已发布内容 | calibration 模型、校验 Worker、数据解释一致性 | P0 | 未确认 |
| PD-22 | P17 | Schema 的命名/版本、兼容级别、审批、废弃、迁移和消费者约束是什么？ | SemVer + 默认 backward compatibility；发布版本不可变；breaking change 要新 major 和迁移计划 | 无兼容门禁；或只按内容 hash | registry、compatibility checker、P03/P05/P07 验证 | P0 | 未确认 |
| PD-23 | P18 | 身份来源、邀请/移除、角色集合、自身降权、最后一个管理员、权限生效延迟如何处理？ | 外部 IdP 负责身份，平台维护 scope binding；禁止移除最后一个 admin；变更即时失效 token/cache 并审计 | 平台自管账号；权限到 token 过期才生效 | auth bootstrap、member API、安全/E2E | P0 | 未确认 |
| PD-24 | P19 | 谁能看哪些审计事件；哪些字段必须脱敏；保留多久；导出是否需要审批/水印/上限？ | 默认管理员只看授权 scope；secret/PII 永不落 event payload 或读时脱敏；在线保留 180 天；大导出异步审批并审计 | 全组织管理员可见所有；永久在线保留；同步 CSV | audit read/export API、索引/分区、安全和成本测试 | P0 | 未确认 |

## 3. 决策填写模板

每项决策完成时，应追加而不是覆盖原问题，并记录：

- 决策编号与最终选择；
- 适用 scope、例外和反例；
- 生效日期、决策人和需要更新的正式需求；
- 可执行的验收样例（正常、空数据、无权限、冲突、部分失败）；
- 对 OpenAPI、数据库迁移、Worker、前端和历史数据的影响。

## 4. 不需要再次进行产品设计、可直接进入技术/编码的边界

以下事项本身是工程一致性要求，不应等待新的产品创意；但它们仍依赖上表相应业务值：

- 用后端运行时 OpenAPI 作为唯一生成源并在 CI 检查 drift；
- 接通真实 principal/token/scope/capability bootstrap；
- 统一 RFC 9457 Problem Details、request id、稳定错误码；
- 所有 list API 使用同一 cursor/filter/sort/time 规范；
- 所有命令使用状态机校验、Idempotency-Key 和乐观并发；
- Repository 强制 project/region scope，不能依赖前端过滤；
- 真实模式不允许静默回退到 Browser fixture；
- 为 Router/Service/Repository/Worker 增加单元、外部依赖集成、E2E、越权和性能测试；
- 记录 query latency、result size、cache/rollup age、Worker lag 和 stable error code；
- 部署前重跑当前 chart 的 pilot、升级/回滚和容量验收。

## 5. 2026-08-17 效果图评审已确认的产品决策

本节追加记录用户已明确给出的答案。它不删除前文问题；与前文推荐方案冲突时，本节为当前产品决定。

| 决策编号 | 页面 | 已确认决定 | 影响 |
|---|---|---|---|
| PD-FE-01 | 认证/P18 | 用户名+密码直接注册；不需要管理员、邮箱或手机号审批/验证；注册后为空账户 | 覆盖前文账户 `PENDING`、外部 IdP 默认和个人私有项目方案 |
| PD-FE-02 | P18 | 只有加入项目和权限申请需要审批 | P18 API/迁移/审计只围绕 membership 和 capability request |
| PD-FE-03 | P20 | 采集任务不进行人员/PICO/机器人分派 | P20 不设计 assignment 表、字段或按钮 |
| PD-FE-04 | P20 | 采集任务无开始/结束时间，无暂停/继续；一直有效直至关闭 | 覆盖 D03 生命周期；只保留创建、编辑、关闭和查看 |
| PD-FE-05 | P20/P03 | 任务创建不配置模态/Topic；使用任务描述；相机/Topic 由上传 Manifest 自动识别 | 任务合同与 ingest 元数据边界确定 |
| PD-FE-06 | P03/P04 | 需要真实上传操作页；上传和上传记录属于同一功能页 | P03 采用新建上传/上传记录 Tab，P04 为包详情 |
| PD-FE-07 | P08/P11 | 标注与清洗为同一功能，不保留独立清洗页面 | 覆盖 PD-17 的独立 cleaning domain 假设；需设计历史数据迁移 |
| PD-FE-08 | P08 | 相机同时显示且由数据自动添加；所有相机共用一条对齐时间轴 | 工作台布局和 Manifest adapter 已确定 |
| PD-FE-09 | P08 | 必须支持多级 Tag；审核主要检查 Tag，而不是重复检查已通过的一致性 | Tag Schema、验证和审核合同必须先设计 |
| PD-FE-10 | P12/P13 | 容量按 Raw、标注完成、待标注、问题数据；不需要冷热/中容量，也不需要模拟影响 | 覆盖 PD-19 的 UI dry-run/simulation 推荐；仍需确认危险操作安全门禁 |

尚未确认但因上述决定新增的 10 个问题，维护在 `P01-P20-POST-EFFECT-REVIEW-IMPLEMENTATION-PLAN.md` 的 OPEN-01～10。

## 6. 2026-08-18 P01、真实主链与公网发布正式覆盖决策

本节记录用户于 2026-08-18 对新会话接续文件第六节 1～12 项推荐方案的整体确认。以下决定均为**正式确认**；它们追加而不删除历史问题，并覆盖与其冲突的旧推荐。确认本节不等于代码、测试、真实网络主链或生产发布已经通过。

| 决策编号 | 关联旧决策/领域 | 正式决定 | 适用边界与反例 | 状态 |
|---|---|---|---|---|
| PD-BR-01 | PD-01/PD-02，P01/全局 | P01 V1 固定一次请求只查询一个项目和一个区域，不提供跨项目或跨区域汇总。服务端和 Repository 都必须强制精确 scope。 | 管理员身份也不自动获得跨区域聚合；未来如需跨区域，另立受控合同、权限和数据驻留评审。 | 正式确认 |
| PD-BR-02 | PD-03，P01 | 默认展示最近 24 小时，可切换最近 7 天或 30 天；使用项目配置的 IANA 时区；时间区间统一为 `[from,to)`，持久化时间使用 UTC。 | V1 页面提供 24h/7d/30d 预设；wire request 仍显式传 `from`、`to`、`timezone`，不由服务端猜测浏览器时区。任意自定义范围和生产查询 SLO 不由本决定推导。 | 正式确认 |
| PD-BR-03 | PD-04/PD-05，P01 activity | `dashboard activity` 定义为最近业务事件流，不是上传吞吐时间桶或字节趋势图。 | 旧 PD-04 的“上传活动时间序列”推荐被覆盖。事件必须来自真实业务事实，并有稳定事件身份、事实时间、scope 和去重规则；没有事实源的事件类型不得由 Mock 补造。 | 正式确认 |
| PD-BR-04 | PD-08，P01 coverage | 在版本化采集计划、机器人组和任务分类形成可追溯 denominator 前，coverage 保持 `BLOCKED`，不得返回 0%、100% 或基于历史出现组合伪造的百分比。 | 可以保留受 scope 约束的 observation/numerator 查询作为技术证据，但不能把它包装成覆盖率完成。 | 正式确认 |
| PD-BR-05 | PD-09，P01 pending-items | 待办 V1 只纳入上传失败、QC 异常、待 Tag 审核和待发布；按严重程度和等待时间排序，并与当前 principal 的 capability 取交集。 | 每类必须使用稳定 source id、真实状态、可授权 deep link 和明确关闭条件；不纳入 Browser Mock 六枚举、生命周期模拟或尚无事实模型的待办。未确认的 SLA 数值不得硬编码。 | 正式确认 |
| PD-BR-06 | PD-07，P01/P12 | 区域 Dashboard 不显示无法按 region 可靠归属的存储容量；容量只在 P12 展示。 | 不能把只有 project scope 的 storage inventory 塞入 region 查询，也不能以 `BLOCKED` 卡片诱导用户认为 P01 将提供容量；P01 正式响应/页面应移除该区域容量展示。 | 正式确认 |
| PD-BR-07 | P01/publishing | 增加 `rollout → publication` 的区域血缘。该关系建立并可追溯前，区域“已发布”指标保持 `BLOCKED`。 | 新写入必须保留 project、region、rollout、dataset/publication version 和事实时间；历史数据只在有确定关系时回填，禁止按项目默认区域猜测。 | 正式确认 |
| PD-BR-08 | PD-12，P03/P04/Worker | 上传完成后由后端自动、幂等地启动 Manifest/QC Worker；不提供用户手动启动按钮或公开 start-ingest 命令。 | HTTP 成功不能早于持久化触发事实；重复 complete、消息重放和 Worker 重启不得启动重复副作用。失败需可重试、可查询、可审计。 | 正式确认 |
| PD-BR-09 | PD-15，Lance/P08 | Lance 准备完成后由系统自动创建标注任务；不增加公开人工创建标注任务接口。 | 自动任务必须绑定 project、region、rollout、Lance/dataset version、基线步数和兼容的已发布 Tag Schema。依赖缺失时明确阻断/重试，不暴露任意 seed API。任务如何领取或租约不由本决定额外推导。 | 正式确认 |
| PD-BR-10 | 公网入口/可观测性 | 生产 `/metrics` 仅允许内网访问；Swagger、ReDoc 和 OpenAPI 默认关闭，确有运维需求时仅通过受控管理入口开放。 | 开发/测试环境可按显式 profile 启用；不能把开发默认直接带到公网生产，也不能只依赖“不在 OpenAPI”作为保护。 | 正式确认 |
| PD-BR-11 | TD-03/公网错误 | 所有 5xx 使用统一脱敏的 RFC 9457 Problem Details；详细异常只进入受控安全日志。 | 5xx 不透传 `HTTPException.detail`、内部 header、DSN、对象路径或堆栈；4xx 仍可返回经过 allowlist 的安全业务 detail。响应与安全日志用 request id 关联。 | 正式确认 |
| PD-BR-12 | PR-07/BE12-008，发布治理 | 5 TB/day + 30% 容量继续作为生产发布阻塞，但不阻塞当前功能开发、前端集成和非生产验证。 | 不得降低 75.23 MB/s 目标、删除 XFAIL、过滤 skip 或用局部/内存性能冒充生产等价全链路证据。 | 正式确认 |

### 6.1 已确认的产品边界

- P01 的 scope、时间预设、activity 语义、coverage 阻断、待办 V1 来源、区域容量去除和发布血缘策略已经冻结。
- 上传自动触发 Worker、Lance 自动创建标注任务、生产 metrics/docs 暴露策略、5xx 脱敏和容量阻断范围已经冻结。
- 上述决定与 Browser Mock、效果图示例数字、草案接口或当前未完成实现无关；这些材料不能改变正式决定。

### 6.2 仍需技术设计，但不再需要重复产品确认

- activity 真实事件目录、事件稳定 ID、同一事实的去重键、展示字段和稳定排序；不得重新讨论其是否为吞吐图。
- pending 四类来源的跨域 union、严重程度规范化、确定性并列排序、deep link allowlist、关闭投影和 capability 交集实现。
- rollout→publication 血缘的表结构、正向写入、可重入回填、索引和历史不可归属数据的 `BLOCKED/PARTIAL` 投影。
- 上传完成触发的事务/outbox/Temporal 边界、确定性 workflow ID、重放幂等和失败恢复。
- 自动标注任务的唯一键、默认/绑定 Tag Schema 选择、并发 winner、Lance version 追踪和审计事件。
- capability key 与旧领域 role 的单一映射源，以及 P20/ingest/annotation 的统一可查询审计事实。
- 生产入口对 metrics 和文档的网络/认证实现，以及安全日志的访问、保留和脱敏策略。

### 6.3 仍未由本次确认解决的问题

- session TTL、并发 session 上限、账户全局撤销/恢复流程；
- 注册/IP/设备限流、登录渐进延迟和锁定的准确数值；
- 是否存在跨域部署及其 CORS allowlist；
- 各公网 route 的生产 query/rate admission 数值、统一分页改造和完整审计事件目录；
- P01 其余 episode/work/signal 指标中尚未冻结的唯一计数身份、状态集合和新鲜度 SLO；
- 生产 p95/p99、持续容量、当前 chart 升级/回滚、备份恢复和真实浏览器主链。

这些事项继续保持未确认、`PARTIAL`、`NOT RUN`、`XFAIL` 或相应阻断状态；不得因本节 12 项已确认而自动提升为 `PASS`。
