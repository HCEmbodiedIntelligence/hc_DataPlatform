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
