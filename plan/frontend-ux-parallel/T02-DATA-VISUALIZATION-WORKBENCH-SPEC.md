# T02｜统一数据可视化工作台效果规格

> 阶段：效果图前置规格，不是实现计划。
> 适用前端：React 19 + Vite。
> 唯一设计内核：DataVisualizationWorkbench。Raw 诊断、Lance 浏览、标注、清洗、复核比较、已发布查看只能通过 adapter 与 capability flags 改变数据、工具和动作，不能再派生角色页面。

以下规则已经由 P01–P20 总计划锁定，不作为本规格的待确认项：

- P01 自动质检异常只能进入 Raw MCAP 只读诊断；人工不能把异常改判为通过，也不能绕过门禁进入 Lance。
- 只有自动质检通过后，才能执行 30 Hz 对齐并进入 Lance 浏览。
- 标注和清洗均版本化、非破坏；对齐后的业务区间统一为半开 step 区间 [start_step, end_step)。
- 清洗人员只提交复核；复核决定与冻结发布是两个独立动作和状态。
- source、edit、compare 必须使用同一空间结构，切换投影不移动摄像头、曲线、时间轴、检查器或主动作位置。

---

## 1. 当前实现地图与代码证据

### 1.1 方向基线

| 事实 | 代码/计划证据 | 对本规格的约束 |
|---|---|---|
| 自动质检通过后才有 30 Hz 与 Lance；标注/清洗不可变且区间半开；复核与发布分离 | plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:39-50 | Raw 与 Lance 必须是两个数据域；不能在 Raw 模式伪造 step，也不能从清洗直接发布 |
| 六种模式应共用一个工作台 | plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:105-125 | 六个 adapter，不是六套页面 |
| 1280 和 1440 都是验收宽度 | plan/P01-P20-FRONTEND-UX-REVISION-PLAN.md:137-140 | 必须以工作台容器宽度响应，而不是只看 viewport |
| “信号轨道”是核心识别元素 | design-system/hc-data-platform/MASTER.md:20-33 | 摄像头、曲线、异常、评论、标签在同一时间坐标上联动 |
| 工作台结构、交互与图表原则 | design-system/hc-data-platform/MASTER.md:129-166 | 左导航、中间媒体、右检查器、底部时间轴形成稳定骨架 |
| React/Vite、状态和验收边界 | design-system/hc-data-platform/MASTER.md:176-215 | 不给出 Next.js 专属实现建议；状态不能只画 happy path |

### 1.2 现有复用究竟复用了什么

| 区域 | 当前事实与代码证据 | 判断 |
|---|---|---|
| EpisodeWorkbenchCore | mode 仅支持 readonly / annotate / cleaning，且 props 直接接受 streams、timeline tracks 与回调：frontend/src/features/viewer/EpisodeWorkbenchCore.tsx:14-31 | 是“媒体 + 播放 + 时间轴”内核，不是六模式工作台 |
| EpisodeWorkbenchCore 布局 | 只渲染媒体格、关节轴、诊断、overlay、播放控制和 ClipTimeline：frontend/src/features/viewer/EpisodeWorkbenchCore.tsx:613-673 | 不拥有左队列、工具、右检查器、比较投影、主动作与页面状态 |
| 共享 Scaffold | 已定义 navigation / media / editor / inspector / timeline 五槽：frontend/src/shared/ui/layout/WorkbenchScaffold.tsx:5-13、32-53 | 可以作为迁移证据，但当前槽位与 EpisodeWorkbenchCore 的内置时间轴重叠 |
| P06 Lance Viewer | 使用 WorkbenchScaffold 与 EpisodeWorkbenchCore：frontend/src/pages/p06-dataset-detail/ViewerShell.tsx:257-315；所有流却被映射为 unsupported：同文件:138-150 | 只有壳与选择流程，尚不是可用的 Lance 浏览效果 |
| P08 标注 | capability 与资源动作共同决定编辑/保存/提交/复核：frontend/src/pages/p08-data-annotation/AnnotationTaskPage.tsx:323-409；页面自行搭建 header、三栏、工具与检查器：同文件:711-997 | 复用了中央内核，但没有复用完整工作台 |
| P08 轨道 | 页面把多级标注与人工问题转换为 tracks，同时又在 core 外绘制额外轨道：frontend/src/pages/p08-data-annotation/AnnotationTaskPage.tsx:669-690、878-915 | 同一事实存在两种轨道表现，跨模式无法稳定对齐 |
| P11 清洗 | 页面自行搭建 EDL、对照区、core、操作轨道和检查器：frontend/src/pages/p11-manual-cleaning/page.tsx:429-716 | 与 P08 只是视觉相似，不是同一组合内核 |
| P11 比较 | source / cleaned 占位比较区在 core 外：frontend/src/pages/p11-manual-cleaning/page.tsx:511-561；随后才渲染单份 core：同文件:562-625 | source、edit、compare 没有同构空间 |
| P11 媒体 | 清洗响应只有流身份，页面映射为 ready 却没有可加载资源：frontend/src/pages/p11-manual-cleaning/page.tsx:91-102；比较区明确显示未授权：同文件:532-560 | “ready”与“可观看”语义混淆，应拆分元数据就绪和资源可用性 |
| P11 主动作 | 当前页面以“提交版本/确认危险提交”为主，描述会创建输出并启动物化：frontend/src/pages/p11-manual-cleaning/page.tsx:302-317、718-755 | 与“清洗只提交复核”冲突，效果图必须改为“提交复核” |
| P07 复核 | 复核位于 Version detail 的 tab：frontend/src/pages/p07-version-detail/page.tsx:724-847；通过条件同时依赖复核与发布权限：同文件:295-314 | 当前复核和发布耦合，复核比较尚未进入统一工作台 |
| P07 通过后影响 | 当前确认文案包含启动发布流程：frontend/src/pages/p07-version-detail/page.tsx:1035-1044 | 新效果图必须拆成“通过复核”和后续独立“冻结发布” |
| P09 问题回跳 | Viewer 链接只带 Dataset / Version / Episode：frontend/src/pages/p09-manual-issues/page.tsx:81-91；问题实际拥有 stream 与半开范围：同文件:94-113 | 回跳会丢失定位；统一 cross-link 必须携带流、坐标与选中事实 ID |
| 数据不可变性 | AnnotationDraft 有 revision、冻结/失效只读态：frontend/src/entities/annotation-draft.ts:63-74；ReviewFinding 明确不可变：frontend/src/entities/review-finding.ts:23-40 | adapter 不能把草稿、提交物、Finding 压扁成可原地修改的同一对象 |
| 固定任务上下文 | 标注 task 固定 Dataset Version、Episode、Revision、streams 与 ns 范围：frontend/src/entities/annotation-task.ts:55-82 | 切模式或回跳时必须验证身份，不允许 latest/current 漂移 |
| 清洗映射 | source 与 output 映射显式验证半开范围和局部时长：frontend/src/features/cleaning/time-mapping.ts:21-35、44-108 | compare 应由 adapter 提供映射，不由组件猜测 |
| 状态覆盖 | 清洗已有 loading、empty、partial、forbidden、gone、conflict、offline、contract mismatch 等状态目录：frontend/src/features/cleaning/page-state.tsx:6-38 | 统一状态模型可以吸收这些状态，但需要补媒体/时间轴局部失败 |
| Mock 现实度 | 标注 Mock 已覆盖 3 相机 + 7 轴、6/14 轴、点云 pending、未知模态与局部媒体错误：frontend/src/mocks/fixtures/annotation/index.ts:119-150、frontend/src/mocks/handlers/annotation.handlers.ts:61-69 | 可复用为效果图数据基线 |
| 清洗 Mock | 已有 source 10 s、排除 0.4 s、复用率 0.96、两段输出与 source→output 映射：frontend/src/mocks/fixtures/cleaning/index.ts:289-345 | 可用于清洗和复核效果图，但动作语义需按新业务规则重命名 |
| 测试空白 | viewer 测试只覆盖拖拽区间、键盘边界、缩放和嵌套轨道标签：frontend/src/features/viewer/EpisodeWorkbenchCore.test.tsx:75-110 | 未覆盖六模式、局部相机失败、重试、可访问图表摘要、1280 与比较同构 |

### 1.3 不是“真复用”的直接证据

- P08 通过全局 .viewer-media-grid / .viewer-panel 重定义 core 外观：frontend/src/pages/p08-data-annotation/p08.css:45-51；P11 又用另一组同名全局规则：frontend/src/features/cleaning/cleaning.css:52-57。内核的视觉行为因此取决于调用页面加载了哪份 CSS。
- P11 在局部模块中直接隐藏 core 自带媒体格：frontend/src/pages/p11-manual-cleaning/workbench.module.css:206-213；后面的媒体格尺寸规则 frontend/src/pages/p11-manual-cleaning/workbench.module.css:276-299 不会恢复 display。这证明比较媒体不是共享内核的一部分。
- P08 默认三栏为 230 + 中心 + 306 px，viewport 达到 1280 后反而扩大为 260 + 中心 + 318 px：frontend/src/pages/p08-data-annotation/workbench.module.css:90-95、433-437；P11 同样从 250 + 中心 + 292 扩到 270 + 中心 + 310：frontend/src/pages/p11-manual-cleaning/workbench.module.css:70-75、410-413。
- 平台壳在宽屏占 220 px 导航并给 main 左右各 20 px：frontend/src/shared/ui/styles.css:31-39。1280 viewport 下实际工作台容器约 1020 px，因此上述 viewport breakpoint 会把中央区域压到约 400 px，而不是得到“桌面宽屏”。
- P08 摄像头最小卡宽 380 px：frontend/src/pages/p08-data-annotation/workbench.module.css:312-316；P11 双列 compare 每格在当前 1280 布局中只剩约 180 px。两页都用 overflow-x: clip 隐藏症状：frontend/src/pages/p08-data-annotation/workbench.module.css:1-9、frontend/src/pages/p11-manual-cleaning/workbench.module.css:1-9。

结论：保留 EpisodeWorkbenchCore 的播放、资源生命周期、composition 与时间轴交互经验，但下一阶段应把它吸收为 DataVisualizationWorkbench 的 MediaStage / Playback / SignalTimeline 子层；P08、P11 的页级三栏和额外轨道不能继续作为模式实现。

---

## 2. 唯一组件树与共享/模式边界

    DataVisualizationWorkbench
    ├─ WorkbenchSessionBoundary
    │  ├─ adapter.load(mode, identity)
    │  ├─ capability projection
    │  ├─ fixed identity / stale guard
    │  └─ WorkbenchStateRouter
    ├─ WorkbenchHeader
    │  ├─ breadcrumbs + human label + CopyableStableId
    │  ├─ mode badge + source lineage + read-only badge
    │  ├─ save/status facts
    │  └─ PrimaryActionSlot + SecondaryActions
    ├─ WorkbenchBody
    │  ├─ CollapsibleLeftPanel
    │  │  ├─ QueueNavigatorSlot
    │  │  ├─ StreamNavigator
    │  │  └─ ToolPaletteSlot
    │  ├─ WorkbenchCenter
    │  │  ├─ ProjectionToolbar
    │  │  ├─ SynchronizedMediaStage
    │  │  │  ├─ CameraPanel × N
    │  │  │  ├─ RobotScenePanel
    │  │  │  └─ PartialResourceState
    │  │  ├─ SignalChartDeck
    │  │  │  ├─ Joint / action / state / force / pose curves
    │  │  │  └─ AccessibleSignalSummary
    │  │  └─ CompareProjection
    │  │     ├─ source
    │  │     ├─ edit
    │  │     └─ compare
    │  └─ CollapsibleRightPanel
    │     ├─ SelectionInspector
    │     ├─ FactDetailSlot
    │     ├─ CommentThreadSlot
    │     └─ ModeEditorSlot
    ├─ StickyPlaybackBar
    └─ StickySignalTimeline
       ├─ ruler + playhead + selection
       ├─ camera availability
       ├─ state / action / joint summary
       ├─ anomaly / QC exception
       ├─ comment
       └─ annotation / cleaning / review tracks

### 永远共享

- 固定身份、来源谱系、模式与只读状态。
- 摄像头卡槽顺序、画面比例、播放位置、曲线排序、时间轴纵向轨道顺序。
- 单一 selection model：active stream、playhead、半开区间、focused fact。
- loading / empty / partial / forbidden / read-only / unavailable 的状态语言。
- 左右面板折叠、键盘帮助、屏幕阅读器摘要、URL 可恢复状态。
- source / edit / compare 的同构投影容器。compare 只是把每个固定卡槽切为 A/B、擦拭或差异叠层，不建立第二套布局。

### 只能由 adapter 提供

- 数据来源、时间坐标、流与单位、可加载资源引用、异常与 frame diagnostics。
- 模式工具集合、编辑器内容、动作及其 blocked reasons。
- source→edit 坐标映射、提交物与不可变 Finding。
- 权限与资源动作的交集；组件不读取角色名称，也不自行推断“谁应该能做什么”。

### 模式专属插槽

| 插槽 | Raw | Lance | Annotation | Cleaning | Review | Published |
|---|---|---|---|---|---|---|
| QueueNavigatorSlot | P01 异常上下项 | Episode/stream | 标注任务队列 | 清洗草稿/退回项 | 待复核版本 | 同版本 Episode |
| ToolPaletteSlot | 测量、复制证据 | 测量、创建人工问题 | 标签/对象/事件/关键帧 | 排除、切分、时间偏移、失效掩码 | Finding、通过、退回 | 无编辑工具 |
| ModeEditorSlot | QC 事实，只读 | 数据与 schema，只读 | schema 驱动表单 | 操作序列与预览摘要 | Finding 与复核意见 | 发布信息，只读 |
| PrimaryActionSlot | 返回异常清单 | 添加人工问题（若允许） | 提交标注复核 | 提交清洗复核 | 通过复核或退回 | 无，或导出（若允许） |

禁止项：不得出现 RawWorkbench、AnnotationWorkbench、CleaningWorkbench 等平行组件；不得在页面 CSS 中重定义共享 viewer class；不得让模式自己增加第二条时间轴或第二套媒体格。

---

## 3. 六模式矩阵

| 模式 | 数据与来源 | 只读性 | 工具 | 主动作 / 次动作 | 权限投影 | 错误与阻断 |
|---|---|---|---|---|---|---|
| Raw Diagnostic | Raw MCAP 授权资源、原始 timestamps、自动 QC 异常、帧序列与原始 stream 元数据；从 P01 exception 稳定链接进入 | 强制只读；不出现对齐 step、不允许改 QC 结论 | seek、原始 ns 测量、流开关、复制诊断证据、异常前后跳转 | 主动作“返回 QC 异常”；可复制链接/证据。绝无“标记通过”“生成 Lance”“人工覆盖” | 只有 Raw 读取许可才能加载；缺失时显示专属 forbidden，绝不回退到 Lance | MCAP 不可读、流缺失、timestamp 非单调、重复帧、丢帧、授权过期、QC 事实缺失；每项保持只读 |
| Lance Browser | 已通过自动 QC 且完成 30 Hz 对齐的固定 Revision；摄像头、joint、action、state、pose、force 等 | 数据只读；允许创建独立人工问题，但不能修改 Lance 基线 | step seek、曲线测量、stream/axis 开关、异常/人工问题定位 | 允许时主动作“添加人工问题”；次动作打开问题清单/标注入口 | 读取与“记录问题”分开；缺少写许可不影响浏览 | 对齐事实不为 ready 时禁止进入；局部相机失败不拖垮曲线；未知模态只读降级 |
| Annotation | 固定 Lance Revision、task、versioned draft、schema、参考标签、人工问题投影 | task stale、unknown schema、submitted draft 或无编辑许可时只读 | action / phase / object / event / keyframe、区间边界、属性表单 | 编辑时“提交标注复核”；保存状态为次级常驻动作；复核者的通过/退回属于同一 Review adapter 语义，不混入普通标注工具 | edit/save/submit/review 分开投影，并与资源 allowed actions 取交集 | draft conflict、schema mismatch、部分媒体失败、stale revision、submission blocked；本地恢复不能越过固定身份 |
| Cleaning | 固定 source Revision、versioned operation draft、权威 preview、source→edit mapping、只读退回 Findings | source 永远只读；操作 draft 可编辑；提交后或 lease/read-only 时整套工具只读 | exclude、split、offset、disable channel、invalid mask、source/edit/compare | 主动作“提交清洗复核”；保存与生成预览是次动作。不得出现“直接发布” | edit/save/preview/submit-review 分开；Finding 只有读取与定位 | preview stale/expired/failed、mapping 缺失、校验 blocker、资源冲突、局部媒体失败；不能把 preview ready 等同于媒体可用 |
| Review Compare | 原始 source、提交的 edit、同构 compare、不可变 Finding 草稿/决定上下文 | 数据与提交物只读；只允许新增本次复核 Finding 和作出复核决定 | source/edit/compare、差异跳转、Finding 区间与评论、证据摘要 | 主动作根据流程为“通过复核”或“退回修改”；发布不在此动作组 | review-decision 与 publish 两套独立许可；没有 publish 许可仍可完成复核（若允许） | 任一侧身份/映射不一致则 fail closed；单相机失败保持其他证据；预检过期要求重跑 |
| Published View | 冻结发布版本、固定 Revision、发布谱系与只读标签/问题/复核事实 | 全部只读 | 浏览、测量、复制稳定链接；可选导出由 capability 决定 | 默认无主写动作；发布管理入口不混入 viewer | 读取与导出独立 | 冻结资源不可用、局部媒体缺失、旧模态、无访问许可；不能静默切到其他版本 |

共同的数据源规则：

1. identity 必须固定 Dataset、Version、Episode、Revision；禁止 latest/current。
2. Raw 使用原始 ns 坐标；Lance 之后的编辑区间使用权威 step 坐标。原始 ns 只作为可展开的诊断值。
3. 30 Hz 不允许通过 1e9 / 30 在浏览器中自行取整生成 step；adapter 必须消费权威 step/timestamp 映射。
4. annotation、cleaning、review 的区间均显示为 [start_step, end_step)，end_step 不包含在内。
5. unavailable、unsupported、unauthorized、pending、partial、failed 是不同状态，不能合并为“暂无数据”。

---

## 4. Adapter 概念接口（仅前端效果 Mock）

此接口用于统一效果图、组件边界和 Mock 组织，不是正式后端合同，不定义接口路径，不承诺字段名。标注为“候选｜待合同确认”的内容只允许留在 mock adapter，不得据此修改正式合同。

    type WorkbenchMode =
      | "raw-diagnostic"
      | "lance-browser"
      | "annotation"
      | "cleaning"
      | "review"
      | "published";

    type StepRange = {
      startStep: number;
      endStepExclusive: number;
    };

    type WorkbenchIdentity = {
      datasetId: string;
      versionId: string;
      episodeId: string;
      revisionId: string;
      humanLabel?: string;                 // 候选｜待合同确认
      sourceRevisionId?: string;
    };

    type WorkbenchTimebase =
      | {
          kind: "raw-ns";
          startNs: string;
          endNs: string;
        }
      | {
          kind: "aligned-step";
          nominalRateHz: 30;
          stepCount: number;
          stepTimestampIndexRef: unknown;  // 候选｜待合同确认：权威映射引用
        };

    type WorkbenchCapabilityProjection = {
      canRead: boolean;
      canMeasure: boolean;
      canCreateManualIssue: boolean;
      canEditDraft: boolean;
      canSaveDraft: boolean;
      canCreatePreview: boolean;
      canSubmitForReview: boolean;
      canReview: boolean;
      canPublish: boolean;
      canExport: boolean;
      blockedReasons: readonly {
        action: string;
        code: string;
        message: string;
      }[];
    };

    type WorkbenchStream = {
      id: string;
      label: string;
      canonicalPath: string;
      modality: string;
      unit?: string;
      axes?: readonly {
        id: string;
        label: string;
        unit: string;
        mappingStatus: "mapped" | "unmapped" | "not-applicable";
      }[];
      metadataState: "ready" | "pending" | "missing" | "unsupported";
      resourceState: "authorized" | "unauthorized" | "expired" | "failed" | "not-needed";
      resourceRef?: unknown;
    };

    type FrameDiagnostics = {
      streamId: string;
      observedFrames?: number;             // 候选｜待合同确认
      droppedFrames?: number;              // 候选｜待合同确认
      duplicateFrames?: number;            // 候选｜待合同确认
      nonMonotonicTimestamps?: number;      // 候选｜待合同确认
      alignmentErrorMs?: {
        p50: number;
        p95: number;
        max: number;
        tolerance: number;
      };                                   // 候选｜待合同确认
      affectedRawRanges?: readonly {
        startNs: string;
        endNs: string;
      }[];                                 // 候选｜待合同确认
    };

    type WorkbenchTrack = {
      id: string;
      kind: "camera" | "joint" | "action" | "state"
          | "anomaly" | "comment" | "label" | "cleaning" | "review";
      label: string;
      streamId?: string;
      unit?: string;
      segments: readonly {
        id: string;
        label: string;
        range?: StepRange;
        rawRange?: { startNs: string; endNs: string };
        severity?: "info" | "warning" | "error" | "critical";
        description?: string;
      }[];
      emptyReason?: "no-events" | "not-collected" | "not-applicable" | "failed";
    };

    type WorkbenchProjection = {
      key: "source" | "edit";
      revisionId: string;
      streams: readonly WorkbenchStream[];
      tracks: readonly WorkbenchTrack[];
      sourceToEditMapRef?: unknown;         // 候选｜待合同确认
    };

    type WorkbenchViewModel = {
      mode: WorkbenchMode;
      identity: WorkbenchIdentity;
      lineageLabel: string;
      timebase: WorkbenchTimebase;
      readOnly: boolean;
      readOnlyReason?: string;
      capabilities: WorkbenchCapabilityProjection;
      projections: readonly WorkbenchProjection[];
      activeProjection: "source" | "edit" | "compare";
      frameDiagnostics: readonly FrameDiagnostics[];
      selection: {
        activeStreamId?: string;
        playheadStep?: number;
        playheadNs?: string;
        range?: StepRange;
        rawRange?: { startNs: string; endNs: string };
        focusedFactId?: string;
      };
      leftPanel: unknown;
      rightPanel: unknown;
      primaryAction?: unknown;
      secondaryActions: readonly unknown[];
      state: WorkbenchLoadState;
    };

    interface DataVisualizationWorkbenchAdapter {
      load(identity: WorkbenchIdentity, signal: AbortSignal):
        Promise<WorkbenchViewModel>;
      subscribe?(listener: (event: unknown) => void): () => void;
      seek(selection: WorkbenchViewModel["selection"]): void;
      perform?(intent: unknown): Promise<unknown>;
      dispose(): void;
    }

Adapter 约束：

- 组件只接受规范化的 ViewModel，不直接解析各模式的 wire shape。
- capability projection 是“用户许可 ∩ 资源允许动作 ∩ 当前状态”的结果；UI 不检查角色。
- perform 只能接受当前 adapter 明确暴露的 intent；Raw 和 Published adapter 默认不提供写 intent。
- source/edit 两个 projection 必须返回相同 slot key 集合。缺少流也保留 slot，以 Missing/Unavailable 状态占位，保证 compare 不跳位。
- 任何候选字段缺失时，效果图必须有诚实降级：例如没有 droppedFrames 就显示“未提供帧诊断”，不能显示 0。

### 数值与标识格式

- aligned 模式主读数：Step 1,842 / 3,600；次读数：01:01.400。详情/复制才展示原始 timestamp。
- Raw 主读数：相对起点 +12.345 678 s；展开后显示精确 12,345,678,900 ns。不得把长 ns 直接塞进每一行主界面。
- 区间统一写作 [540, 660) steps，并紧邻显示“4.00 s”；不能写模糊的“540–660”。
- 稳定 ID 显示“可读标签 + 尾 8 位”，旁边提供有名称的复制按钮；完整值允许换行，不允许只有 hover 才能获得。
- unit 放在轴标题或列标题，例如 Joint position (rad)、Force (N)、Alignment error (ms)；缺失 unit 显示“单位未提供”，不能猜。
- 空轨道保留固定高度并写明“无事件 / 未采集 / 不适用 / 加载失败”，不能直接消失。
- dropped 与 duplicate 不能只用颜色：同时显示图形、数量和可读文本，例如“丢帧 18 · 重复 3”。
- alignment error 使用有符号 ms、容差线和文字结论，例如“p95 +47.8 ms，超过 ±20 ms 容差”；颜色只是辅助手段。

---

## 5. 1440 / 1280 ASCII 线框

线框中的宽度指 viewport；平台导航仍占现有壳空间。工作台以 ResizeObserver / container query 读取“自身可用宽度”，不以 viewport 猜测。

### 1440 viewport（工作台容器约 1180 px，左右栏默认展开）

    ┌──────────────────────────────────────────────────────────────────────────────────────────────┐
    │ Dataset / Version / Episode     [Lance · 30 Hz] [只读]        [添加人工问题] [更多 ▾]        │
    │ 可读名称 · revision …a91c2d4e  保存/同步事实                  ↑ 主动作固定右上，不随滚动消失  │
    ├─────────────────┬──────────────────────────────────────────────────────┬─────────────────────┤
    │ [‹] 队列/流 228 │ Projection: [Source] [Edit] [Compare]   Step 1842    │ 检查器 300      [›] │
    │                 │ ┌───────────────────┬───────────────────┐            │ Selection           │
    │ 异常/任务上下项 │ │ Front camera     │ Left wrist       │            │ [540,660) steps     │
    │                 │ ├───────────────────┼───────────────────┤            │ 4.00 s              │
    │ Streams         │ │ Right wrist      │ Robot scene      │            │                     │
    │ ● front         │ └───────────────────┴───────────────────┘            │ Fact / Comment      │
    │ ● wrist-left    │ ┌ Joint position (rad) ────────────────┐            │ Mode editor         │
    │ ○ wrist-right   │ ├ Action / State ──────────────────────┤            │                     │
    │                 │ └ Anomaly marker + textual summary ────┘            │                     │
    │ Mode tools      │                                                      │                     │
    ├─────────────────┴──────────────────────────────────────────────────────┴─────────────────────┤
    │ [▶] 00:01:01.400 / 00:02:00.000   1×   J/K/L   Camera 2 partial                            │
    ├──────────────────────────────────────────────────────────────────────────────────────────────┤
    │ Camera availability ─────────────────────────────────────────────────────────────────────── │
    │ Joint / Action / State ──────────────────────────────────────────────────────────────────── │
    │ Anomaly / Comment / Label / Edit / Finding ──────────────────────────────────────────────── │
    │                                  sticky bottom Signal Timeline                               │
    └──────────────────────────────────────────────────────────────────────────────────────────────┘

### 1280 viewport（工作台容器约 1020 px，左栏收成 rail、右栏默认关闭）

    ┌────────────────────────────────────────────────────────────────────────────────────────┐
    │ Episode 装配 A-042  [Cleaning] [草稿已保存]        [提交复核] [检查器] [更多 ▾]          │
    │ revision …a91c2d4e · source→edit                    ↑ 主动作始终可见；不放进 overflow 菜单 │
    ├────┬───────────────────────────────────────────────────────────────────────────────────┤
    │[»] │ [Source] [Edit] [Compare]     Step 1842 / 3600                Inspector closed [I] │
    │ Q  │ ┌───────────────────────────────┬───────────────────────────────┐                  │
    │ S  │ │ Front camera: same slot A/B  │ Left wrist: same slot A/B    │                  │
    │ T  │ ├───────────────────────────────┼───────────────────────────────┤                  │
    │ ?  │ │ Right wrist: partial         │ Robot scene                  │                  │
    │    │ └───────────────────────────────┴───────────────────────────────┘                  │
    │    │ ┌ Joint / action / state curves; horizontal pan stays inside chart only ─────────┐│
    │    │ └─────────────────────────────────────────────────────────────────────────────────┘│
    ├────┴───────────────────────────────────────────────────────────────────────────────────┤
    │ [▶] 01:01.400  1×       [540,660) steps · 4.00 s       partial camera status             │
    ├────────────────────────────────────────────────────────────────────────────────────────┤
    │ Camera ──────────────────────────────────────────────────────────────────────────────── │
    │ State / Action ───────────────────────────────────────────────────────────────────────── │
    │ Anomaly / Comment / Cleaning / Finding ───────────────────────────────────────────────── │
    └────────────────────────────────────────────────────────────────────────────────────────┘

1280 行为规则：

- 当工作台容器小于 1120 px：左栏自动收为 44 px rail，右栏成为不改变中心宽度的 drawer；用户仍可分别展开，两者状态可恢复。
- 展开 drawer 时焦点进入 drawer，Escape 关闭并回到触发按钮；背景不形成第二个纵向滚动容器。
- 摄像头最小可读宽度 280 px；不足时从 2×2 变为 1×N，不把卡压到 180 px，也不横向裁切整个页面。
- 页面只有一个主纵向滚动；时间轴内部可以水平缩放/平移，队列长列表采用虚拟化而不是固定高度嵌套滚动。
- 主动作固定在 header 右侧；1280 下仍显示文字。只有次动作进入“更多”，不得隐藏保存状态、只读状态或 blocked reason。

---

## 6. 摄像头、曲线、异常、评论的联动与可访问性

### 单一选择模型

    camera / curve / anomaly / comment / label
                         │
                         ▼
      { activeStreamId, playhead, range, focusedFactId, origin }
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
       MediaStage    SignalChart    SignalTimeline + Inspector

联动规则：

1. 点击摄像头卡：设置 activeStreamId；对应曲线与所有属于该流的轨道加粗，其他流不隐藏。
2. 点击/键盘激活曲线采样点：播放头移动到权威 step；所有可用摄像头同步 seek；检查器显示值、unit、stream 与采样质量。
3. 点击异常：选择其半开区间，将播放头置于 start_step，并把对应摄像头、曲线区间、异常轨道和检查器绑定到同一 focusedFactId。
4. 点击评论或标签：使用相同规则定位；点评论中的 stream 引用只改变 activeStreamId，不清除区间。
5. source / edit / compare 切换保留 selection。若映射后区间不存在，保持 source 区间并显示“此段在 edit 中被排除”，不能跳到相邻内容。
6. hover 只做预览；click、Enter 或 Space 才提交 selection，避免多个面板互相触发循环。联动事件带 origin，来源面板不重复回写。
7. URL 只持久化固定身份、mode、projection、active stream、playhead step/ns、半开区间、focused fact 与面板折叠状态；不写评论正文、临时授权资源或敏感 payload。

### 键盘

- Tab 顺序：跳过链接 → header 主动作 → 左 rail → projection → 媒体卡 → 播放 → 时间轴 → 右检查器。
- Space：播放/暂停切换；J / K / L：后退、暂停、前进；Left / Right：单 step；Shift + Left / Right：10 steps；Home / End：固定范围首尾。
- [ / ]：把当前 playhead 设为区间入点/出点；Alt + Left / Right：上一个/下一个异常或 Finding。
- 1–9：选择对应可见摄像头；C：聚焦评论轨；G：聚焦异常轨；?：打开快捷键说明。
- 快捷键只在 workbench focus scope 内生效；input、textarea、select、contenteditable 内不劫持。所有命令都有按钮或菜单等价入口。
- timeline segment 必须是可聚焦 button/option，而不是只带 title 的 span；轨道内使用 roving tabindex，方向键在同轨移动，上下键换轨。

### 屏幕阅读器

- 每个图表同时提供可见或可展开的文本摘要：流名、unit、当前值、可见区间 min/max、趋势、异常数与最大偏差。canvas 的 aria-label 不能作为唯一替代。
- 时间轴提供结构化列表：“异常 TIMESTAMP_DRIFT，High，[1200,1236) steps，1.2 s”；颜色、图形与位置之外必须有文字。
- selection 只在用户完成 seek/选择后用礼貌 live region 宣告；播放每帧不播报。
- partial camera failure 宣告一次，并说明“其他 2 路摄像头、曲线和时间轴仍可用”。
- compare 模式以“Source/Edited/差异”作为组名；相同 slot 的两个画面共享可读标题，避免读屏用户误认为是不同摄像头。
- 所有 icon-only 按钮都有可访问名称；复制成功、保存成功、冲突和只读切换有文字反馈。

---

## 7. 状态规格

| 状态 | 中央区 | 左/右栏 | 主动作 | 恢复方式 |
|---|---|---|---|---|
| First loading | 保留最终骨架比例的 camera/chart/timeline skeleton；不闪现空态 | 保留栏宽与标题 skeleton | disabled，显示“加载中” | 自动完成；超过阈值显示可取消/重试 |
| Empty Episode | 明确“固定 Revision 无可视 stream”；列出已知元数据与可能原因 | Stream 列表为空但保留说明 | 只保留返回/复制身份 | 返回列表或切固定 Revision；不自动切 latest |
| Empty track | 轨道保留，显示无事件/未采集/不适用 | 检查器说明时间域与来源 | 不受影响 | 无需重试，除非原因是 failed |
| Partial camera failure | 失败卡保持原 slot，显示失败原因、最近成功时间与重试；其他卡/曲线/时间轴继续 | Stream 状态同步为 failed；检查器保留诊断 | 与失败流强相关的动作单独阻断 | 单卡重试，不刷新整个 workbench |
| Camera unauthorized | 卡内显示“无媒体读取许可”，不把元数据标成 missing | 右栏不泄露资源引用 | 依赖画面的动作阻断并给理由 | 权限变化后显式重新加载 |
| Timeline failure | media 保持可看；底部显示时间轴加载失败和文本事实列表；禁止区间编辑 | Inspector 仍能展示固定事实 | 依赖区间的动作 disabled | 只重试 timeline/track 数据 |
| Raw no permission | 整个 Raw 内容 forbidden；仅显示固定异常引用的最小安全摘要 | 不显示 stream 名、路径或帧统计 | 只有“返回 QC 异常” | 获取许可后重新进入；绝不跳 Lance |
| Read-only | header 常驻“只读 + 原因”；编辑控件呈阅读样式而非一片 disabled 灰 | 工具栏变为浏览/测量；检查器内容可复制 | 隐藏写主动作或显示明确只读原因，不伪装可点击 | 状态变化后重新验证 identity/capabilities |
| Feature unavailable | 保留其 slot，说明“当前数据未提供此能力” | 左栏对应工具隐藏或 disabled 并给理由 | 不影响无关主动作 | 合同/资源提供后局部加载 |
| Preview pending | source 正常；edit slot 显示进度事实而非假画面 | 清洗摘要标 pending | 提交复核阻断 | 完成后只替换 edit projection |
| Preview stale/expired | source 正常；edit 带 stale overlay，旧预览不作为可提交证据 | 显示当前 draft 与 preview 身份差异 | 提交复核阻断 | 保存后重新生成 preview |
| Identity/contract mismatch | fail closed，不显示可能属于其他 Revision 的内容 | 显示 request reference 与固定身份 | 全部写动作阻断 | 手动重试或返回；不拼接局部旧数据 |
| Offline/reconnecting | 保留最后一次成功只读快照并标陈旧 | 编辑内容可保留在当前 session，但不可提交 | 写动作阻断 | 连接恢复后重新验证版本，不乐观合并 |
| Unknown enum/modality | 对应区域只读，展示安全 raw label 与“客户端未知” | 其他已知区域继续 | 影响范围内的写动作阻断 | 升级客户端或合同适配 |

所有错误状态必须区分“无数据”和“加载失败”；必须有明确作用域（整页、projection、stream、track、action），不得让一条腕部相机失败把整个 Episode 变成 fatal error。

---

## 8. 视觉效果图清单与真实数据

至少交付以下 6 张核心效果图；前 5 张为必选，Published 用来证明第六模式仍复用同一内核。每张同时标注 1440 或 1280、主任务、焦点状态与一项非 happy-path 事实。

### M01｜Raw MCAP 自动质检异常诊断（1440）

- 身份：Dataset“装配产线 A”，Episode“A-042”，Raw object 尾号 …7f3a91c2。
- 数据：3 路相机；joint/state 原始 stream；原始范围 +00.000000 s 至 +120.000000 s。
- 异常：TIMESTAMP_NON_MONOTONIC；Front camera 丢帧 18、重复 3；p95 alignment error +47.8 ms，容差 ±20 ms。
- 画面：异常轨与 frame diagnostics 同时定位 +41.200000 s；显示精确 ns 的展开详情。
- 焦点：用户从 P01 异常进入，查看证据后返回。主动作只有“返回 QC 异常”，图中不得出现“通过”“Lance”“覆盖”按钮。

### M02｜Lance 30 Hz Episode 浏览（1440）

- 身份：同 Dataset 的已通过 Revision“baseline-2026-08-16”，Step 1,842 / 3,600。
- 数据：Front / left wrist / right wrist，7-DOF joint position (rad)，gripper state，action command。
- 时间：主读数 01:01.400；选区 [1,800, 1,920) steps = 4.00 s。
- 异常：Left wrist camera 单卡授权过期，其他画面与曲线正常。
- 焦点：从曲线点击 J03 峰值后，三路画面和 timeline 同步定位；允许时显示“添加人工问题”。

### M03｜标注工作台（1280）

- 任务：ann-task-progress-01；固定 3 camera + 7 axes；Ontology“assembly 2.1”。
- 草稿：revision 3；PHASE GRASP_PART；confidence 0.92；区间 [360, 1,080) steps。
- 状态：本地恢复摘要已加载；right wrist partial failure；submission gate advisory。
- 焦点：左栏折叠、右 inspector drawer 打开，键盘调整 end_step；主动作“提交标注复核”，保存状态常驻且不与主动作抢色。

### M04｜清洗 source/edit/compare（1280）

- Source：10.00 s / 300 steps；Edit：9.60 s / 288 steps；复用率 96%，两段输出。
- 操作：EXCLUDE_RANGE [54, 66) steps（0.40 s）；SPLIT at step 180；另有一个 disabled channel 示例。
- Compare：相同 camera slot 内 A/B 擦拭；曲线使用同一纵轴和 unit；被排除区显示 hatch + 文字。
- 状态：preview ready 但 right wrist 资源 unavailable；Finding 只读定位仍可用。
- 焦点：选中排除区，检查器显示 source/edit 映射。主动作必须是“提交清洗复核”，不是提交版本或发布。

### M05｜复核比较（1440）

- 对象：提交的 cleaning revision 与其固定 source；compare 不改变 camera/curve/timeline 位置。
- Finding：POSE_DISCONTINUITY，High，[120, 132) steps，0.40 s；评论“第一输出边界仍存在跳变”。
- 证据：source 与 edit 同步播放，差异曲线显示 max 0.18 rad，文本摘要可展开。
- 状态：预检当前、无 blocker；发布许可缺失但复核许可存在。
- 焦点：复核者选择 Finding 后执行“退回修改”或“通过复核”。界面没有“发布”合并动作。

### M06｜Published 只读回放（1440）

- 对象：冻结版本“2026.08.17-release”，同一 3-camera / curves / timeline 布局。
- 事实：复核通过、发布时间、谱系只读；评论与标签可查看。
- 状态：一个历史点云模态 unsupported，slot 保留并给可读解释。
- 焦点：复制稳定深链并打开 chart textual summary；无写工具。

每张图的验收截图还需包含：

- 一张默认视图、一张 keyboard focus 可见视图。
- 至少一个完整长 ID 的换行/复制表现。
- 至少一个空轨或 unavailable slot。
- 颜色之外的异常/状态文字。
- 1440 图验证左右栏展开；1280 图验证左 rail + 右 drawer，中央媒体不被压到不可读。

---

## 9. 最新 Web 指南审计与 React 性能规格

审计依据：2026-08-17 拉取的 [Web Interface Guidelines 最新 command.md](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md)。以下为针对当前 viewer/workbench 的 file:line 发现；不是泛化建议。

### 9.1 file:line 审计

- [P0] frontend/src/pages/p11-manual-cleaning/workbench.module.css:211-213 — 共享 core 的媒体格被隐藏，source/edit compare 另建布局；违反同构结构与真实复用目标。
- [P0] frontend/src/pages/p08-data-annotation/workbench.module.css:433-437、frontend/src/pages/p11-manual-cleaning/workbench.module.css:410-413 — 以 viewport ≥1280 扩大侧栏，没有考虑平台壳占宽；1280 实际容器被挤压。改用容器响应。
- [P0] frontend/src/pages/p08-data-annotation/workbench.module.css:8、frontend/src/pages/p11-manual-cleaning/workbench.module.css:7 — overflow-x: clip 会隐藏溢出而不是修复；验收应证明无横向溢出。
- [P1] frontend/src/features/viewer/EpisodeWorkbenchCore.tsx:99-106 — 快捷键挂在不可聚焦 div，Space 只 play 不 toggle；只有焦点恰在内部按钮时才工作。需要明确 workbench focus scope 和等价按钮。
- [P1] frontend/src/features/viewer/EpisodeWorkbenchCore.tsx:581-600 — timeline segment 是 span + title，不能键盘聚焦，名称依赖 hover。应改为可聚焦语义项，并提供结构化文字列表。
- [P1] frontend/src/features/viewer/EpisodeWorkbenchCore.tsx:262-264 — canvas 只有 aria-label，没有数据摘要或表格替代；图表对屏幕阅读器信息不足。
- [P1] frontend/src/features/viewer/EpisodeWorkbenchCore.tsx:247-257 — panel 失败只显示 alert，没有局部重试、错误范围和“其他面板仍可用”说明。
- [P1] frontend/src/pages/p08-data-annotation/AnnotationTaskPage.tsx:731-733 — “报告人工问题”按钮没有动作处理；视觉上可用但无响应，属于误导交互。
- [P1] frontend/src/pages/p09-manual-issues/page.tsx:81-91、231-237 — 回 Viewer 未携带 stream、range、issue focus，深链不能恢复用户上下文。
- [P1] frontend/src/pages/p11-manual-cleaning/workbench.module.css:125-131、405-408 — 左操作列表和右检查器各自滚动，叠加页面/时间轴滚动，产生嵌套滚动与键盘迷失风险。
- [P1] frontend/src/pages/p08-data-annotation/workbench.module.css:200-205 — task queue 固定 212 px 内滚；当前页面又只截取少量任务，无法验证真实长队列与虚拟化。
- [P1] frontend/src/pages/p08-data-annotation/workbench.module.css:70-78、frontend/src/pages/p11-manual-cleaning/workbench.module.css:47-60 — 核心动作最小高度 32 px；需在密集桌面场景验证 target 间距，并为触摸/高缩放提供至少 44 px 命中区域。
- [P1] frontend/src/pages/p08-data-annotation/AnnotationTaskPage.tsx:718-728、frontend/src/pages/p06-dataset-detail/ViewerShell.tsx:259-274 — 原始长 ID 占据标题层级；应使用可读标签 + 尾号 + 显式复制/展开。
- [P1] frontend/src/pages/p11-manual-cleaning/page.tsx:571-619 — 页面在 core 时间轴外再画一套操作轨；键盘、缩放和 selection 无法共享。
- [P2] frontend/src/features/viewer/EpisodeWorkbenchCore.tsx:248-256 — ready/missing/unsupported 等内部英文状态和 schema 标识直接进入用户界面；需要面向任务的中文标签与技术详情分层。
- [P2] frontend/src/pages/p11-manual-cleaning/page.tsx:645-654、frontend/src/pages/p09-manual-issues/page.tsx:102-112、219-224 — 大段 raw ns 作为主读数，可扫描性差；按第 4 节格式化并保留精确复制。
- [P2] frontend/src/pages/p08-data-annotation/p08.css:45-51、frontend/src/features/cleaning/cleaning.css:52-57 — 两个页面通过全局选择器覆盖同一 viewer classes，样式依赖导入顺序；共享内核应拥有隔离样式与 tokens。
- [P2] frontend/src/features/viewer/EpisodeWorkbenchCore.tsx:100-105 — 播放按钮使用文本符号，视觉和读屏名称虽存在，但图标系统不统一；使用现有图标并保留 aria-label。
- [Pass] frontend/src/features/viewer/EpisodeWorkbenchCore.tsx:520-545、550-576 — playhead slider 与区间手柄已有键盘基础，应保留并把单位从任意比例步长升级为权威 step。
- [Pass] frontend/src/pages/p08-data-annotation/p08.css:13、frontend/src/shared/ui/styles.css:78-80 — 已有 focus-visible 与 reduced-motion 处理，可作为共享 workbench 基线。
- [Pass] frontend/src/features/viewer/EpisodeWorkbenchCore.tsx:371-376 — layout read 发生在 pointer event 中，不在 render；维持这一点。

### 9.2 先测量，再优化

建立三份固定 profiling 数据集：

1. Medium：3 cameras、7 axes、3,600 steps、20 track segments。
2. Large：6 cameras、14 axes、108,000 steps（1 h @ 30 Hz）、200 track segments、100 comments、100 queue items。
3. Stress：8 cameras、32 signal axes、1,000,000 visible source samples、2,000 track segments、100,000 annotation entries 的只读投影。

每次性能变更先记录 React Profiler、浏览器 Performance、memory timeline 和实际 dropped frames；未达到阈值时不凭感觉引入 memo、虚拟化或降采样。

### 9.3 预算

| 指标 | 目标预算 | 失败后动作 |
|---|---|---|
| 30 Hz 播放主线程 frame | p95 ≤16 ms；5 分钟 dropped visual frames <1% | 先定位 decode、canvas、3D 或 React commit；只优化占比最高项 |
| React playback commits | workbench shell ≤10 commits/s；单次 p95 ≤8 ms | 把 playhead 订阅局部化，禁止整页订阅逐帧 clock |
| seek → 可见 playhead | p95 ≤100 ms | 检查同步扇出、资源 seek 与 chart hit-test |
| projection 切换 | 已缓存 source/edit 时 p95 ≤150 ms；不得重建不变 shell | 只替换 projection data，稳定 slot keys |
| queue/filter 输入 | p95 ≤100 ms，输入不丢帧 | 非紧急过滤使用 transition，列表使用稳定 key |
| 5 分钟稳定内存 | warm-up 后增长 ≤20 MB，资源/订阅数回到基线 | 检查 object URL、media、canvas、3D、AbortController 与 listener disposal |

预算需在产品确认的目标浏览器和硬件上校准；未校准前标记为“目标值”，不能宣称已通过。

### 9.4 分割、订阅、虚拟化与降采样触发器

- 路由级：Raw parser/diagnostics、3D/pointcloud、Annotation editor、Cleaning editor、Review diff 各自用 React.lazy + Vite dynamic import 条件加载；共享 media/timeline shell 保持常驻。现有 3D 动态加载可参考 frontend/src/features/viewer/lazy-three-loader.ts:19-33。
- 条件级：只有 adapter capability 与 modality 同时需要时才加载重模块；禁止把六模式全部打进首屏。
- 本地订阅：PlaybackClock 当前每个 animation frame 向全部 listener emit：frontend/src/features/viewer/PlaybackClock.ts:44-81；React 文本已限至 10 Hz：同文件:132-159。下一阶段把 playhead、各 canvas 与 video 同步保持外部局部订阅，header、左右栏、动作区不得订阅逐帧值。
- 3D：当前 runtime 连续 requestAnimationFrame render，applyTime 又额外 render：frontend/src/features/viewer/runtime/threeRobotSceneRuntime.ts:105-118。先 profile 是否双重绘制，再决定按脏状态渲染或降低不可见频率。
- queue：超过 50 项或 DOM 节点超过 200 时启用虚拟化；折叠时完全卸载 item body，只保留计数与当前项。
- timeline：可见 segment 超过 200 或轨道超过 24 时启用按可见窗口虚拟化；完整事实仍通过文本摘要/搜索访问。
- chart：单可见序列 <1,000 points 可直接绘制；≥1,000 使用 Canvas 与 viewport downsampling；>10,000 先按像素列/LOD 聚合。异常峰、边界和最值必须作为 protected points，不能被采样抹掉。
- multi-camera：超过 4 路或非可见卡时延迟授权/解码；active 与 compare 对应卡优先，offscreen 卡显示最后帧/占位并保留状态。
- annotation entries：编辑列表超过 100 项先虚拟化；100,000 项只读 stress 数据不得一次建立全部 DOM。
- memo：只对 profiler 证明昂贵且 inputs 稳定的 composition、normalized tracks、axis groups、mapping index 使用 memo；不为简单字符串和按钮盲目 memo。
- transition：队列筛选、轨道搜索、非关键 inspector 聚合可用 startTransition；playhead、拖拽手柄、输入回显和主动作状态保持紧急更新。
- content-visibility 只用于真正位于滚动视口外的长只读段落/列表，不用于 camera、playback、timeline 等首屏关键区。

### 9.5 后续实现验证清单（本阶段不实施）

- 六 adapter 对同一组件树的 contract tests。
- 1440/1280 screenshot tests：左右栏、drawer、主动作、无页面横向溢出。
- keyboard-only：进入、seek、区间、segment、compare、drawer、主动作完整闭环。
- axe + 人工读屏：canvas summary、partial error、range 与 compare 语义。
- fixed identity / URL 恢复 / stale guard tests。
- Medium/Large/Stress profiling 报告与上述预算对照。

---

## 10. 产品确认与技术验证（分开处理）

### 10.1 已锁定、无需再确认的产品规则

- 一个 DataVisualizationWorkbench；六模式由 adapter/capability 驱动。
- Raw exception 只读，不可人工改 QC pass，不可绕行 Lance。
- 30 Hz 与 Lance 只在自动 QC pass 后存在。
- annotation / cleaning 非破坏、版本化，区间 [start_step, end_step)。
- cleaner 只提交复核；review 与 freeze publish 分离。
- source/edit/compare 同构。

### 10.2 仍需产品确认

1. Raw 诊断允许的次动作边界：是否只允许复制证据/链接，还是允许创建一个独立诊断备注；无论答案如何，都不改变 QC 结论。
2. “提交清洗复核”后的产品状态名称、队列归属和返回路径文案。
3. 标注复核与数据版本复核是否共享 Review 模式的视觉动作词，还是保留“通过标注/退回标注”和“通过版本/退回版本”两套文案。
4. Comment 在 Lance、Annotation、Cleaning、Review、Published 中分别是可写、只读还是隐藏；评论是否也是版本化事实。
5. Published 模式是否展示内部 Finding / ManualIssue，或只展示面向发布消费者的安全摘要。
6. 1280 下右 inspector 默认关闭是否符合高频审核任务；若 Review 必须默认开，则左栏保持 rail、camera 自动单列。
7. 异常严重度、Finding 严重度和颜色词汇是否统一；颜色不影响无障碍文本要求。
8. 主动作优先级：Annotation 的“保存”是否始终为次动作；Review 同时可通过/退回时哪个作为 primary、哪个为 danger secondary。

### 10.3 需要工程验证，不由产品拍脑袋决定

1. 权威 30 Hz step↔timestamp 映射的来源、精度、gap/duplicate 处理，以及 source→edit 一对多/零映射语义。
2. Raw MCAP 是否能按时间窗口读取、可支持哪些 modality、授权过期后的局部刷新方式。
3. Lance/preview/published 的媒体资源描述是否覆盖多 camera、pointcloud、曲线 window 与局部重试。
4. droppedFrames、duplicateFrames、nonMonotonicTimestamps、alignmentError 的正式定义与可用聚合层级；本规格字段均为候选、待合同确认。
5. camera slot 的稳定排序键、缺失 camera 的占位身份、source/edit stream 对应关系。
6. Review compare 所需 source/edit 固定身份和映射是否能一次性验证；失败时可提供哪些安全摘要。
7. 各语义 capability 与资源 allowed actions 的映射；不得从角色名反推。
8. URL 中 step、raw ns、range、stream、focused fact、projection 的规范化与长度上限。
9. 目标浏览器、目标硬件、最大 cameras/axes/steps/queue/comments/entries；据此校准第 9 节预算与触发器。
10. 现有 EpisodeWorkbenchCore 的 media source、window source、资源清理和 signed-resource retry 能否在不泄漏资源引用的前提下迁入新内核。

---

## 效果图阶段验收门

- 六模式都能指向同一组件树，并可在图上标出 adapter slot；没有角色页面分叉。
- Raw 图完全不存在人工通过或进入 Lance 的路径。
- Lance 之后主坐标是 30 Hz step；所有编辑区间明确为 [start_step, end_step)。
- Cleaning 主动作是提交复核；Review 通过后没有自动发布暗示；Published 单独只读。
- source/edit/compare 切换时所有 camera、curve、timeline、inspector slot 坐标不移动。
- 1440 和 1280 都展示主动作、折叠控制和 sticky timeline；无页面级横向裁切、无隐藏主动作。
- 至少一张图展示局部 camera failure、一张展示空轨、一张展示无 Raw 权限、一张展示 read-only。
- 图表有文本摘要；异常不只靠颜色；键盘 focus 可见；segment 可聚焦。
- 所有未知诊断/对齐字段明确标记“候选｜待合同确认”；不把本规格伪装成正式后端合同。
- 性能先 profile，再按明确阈值进行 splitting、局部订阅、虚拟化和 downsampling。
