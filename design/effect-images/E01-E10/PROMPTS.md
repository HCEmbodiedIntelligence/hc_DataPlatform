# E01–E10 图像生成提示词记录

> 生成方式：Codex 内置 `image_gen`，`ui-mockup` 模式
>
> 日期：2026-08-17
>
> 用途：前端信息架构和视觉效果评审，不代表页面或真实 API 已实现

## 1. 共享提示词基线

所有图片共同使用以下设计约束：

```text
Use case: ui-mockup
Asset type: high-fidelity shippable Chinese enterprise web UI effect image for HC Data Platform.
Product: public-internet industrial robot data collection, ingest, QC, Lance conversion, annotation, cleaning, review, publication and governance platform.
Style: realistic React + Ant Design product UI; light lavender-gray page; white surfaces; indigo #5965D8 primary; cyan used sparingly; dark slate text; teal/amber/red semantic states; Lucide-style outline icons; Chinese system sans typography; precise high-density layout; restrained shadows and borders.
Signature: one thin signal rail only where it encodes real ordered data stages or lineage.
Responsive target: desktop 1440×900; workbench remains complete at 1280; no body horizontal overflow.
Constraints: pages are organized by function, never duplicated by role; no gold/cream template; no glassmorphism; no marketing hero; no emoji; no watermark; no pricing or costs; no fake API success; no cloud robot-control actions.
```

E01 先生成并完成文字修订，随后作为产品身份参考。E02 作为完整 Shell 参考，E06 作为共享工作台结构参考。

## 2. 每张图的最终增量提示

### E01｜认证与账户状态

```text
Show one desktop login page and compact registration, pending-approval and no-project state panels. Exact concepts: username/password login; public registration needs admin approval; no email verification; no MFA; account approval and project access are distinct. Authentication pages do not show the protected sidebar.
```

修订：删除等待审批卡片中的错误“申请未通过”；页脚改为 2026。

### E02｜全局 Shell

```text
Show the protected application shell with grouped functional navigation, P20 collection tasks first in collection/ingest group, separate project and region selectors, disabled future search/notification states, user menu and stable loading skeleton. One shared navigation, never role-specific.
```

### E03｜P01 工作台

```text
Show one real business signal rail: collection, received, automatic QC, 30 Hz alignment, Lance, annotation, cleaning, review and published. Keep SAVED separate from received. Below it show actionable to-dos, recent activity and one localized partial-data warning. QC anomaly only links to Raw diagnostics.
```

已知图像文字限制：`待冻结发布` 行右侧仍显示 `进入审核`；正式设计和实现必须使用 `进入发布`。两次单点图像文字编辑均未稳定生效，因此未伪造“已修正”结论。

### E04｜P20 采集任务

```text
Show collection task list plus detail drawer. The 8-digit task code is only classification. Separate people, PICO and robot assignment sets. Show SAVED, received, QC pass/risk/reject independently. P20 links to upload/QC but does not perform upload retry.
```

### E05｜P04 摄取/QC 详情

```text
Show ordered receive/fragment/CRC64/SHA-256/Manifest/MCAP/Topic/automatic-QC validation rail, identity relation, technical disclosure and findings. Automatic-QC anomaly stays in Raw and cannot enter Lance. Primary action is Raw diagnostics; automatic re-run does not manually change the decision.
```

### E06｜Raw 诊断工作台

```text
Show the shared workbench in read-only Raw mode: stream list, 2×2 camera grid, finding inspector, append-only diagnostic notes and one synchronized camera/joint/action/state/QC timeline. Allow evidence copy, re-collection request and automatic re-run; no pass/release/edit/download/publish actions.
```

### E07｜P08 标注工作台

```text
Reuse E06 spatial structure in Annotation mode for a fixed Lance version. Keep camera grid and shared timeline; replace findings with label/property/comment tools and annotation items. Show half-open logical step interval, autosave, save draft and submit for review. No task queue, Raw download or publishing.
```

修订：底部冲突状态从“未保存的更改”改为“所有更改已保存”。

### E08｜P11 清洗审核对比

```text
Reuse E06 spatial structure in Cleaning Review mode. Use original/edit/compare projections without moving the viewer. Original means baseline Lance, not Raw. Show excluded interval, impact summary, checklist, reviewer comment and approve/request-changes/reject. No freeze publication.
```

### E09｜P18 账户与权限

```text
Show pending-account queue, selected application and separate real-project grant. Approval automatically creates a private read-only project; real projects need separate capability-template plus data-scope grant. No fixed internal/contractor account type. High-risk actions require recent password re-authentication.
```

修订：将“创建个人私有项目”从可选复选框改为不可编辑的强制策略说明。

### E10｜P12/P13 容量与生命周期

```text
Show a capacity screen with used/total/growth, trend, hot/cold/archive distribution and project table, plus lifecycle policies and a fresh impact-simulation panel. Protect Raw, Manifest and published Manifest from deletion. Production archive migration and cache cleanup require dual review. No fees, costs, billing, delete or multipart-abort actions.
```

## 3. 最终文件

- `E01-auth-and-account-states.png`
- `E02-global-shell.png`
- `E03-p01-dashboard.png`
- `E04-p20-collection-tasks.png`
- `E05-p04-ingest-qc-detail.png`
- `E06-raw-diagnostic-workbench.png`
- `E07-p08-annotation-workbench.png`
- `E08-p11-cleaning-review.png`
- `E09-p18-access-approval-grants.png`
- `E10-p12-p13-capacity-lifecycle.png`

图片中的日期、用户名、任务码、数量、ID、IP、浏览器版本、容量和时间均为效果图示例，不得直接复制为测试验收值或正式合同。

## 4. 用户评审后的 V3 生成规则

首版提示词保留作为生成历史，以下规则覆盖首版中的冲突项：

- 品牌只引用 `frontend/src/assets/hangcha-logo.png`，不再让模型生成 Logo。
- 注册无审批、无邮箱/手机号验证；账户创建后为空账户。
- 数据上传页复用“新建上传/上传记录”，并从 Manifest 自动识别相机/Topic。
- P20 无分派、有效期、暂停/继续和模态/Topic 字段；同页抽屉创建，使用任务描述。
- 数据标注与清洗合并；相机同时显示且共享一个时间轴；编辑和审核均支持多级 Tag。
- 容量按 Raw、标注完成、待标注、问题数据分类；无冷热/中容量和影响模拟。

V3 文件：

- `E01-auth-and-account-states-v3.png`
- `E02-global-shell-v3.png`
- `E03-p01-dashboard-v3.png`
- `E04-p20-collection-task-create-v3.png`
- `E05-data-upload-v3.png`
- `E06-raw-diagnostic-workbench-v3.png`
- `E07-p08-annotation-workbench-v3.png`
- `E08-p08-tag-review-v3.png`
- `E09-p18-project-permission-approval-v3.png`
- `E10-p12-p13-capacity-lifecycle-v3.png`

导航文字收尾后的当前文件：

- `E04-p20-collection-task-create-v4.png`
- `E05-data-upload-v4.png`
- `E09-p18-project-permission-approval-v4.png`
- `E10-p12-p13-capacity-lifecycle-v4.png`

E04 为避免导航生成文字干扰且保持字段准确，当前使用折叠导航版：

- `E04-p20-collection-task-create-v5.png`
