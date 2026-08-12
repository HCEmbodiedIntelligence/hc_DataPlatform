# 集成问题清单（Integrator 审查产出）

审查时间：2026-08-11
审查者：Integrator（只读体检，未修改任何业务代码）
供后续「专家修复终端」按优先级逐项处理。

---

## 0. 本轮验证基线（真实退出码，非推断）

| 检查项 | 命令 | 结果 |
|---|---|---|
| 前端类型 | `./node_modules/.bin/tsc -b --pretty false` | ✅ exit=0，0 error |
| 前端单测 | `./node_modules/.bin/vitest run` | ✅ 23 files / 114 tests 全绿 |
| 前端 ESLint | `./node_modules/.bin/eslint . --ext .ts,.tsx` | ❌ 4 error / 2 warning |
| 后端单测 | `uv run pytest -q` | ✅ 28 passed |
| 后端 ruff | `uv run --with ruff ruff check` | ❌ 54 error（6 可自动修） |
| Alembic heads | `uv run alembic heads` | ⚠️ 7 个独立 head，未合并 |
| 空库 upgrade | `alembic upgrade heads`（SQLite） | ❌ 失败，`CREATE SCHEMA` 语法错误 |
| 运行时 OpenAPI | `app.openapi()` | ⚠️ 225 operations，70 个未打 tag |

后端 ruff 已可运行（此前 spawn 失败的问题已解决），但需通过 `uv run --with ruff` 调用；
ruff 仍未写入 `pyproject.toml` 的 `[dependency-groups].dev`。

---

## P0 — 阻断收口，必须修

### P0-1 空库 Alembic upgrade 失败：`CREATE SCHEMA` 不兼容 SQLite

**现象**

```
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) near "SCHEMA": syntax error
[SQL: CREATE SCHEMA IF NOT EXISTS manual_cleaning]
```

**根因**

两个 migration 无条件执行 PostgreSQL 专有 DDL，没有按 dialect 分支：

- `alembic/versions/annotation_0002_annotation.py:32` → `op.execute("CREATE SCHEMA IF NOT EXISTS annotation")`
- `alembic/versions/cleaning_0002_manual_cleaning.py:39` → `op.execute("CREATE SCHEMA IF NOT EXISTS manual_cleaning")`

对应的 `downgrade()` 里 `DROP SCHEMA IF EXISTS ...` 同样有问题。

其他域（access / datasets / ingest / robotics / storage / foundation）都没有用 named schema，
所以只有这两个域阻断 SQLite 路径。

**影响**

收口标准要求「空库 upgrade 到 merge head 成功」。目前本机无 PostgreSQL
（`pg_isready` 不存在、docker 未运行），SQLite 是唯一可验证路径，因此这条收口项当前无法通过。

**修复方向**

在两个 migration 里按 dialect 分支，SQLite 上跳过 schema 创建（SQLite 无 schema 概念，
或用 `ATTACH DATABASE` 模拟）：

```python
def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE SCHEMA IF NOT EXISTS annotation")
    for table in TABLES:
        table.create(bind=bind, checkfirst=False)
```

`downgrade()` 同样处理。注意如果表定义里带 `schema="annotation"`，SQLite 上还需要
把 schema 名映射掉，否则建表也会失败——修完要实际跑一遍验证，别只改 DDL 那一行。

**验证**

```bash
rm -f /tmp/test_alembic.db
env -u PYTHONPATH -u AMENT_PREFIX_PATH \
  DATABASE_URL="sqlite+aiosqlite:////tmp/test_alembic.db" \
  uv run alembic upgrade heads
```

### P0-2 前端 ESLint 4 个 error

收口标准要求 ESLint 0 error。

**`src/pages/p13-storage-lifecycle/query-codec.ts:87`**（2 个）

```
87:18  error  '_after' is assigned a value but never used
87:34  error  '_before' is assigned a value but never used
```

下划线前缀通常意味着「刻意丢弃」，但项目 ESLint 配置没有开
`argsIgnorePattern` / `varsIgnorePattern`。两种修法选一：
要么在解构里改用 rest 忽略，要么在 `.eslintrc.cjs` 给
`@typescript-eslint/no-unused-vars` 加 `{ "varsIgnorePattern": "^_" }`。
后者会影响全仓规则，改之前确认不会掩盖其他终端的真实未用变量。

**`tests/contracts/viewer.spec.tsx`**（2 个）

```
2:10  error  'useRef' is defined but never used
58:5   error  Promises must be awaited ... or be explicitly marked as ignored with the `void` operator
```

第 58 行的 floating promise 是真问题，不是噪音——测试里未 await 的 promise
会导致断言在异步完成前就跑完，可能掩盖失败。修法是加 `await` 或显式 `void`，
优先 `await`（这是测试，应该等它完成）。

---

## P1 — 影响收口质量

### P1-1 后端 ruff 54 errors，全部集中在 robotics 域

按文件分布：

| 文件 | 错误数 |
|---|---|
| `app/domains/robotics/schemas/router.py` | 22 |
| `app/domains/robotics/calibrations/router.py` | 16 |
| `app/domains/robotics/robots/router.py` | 12 |
| `app/domains/robotics/router.py` | 3 |
| `alembic/versions/robotics_0001_calibration_schema.py` | 1 |

按规则分布：

| 规则 | 数量 | 说明 |
|---|---|---|
| E501 | 48 | 行超 100 字符，最长 183 字符 |
| I001 | 4 | import 块未排序（可自动修） |
| F401 | 2 | `typing.Annotated`、`fastapi.Query` 导入未使用（可自动修） |
| F403 | 1 | wildcard import |

**修复步骤**

```bash
# 先自动修 6 个
env -u PYTHONPATH -u AMENT_PREFIX_PATH uv run --with ruff ruff check --fix
# 剩余 48 个 E501 手动折行
env -u PYTHONPATH -u AMENT_PREFIX_PATH uv run --with ruff ruff check
```

E501 集中在几种模式：审计事件调用（`..."data_schema_version", row.row_id, {...}`）、
`with_idempotency(...)` 的长参数元组、f-string 拼接。折行时注意别改变语义，
尤其是 idempotency key 元组的元素顺序。

**注意**：不要用 `pyproject.toml` 的 `per-file-ignores` 给 robotics 整个域
豁免 E501。现有配置只给 `app/domains/ingest/schemas.py` 开了这个口子（那是
生成的 schema 文件），业务 router 应该老实折行。

### P1-2 ruff 未进 dev 依赖组

`pyproject.toml` 有 `[tool.ruff]` 和 `[tool.ruff.lint]` 配置，但
`[dependency-groups].dev` 里没有 ruff，导致 `uv run ruff check` 直接 spawn 失败，
必须写成 `uv run --with ruff ruff check`。

**修复**：把 ruff 加进 dev 组并 pin 版本，然后 `uv sync`：

```toml
[dependency-groups]
dev = [
  "aiosqlite==0.22.1",
  "httpx==0.28.1",
  "pytest==9.1.1",
  "pytest-asyncio==1.4.0",
  "ruff==0.14.4",
]
```

版本号要 pin（项目所有依赖都是精确版本），别用范围。

### P1-3 后端 4 个域没有任何测试文件

```
tests/access     : 2 files  ✅
tests/core       : 1 file   ✅
tests/ingest     : 1 file   ✅
tests/storage    : 1 file   ✅
tests/annotation : 0 files  ❌ 空目录
tests/cleaning   : 0 files  ❌ 空目录
tests/datasets   : 0 files  ❌ 空目录
tests/robotics   : 0 files  ❌ 空目录
```

`pytest --collect-only` 收集到 28 个测试，全部来自 core / ingest / storage / access。
annotation、cleaning、datasets、robotics 四个域有完整的 router、models、Alembic head，
但零测试覆盖——「pytest 全绿」这个收口项目前是靠「没有测试所以不会失败」达成的，
不是真实覆盖。

robotics 域尤其突出：43 + 20 + 15 = 78 个 operation（与 T10 状态文件里的
「0 / 78」目标数吻合），代码已写完但一行测试都没有。

**修复方向**

参考 `tests/storage/test_storage_operations.py` 的三段式结构（happy path 全覆盖 +
fail-closed 错误 + frozen operation set 断言），给四个域各补一份。
每个域至少要有：

- 所有 operation 的 happy path
- 代表性的 403 / 404 / 409 / 412 / 422
- operation set 与规格的冻结断言（防止 operation 漂移）

### P1-4 运行时 OpenAPI 有 70 个 operation 未打 tag

225 个 operation 里 70 个 `tags` 为空。已打 tag 的分布：

| tag | ops |
|---|---|
| robotics | 43 |
| access-audit | 26 |
| storage-lifecycle | 22 |
| calibrations | 20 |
| P04 UploadDetail | 15 |
| data-schemas | 15 |
| P03 UploadSession | 13 |
| P02 DataSource | 9 |
| （untagged） | 70 |

未打 tag 的全部是 datasets / annotation / cleaning 三个域的 operation，例如：

```
GET    /api/v1/projects/{projectId}/datasets
GET    /api/v1/projects/{projectId}/datasets/{datasetId}/bootstrap
DELETE /api/v1/projects/{projectId}/regions/{regionCode}/cleaning-drafts/{draftId}/edit-sessions/{sessionId}
```

收口标准要求「运行时 OpenAPI operation 数与各域规格对齐」。没有 tag 就无法
按域统计核对，这条目前只能靠路径前缀猜。三个域的 router 都应该像 ingest / storage /
robotics 那样声明 tag。

operationId 已确认无重复（225 unique / 225 total），这点是好的。

---

## P2 — 需确认，可能不是问题

### P2-1 T9 / T10 状态文件与实际实现严重脱节

`backend/docs/status/T10.md` 仍写着：

```
Domain 04 — storage and lifecycle
- Operation coverage: 0 / 22 while implementation is in progress.
Domain 05 — robotics, calibration and schema
- Operation coverage: 0 / 78 while implementation is pending.
```

但运行时 OpenAPI 显示 storage-lifecycle 22 ops、robotics 系 78 ops（43+20+15）
都已实现，`tests/storage/` 也有 `test_all_22_storage_operations_happy_path`。
状态文件没更新。

`backend/docs/status/` 下只有 `T8.md` 和 `T10.md`，**T9 完全没有状态文件**，
但 annotation / cleaning / datasets 三个域的代码和 Alembic head 都在。

**处理**：更新 T10.md 到实际覆盖数，补写 T9.md。这不影响功能，但收口报告
需要准确的交付台账，现在的状态文件会误导人。

### P2-2 前端 19 页目录结构完整，但 T5 状态文件的命名不一致

19 个页面目录全部存在，`page.tsx` / `routes.tsx` / `query-codec.ts` 三件套齐全，
唯一例外是 `p08-data-annotation/`：

```
p08-data-annotation/
  AnnotationQueuePage.tsx    ← 不是 page.tsx
  AnnotationTaskPage.tsx
  query-codec.ts
  routes.tsx
  p08.css
```

P08 是双页面（队列 + 任务详情），所以拆成两个组件文件是合理的，不算缺失。
但如果有脚本按 `page.tsx` 约定扫描，P08 会被误报。确认一下有没有这类脚本。

`tests/e2e/` 下 19 个页面各有一份 spec，`tests/contracts/` 下有 12 个域级
contract spec + 11 个 shared spec，覆盖看起来是完整的。

### P2-3 Alembic 7 个 head 尚未合并，其中 3 个脱离 foundation

`alembic heads` 输出 7 个 head，`alembic history` 揭示了一个需要确认的结构问题：

```
<base> -> foundation_0001 (branchpoint)
          ├─ foundation_0001 -> annotation_0002 (annotation)
          ├─ foundation_0001 -> cleaning_0002   (cleaning)
          ├─ foundation_0001 -> datasets_0002   (datasets)
          └─ foundation_0001 -> ingest_0002
<base> -> access_0001   (access)      ← 直接挂 base，不经 foundation
<base> -> robotics_0001 (robotics)    ← 直接挂 base，不经 foundation
<base> -> storage_0001  (storage)     ← 直接挂 base，不经 foundation
```

annotation / cleaning / datasets / ingest 四个域的 `down_revision` 指向
`foundation_0001`，而 access / robotics / storage 三个域的 `down_revision = None`
（例如 `robotics_0001_calibration_schema.py:15`），直接从 `<base>` 分叉。

**为什么这可能是问题**

`foundation_0001` 建的是 idempotency / outbox / audit 三张公共表。如果
access / robotics / storage 的业务逻辑要写审计或用幂等（T10 状态文件明确说
access 域有「trusted-only audit ingestion」和「append-only trigger」），
那它们在依赖关系上应该在 foundation 之后，而不是与它并行。

当前 `alembic upgrade heads` 会把 7 条链都跑一遍，最终表都会建出来，
所以**功能上暂时看不出问题**。但一旦：

- 有人 `upgrade access_0001`（只升单个 head），foundation 的表不会建，审计写入会炸
- merge 时 Alembic 需要决定顺序，三个脱链 head 与 foundation 的先后没有声明

**处理**

先确认这是刻意设计还是漏写。如果 access / robotics / storage 确实要用
foundation 的公共表，把它们的 `down_revision` 改成 `"foundation_0001"`；
如果它们真的完全自治（不写 audit、不用 idempotency），在各域
`OPEN_QUESTIONS.md` 里显式记录这个决定，别让它静默存在。

按派发总纲，多 head 是并行开发的预期结果，**最后**才 `alembic merge`。
但 P0-1 的 SQLite 问题必须先修，否则 merge 完也验证不了空库 upgrade。

合并顺序建议：先修 P0-1 → 确认三个脱链 head 的 `down_revision` 是否需要
改挂 foundation → `alembic merge` → 空库 upgrade 验证 → 写收口报告。

### P2-4 capability registry 数字核对

`capability-event-registry.json` 权威数据：

- `capabilities`: 94 项
- `audit_events`: 142 项

前端 T2 状态文件声称 P19 的事件目录由「142 个 audit_events」生成，数字对得上。
后端 T10 声称「142-name event gate」，也对得上。

但 T10.md 提到「34 canonical events without producer declarations」——
这 34 个事件没有 producer 声明，记在域的 `OPEN_QUESTIONS.md` 里。
这是上游规格的开放问题，不是实现缺陷，但收口报告里应该显式列出，
不要让它静默通过。

三角色 capability 集合与 `frontend-shared-contracts §8.1` 的一致性
**本轮未验证**（需要逐项比对 94 个 capability 的角色映射）。这是收口
必查项，留给专家终端。

---

## 收口检查清单（当前状态）

前端：

- [x] tsc 0 error
- [x] vitest 全绿（114 tests）
- [ ] ESLint 0 error ← **P0-2，4 个 error**
- [x] 19 页 routes / codec / page 齐全
- [x] `public/mockServiceWorker.js` 已就位（8928 bytes）
- [x] `playwright.config.ts` 用 `./node_modules/.bin/vite`，5 个 VITE_* 已注入
- [ ] Playwright 主流程实跑 ← 本轮未执行，需专家终端跑一遍

后端：

- [ ] ruff 通过 ← **P1-1，54 error**
- [x] pytest 全绿（28 passed）但覆盖不全 ← **P1-3，4 个域零测试**
- [ ] 空库 upgrade 到 merge head ← **P0-1 阻断**
- [ ] OpenAPI operation 数与规格对齐 ← **P1-4，70 个未打 tag 无法核对**
- [ ] 三角色 capability 集合与 §8.1 一致 ← 本轮未验证

跨域合同：

- [x] operationId 无重复（225 unique）
- [ ] ManualIssue / ReviewFinding 隔离 ← 前端 contract test 有覆盖，后端未验证
- [ ] capability 名全在 registry 内 ← 未逐项验证
- [x] Query Key 5 元组（shared-query-keys.spec.ts 通过）
- [x] 错误 envelope 统一（shared-domain-error.spec.ts 11 tests 通过）

---

## 建议执行顺序

1. **P0-1** 修 SQLite `CREATE SCHEMA`，跑通空库 upgrade（解锁后续所有 alembic 验证）
2. **P0-2** 清 ESLint 4 个 error（其中 viewer.spec.tsx 的 floating promise 是真 bug）
3. **P1-2** ruff 进 dev 组，`uv sync`
4. **P1-1** `ruff check --fix` + 手动折 48 个 E501
5. **P1-4** 给 datasets / annotation / cleaning 三个域的 router 打 tag，重新核对 operation 数
6. **P1-3** 补 annotation / cleaning / datasets / robotics 四个域的测试
7. **P2-3** 确认 access / robotics / storage 三个脱链 head 的 `down_revision`，
   然后 `alembic merge`，再验空库 upgrade
8. **P2-4** 逐项比对三角色 capability 与 §8.1
9. **P2-1** 更新 T10.md、补 T9.md
10. Playwright 主流程实跑
11. 写 `docs/status/INTEGRATOR.md` 收口报告

---

## 禁止事项（提醒专家终端）

- 不修改 `/home/czy/plan` 下任何文件
- 不 `git add` / `commit` / `push`（交人工提交）
- 不用 `per-file-ignores` / `eslint-disable` / `skip` 掩盖问题——收口标准要的是
  真实通过，不是让检查工具闭嘴
- 改 `shared/` / `core/` 公共文件前先跑相关测试确认不回归

---

## 专家修复终端处理记录（2026-08-11）

处理开始时保留了既有审查记录；以下内容为增量核验和修复记录，不改写上方
Integrator 的原始只读结论。

### 原清单逐项状态

| 编号 | 处理状态 | 原因 / 结论 | 关键证据 |
|---|---|---|---|
| P0-1 | 已关闭 | Annotation 与 Manual Cleaning migration 的 PostgreSQL 命名 schema DDL 未按 dialect 分支，且 SQLite 建表需要映射 schema。 | 空 SQLite 库 `upgrade heads`、`current`、`downgrade base` 均 exit=0；唯一 head 为 `merge_0001`。 |
| P0-2 | 已关闭 | 接手时所列 4 个 ESLint error 已由当前工作区中的既有改动消除，本终端未重复覆盖这些前端源码。 | `eslint . --ext .ts,.tsx` exit=0；TypeScript、Vitest 与 Vite build 同时通过。 |
| P1-1 | 已关闭 | 接手时 Robotics 的 54 个 Ruff 问题已不存在；继续对本轮全部后端改动做全仓检查。 | `uv run --offline ruff check .`：`All checks passed!`。 |
| P1-2 | 已关闭 | Ruff 虽已在 dev group 中，但版本使用范围约束，不符合仓库精确 pin 约定。 | `pyproject.toml` 与 `uv.lock` 均固定为 `ruff==0.16.2`；直接 `uv run --offline ruff check .` 成功。 |
| P1-3 | 已关闭 | Robotics 测试接手时已存在；Annotation、Cleaning、Datasets 仍需真实 operation happy path 与失败矩阵。 | 新增三个域 11 个测试；后端全仓由原记录 28 个提升为 61 个，四域均有 frozen operation set、happy path 和负向覆盖。 |
| P1-4 | 已关闭 | Dataset、Cleaning、Annotation router 缺少稳定 tag。 | 运行时 256 operations / 256 unique / 0 untagged；三个 tag 分别为 29 / 27 / 12。 |
| P2-1 | 已关闭 | T9 缺失，T10 migration 拓扑和验证数过期。 | 新增 `backend/docs/status/T9.md`，更新 `T10.md` 为当前覆盖、61 tests、foundation 依赖和 SQLite migration 结果。 |
| P2-2 | 已关闭 | P08 是双页面组件设计；应用路由通过各页 `routes.tsx` 聚合，不存在按 `page.tsx` 扫描的构建/测试脚本。 | `rg` 核验入口、测试与构建配置；tsc、Vitest、ESLint、build、Chromium E2E 均通过。 |
| P2-3 | 已关闭 | Access、Robotics、Storage 均实际使用共享幂等、审计或 Outbox，脱离 foundation 不是刻意自治。 | 三个 migration 均改为 `down_revision = "foundation_0001"`；既有 merge migration 保持最终唯一 head；空库正向/逆向实跑通过。 |
| P2-4 | 已关闭 | 需要把三角色集合逐项与 §8.1 比对，而非只核数字。 | 新增解析 `/home/czy/plan/frontend-shared-contracts.md` §8.1 的精确集合测试；canonical 76、reserved 18、Developer 34、Processor 19 全部一致。34 个无 producer 的 canonical events 仍是已登记上游开放项。 |

### 修复过程中发现的新问题

| 编号 | 处理状态 | 根因 | 处理结果 |
|---|---|---|---|
| NEW-1 | 已关闭 | 多个 Annotation/Cleaning/Dataset 代码路径调用了未登记 prefix 的 `new_id()`，会在首次运行时抛出 `unknown id prefix`。 | 补齐 prefix 登记，并增加 AST 扫描测试，确保所有字面量 `new_id` 调用均已注册。 |
| NEW-2 | 已关闭 | Dataset Review Return 的 `ReviewSuccessorDraftAdapter` 已实现但未注入生产 service，正常退回会固定返回 500。 | 在同一请求会话/事务中注入适配器；测试证明 Decision、Finding、RETURNED Version 和 successor Draft 全部原子落库。 |
| NEW-3 | 已关闭 | Cleaning 默认使用 `MissingDatasetVersionPort`，导致生产 `createManualIssue` 无法从稳定 Version 推导 Dataset。 | 新增受 scope 约束的 `DatasetVersionReaderAdapter` 并注入生产依赖；显式 dataset 关系不匹配时 fail closed。 |
| NEW-4 | 已关闭 | Cleaning 时间边界映射用 `zip(boundaries, boundaries[1:], strict=True)`，两侧长度天然相差 1，正常输入必然异常。 | 改为相邻窗口允许的 `strict=False`，全 27 operation happy path 覆盖该路径。 |
| NEW-5 | 已关闭 | SQLite 返回 naive `datetime`，Annotation 与 UTC-aware 过期时间比较时抛出异常。 | 在序列化和过期判断边界统一归一化为 UTC。 |
| NEW-6 | 已关闭 | Cleaning router 的共享 header dependency 把 `regionCode` 声明为普通参数，使 4 个不含该 path 参数的公开 operation 错误暴露同名 query。 | 改从 `request.path_params` 条件校验；运行时 OpenAPI 扫描无额外 `regionCode` query。 |
| NEW-7 | 复检阻塞 | Annotation Submit 依赖 ManualIssue Owner 提供强一致 gate、版本化 policy 与单调 watermark；该内部合同尚未交付。 | 按冻结规格保留生产 fail-closed：读取/编辑/保存可用，Submit 返回 `ISSUE_GATE_UNAVAILABLE`；未用本地严重度猜测伪造权威策略。 |
| ENV-1 | 复检阻塞 | Playwright Mobile 项目使用 WebKit，宿主缺少 `libsoup-3.0`、GTK4、WebKitGTK、JavaScriptCoreGTK、Graphene、GStreamer/Flite/JXL/AVIF、Enchant、Secret、Manette 等系统动态库。 | 全项目运行中 Chromium 38 passed，Mobile 38 个均在 browser launch 前失败；独立 Chromium 重跑 38/38 passed。需环境 Owner 安装 Playwright WebKit 依赖后复跑 Mobile。 |

### 实际改动文件

- Migration：`backend/alembic/versions/{access_0001_access_audit,annotation_0002_annotation,cleaning_0002_manual_cleaning,robotics_0001_calibration_schema,storage_0001_storage_lifecycle}.py`。
- 核心与领域：`backend/app/core/ids.py`、
  `backend/app/domains/annotation/{router,service}.py`、
  `backend/app/domains/cleaning/{router,service}.py`、
  `backend/app/domains/datasets/{repository,router}.py`。
- 工具链：`backend/pyproject.toml`、`backend/uv.lock`。
- 测试：`backend/tests/conftest.py`、`backend/tests/access/test_contracts.py`、
  `backend/tests/core/{test_foundation,test_migrations,test_runtime_operation_alignment}.py`、
  `backend/tests/annotation/test_annotation_operations.py`、
  `backend/tests/cleaning/test_cleaning_operations.py`、
  `backend/tests/datasets/test_dataset_operations.py`。
- 状态文档：`backend/docs/status/{T9,T10}.md`、`docs/status/INTEGRATOR.md` 和本文件。

根目录 `README.md` 在本轮开始前已有用户改动，本终端未修改或还原；
`/home/czy/plan` 全程只读，未执行 `git add`、`commit` 或 `push`。

### 最终验证命令与结果

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端静态检查 | `PYTHONDONTWRITEBYTECODE=1 uv run --offline ruff check .` | exit=0，All checks passed |
| 后端全量测试 | `env -u PYTHONPATH -u AMENT_PREFIX_PATH PYTHONDONTWRITEBYTECODE=1 uv run --offline pytest -q` | exit=0，61 passed |
| T10 三域 | 同上，目标 `tests/access tests/storage tests/robotics` | exit=0，32 passed |
| SQLite migration | 临时空库执行 `alembic upgrade heads && alembic current && alembic downgrade base` | exit=0，current=`merge_0001` |
| 前端类型 | `./node_modules/.bin/tsc -b --pretty false` | exit=0 |
| 前端单测 | `./node_modules/.bin/vitest run` | exit=0，23 files / 114 passed |
| 前端 ESLint | `./node_modules/.bin/eslint . --ext .ts,.tsx` | exit=0 |
| 前端构建 | `./node_modules/.bin/vite build` | exit=0；仅有 chunk size/empty chunk 警告 |
| Chromium E2E | `./node_modules/.bin/playwright test --project=chromium --reporter=dot` | exit=0，38 passed |
| 全 Playwright | `./node_modules/.bin/playwright test` | Chromium 38 passed；Mobile 38 项因 WebKit 宿主库缺失在 launch 前阻塞 |

处理记录完成时间：2026-08-11 11:07:26 +0800。此后进入对本文件的低频变更监听；
如出现新增问题，将重新按“读取 → 定位 → 修复/阻塞 → 验证 → 回写”的流程处理。

---

## 代码修复复检记录（2026-08-11 11:12 +0800）

复检前已重新读取本文件全文，并以当时最新 SHA-256
`2705f84251411d9cda87b4c4b609afa577768a437ac7fe79f456de244d4d153f`
为并发编辑基线；以下结论来自对实际代码、迁移、测试和运行时文档的独立检查。

### 逐项复检结果

| 编号 | 状态 | 验证内容与关闭依据 | 命令证据 |
|---|---|---|---|
| P0-1 | 已关闭 | 检查两个 migration 的 dialect 分支及 SQLite `schema_translate_map`；临时空库完整执行 upgrade、current、downgrade。 | C1、C2 |
| P0-2 | 已关闭 | 游标清理没有放宽全仓 unused 规则，fake timer 使用异步 `act`；类型、单测、零 warning lint、构建及 Chromium 均通过。 | C4、C5 |
| P1-1 | 已关闭 | 未增加 Robotics 域 Ruff 豁免；全仓 Ruff 无错误。 | C1 |
| P1-2 | 已关闭 | `pyproject.toml` 与 `uv.lock` 均精确固定 `ruff==0.16.2`，无需 `--with ruff` 即可离线直接运行。 | C1 |
| P1-3 | 已关闭 | 四域测试文件真实存在；冻结 operation set、happy path、403/404/409/412/422 与新增生产边界回归均执行。 | C1、C3 |
| P1-4 | 已关闭 | 运行时为 256 operations / 256 unique / 0 untagged；Dataset/Cleaning/Annotation tag 为 29/27/12。 | C3 |
| P2-1 | 已关闭 | `T9.md`、`T10.md` 的覆盖数、迁移拓扑、测试数和外部依赖与当前代码/实跑结果一致。 | C1、C2、C3 |
| P2-2 | 已关闭 | P08 路由显式 lazy import 双页面组件，未发现依赖 `page.tsx` 文件名的扫描入口；构建与 E2E 通过。 | C4、C5 |
| P2-3 | 已关闭 | Access/Robotics/Storage 均以 `foundation_0001` 为 down revision，Alembic 只有 `merge_0001` head，空库正逆向成功。 | C2 |
| P2-4 | 已关闭 | 角色集合精确测试通过：canonical 76、reserved 18、Developer 34、Processor 19；34 个无 producer 事件仍保留为已登记开放项。 | C1 |
| NEW-1 | 已关闭 | 检查新增 ID prefix，AST 回归确保所有字面量 `new_id()` 资源类型已登记；相关完整流程不再抛 unknown prefix。 | C1 |
| NEW-2 | 已关闭 | 生产路由注入同会话 successor adapter；Return 流程验证 Decision、Finding、RETURNED Version、lineage 与 successor Draft 同事务落库。 | C1 |
| NEW-3 | 已关闭 | scope-safe adapter 的作用域与 dataset 关系检查通过；复检新增断言锁定生产默认依赖确实构造 `DatasetVersionReaderAdapter`。 | C1 |
| NEW-4 | 已关闭 | 相邻窗口改为 `zip(..., strict=False)`；Cleaning 的 Preview/Commit 主流程实际经过时间映射并通过。 | C1 |
| NEW-5 | 已关闭 | naive SQLite datetime 在序列化及过期比较边界统一按 UTC 归一化；Annotation SQLite 流程通过。 | C1 |
| NEW-6 | 已关闭 | 运行时逐 operation 扫描确认，不含 `{regionCode}` 的路径没有额外 `regionCode` query 参数。 | C3 |

### 验证命令与真实结果

- C1：`PYTHONDONTWRITEBYTECODE=1 uv run --offline ruff check . && PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --offline pytest -p pytest_asyncio.plugin -q`：exit=0，`All checks passed!`，`62 passed`。
- C2：`uv run --offline alembic heads`、`uv run --offline alembic history`，并对 `mktemp` 临时 SQLite 库执行 `alembic upgrade heads`、`current`、`downgrade base`：全部 exit=0，current 为 `merge_0001 (head) (mergepoint)`。
- C3：运行时 `create_app().openapi()` 扫描：256 operations、256 unique、0 untagged、0 个意外 `regionCode` query；`pytest ... tests/access tests/storage tests/robotics` 为 `32 passed`。
- C4：`./node_modules/.bin/tsc -b --pretty false && ./node_modules/.bin/vitest run && ./node_modules/.bin/eslint . --ext .ts,.tsx --max-warnings=0 && ./node_modules/.bin/vite build`：全部 exit=0，23 files / 114 tests；构建只有 chunk size/empty chunk 警告。
- C5：`./node_modules/.bin/playwright test --project=chromium --reporter=dot`：exit=0，`38 passed`。
- C6：`git diff --check`：exit=0。

### 复检补充回归

- `backend/tests/annotation/test_annotation_operations.py`：新增生产默认 ManualIssue gate
  不可用时，preflight 明确 `valid=false` 且 Submit 返回可重试
  `ISSUE_GATE_UNAVAILABLE` 的 fail-closed 回归。
- `backend/tests/datasets/test_dataset_operations.py`：把 DatasetVersion 端口测试改为从
  Cleaning 生产依赖工厂取实例，锁定默认 wiring 与 scope-safe adapter 一致。

### 复检阻塞：NEW-7 Annotation Submit 权威门禁

- 状态：复检阻塞
- 已完成检查：检查生产默认端口、preflight 与 Submit 路径，并新增上述 fail-closed
  回归；该回归及后端全仓 62 个测试通过。
- 阻塞原因：ManualIssue Owner 尚未提供按 Revision/Stream/半开区间查询的强一致
  gate、版本化 policy 和单调 watermark，无法验证真实 PASS/BLOCKED 决策。
- 当前证据：默认 preflight 返回 `UNAVAILABLE`，Submit 返回 HTTP 500、错误码
  `ISSUE_GATE_UNAVAILABLE`、`retryable=true`，未被错误标记为成功。
- 继续复检条件：交付权威 gate 合同与生产适配器，并提供 PASS、ADVISORY、BLOCKED、
  watermark 变化及并发一致性的集成测试环境/数据。

### 复检阻塞：ENV-1 Playwright Mobile/WebKit

- 状态：复检阻塞
- 复现命令：`./node_modules/.bin/playwright test tests/e2e/p01-dashboard.spec.ts --project=mobile --workers=1 --max-failures=1 --reporter=line`
- 预期结果：WebKit 成功启动并进入页面主流程断言。
- 实际结果：浏览器在任何页面断言前启动失败；Playwright 报宿主缺少
  `libsoup-3.0.so.0`、`libwebkitgtk-6.0.so.4`、`libgtk-4.so.1`、
  `libjavascriptcoregtk-6.0.so.1`、Graphene、GStreamer/Flite/JXL/AVIF、Enchant、
  Secret、Manette 等动态库；1 failed、1 did not run。
- 已完成检查：同一工作区 Chromium 38/38 通过，因此没有把 WebKit 环境失败伪报为
  页面测试通过或业务回归。
- 继续复检条件：环境 Owner 安装 Playwright WebKit 宿主依赖后，重跑完整
  `./node_modules/.bin/playwright test --project=mobile`。

### 复检结论

P0-1、P0-2、P1-1～P1-4、P2-1～P2-4 及 NEW-1～NEW-6 已解决，未发现相关回归；
NEW-7 与 ENV-1 保持复检阻塞，不关闭。

---

## 监听增量处理记录（2026-08-11 11:17:19 +0800）

### NEW-8 Annotation 门禁不可用 HTTP 状态偏离冻结合同

- 处理状态：待修复
- 触发来源：11:14:31 检测到本文件新增“代码修复复检记录”后重新读取并复核。
- 实际问题：冻结 Annotation OpenAPI 在 Submit Preflight 与最终 Submit 上均声明
  `503 IssueGateUnavailable`，但生产 Submit 抛通用 `ServerError` 返回 500，新增
  回归也把 500 当成正确结果；Preflight 则将权威 gate 不可用包装为 200
  `valid=false` 并准备持久化 advisory preflight，与 503 合同不一致。
- 根因：共享错误类型缺少 503，Annotation service 只能复用 500；Preflight 未把
  `UNAVAILABLE` 与可修正的 `BLOCKED`/validation 业务结果分开。
- 修复：在 `backend/app/core/errors.py` 增加默认可重试的
  `ServiceUnavailableError`；Annotation Preflight 与 Submit 对 `UNAVAILABLE` 均抛
  `ISSUE_GATE_UNAVAILABLE` 503。回归覆盖 gate 在 Preflight 前不可用、在成功
  Preflight 后于最终 Submit 线性化点失效两种时序，并确认失败 Preflight 不落记录。
- 实际改动文件：`backend/app/core/errors.py`、
  `backend/app/domains/annotation/service.py`、
  `backend/tests/core/test_foundation.py`、
  `backend/tests/annotation/test_annotation_operations.py`、
  `backend/docs/status/{T9,T10}.md`、`docs/status/INTEGRATOR.md` 和本文件。
- 验证：`uv run --offline ruff check .` exit=0；
  `env -u PYTHONPATH -u AMENT_PREFIX_PATH PYTHONDONTWRITEBYTECODE=1 uv run --offline pytest -q`
  exit=0，`62 passed`。NEW-7 的权威 gate 外部依赖仍为复检阻塞；本修复只纠正
  失败关闭的 wire 行为，没有伪造 PASS/ADVISORY/BLOCKED 策略。

### 复检新增问题：NEW-8-R1 门禁 503 后幂等键残留为 IN_PROGRESS

- 状态：待修复
- 发现时间：2026-08-11 11:20 +0800
- 关联问题：NEW-8 Annotation 门禁不可用 HTTP 状态偏离冻结合同
- 问题描述：503 wire 状态已经符合 Annotation 合同，但失败的 Submit Preflight
  在共享 `idempotency_records` 中留下 `IN_PROGRESS` 占位。门禁恢复后使用相同
  Idempotency-Key 与相同请求重试，服务端返回 409
  `IDEMPOTENCY_IN_PROGRESS`，无法重新执行，也没有按平台 ADR 返回
  `Retry-After`。当前 `with_idempotency()` 读取已有记录时不检查
  `locked_until` / `expires_at`，因此该 key 会持续阻塞。
- 复现步骤：
  1. 使用生产默认不可用 gate 和固定 Idempotency-Key 调用
     `preflightAnnotationSubmit`，确认返回 503 `ISSUE_GATE_UNAVAILABLE`。
  2. 将 gate 恢复为 PASS，保持相同 path、body、ETag 和 Idempotency-Key 再次调用。
  3. 观察第二次请求没有返回成功 Preflight，而是返回 409
     `IDEMPOTENCY_IN_PROGRESS`。
- 预期结果：503 失败不得留下永久进行中的幂等占位；同 key/hash 的可重试请求应按
  平台 `FAILED_RETRYABLE`/租约策略恢复执行或得到明确定义的稳定重放结果，不能无限
  409，短时真实并发返回 409 时还必须带 `Retry-After`。
- 实际结果：首次 503；同 key/hash 重试为 409
  `IDEMPOTENCY_IN_PROGRESS`，且响应没有 `Retry-After`。业务
  `AnnotationSubmitPreflight` 表确实为 0，但共享幂等副作用仍然存在。
- 验证命令：`PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --offline pytest -p pytest_asyncio.plugin -q`
- 验证结果：exit=1，`1 failed, 61 passed`；失败测试为
  `test_annotation_submit_fails_closed_when_production_issue_gate_is_unavailable`。
- 错误信息或证据：第二次响应为 HTTP 409，错误码
  `IDEMPOTENCY_IN_PROGRESS`；目标回归预期门禁恢复后成功创建 Preflight。
- 涉及文件：`backend/app/core/idempotency.py`、`backend/app/core/db.py`、
  `backend/tests/conftest.py`、
  `backend/tests/annotation/test_annotation_operations.py`。
- 建议修复方向：按平台候选状态机实现 `FAILED_RETRYABLE` 与过期租约接管，或确保
  可重试 DomainError 退出时回滚本次幂等 claim；补充同 key/hash 503→恢复、真实并发
  `Retry-After`、同 key 不同 hash 以及超时租约接管测试。不要要求客户端换 key
  绕过原意图。

### NEW-8-R1 处理结果（2026-08-11 11:28:13 +0800）

- 处理状态：已关闭；NEW-8 的 HTTP 503 修复也恢复为已关闭。
- 根因确认：aiosqlite 驱动的 legacy transaction control 没有在首个 SAVEPOINT 前
  发出真实外层 `BEGIN`，释放保存点后 claim 可独立持久化，request dependency 的
  rollback 因而无法删除它。共享 helper 同时把所有非 COMPLETED 记录永久视为进行中，
  未实现模型中已有的 lease/state 字段语义。
- 修复内容：
  - `backend/app/core/db.py` 为 SQLite/aiosqlite engine 安装显式 `BEGIN` 事务事件；
    `backend/tests/conftest.py` 对测试 engine 使用同一生产配置。
  - `backend/app/core/idempotency.py` 持久化可选 request hash；同 key 不同 hash 返回
    `IDEMPOTENCY_KEY_REUSED`；同 hash 的 `FAILED_RETRYABLE`、`EXPIRED` 或过期
    `IN_PROGRESS` lease 可原子接管；活动 lease 仍返回 409。
  - `backend/app/core/errors.py` 允许领域错误携带安全响应头；
    `IDEMPOTENCY_IN_PROGRESS` 现在返回整数秒 `Retry-After`。
  - Annotation、Cleaning、Dataset 的集中写包装器把既有 canonical body digest
    传入共享 helper，避免只在完成响应内部事后比较。
- 回归证据：
  - 同一 HTTP Idempotency-Key 首次因 gate 不可用返回 503，gate 恢复后相同
    path/body/ETag/key 成功创建 Preflight。
  - 第一请求在 gate 内等待时，第二个真实并发 HTTP 请求返回 409
    `IDEMPOTENCY_IN_PROGRESS` 且带 `Retry-After`；释放后第一请求成功。
  - Core 测试覆盖同 key/hash 精确重放、同 key/不同 hash 拒绝、retryable failure
    接管、过期 lease 接管；最终 Submit gate 再失效仍为 503 且 Task/Draft/Set 零错写。
- 实际改动文件：`backend/app/core/{db,errors,idempotency}.py`、
  `backend/app/domains/{annotation,cleaning,datasets}/service.py`、
  `backend/tests/{conftest.py,core/test_foundation.py,annotation/test_annotation_operations.py}`、
  `backend/docs/status/{T9,T10}.md`、`docs/status/INTEGRATOR.md` 和本文件。
- 验证结果：针对性 13 tests 通过；全仓 Ruff 通过；后端全仓 `64 passed`。
  NEW-7 与 ENV-1 仍为外部条件复检阻塞，本次没有改变其状态。

### 复检新增问题：NEW-8-R2 幂等身份未包含 Task 路径导致跨资源错误重放

- 状态：待修复
- 发现时间：2026-08-11 11:33 +0800
- 关联问题：NEW-8 / NEW-8-R1 Annotation 幂等错误处理与重试状态机
- 问题描述：NEW-8-R1 的 503 后同键恢复、活动租约 `Retry-After` 与失败原子性
  已通过复检，但集中写包装器只把 canonical body 计算为 `request_hash`，幂等 scope
  只包含组织、项目、区域、actor 和 operation，没有包含冻结合同要求的 `task_id`。
  因此两个不同 Annotation Task 在 body 相同时复用同一 Idempotency-Key，第二个
  Task 会把第一个 Task 的 COMPLETED 响应当作自己的响应重放。
- 复现步骤：
  1. 创建、领取并保存两个不同 Annotation Task，使两者草稿均为 revision 1 且空草稿
     `content_hash` 相同。
  2. 对第一个 Task 调用 `preflightAnnotationSubmit`，使用固定 Idempotency-Key 及其
     revision/hash 请求体，确认返回该 Task 的成功 Preflight。
  3. 对第二个 Task 使用相同 Idempotency-Key 和相同请求体调用同一 operation。
  4. 检查第二次 HTTP 200 响应中的 `data.task_id` 与 `preflight_id`。
- 预期结果：冻结 Annotation OpenAPI 的 `x-idempotency.identity` 为
  `principal + operation + scope + task_id + canonical_body`；不同 Task 应分别执行，
  第二次响应必须属于第二个 Task，不能重放第一个 Task 的资源标识。
- 实际结果：第二次请求返回 HTTP 200，但 `data.task_id` 等于第一个 Task，且复用了
  第一个 `preflight_id`；第二个 Task 没有执行自己的 Preflight。
- 验证命令：
  `PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --offline pytest -p pytest_asyncio.plugin -q tests/annotation/test_annotation_operations.py::test_annotation_preflight_idempotency_is_scoped_to_task_path`
- 验证结果：exit=1，`1 failed`；全仓同配置为 exit=1，`1 failed, 64 passed`；
  `uv run --offline ruff check tests/annotation/test_annotation_operations.py` 通过。
- 错误信息或证据：断言
  `second_preflight["task_id"] == second["task"]["task_id"]` 失败，实际值为
  `first["task"]["task_id"]`。规格证据位于
  `/home/czy/plan/backend/09-data-annotation/data-annotation-api.openapi.yaml` 的
  `preflightAnnotationSubmit.x-idempotency.identity`。
- 涉及文件：`backend/app/domains/annotation/service.py`、
  `backend/app/core/idempotency.py`、
  `backend/tests/annotation/test_annotation_operations.py`。
- 建议修复方向：将稳定的资源路径身份（至少 Annotation 的 `task_id`，保存草稿还需
  保留合同中的 `client_mutation_id` 语义）纳入传给共享 helper 的 scope 或 request
  fingerprint；同时审计 Cleaning、Dataset 集中包装器的资源型 operation，避免相同
  key/body 跨 draft、dataset、version 等资源重放。保留现有 create operation 不含
  资源 ID 的合同语义，并补充跨资源同键同 body 回归。
- 复检结论：NEW-8-R1 针对 503 后永久 `IN_PROGRESS` 的修复本身验证通过，但
  NEW-8 相关幂等修复尚不完整，原问题保持待修复，不关闭。

### NEW-8-R2 处理结果（2026-08-11 11:40:59 +0800）

- 处理状态：已关闭；NEW-8、NEW-8-R1、NEW-8-R2 当前均已关闭。
- 根因确认：三个域的 `_idem_scope()` 只包含 actor/组织/项目/区域/operation，
  canonical request hash 又只覆盖 body；稳定 path 参数既不在 scope 也不在 hash，
  因而不能区分同 operation/key/body 的两个资源。
- 修复策略：为三个集中 `_write()` 增加显式 `idempotency_resource_id`，把
  `resource_type + resource_id` 加入共享 helper 的 scope。该参数必须来自客户端已知
  的稳定 path 身份，不能自动取服务端执行后才生成的 target ID：
  - Annotation：materialize 按 resolution；claim/assign/save/preflight/submit/rebase
    按 Task；review 按 Task/Submission 组合身份；create Task 不加未知新 ID。
  - Cleaning：triage/draft-create 按 Issue；EDL/Preview/Commit 按 Draft；create Issue
    不加未知新 ID。
  - Dataset：approve/return 按 Dataset/Version 组合身份；create Dataset 不加未知新 ID。
- 回归证据：
  - 两个 Task 的 revision/hash 相同，复用 key/body 分别得到各自 task/preflight ID。
  - 两个 Draft 的 baseline EDL 相同，复用 client mutation/body 分别更新各自 Draft。
  - 两个 REVIEWING Version 复用 approve key/body 分别得到各自 Version/Decision。
- 实际改动文件：`backend/app/domains/{annotation,cleaning,datasets}/service.py`、
  `backend/tests/annotation/test_annotation_operations.py`、
  `backend/tests/cleaning/test_cleaning_operations.py`、
  `backend/tests/datasets/test_dataset_operations.py`、
  `backend/docs/status/{T9,T10}.md`、`docs/status/INTEGRATOR.md` 和本文件。
- 验证结果：三条针对性 HTTP 回归 `3 passed`；全仓 Ruff 通过；后端全仓
  `67 passed`。NEW-7 与 ENV-1 仍为外部条件复检阻塞。

### 复检新增问题：NEW-8-R3 Router 直调未传资源身份，跨 Draft 重放仍存在

- 状态：已关闭
- 发现时间：2026-08-11 11:45 +0800
- 关联问题：NEW-8-R2 幂等身份未包含资源路径
- 问题描述：本次修复为三个 Service 的 `_write()` 增加了
  `idempotency_resource_id`，且 Annotation、Cleaning EDL、Dataset Review 三条新增
  回归均通过；但只修了 Service 内部调用。Cleaning 与 Dataset router 仍有多处直接
  调用 `service._write()`，没有传资源身份。实测 `acquireCleaningEditLease` 对不同
  Draft 的同 key/body 仍会错误重放第一个 Draft 的 lease。
- 复现步骤：
  1. 创建两个不同 ManualIssue，分别 triage 后创建两个处于 EDITING 的 Cleaning Draft。
  2. 先用同一 `client_mutation_id` 和相同空 EDL body 保存两个 Draft，确认 R2 对
     `saveCleaningEdl` 的修复有效且返回各自 Draft。
  3. 对第一个 Draft 调用 `acquireCleaningEditLease`，使用固定 Idempotency-Key、
     相同 `client_session_id` 和 `client_instance_id`。
  4. 对第二个 Draft 使用完全相同的 key/body 调用同一 operation，检查响应资源身份。
- 预期结果：冻结 Manual Cleaning OpenAPI 要求该 identity 包含 `draftId` 与
  `client_session_id`；第二次请求必须创建/返回属于第二个 Draft 的独立 lease。
- 实际结果：两次请求均返回 HTTP 201，但第二次 `data.draft_id` 等于第一个 Draft，
  `session_id` 也来自第一次响应；第二个 Draft 的 action 未执行。
- 验证命令：
  `PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --offline pytest -p pytest_asyncio.plugin -q tests/cleaning/test_cleaning_operations.py::test_cleaning_idempotency_is_scoped_to_draft_path`
- 验证结果：exit=1，`1 failed`；全仓同配置为 exit=1，`1 failed, 66 passed`；
  `uv run --offline ruff check tests/cleaning/test_cleaning_operations.py` 通过。
- 错误信息或证据：断言
  `second_lease["draft_id"] == second["draft_id"]` 失败，实际值为
  `first["draft_id"]`。冻结规格证据为
  `/home/czy/plan/backend/03-manual-cleaning/manual-cleaning-api.openapi.yaml` 中
  `acquireCleaningEditLease.x-idempotency.identity`。
- 代码审计证据：`backend/app/domains/cleaning/router.py` 的
  `resolveManualIssue`、`acquireCleaningEditLease` 均未传资源身份；
  `backend/app/domains/datasets/router.py` 的 `createVersionDiffJob`、
  `createManifestJob`、`authorizeManifestDownload`、`preflightDatasetDeletion`、
  `preflightVersionDeletion` 等资源路径入口也直接调用 `_write()` 且未传该参数。
  后者为同根因代码证据，本次至少已用 Cleaning HTTP 路径稳定复现。
- 涉及文件：`backend/app/domains/cleaning/router.py`、
  `backend/app/domains/datasets/router.py`、
  `backend/app/domains/{cleaning,datasets}/service.py`、
  `backend/tests/cleaning/test_cleaning_operations.py`。
- 建议修复方向：审计所有 `service._write()` / `self._write()` 调用，而不仅是 Service
  文件；所有具有稳定资源 path 参数的幂等 operation 都显式传入合同要求的资源组合。
  可让包装器对资源型 operation 强制要求 identity，或增加静态/参数化测试防止新增
  调用静默遗漏。为 Cleaning lease/resolve 及 Dataset version/deletion 路径补跨资源
  同 key/body HTTP 回归。
- 复检结论：NEW-8-R2 的 Annotation、Cleaning EDL、Dataset Review 子路径已修复，
  但同根因仍存在于 router 直调路径，NEW-8-R2 复检不通过并保持待修复，不关闭。

### NEW-8-R3 处理结果（2026-08-11 11:49:13 +0800）

- 处理状态：已关闭；NEW-8-R2 的跨资源修复重新通过复检。
- 修复内容：
  - Cleaning router 的 `resolveManualIssue` 按 Issue、
    `acquireCleaningEditLease` 按 Draft 加入稳定资源身份。
  - Dataset router 的 Export download 按 Job，Version diff/Manifest job/Manifest
    authorization 按 Dataset/Version，删除预检按 Dataset 或 Dataset/Version 加入身份；
    Dataset list export 是无资源路径 create，继续只使用 canonical selection body。
  - 新增 AST 门禁枚举 23 个 Annotation/Cleaning/Dataset 资源型 `_write()` operation，
    要求每个调用显式携带 `idempotency_resource_id`，防止 service/router 新入口再次
    静默遗漏；服务端生成 ID 的 create operation 不在该强制集合。
- 回归证据：复检新增的 Cleaning 测试现同时证明跨 Draft EDL 与跨 Draft edit lease
  使用同 key/body 时均返回各自资源；静态资源身份门禁通过。
- 实际改动文件：`backend/app/domains/cleaning/router.py`、
  `backend/app/domains/datasets/router.py`、`backend/tests/core/test_foundation.py`、
  `backend/tests/cleaning/test_cleaning_operations.py`、
  `backend/docs/status/{T9,T10}.md`、`docs/status/INTEGRATOR.md` 和本文件。
- 验证结果：R3 针对性 `2 passed`；全仓 Ruff 通过；后端全仓 `68 passed`。
  NEW-7 与 ENV-1 仍为外部条件复检阻塞。

### NEW-8-R3 复检结果

- 状态：已关闭
- 复检时间：2026-08-11 11:51 +0800
- 验证内容：检查 Cleaning/Dataset router 的全部 `_write()` 直调，确认
  `resolveManualIssue`、`acquireCleaningEditLease`、Export download、Version
  diff/Manifest、Manifest authorization 及 Dataset/Version 删除预检均加入稳定资源
  身份；无资源路径的 Dataset list export 保持 canonical selection body 身份。
  检查新增 AST 门禁，23 个资源型 Annotation/Cleaning/Dataset operation 均必须显式
  声明 `idempotency_resource_id`。重新执行此前失败的跨 Draft EDL/lease HTTP 回归。
- 验证命令：
  `PYTHONDONTWRITEBYTECODE=1 uv run --offline ruff check . && PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --offline pytest -p pytest_asyncio.plugin -q`
- 验证结果：exit=0；Ruff `All checks passed!`，后端全仓 `68 passed`。单独执行
  `tests/cleaning/test_cleaning_operations.py::test_cleaning_idempotency_is_scoped_to_draft_path`
  为 `1 passed`；`git diff --check` 为 exit=0。
- 关闭依据：第二个 Draft 不再重放第一个 Draft 的 lease，返回各自 `draft_id` 与
  `session_id`；service/router 的资源型集中写入口均受静态门禁保护，未发现相关回归。
- 结论：NEW-8-R3 已解决，NEW-8-R2 的跨资源身份修复复检通过；NEW-7 与 ENV-1
  仍保持复检阻塞，本次不改变其状态。

---

## Mobile/WebKit 环境恢复与增量处理记录（2026-08-11 12:48:22 +0800）

### ENV-1 Playwright Mobile/WebKit

- 处理状态：已关闭。
- 外部状态变化：监听期间另一终端补齐了 WebKitGTK/GTK4/libsoup 等宿主依赖并生成
  Mobile 运行产物；本终端没有覆盖其依赖安装文件，先以只读方式确认无运行中的
  Playwright 进程，再使用 `/tmp` 独立输出目录复测。
- 关闭证据：最小 Mobile P01 为 `2 passed (13.9s)`；完整 Mobile 首轮已能执行全部
  业务断言并达到 `37 passed`，不再出现浏览器 launch 失败。处理下述 NEW-9～NEW-11
  后，Chromium + Mobile 全量最终为 `76 passed (1.1m)`。
- 最终命令：
  `./node_modules/.bin/playwright test --reporter=dot --output=/tmp/hc-e2e-final3-results`
  （在 `frontend/` 执行，exit=0）。
- 结论：原缺失动态库的环境阻塞已消失，Mobile 38 项均已真实进入页面与业务断言；
  不再把 ENV-1 保持为阻塞。

### NEW-9 P02 Job Mock 跨域误接管且视觉快照跨越重连状态

- 处理状态：已关闭。
- 发现时间：2026-08-11 12:00 +0800。
- 实际问题：Dataset 与 Ingest 都注册了无资源归属判断的全局
  `GET /api/v1/jobs/:jobId` handler。P02 的 Ingest connection Job 会被 Dataset
  handler 接管，并随 Dataset `jobPoll` 从 QUEUED 漂移到 RUNNING/SUCCEEDED；同时
  四档截图从 SSE `reconnecting` 跨越到 `polling`，WebKit 首轮因此在 768 宽度出现
  566 像素的非确定性差异。
- 修复：Dataset handler 只接管 Manifest/Version Diff Job；Ingest handler 只接管
  connection/verification/cancel Job，其余请求显式 fall through。P02 在截图前等待
  稳定的 `QUEUED（polling）`，并在已安装 CJK 字体的现行环境重建双浏览器 P02
  基线。
- 实际改动文件：
  `frontend/src/mocks/handlers/{datasets,ingest}.handlers.ts`、
  `frontend/tests/e2e/p02-data-sources.spec.ts` 及其 Chromium/Mobile 四档快照。
- 验证：类型检查和定向 ESLint 均 exit=0；P02 双浏览器 `4 passed (52.6s)`；最终
  全量 E2E `76 passed`。

### NEW-10 P12 对象详情 Escape 关闭存在 effect 安装竞态

- 处理状态：已关闭。
- 发现时间：2026-08-11 12:23 +0800。
- 实际问题：对象详情打开后依赖 `useEffect` 才向 `globalThis` 安装 keydown listener；
  高并发 WebKit 中，详情内容可见与 listener 安装之间存在窗口，立即按 Escape 时抽屉
  保持可见。
- 修复：打开对话框时让“关闭对象详情”按钮立即获得焦点，并由对话框自身同步处理
  Escape，移除延迟安装的全局 listener；E2E 增加焦点断言。
- 实际改动文件：`frontend/src/pages/p12-storage-overview/page.tsx`、
  `frontend/tests/e2e/p12-storage-overview.spec.ts`。
- 验证：P12 双浏览器、6 workers、重复三轮为 `12 passed (11.7s)`；最终全量 E2E
  `76 passed`。

### NEW-11 高并发合同失败页超过默认 5 秒等待边界

- 处理状态：已关闭。
- 发现时间：2026-08-11 12:35 +0800。
- 实际问题：12 workers 全量运行时，P12/P19 的合同失败提示偶发刚好在 Playwright
  默认 5 秒断言超时之后提交到 DOM；失败时的 error context 已包含目标 alert，证明
  合同校验没有落回成功路径，也没有发生敏感字段泄漏。
- 修复：仅将两个明确依赖应用启动、MSW 注册和合同解析的失败页可见性断言上限提高
  到 15 秒；保留全部 fail-closed 与不泄漏断言，没有添加 retry 或放宽匹配内容。
- 实际改动文件：`frontend/tests/e2e/{p12-storage-overview,p19-audit}.spec.ts`。
- 验证：P12 + P19 双浏览器、12 workers、重复三轮为 `30 passed (16.5s)`；最终
  全量 E2E `76 passed (1.1m)`。

### 字体与视觉基线复核

- WebKit 宿主依赖安装同时提供了 CJK 字体；旧 Chromium P03/P04 基线以方框缺字
  渲染，现行实际图显示正确中文并因字体度量产生换行/高度变化。
- 已逐图检查 P03/P04 1440 宽度的 expected/actual，确认事实内容和页面结构一致，
  随后重建两页共 8 张 Chromium 四档基线；P03/P04 Chromium 为
  `5 passed (6.0s)`。另一终端新增的 P03/P04 Mobile 基线保留不覆盖，并已由最终
  全量 E2E 验证通过。

### 本轮最终验证与剩余阻塞

| 检查 | 命令 | 结果 |
|---|---|---|
| 前端类型 | `./node_modules/.bin/tsc -b --pretty false` | exit=0 |
| 定向 ESLint | `./node_modules/.bin/eslint <本轮 TS/TSX 文件>` | exit=0 |
| P02 双浏览器 | `playwright test tests/e2e/p02-data-sources.spec.ts` | 4 passed |
| P12 压力回归 | `playwright test ...p12... --repeat-each=3 --workers=6` | 12 passed |
| P12/P19 高并发 | `playwright test ...p12... ...p19... --repeat-each=3 --workers=12` | 30 passed |
| 双浏览器全量 | `playwright test --reporter=dot --output=/tmp/hc-e2e-final3-results` | 76 passed |
| 后端静态检查 | `PYTHONDONTWRITEBYTECODE=1 uv run --offline ruff check .` | exit=0 |
| 后端全量测试 | `env -u PYTHONPATH -u AMENT_PREFIX_PATH PYTHONDONTWRITEBYTECODE=1 uv run --offline pytest -q` | 68 passed |

ENV-1、NEW-8～NEW-11 均已关闭。仅 NEW-7（ManualIssue Owner 尚未交付权威
Annotation submission gate 合同、policy 与 watermark）继续保持外部依赖阻塞；
现有生产路径仍按冻结 wire 以 retryable 503 fail-closed。

---

## ENV-1 / NEW-9～NEW-11 独立复检记录（2026-08-11 12:55 +0800）

### ENV-1 复检结果

- 状态：已关闭。
- 复检时间：2026-08-11 12:55 +0800。
- 验证内容：确认宿主具备 CJK 字体并实际启动 Chromium、WebKit 执行完整页面断言；
  WebKit 不再出现缺失系统依赖导致的启动失败。
- 验证命令：`./node_modules/.bin/playwright test --reporter=dot --output=/tmp/hc-review-full-e2e`。
- 验证结果：双浏览器全量 E2E `76 passed (1.1m)`，WebKit 用例实际进入并完成页面断言；
  另抽查 P02 Mobile、P03/P04 Chromium 实际截图，中文字体、页面事实和结构均正常。
- 结论：原环境阻塞已经解除，未发现相关回归问题。

### NEW-9 复检结果

- 状态：已关闭。
- 复检时间：2026-08-11 12:55 +0800。
- 验证内容：检查 dataset/ingest mock job handler 的任务所有权集合与未命中透传逻辑；
  验证 P02 精确等待 `连接测试：QUEUED（polling）` 后再截图，不再由无关全局 handler
  抢占 job 请求。
- 验证命令：`./node_modules/.bin/playwright test tests/e2e/p02-data-sources.spec.ts --project=chromium --project=webkit --reporter=line --output=/tmp/hc-review-p02`。
- 验证结果：P02 双浏览器 `4 passed (40.6s)`；全量 E2E `76 passed (1.1m)`；
  人工抽查 768 宽度 Mobile 截图，状态稳定且中文渲染正常。
- 结论：handler 归属冲突和截图时序问题已经解决，未发现相关回归问题。

### NEW-10 复检结果

- 状态：已关闭。
- 复检时间：2026-08-11 12:55 +0800。
- 验证内容：检查对象详情对话框的焦点初始化和对话框内同步 Escape 处理；确认已移除
  延迟安装的全局 keydown listener，并保留 E2E 焦点断言。
- 验证命令：`./node_modules/.bin/playwright test tests/e2e/p12-storage-overview.spec.ts --project=chromium --project=webkit --repeat-each=3 --workers=6 --reporter=line --output=/tmp/hc-review-p12-repeat`。
- 验证结果：双浏览器重复三轮 `12 passed (39.7s)`；全量 E2E `76 passed (1.1m)`。
- 结论：Escape effect listener 竞争已经解决，未发现相关回归问题。

### NEW-11 复检结果

- 状态：已关闭。
- 复检时间：2026-08-11 12:55 +0800。
- 验证内容：确认改动仅提高 P12/P19 合同失败提示的等待上限，精确错误匹配、
  fail-closed 与敏感字段不泄漏断言均保留；在更高并发下重复验证。
- 验证命令：`./node_modules/.bin/playwright test tests/e2e/p12-storage-overview.spec.ts tests/e2e/p19-audit.spec.ts --project=chromium --project=webkit --repeat-each=3 --workers=12 --reporter=line --output=/tmp/hc-review-p12-p19-repeat`。
- 验证结果：双浏览器重复三轮 `30 passed (18.0s)`；全量 E2E `76 passed (1.1m)`。
- 结论：默认 5 秒边界造成的高并发不稳定已经解决，且未以重试或放宽内容匹配掩盖
  合同失败，未发现相关回归问题。

### 独立复检公共检查

- 验证命令：`./node_modules/.bin/tsc -b --pretty false && ./node_modules/.bin/vitest run && ./node_modules/.bin/eslint . --ext .ts,.tsx --max-warnings=0 && ./node_modules/.bin/vite build`。
- 验证结果：TypeScript、ESLint、构建均 exit=0，Vitest `23` 个测试文件、
  `114` 个测试全部通过；仅有既有 Node/颜色变量/构建 chunk 大小提示，无测试失败。
- 并发保护：写入前完整重读文件，确认 SHA-256 仍为
  `e8ad177d75a5d38ba49ce4bb0fd8bd69a054cf48d9c66bb32870ab03dae59131`，未覆盖其他进程记录。
- 剩余状态：NEW-7 仍为“复检阻塞”，本次未改变其状态。

### NEW-12 独立复检定向命令使用了不存在的 Playwright project 名

- 处理状态：已关闭（记录勘误，不影响复检结论）。
- 发现与处理时间：2026-08-11 12:57:31 +0800。
- 实际问题：上方 NEW-9～NEW-11 的三条定向复检命令写为
  `--project=chromium --project=webkit`，但当前 `playwright.config.ts` 的 project
  名称只有 `chromium` 与 `mobile`；原文字面命令不可复现。
- 验证命令与结果：
  - `./node_modules/.bin/playwright test tests/e2e/p02-data-sources.spec.ts --project=webkit --list`
    exit=1，明确报告 `Project(s) "webkit" not found`。
  - 同命令改为 `--project=mobile --list` exit=0，列出 P02 的 2 个 Mobile tests。
- 勘误：上方所有定向复检命令中的 `--project=webkit` 均应读作
  `--project=mobile`。其记录的 4/12/30 passed 与独立全量 76 passed 结论仍可由
  本轮已实跑的 `mobile` 项目命令复现，因此不重新打开 ENV-1 或 NEW-9～NEW-11。
- 实际改动文件：`docs/INTEGRATION-ISSUES.md`、`docs/status/INTEGRATOR.md`；无代码、
  配置或快照改动。
- 文档验证：`git diff --check` exit=0。

### NEW-12 复检结果

- 状态：已关闭。
- 复检时间：2026-08-11 13:01 +0800。
- 验证内容：检查 `playwright.config.ts`，确认当前项目名只有 `chromium` 和
  `mobile`；分别验证错误项目名的失败行为与正确项目名的测试枚举，并用勘误后的
  `chromium + mobile` 参数重新执行 NEW-9～NEW-11 的三组定向回归。
- 验证命令与结果：
  - `./node_modules/.bin/playwright test tests/e2e/p02-data-sources.spec.ts --project=webkit --list`：exit=1，报告 `Project(s) "webkit" not found`。
  - `./node_modules/.bin/playwright test tests/e2e/p02-data-sources.spec.ts --project=mobile --list`：exit=0，列出 2 个 Mobile tests。
  - `./node_modules/.bin/playwright test tests/e2e/p02-data-sources.spec.ts --project=chromium --project=mobile --reporter=line --output=/tmp/hc-review-new12-p02`：exit=0，`4 passed (10.4s)`。
  - `./node_modules/.bin/playwright test tests/e2e/p12-storage-overview.spec.ts --project=chromium --project=mobile --repeat-each=3 --workers=6 --reporter=line --output=/tmp/hc-review-new12-p12-repeat`：exit=0，`12 passed (12.4s)`。
  - `./node_modules/.bin/playwright test tests/e2e/p12-storage-overview.spec.ts tests/e2e/p19-audit.spec.ts --project=chromium --project=mobile --repeat-each=3 --workers=12 --reporter=line --output=/tmp/hc-review-new12-p12-p19-repeat`：exit=0，`30 passed (49.4s)`。
- 验证结果：勘误准确，修正后的命令可逐字执行；P02 状态/视觉、P12 焦点与 Escape、
  P12/P19 fail-closed 和敏感字段不泄漏断言均在 Chromium 与 Mobile 上通过。
- 并发保护：写入前重新读取完整文件并确认 SHA-256 仍为
  `351f62bae504dfbc7dade6669c60cd6188f6d13f1c8cca02e29c272a8d578155`。
- 结论：NEW-12 已解决；该问题仅影响复检记录的命令可复现性，不影响 ENV-1、
  NEW-9～NEW-11 的既有关闭结论，未发现相关回归。
