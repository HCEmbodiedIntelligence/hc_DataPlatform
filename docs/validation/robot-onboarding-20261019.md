# 机器人接入 A～F 本地验证记录

实现与测试日期：2026-10-19；本地旧数据关联复核：2026-10-20。

范围与状态以 [主方案的实现记录](../robot-ingest-onboarding-proposal.md) 为准。未推送、未远端部署；未更换既有凭据密钥。开发开始时检查工作区并阅读仓库说明，未发现适用于本目录的 AGENTS.md。保留方案原文及其他并行修改，包括 MCAP 原生处理、模型快照、LeRobot 处理器和工作流中的无关修改。

## 实际修改

| 入口 | 实现 |
| --- | --- |
| `frontend/src/pages/p02-data-sources/page.tsx`、`external-page.tsx` | 双标签入口，外部连接器保留项目/Region、旧详情及原有编辑能力。 |
| `components/RobotOnboardingTab.tsx`、`OnboardRobotDialog.tsx`、`RobotIdentityConsole.tsx` | 原 UI 样式下的组织列表、四项统计、URL 筛选、接入步骤及嵌入身份详情；修复窄屏表格撑大页面。 |
| `backend/src/hc_data_platform/robot_ingest/onboarding*.py` | 真实聚合、分页前筛选、通信状态、故障恢复、接入编排和成功提交量。 |
| `robot_ingest/service.py`、`repository.py`、`models.py` | 身份复用、凭据行锁与请求回执、更新字段合并、认证观测，保持停用状态。 |
| `backend/migrations/ingest/021_robot_onboarding.sql` | 凭据请求回执、索引与认证函数更新，撤回凭据库检查写入的无依据 ONLINE。 |
| `lerobot_imports/robot_eligibility.py`、`configuration.py`、`mcap_processing.py`、`router.py` | 使用权威组织机器人，保留旧项目停用与撤权限制，并保留任务/数据集/Region/权限检查。 |
| `frontend/src/features/robot-ingest/SuccessfulVolume.tsx`、`onboarding.ts`、P03 页面 | 成功原始字节量与详情采用同一接口，显示 Asia/Shanghai，查询失败显式显示暂不可用。 |
| `backend/openapi/robot_onboarding.yaml`、`backend/openapi.generated.yaml`、前端 `shared/api/generated/*` | 补齐真实接口合同及运行时白名单，修复请求在浏览器发出前被拦截的问题。 |

## 执行结果

测试在仓库现有开发镜像内运行，使用源码挂载和现有前端依赖卷；数据库测试指向新建、已应用全部迁移的临时 PostgreSQL，未使用用户组织作为新设备验收数据。

| 检查 | 结果 |
| --- | --- |
| 相关后端回归 | 190 passed、12 skipped。 |
| 最后一次 MCAP 兼容边界复跑 | 1 passed。 |
| 全量后端 | 2103 passed、15 failed、422 skipped、2 xfailed。 |
| 上述 15 个失败在修改前 HEAD 的复跑 | 15 个全部复现，未扩大本次修改范围处理。 |
| 相关前端回归 | 100 passed；最后外部页面微调后该页面 2 passed。 |
| 全量前端 | 1192/1193 passed；P21 原始文件下载测试未等到异步按钮。其整个文件随后单独复跑 13 passed。 |
| 前端类型检查与构建 | 通过；Vite 有大于 500 kB 的已有体积警告。 |
| 前端架构、生成器测试 | 54 与 15 项通过。 |
| runtime OpenAPI、生成客户端 `--check` | 通过，新增操作已进入请求白名单。 |
| Makefile 默认的 formal OpenAPI `--check` | 当前与 HEAD 基线都报生成文件过期：仓库现存文件采用 runtime 合同，默认命令生成 formal 合同。本轮按原 runtime 口径同步生成并验证，未覆盖为另一份合同。 |
| 本轮文件 Ruff / Prettier、`git diff --check` | 通过。 |
| 全仓 Ruff / 格式 / mypy | 分别有 153 项 lint、53 文件格式、2321 项类型问题；HEAD 基线分别为 154、52、2321。本轮涉及文件无新增诊断，新增格式差异来自保留的并行修改。 |

[机器可读结果摘要](robot-onboarding-20261019/results.json)列出了全量失败测试名。全量失败涉及既有标注/数据集合同、Episode 模块依赖、CRC 检查、发布基线、Compose、工作流注册、存储时间窗口，以及缺少 Helm、专用数据库配置等条件；不能据此声明仓库整体检查通过。

相关后端命令如下。运行时显式提供一次性数据库的 `ROBOT_INGEST_POSTGRES_DSN`、`HC_TEST_POSTGRES_DSN` 和本工作区的 `HC_MIGRATIONS_DIR`。

```bash
cd backend
python -m hc_data_platform.core.migrations upgrade
python -m pytest -q -p no:cacheprovider \
  tests/robot_ingest tests/lerobot_imports tests/data_sources \
  tests/continuous_recordings/test_mcap_native.py tests/core/test_migrations.py \
  --ignore=tests/robot_ingest/test_processing_integration.py
python -m hc_data_platform.core.openapi --runtime --check
```

`test_processing_integration.py` 的 6 项实际尝试因缺少 `/g0/fixtures/robot-ingest-manifest.json` 失败，因此未包含在 190 项通过数字中。12 项跳过依赖 G1 原始数据、注册/媒体服务和 OpenArm 专用 PostgreSQL/MinIO/Temporal。测试没有用虚构 READY 结果替代这些缺失条件。

```bash
cd frontend
pnpm exec vitest run
pnpm exec vitest run src/pages/p02-data-sources src/pages/p03-upload-jobs \
  src/features/ingest/processing src/features/ingest/api src/features/robot-ingest
pnpm typecheck
pnpm build
pnpm check:architecture
pnpm check:api
pnpm gen:api --runtime-contract /tmp/hc-runtime-openapi.yaml --check
```

这里的 runtime 文件通过 `python -m hc_data_platform.core.openapi --runtime --output /tmp/hc-runtime-openapi.yaml` 生成。

## 浏览器验证

使用真实 Firefox、WebDriver、开发 API 和 PostgreSQL。通过临时本地管理员会话登录，验证结束后撤销会话。上传凭据没有写入截图或测试报告。

隔离环境完整检查：新实例与模型绑定、首次凭据签发、响应已丢失但后端完成后的重试、重复请求不再返回明文、真实 Bearer 心跳上线、停用后重放保持停用、可用凭据与上传权限组合筛选、刷新恢复、卡片重置业务条件且保留排序/页大小、标签筛选隔离、空结果、503 错误/重试恢复、第二页搜索命中完整结果集并回第一页、边缘代理/OSS 表单、上传记录量与时区、窄屏布局。

错误场景在验收浏览器中拦截一次响应或注入 503，实际创建、认证、心跳、停用和统计仍通过真实 API 执行。这些只用于验证失败恢复，不属于生产逻辑。

[完整检查记录](robot-onboarding-20261019/isolated-browser.json) · [当前本地页面复核](robot-onboarding-20261019/current-browser.json)

- [当前机器人页面](robot-onboarding-20261019/robots-final.png)
- [窄屏页面](robot-onboarding-20261019/robots-mobile.png)（Firefox 实际视口 500 px，文档宽 488 px；表格在容器内滚动）
- [接入详情](robot-onboarding-20261019/onboarding-detail.png)
- [部分失败恢复提示](robot-onboarding-20261019/onboarding-partial-failure.png)
- [外部 OSS 表单](robot-onboarding-20261019/external-connector-form.png)
- [空状态](robot-onboarding-20261019/robots-empty.png)
- [注入 503 时的暂不可用状态](robot-onboarding-20261019/robots-unavailable.png)

## 迁移及限制

迁移 021 已在空白隔离库和当前本地开发库应用。只读校验当前库的 1 条旧机器人数据源：悬空 Robot ID、缺失项目关联、重复有效身份、同范围旧配置状态冲突、成功 Raw 缺少提交时间均为 0。旧详情/API 继续兼容，主入口改为统一机器人接入。没有删除旧记录或重建身份。

其他本地副本需先应用迁移并重启对应后端，再使用同步生成的前端客户端。使用受限数据库应用角色时，按原授权流程添加新回执表的 SELECT/INSERT 权限；保持组织 RLS，不变更签名/加密密钥。建议采集端每 60 秒报告一次心跳，300 秒无有效通信即离线；旧端上传认证仍有效。

尚未执行真实机器人、实际 OSS/边缘代理连接和专用环境中的完整 LeRobot/MCAP 端到端处理。本轮没有实现后续 G～J 的连接票据、机器人端选任务录制与自动上传。原始测试日志和浏览器运行脚本保留在本机 `/tmp/hc-onboarding-validation`；永久摘要和脱敏截图保存在本目录。
