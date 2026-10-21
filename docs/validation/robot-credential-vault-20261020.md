# 机器人上传凭据查看与删除：本地验证

日期：2026-10-20。本次在现有 A～F 工作区增量实施，保留未提交方案和并行修改，未推送或远端部署。

## 已实现

- 新签发凭据加密保存；机器人详情的“查看凭据”可多次查看、复制同一完整凭据，关闭/刷新不丢失。列表仍只显示版本、前缀和状态。沿用原有 UI 组件与样式，凭据列表可滚动查看全部记录。
- 已撤销、已过期的凭据可确认删除。删除从列表移除并清空密文；保留原 ID、认证摘要和历史上传外键、签发请求回执。重复删除安全，不恢复已停用身份或被撤销/删除的凭据。
- 只有 `ingest_source.manage` 管理权限可以查看或删除；跨组织、跨身份访问拒绝。查看响应 `no-store`，前端不持久保存明文；查看不触发设备认证或改变在线状态。
- 原来只存摘要的旧凭据不可逆，仍保持有效。设备下次使用原凭据成功认证后补存密文，可随后查看；补存前有明确提示。错误密钥/密文篡改返回真实错误，不换发凭据。

## 关键文件

| 文件 | 修改 |
| --- | --- |
| `backend/src/hc_data_platform/robot_ingest/credential_vault.py` | 用现有密钥派生专用加密密钥，认证加密内容绑定组织/身份/凭据 ID。 |
| `robot_ingest/service.py`、`repository.py`、`router.py`、`models.py` | 签发保存、受权查看、删除状态检查、旧凭据认证后补存；内存与 PostgreSQL 实现一致。 |
| `robot_ingest/onboarding.py`、`onboarding_repository.py` | 聚合不再展示已删除凭据；删除最后一份后属于无可用凭据，而非从未签发。 |
| `backend/migrations/ingest/023_robot_credential_vault.sql` | 新增密文、删除时间、删除必须撤销且无密文的检查约束。 |
| `frontend/src/pages/p02-data-sources/components/RobotIdentityConsole.tsx`、`features/robot-ingest/api.ts` | 查看/复制、删除确认、错误重试、关闭清除明文，以及查询刷新。 |
| `backend/openapi/robot_ingest.yaml`、`backend/openapi.generated.yaml`、`frontend/src/shared/api/generated/*` | 同步查看/删除接口、状态字段及请求白名单。 |

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 后端机器人接入与迁移回归 | **122 passed、3 skipped**。包括新增 13 项凭据测试、真实 PostgreSQL、幂等、上传关联、组织隔离、旧凭据补存、匿名拒绝与管理权限。 |
| 前端 P02 与 robot-ingest 回归 | **25 passed**，其中凭据/身份组件 11 项。覆盖关闭后重新查看/复制、撤销/过期删除、确认/取消、失败重试、无管理权限隐藏操作。 |
| 前端类型检查、生产构建 | 通过；构建仍有已有大文件体积提示。 |
| 前端架构、客户端生成器测试 | **54 passed、15 passed**。 |
| Runtime OpenAPI 与生成客户端一致性 | `--runtime --check`、`pnpm gen:api --runtime-contract … --check` 均通过。 |
| 本次 Python 文件 Ruff、前端格式、git diff --check | 通过。 |
| 当前开发数据库迁移状态 | **162/162，current**；无缺失、未知版本或校验和漂移。 |
| 真实 Firefox 浏览器 | 隔离数据库实际签发→关闭→查看→复制→刷新再次查看，保持相同 Token/版本且不变在线；停用状态保持；撤销→取消删除→503 错误→重试删除→刷新无记录；重复删除 204；桌面与 500 px 窄屏布局通过。 |
| 当前本地页面复核 | 真实组织汇总正常，无“机器人接入暂不可用”；上传记录成功量及时区读取正常；窄屏无整页横向溢出。 |

浏览器仅在独立临时 PostgreSQL 中创建和删除验收凭据。503 由验收浏览器注入一次，后续成功重试真实调用接口和数据库；生产逻辑无模拟数据。当前开发库只做迁移及页面只读复核，未撤销、轮换或删除用户的既有凭据。

执行命令（使用仓库已有开发镜像和依赖卷，数据库变量指向一次性隔离库）：

```bash
cd backend
python -m hc_data_platform.core.migrations upgrade
python -m pytest -q -p no:cacheprovider tests/robot_ingest tests/core/test_migrations.py \
  --ignore=tests/robot_ingest/test_processing_integration.py
python -m hc_data_platform.core.openapi --runtime --check
cd ../frontend
pnpm exec vitest run src/pages/p02-data-sources src/features/robot-ingest
pnpm typecheck
pnpm build
pnpm check:architecture
pnpm check:api
pnpm gen:api --runtime-contract ../backend/openapi.generated.yaml --check
```

浏览器结果和截图见 [结果记录](robot-credential-vault-20261020/credential-browser.json)、
[删除确认](robot-credential-vault-20261020/credential-delete-confirm.png)、
[删除错误提示](robot-credential-vault-20261020/credential-delete-error.png)、
[删除后列表](robot-credential-vault-20261020/credential-after-delete.png)、
[窄屏布局](robot-credential-vault-20261020/credential-mobile.png)。截图不包含完整凭据。

## 迁移、启动与限制

迁移 023 已在当前本地开发库和空白隔离库应用，保留原 ID、摘要与历史记录。其他本地副本需使用原连接配置执行 `python -m hc_data_platform.core.migrations upgrade`，再启动/重启对应 API 与前端。保持原 `HC_DATA_SOURCE_CREDENTIAL_KEY`；不需新增环境变量。受限应用数据库角色沿用原凭据表的 SELECT/INSERT/UPDATE 权限与组织 RLS。

旧版只有摘要且设备不再提交原凭据时，不能凭空恢复明文；用户可保留原凭据继续使用，或主动生成新凭据。已失效凭据直接删除即可。删除保留历史关联所需的最小记录，不物理级联删除历史上传。

本次 3 项跳过需要专用 G1 PostgreSQL；排除的 6 项处理集成测试依赖此前已确认缺失的 `/g0/fixtures/robot-ingest-manifest.json`。未复跑全仓已有失败，也未执行真机、实际 OSS/边缘代理端到端验收；此前全仓结果与限制保留在 [A～F 验证记录](robot-onboarding-20261019.md)。
