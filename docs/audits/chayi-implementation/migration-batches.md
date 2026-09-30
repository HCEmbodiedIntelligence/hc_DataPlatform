# W7 批次原子发布与指针回退

2026-09-30，隔离 `hc-chayi-g1`，未修改 8088 业务数据。新增
`episode_migration_batch.py prepare/publish/status/rollback` 与追加迁移
`episode_store/0016_migration_batches.sql`；隔离数据库已应用 150 项，重复 upgrade
无变更。此工具接收已经完成固定版本转换、物理提交的 checkpoint 目录。

`prepare` 重新核对旧 revision 行哈希、Lance 全行、转换文件、MinIO 全包和提交
收据，通过现有 RealSense SDK 处理生成浏览器预览，保存不可变批次计划。它会
写准备记录和派生预览，但不改变当前版本；不是只读 inventory。每个成员保留
旧引用、转换计划、提交收据与 revision 行哈希，明确不自动转移历史审核结论。

`publish` 再核对全部主文件和预览，检查冻结的旧集合头、Lance 头、产品快照和
成员指针，然后在同一 PostgreSQL 事务登记集合、全部成员指针、完整页面快照
和批次发布收据。保留身份、数值/时间、Schema、标定、来源及 inclusion，改变
存储绑定的 revision 获得新 ID，人工数据审核状态清空。已有业务发布默认值不变。
不完整窗口、时间不符、缺失通道/附件、冲突引用不能悄悄通过；需要单独处理。

旧页面 stream 可能仍固定到更早的逻辑 Lance 版本。这里逐条验证该版本的同一
rollout/schema/逻辑行哈希，而不是把集合编号伪装成物理 Lance 版本。新 stream
仅包含 `package_binding`，深度标为 DEPTH；浏览器预览属于独立派生文件，主
H.265 RGB/gray12le 深度文件逐字节复用，不再次量化。

`rollback` 只允许当前批次仍为头部且成员指针未被后续任务改变，核验旧 Lance
实际全行及原产品快照后原子恢复指针。新的标准包、预览、产品版本、集合和旧
Lance 均保留；明确版本仍可读取。ROLLED_BACK 为终态，普通 prepare/publish
重试返回该状态，不重新激活。已经保留的集合号不能被另一份旧准备计划复用。
数据库另外阻止改计划、伪造提前发布、非法状态转换和删除批次记录。

验证结果：

- [最终 19 项批次服务测试](migration-batches-tests.xml)全部通过，无 skip/xfail。
  覆盖实际两 Episode Lance/RGB-D、SDK 预览、准备/发布六处及回退两处中断、
  三路并发、分批混合存储、后续工作保护、编号竞争、源 revision 改变、
  对象篡改、checkpoint 数据库保护及组织/项目/区域隔离。
- [扩展回归](migration-batches-regression.xml)37 项通过：当时 18 项批次，加
  15 项转换与 4 项清点。最终批次新增预览中断后重跑 19 项；合计 **38 个独立
  用例**。回归有一个已有 Starlette/httpx 弃用提示。Ruff/format/diff 检查通过。
- [实际 CLI](migration-batch-cli-results.json)运行 prepare→publish→status→rollback
  →publish 重试，最后仍为 ROLLED_BACK，报告权限 0600、不可覆盖。回退后从
  MinIO 下载两份完整包，[禁网官方 LeRobot 0.6.1 reader/DataLoader](migration-batch-offline-reader.json)
  读取成功，独立复算全行逻辑哈希、媒体哈希及 HC 掩码；不导入平台模块。
- [实际 HTTP](migration-batch-http-results.json)通过标准会话/能力授权，确认默认
  旧版本、归档集合 bootstrap、新包详情、两条 RGB/深度预览及字节哈希、Range
  206、有效深度米值和无效值 null；未登录 401，测试会话均注销。未执行浏览器。

最后 CLI 完整工件位于
`backend/.g1-artifacts/migration-batches/278937fedcc94feabe0727b27bc5052e`，包含
原 Lance、转换 spool、各阶段报告和回退后重新下载的标准包；未删除。HTTP 驱动
在 `backend/.g1-artifacts/migration-batch-http.py`。早期失败分别是测试错误解包
helper、源深度时间证据未补齐、错误使用对象存储测试方法/参数，修正后重跑；
对应失败 XML 保留在 `.g1-artifacts/migration-batches-before-*.xml`。

复现最终服务测试：

```bash
bash deploy/chayi/compose.sh run --rm --no-deps test -q \
  tests/episode_store/test_migration_batches_services.py \
  --fail-on-skipped --fail-on-xfailed --tb=short \
  --junitxml=/app/.g1-artifacts/migration-batch-previews-tests.xml
```

操作命令（所有连接和凭据由受限环境提供，不进入报告）：

```bash
# 必填 HC_EPISODE_MIGRATION_DSN、HC_EPISODE_MIGRATION_OBJECT_ENDPOINT、
# *_OBJECT_BUCKET、*_OBJECT_ACCESS_KEY、*_OBJECT_SECRET_KEY、*_LANCE_ROOT。
# 可选 *_OBJECT_REGION 和 JSON 格式 *_LANCE_OPTIONS；没有应用配置回退。
PYTHONPATH=backend/src python backend/tools/episode_migration_batch.py prepare \
  --organization "$MIG_ORG" --project "$MIG_PROJECT" --region "$MIG_REGION" \
  --dataset "$MIG_DATASET" --batch-key "$MIG_KEY" \
  --plan-dir "$MIG_SPOOL_PLAN_A" --plan-dir "$MIG_SPOOL_PLAN_B" --output "$MIG_PREPARED"
# publish 使用相同 scope、--batch-id mb_*、全部 --plan-dir 和新的 --output；
# status/rollback 使用相同 scope、--batch-id 和各自新的 --output。
```

G2 须先部署能读取新旧存储及 SDK 预览的兼容服务端，再运行上述追加 SQL；保留
spool、原件和旧 Lance。在已经提交新包后，不能回退到只能读取旧 Lance 的
二进制。本轮仅服务/工具演练，尚未执行现网备份恢复、任务排空或机器人 R5 安装。

**限制仍在：** 样本均为两条四帧的合成 RGB-D，旧产品投影为隔离夹具；不算真实
历史副本、规模、真人或浏览器验收。旧标注/提交/审核没有被本工具改写，但本轮
批次夹具没有非空旧审核链，完整等价映射/附件验收继续。历史对齐媒体、MKV/
数组的输入适配及 plan/stage CLI、真实完整副本、浏览器、P06 业务发布适配和
W8 仍需完成。inventory 现在包含批次表，继续核验前需重新生成当前清单。
官方读取成功不代表任何未指定模型已支持深度训练。MIG-01/G1 仍是部分完成。

后续历史副本演练见 [historical-native.md](historical-native.md)：独立恢复完整数据库、
复制原生数据集全部原件/Lance/媒体，完成一条 958 帧四路 RGB 的实际 CLI 发布
与回退、全帧离线读取和非空历史标注行保护。该增量没有将本页合成 RGB-D 证据
改成实采 RGB-D；共享窗口、旧审核等价映射及完整批次仍未验收。
