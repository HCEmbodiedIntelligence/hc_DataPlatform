# FE16-R1 CRC64 修复后 fresh upload smoke 独立复验报告

- 日期：2026-08-19（Asia/Shanghai）
- 范围：只运行 fresh isolated browser upload smoke；未运行完整 10 分钟主链、E01-E10 或视觉 baseline。
- 本轮 runId：`fe16-mszrkbwb-28e25b`
- project：`be22-fe16-mszrkbwb-28e25b-p1`
- foreignProject：`be22-fe16-mszrkbwb-28e25b-p2`
- smoke artifact：`real-api-main-chain-fe16-mszrkbwb-28e25b.json`，status=`FAILED`

## 1. Git、冲突与 CRC64 前置

| 检查 | Exit | 结果 |
| --- | ---: | --- |
| `git status --short` | 0 | 共享工作树开始时已有大量 tracked/untracked 改动；全部保留，未 reset/checkout/clean/stash |
| `git diff --check` | 0 | 无 whitespace error |
| `git diff --name-only --diff-filter=U` | 0 | 无 unmerged path |
| `git ls-files -u` | 0 | index 冲突项为 0 |

CRC64 静态合同核验通过：

- Wave2 legal Manifest 的顶层 CRC64 与唯一 RAW_MCAP file CRC64 均为 JSON `string`，两者逐字一致；该值大于 `Number.MAX_SAFE_INTEGER`，本报告不记录数值本身。
- Node/V8 `JSON.parse` → `JSON.stringify` → `JSON.parse` 后，顶层与 RAW CRC64 类型仍均为 `string`，且分别与原始字符串逐字一致。
- `backend/openapi/ingest.yaml` 的 `Crc64Decimal` 为 `type: string`；`RolloutManifestV1.crc64`、`ManifestFileV1.crc64`、`UploadSession.expected_crc64` 均引用该 schema。
- generated `platform.ts` 中 `RolloutManifestV1.crc64`、`ManifestFileV1.crc64`、`UploadSession.expected_crc64` 均为 `string`。
- P03 `formal-client.ts` 使用 generated `platform.ts`；未使用旧 `storage.ts`。
- `frontend/src/shared/api/generated/storage.ts` 仍保留历史 CRC64 `number` 类型，记录为独立生成来源债务，不作为本轮 P03 blocker。

## 2. 阶段 0 门禁

| 命令 | Exit | 计数/结果 |
| --- | ---: | --- |
| `.venv/bin/python -m pytest tests/ingest/test_manifest_security_gate.py tests/ingest/test_router_contract.py tests/load/test_capacity_controls.py tests/system/test_wave2_fixture_contracts.py -q` | 0 | 64 passed，0 failed |
| `.venv/bin/python -m pytest -q` | 0 | 511 passed，18 skipped，1 xfailed，0 failed；skip 均为未注入外部 PostgreSQL/MinIO 等环境的集成项，XFAIL 为既有 5 TB/day + 30% 容量证据缺口 |
| `pnpm gen:api --check` | 0 | runtime/generated client current；runtime hash `edf9ed7e9de1949e166d4f187443685f8a50f3bc4e3c6de46bb59db45b5b2a8b` |
| `pnpm typecheck` | 0 | PASS |
| `pnpm exec vitest run` | 0 | 40/40 files，221/221 tests |
| `VITE_MOCK_MODE=off VITE_RELEASE_ENV=production pnpm build` | 0 | 3,740 modules；build PASS；最大 chunks 330.25、285.92、266.54 kB |

focused router contract 明确断言：大于 JS safe integer 的 CRC64 仍以字符串进入请求，preflight 的 Manifest/RAW file CRC64 与原始值完全一致，create session 的 `expected_crc64` 与原始值完全一致；数值型 wire CRC64 被拒绝。以上是门禁证据，不冒充本轮 live smoke 响应。

## 3. Compose 与 migration

- `HC_ENVIRONMENT=test HC_JWT_SIGNING_KEY=<ephemeral-64-char-key> docker compose -f compose.dev.yaml -f compose.real-api.yaml up -d --build`：exit 0；key 只在进程环境中生成，未输出、未写 artifact。
- PostgreSQL、MinIO、Temporal、API、Worker、frontend、browser object-store edge 均 healthy；gateway 与 Temporal UI up；migration、migration-check、minio-init completed/exited 0。
- API/Worker 实际 `HC_ENVIRONMENT=test`；API signing key 长度 64；frontend 实际 `VITE_MOCK_MODE=off`。
- API live/ready、frontend、gateway、browser edge probes 均为 200。
- migration before smoke：`applied=28`、`expected=28`、`status=current`，missing/unknown/checksum drift 均为空；upgrade `applied_now=[]`。
- migration after cleanup：使用一次性 migration-check 容器复核，仍为 28/28 current，missing/unknown/checksum drift 均为空，exit 0。
- 两次尝试在 dev API 容器直接调用 migration CLI 均 exit 127（该 dev 容器没有安装 console entry point）；随后使用正式 migration-check image 重跑 exit 0。两次 invocation error 未修改数据库。

## 4. public/internal endpoint 与 exact CORS

| 事实 | 运行时结果 |
| --- | --- |
| browser/gateway origin | `http://127.0.0.1:8088` |
| internal object-store | scheme=`http`、host=`minio`、port=`9000` |
| public object-store | scheme=`http`、host=`127.0.0.1`、port=`9000`；浏览器可达，非 internal hostname |
| PUT preflight | OPTIONS 204；exact allow-origin=`http://127.0.0.1:8088`；method=`PUT`；allowed header=`content-type`；exposed=`ETag`；max-age=`600`；credentials absent |
| HEAD preflight | OPTIONS 204；exact allow-origin；method=`HEAD`；allowed header=`content-type`；exposed=`ETag`；credentials absent |
| 未授权 origin | allow-origin absent |
| native bucket CORS | `NoSuchCORSConfiguration`；Community MinIO 的已知非阻塞偏差，未声称 bucket CORS 已配置 |

第一次组合执行 native bucket CORS 子探针时 shell 引号错误，凭据变量未建立、未发起 S3 调用；修正后的独立探针 exit 0 并得到上述结果。由于 smoke 在 upload session 之前失败，本轮没有 API 返回的 presigned URL；因此只能确认运行时 public signer 配置与 edge CORS，不能把它冒充 live API presigned response 证据。

## 5. Fresh isolated browser smoke

执行环境仅记录变量名，值未展开：

```text
HC_ENVIRONMENT=test
HC_REAL_API_E2E_ENABLED=1
HC_REAL_API_E2E_RUN_OWNER=fe16
HC_REAL_API_UPLOAD_SMOKE_ONLY=1
HC_WAVE2_CLEANUP_ENABLED=1
HC_TEST_POSTGRES_DSN=<redacted>
HC_MINIO_ENDPOINT=<redacted>
HC_MINIO_BUCKET=<redacted>
HC_MINIO_ACCESS_KEY=<redacted>
HC_MINIO_SECRET_KEY=<redacted>
HC_WAVE2_TEST_DATABASE_ACK=<redacted>
HC_WAVE2_JWT_SIGNING_KEY=<redacted>
HC_REAL_API_OBJECT_STORE_ORIGIN=<redacted>
```

命令：

```text
pnpm exec playwright test e2e/real-api/main-chain.spec.ts --config playwright.real-api.config.ts
```

- Exit：1；1 failed；约 3.4 秒；未提高 Playwright 的 10 分钟总 timeout，未增加 sleep，未更换 fixture。
- 已完成：fresh pre-cleanup；首个管理员身份注册、登录与空账户 bootstrap；membership request 创建。
- 精确失败：首个管理员 membership bootstrap approval 返回 401，断言期望 200；失败发生在第二身份注册、P18 审批、P20、P03 preflight 和任何上传之前。
- 直接配置证据：Compose API issuer=`https://identity.example.invalid/`；本次 Playwright 启动未注入 `HC_JWT_ISSUER`，harness 因此使用默认 `https://be22.test.invalid/`；两者不一致。audience 一致。按“一次失败即停止”边界，未修正配置后重跑。
- Playwright 同时输出 `Internal error: step id not found: fixture@73`；主测试仍给出明确的 401 assertion stack，故主失败阶段可准确定位。

### 5.1 上传/CRC64 阶段状态

| 阶段 | 本轮 live 结果 |
| --- | --- |
| A/B 注册与空账户 | PARTIAL：首个身份完成，第二身份 NOT RUN |
| membership/capability 批准与拒绝 | FAIL：首个 bootstrap membership approval 401；后续 NOT RUN |
| current 200 / foreign 403 | NOT RUN |
| P20 create 201 / changed-body 409 | NOT RUN |
| legal Manifest preflight | NOT RUN；无 live response CRC64 可比较 |
| create upload session | NOT RUN；无 live `expected_crc64` 可比较 |
| browser multipart PUT | NOT RUN；object-store request count=0 |
| server parts reconciliation | NOT RUN |
| session complete | NOT RUN |
| commit-manifest | NOT RUN；commit 200 未形成，也未观察到本轮 422 CRC64_MISMATCH |
| stable workflow locator | NOT RUN |
| streamed/Manifest/MCAP CRC64 live equality | NOT RUN；checked-in fixture contract测试通过，但不能替代真实上传 |

因此，本轮不能回答“CRC64 十进制字符串修复是否真正关闭真实 commit 的 422”；不得用阶段 0 合同测试包装为 live PASS。

### 5.2 no-mock

- 首个注册页的即时 service-worker 检查为 0。
- 失败前观察窗口内 mock URL 计数为 0；same-origin browser responses=4（200×1、201×3）。
- 双身份最终 `0 mock Service Worker` 与完整 `0 MSW/mock URL` 断言位于 upload commit 之后，未到达；artifact 中默认的 0 不能冒充完整 no-mock gate，结论为 NOT RUN。

## 6. Cleanup 与独立零残留复核

| 检查 | 结果 |
| --- | --- |
| harness cleanup before | database total=0；object total=0 |
| harness cleanup after | 删除本轮 database rows=6；object total=0 |
| harness cleanup verification | database total=0；object total=0 |
| 独立再次 scoped cleanup | database rows=0；object-prefix objects=0 |
| 独立 multipart | 本轮 marker=0；六个本轮精确 prefix 下=0 |
| 全库 marker | 0 occurrences |
| MinIO 全 bucket 本轮 marker | objects=0；multipart=0 |
| 其他 namespace | cleanup SQL 只接收本轮两个精确 project，object cleanup 只接收本轮六个精确 prefix；未使用跨 namespace predicate，未命中其他 project |

独立 `pg_dump --data-only` marker 扫描只读且 exit 0；PostgreSQL 输出既有循环外键 restore warning，不影响 marker=0。未执行 DROP、TRUNCATE、down -v 或 volume rm。

## 7. Artifact/secret scan

- 扫描本轮 JSON、JUnit 与失败截图，共 3 个文件。
- JWT、Bearer、X-Amz 签名字段、signed query、带凭据 DSN、password value、secret value：全部 0 matches。
- JUnit 中发现 4 处内部测试 stack/path；formal JSON 中发现 18 处 cleanup object-prefix key（before/after/verification 各六个）。未在本报告展开其值。
- 因此敏感 credential scan 为 clean，但 artifact 的“禁止内部栈/对象 path”卫生要求未完全满足；失败证据按验收边界保留，未事后改写。

## 8. 停止状态与 NOT RUN 边界

- `docker compose -f compose.dev.yaml -f compose.real-api.yaml down`：exit 0；未使用 `-v`。
- Compose `ps -a` 为空；共享 PostgreSQL 与 MinIO volumes 均仍存在。
- 完整 Worker/Temporal、QC/Raw verification、Lance、annotation、review、P20 close 主链：**NOT RUN**。
- E01-E10、axe、keyboard/focus、fixture visual、视觉 baseline：**NOT RUN**。
- 生产性能：**NOT RUN**。
- 未建立或更新任何视觉 baseline；失败截图仅为诊断证据。

## 9. 最终裁决

**FE16-R1 FRESH UPLOAD SMOKE FAIL AT A/B IDENTITY BOOTSTRAP MEMBERSHIP APPROVAL (401 JWT ISSUER MISMATCH)**
