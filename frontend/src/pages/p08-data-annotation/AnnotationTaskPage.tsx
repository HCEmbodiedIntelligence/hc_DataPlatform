import { useEffect, useMemo, useRef, useState } from "react";
import type { JSX } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, LockKeyhole, RefreshCw } from "lucide-react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { isDomainError } from "../../shared/api/domain-error";
import { useShellStore } from "../../shared/scope/shell-store";
import { AnnotationPageState } from "../../features/annotation";
import { createAnnotationManualIssueCommand } from "../../features/cleaning/api";
import {
  authorizeRobotModelViewerAssets,
  useRobotModelAssets,
  useRobotModelJointMappings,
  useRobotModelVersion,
} from "../../features/robot-models/api";
import { useRobotBootstrap } from "../../features/robots/api";
import {
  createLazyThreeRobotSceneLoader,
  createPlaybackClock,
  DataVisualizationWorkbench,
} from "../../features/viewer";
import { AnnotationWorkbenchView } from "./AnnotationWorkbenchView";
import type {
  AnnotationWorkbenchPermissions,
  DataIssueReportInput,
} from "./AnnotationWorkbenchView";
import { RuntimeAutoAnnotationPanel } from "./RuntimeAutoAnnotationPanel";
import {
  createRuntimeAnnotationCommands,
  createClientMutationId,
  loadRuntimeAnnotationBundle,
  resolveReviewRevision,
  resolveReviewSubmission,
  runtimeAnnotationScopeFromShell,
} from "./runtime-annotation-adapter";
import type {
  AnnotationWorkbenchMode,
  RuntimeAnnotationBundle,
  RuntimeAnnotationScope,
  RuntimeAnnotationTag,
  RuntimeReviewDecision,
} from "./runtime-annotation-adapter";
import {
  clearTagDraftRecovery,
  loadTagDraftRecovery,
  saveTagDraftRecovery,
} from "./tag-draft-recovery";
import type { TagDraftRecoveryIdentity } from "./tag-draft-recovery";
import { annotationRoutes } from "./routes";
import {
  buildRuntimeJointAngleStream,
  buildRuntimeJointFrameSource,
} from "./joint-angle-stream";
import styles from "./workbench.module.css";

function taskQueryKey(
  scope: RuntimeAnnotationScope,
  taskId: string,
  mode: AnnotationWorkbenchMode,
) {
  return [
    "p08-runtime-annotation",
    scope.projectId,
    scope.regionCode,
    taskId,
    mode,
  ] as const;
}

function workbenchErrorKind(
  error: unknown,
): Parameters<typeof AnnotationPageState>[0]["kind"] {
  if (!isDomainError(error)) return "fatal-error";
  if (error.code === "FORBIDDEN" || error.code === "UNAUTHENTICATED")
    return "forbidden";
  if (error.code === "NOT_FOUND" || error.code === "GONE")
    return "not-found-gone";
  if (error.code === "VERSION_CONFLICT" || error.code === "PRECONDITION_FAILED")
    return "conflict";
  if (error.code === "RATE_LIMITED") return "rate-limited";
  if (error.code === "NETWORK_ERROR") return "offline-reconnecting";
  if (error.code === "CONTRACT_MISMATCH") return "contract-mismatch";
  return "fatal-error";
}

function WorkbenchState(props: {
  readonly kind: Parameters<typeof AnnotationPageState>[0]["kind"];
  readonly detail?: string;
  readonly problemCode?: string;
  readonly requestId?: string;
  readonly retryable?: boolean;
  readonly onRetry?: () => void;
}): JSX.Element {
  const clock = useMemo(
    () => createPlaybackClock({ startNs: "0", endNs: "1000000000" }),
    [],
  );
  useEffect(() => () => clock.dispose(), [clock]);
  return (
    <div className={styles.page}>
      <div className={styles.workbenchFrame}>
        <DataVisualizationWorkbench
          adapter={{
            mode: "annotation",
            id: `p08-state-${props.kind}`,
            title: "数据标注 / Tag 审核",
            description: "固定 Lance 版本 · 多级 Tag · 共享时间轴",
            readOnly: true,
            clock,
            cameraStreams: [],
            collectionItems: [],
            findings: [],
            timelineTracks: [],
            actions: [],
            banner: {
              label:
                props.kind === "first-loading" ? "正在加载" : "真实 API 状态",
              title:
                props.kind === "first-loading"
                  ? "正在读取标注事实"
                  : "页面未切换到 Browser Mock",
              description:
                props.detail ?? "保持工作台骨架并等待正式 runtime API。",
              tone: props.kind === "first-loading" ? "info" : "warning",
            },
          }}
          slots={{
            inspector: () => (
              <section className={styles.inspectorPanel}>
                <header className={styles.inspectorHeader}>
                  <span>
                    <AlertTriangle aria-hidden="true" size={15} />
                  </span>
                  <div>
                    <h2>Tag 工具状态</h2>
                    <small>正式 runtime API</small>
                  </div>
                  <em>只读</em>
                </header>
                <AnnotationPageState
                  kind={props.kind}
                  detail={props.detail}
                  problemCode={props.problemCode}
                  requestId={props.requestId}
                  retryable={props.retryable}
                  onRetry={props.onRetry}
                />
              </section>
            ),
            actionDock: () => (
              <section className={styles.actionDock}>
                {props.onRetry ? (
                  <button type="button" onClick={props.onRetry}>
                    <RefreshCw aria-hidden="true" size={15} />
                    重试真实请求
                  </button>
                ) : null}
                <p>
                  <LockKeyhole aria-hidden="true" size={13} />
                  错误、无权限或加载中不会启用写操作。
                </p>
              </section>
            ),
          }}
        />
      </div>
    </div>
  );
}

function tagsForBundle(
  bundle: RuntimeAnnotationBundle,
  mode: AnnotationWorkbenchMode,
): readonly RuntimeAnnotationTag[] {
  if (mode === "annotation")
    return bundle.draft?.tags ?? bundle.history.revisions.at(-1)?.tags ?? [];
  return (
    resolveReviewRevision(bundle)?.tags ??
    bundle.history.revisions.at(-1)?.tags ??
    []
  );
}

function permissionSnapshot(input: {
  readonly mode: AnnotationWorkbenchMode;
  readonly bundle: RuntimeAnnotationBundle;
  readonly principalId: string | undefined;
  readonly dirty: boolean;
  readonly has: (capability: string) => boolean;
}): AnnotationWorkbenchPermissions {
  const { task } = input.bundle;
  const assignedToCurrent =
    !!input.principalId && task.assignee_id === input.principalId;
  const hasAnnotationDraft = assignedToCurrent && input.bundle.draft !== null;
  const editableStatus =
    task.status === "DRAFT" || task.status === "NEEDS_REVISION";
  const canEdit =
    hasAnnotationDraft &&
    editableStatus &&
    input.has("annotation.edit") &&
    input.has("annotation_draft.edit");
  const canSave = canEdit && input.has("annotation.save");
  const canSubmit = canEdit && !input.dirty && input.has("annotation.submit");
  const canCreate =
    input.mode === "annotation" &&
    !hasAnnotationDraft &&
    task.assignee_id === null &&
    task.status === "DRAFT" &&
    input.has("annotation_task.claim") &&
    input.has("annotation.edit") &&
    input.has("annotation_draft.edit");
  const selfReview =
    !!input.principalId && task.submitted_by === input.principalId;
  const reviewSubmission = resolveReviewSubmission(input.bundle);
  const canReview =
    task.status === "SUBMITTED" &&
    reviewSubmission !== null &&
    !selfReview &&
    input.has("annotation.review");
  const canRevise =
    canSave &&
    input.bundle.history.revisions.some(
      (revision) => revision.revision < task.current_revision,
    );
  const createUnavailableReason = !input.has("annotation_task.claim")
    ? "当前账号没有创建标注草稿的权限。"
    : task.assignee_id && !assignedToCurrent
      ? "该标注任务已由其他标注人员领取。"
      : task.status !== "DRAFT"
        ? `任务状态为 ${task.status}，不能创建新的标注草稿。`
        : "当前任务尚不能创建标注草稿。";
  const annotationUnavailableReason = !hasAnnotationDraft
    ? "请先创建标注草稿。"
    : !editableStatus
      ? `任务状态为 ${task.status}，标注编辑已锁定。`
      : !canEdit
        ? "当前账号没有编辑标注草稿的权限。"
        : undefined;
  const reviewUnavailableReason = !input.has("annotation.review")
    ? "当前账号没有 Tag 审核权限。"
    : selfReview
      ? "提交人与审核人必须分离；当前账号不能自审。"
      : reviewSubmission === null
        ? task.status === "DRAFT" || task.status === "NEEDS_REVISION"
          ? "任务仍为草稿，标注尚未提交。"
          : "当前没有有效的待审核提交。"
        : undefined;
  const revisionUnavailableReason = !hasAnnotationDraft
    ? "请先创建标注草稿。"
    : !canSave
      ? "当前账号或任务状态不允许写入数据修订。"
      : !canRevise
        ? "当前没有可用于数据修订的历史版本。"
        : undefined;
  let readOnlyReason: string | undefined;
  if (input.mode === "tag-review" && selfReview)
    readOnlyReason = "提交人与审核人必须分离；当前账号不能自审。";
  else if (input.mode === "tag-review" && task.status !== "SUBMITTED")
    readOnlyReason = `任务状态为 ${task.status}，没有待审核提交。`;
  else if (input.mode === "tag-review" && !input.has("annotation.review"))
    readOnlyReason = "当前授权仅允许查看，不允许创建审核决定。";
  else if (input.mode === "annotation" && !assignedToCurrent)
    readOnlyReason = "任务未分配给当前账号，草稿以只读方式呈现。";
  else if (input.mode === "annotation" && !editableStatus)
    readOnlyReason = `任务状态为 ${task.status}，当前修订只读。`;
  else if (input.mode === "annotation" && !canEdit)
    readOnlyReason = "编辑权限已撤销或当前授权不包含标注写能力。";
  return {
    hasAnnotationDraft,
    canCreate,
    canEdit,
    canSave,
    canSubmit,
    canReview,
    canRevise,
    createUnavailableReason,
    ...(annotationUnavailableReason ? { annotationUnavailableReason } : {}),
    ...(reviewUnavailableReason ? { reviewUnavailableReason } : {}),
    ...(revisionUnavailableReason ? { revisionUnavailableReason } : {}),
    ...(readOnlyReason ? { readOnlyReason } : {}),
  };
}

function RuntimeAnnotationTaskPage({
  mode,
}: {
  readonly mode: AnnotationWorkbenchMode;
}): JSX.Element {
  const { taskId } = useParams<{ taskId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const capabilities = useCapabilities();
  const shellScope = useShellStore((state) => state.scope);
  const principal = useShellStore((state) => state.principal);
  const scope = useMemo<RuntimeAnnotationScope | null>(() => {
    return runtimeAnnotationScopeFromShell(shellScope);
  }, [
    shellScope?.organizationId,
    shellScope?.projectId,
    shellScope?.regionCode,
  ]);
  const canRead =
    capabilities.has("annotation_task.read") &&
    capabilities.has("episode.read");
  const query = useQuery({
    queryKey:
      scope && taskId
        ? taskQueryKey(scope, taskId, mode)
        : ["p08-runtime-annotation", "disabled"],
    queryFn: ({ signal }) =>
      loadRuntimeAnnotationBundle(scope!, taskId!, mode, signal),
    enabled:
      !!scope &&
      !!taskId &&
      canRead &&
      !capabilities.loading &&
      !capabilities.failed,
    staleTime: 0,
    retry: false,
  });
  const [tags, setTags] = useState<readonly RuntimeAnnotationTag[]>([]);
  const [dirty, setDirty] = useState(false);
  const [recoveryTags, setRecoveryTags] = useState<
    readonly RuntimeAnnotationTag[] | null
  >(null);
  const initializedBaseline = useRef<string | null>(null);
  const bundle = query.data;
  const manifestRobotId = bundle?.manifest?.robot_id ?? null;
  const robotBootstrap = useRobotBootstrap(manifestRobotId);
  const boundVersionId =
    robotBootstrap.data?.effectiveModelBinding?.robotModelVersionId ?? null;
  const robotModelVersion = useRobotModelVersion(boundVersionId);
  const robotModelAssets = useRobotModelAssets(boundVersionId);
  const robotJointMappings = useRobotModelJointMappings(boundVersionId);
  const jointAngleStream = useMemo(
    () =>
      bundle && scope ? buildRuntimeJointAngleStream({ bundle, scope }) : null,
    [bundle, scope],
  );
  const jointFrameSource = useMemo(() => {
    const base = buildRuntimeJointFrameSource(jointAngleStream);
    if (!base) return;
    const directions = new Map(
      (robotJointMappings.data ?? []).map((mapping) => [
        mapping.source_joint_name,
        mapping.direction,
      ]),
    );
    return {
      async sampleAt(ns: string, signal: AbortSignal) {
        const frame = await base.sampleAt(ns, signal);
        return Object.fromEntries(
          Object.entries(frame).map(([joint, value]) => [
            joint,
            directions.get(joint) === "INVERTED" ? -value : value,
          ]),
        );
      },
    };
  }, [jointAngleStream, robotJointMappings.data]);
  const urdfAsset = robotModelAssets.data?.find(
    (asset) => asset.role === "URDF",
  );
  const robotScene = useMemo(() => {
    if (
      !scope ||
      !robotModelVersion.data ||
      robotModelVersion.data.lifecycle !== "PUBLISHED" ||
      !urdfAsset ||
      !jointFrameSource
    )
      return;
    const modelId = robotModelVersion.data.robotModelId;
    const modelVersion = robotModelVersion.data.id;
    const jointMapping = Object.fromEntries(
      (robotJointMappings.data ?? []).map((mapping) => [
        mapping.source_joint_name,
        mapping.target_joint_name,
      ]),
    );
    const requiredJoints = (robotJointMappings.data ?? []).map(
      (mapping) => mapping.source_joint_name,
    );
    const isCompatibilityPreview =
      manifestRobotId === "droid-franka" &&
      robotModelVersion.data.versionLabel === "DROID-open-assets-v1";
    return {
      title: isCompatibilityPreview
        ? "兼容性机器人模型（非源数据 URDF）"
        : "机器人 URDF",
      modelRef: { modelId, modelVersion },
      jointMapping,
      jointFrameSource,
      runtimeLoader: createLazyThreeRobotSceneLoader(async (_props, signal) => {
        const viewerAssets = await authorizeRobotModelViewerAssets(
          scope.organizationId,
          modelVersion,
          robotModelAssets.data ?? [],
          signal,
        );
        return {
          manifest: { modelId, modelVersion, requiredJoints },
          ...viewerAssets,
        };
      }),
    };
  }, [
    jointFrameSource,
    robotJointMappings.data,
    robotModelAssets.data,
    robotModelVersion.data,
    manifestRobotId,
    scope,
    urdfAsset,
  ]);
  const robotSceneUnavailableReason = useMemo(() => {
    if (!jointAngleStream)
      return "当前数据未发现关节角 Topic；写入关节角数据后才能同步机器人姿态。";
    if (!manifestRobotId)
      return "采集数据清单未返回 robot_id，无法解析本次数据对应的机器人。请重新生成采集清单。";
    if (robotBootstrap.isPending)
      return `正在解析机器人 ${manifestRobotId} 的模型绑定…`;
    if (robotBootstrap.isError)
      return `无法读取机器人 ${manifestRobotId} 的配置或当前账号无权访问。`;
    if (!boundVersionId)
      return `机器人 ${manifestRobotId} 尚未绑定已发布 URDF 模型。请在“机器人管理 → 模型配置”中完成绑定。`;
    if (robotModelVersion.isPending || robotModelAssets.isPending)
      return "正在加载已发布模型版本与 URDF 资产…";
    if (robotModelVersion.isError || robotModelAssets.isError)
      return "模型绑定已存在，但固定版本或 URDF 资产加载失败。";
    if (robotModelVersion.data?.lifecycle !== "PUBLISHED")
      return "当前机器人绑定的模型版本尚未发布，已阻止加载非固定 3D 事实。";
    if (!urdfAsset)
      return "当前已发布模型没有可用的 URDF 资产。请在机器人管理中创建更新草稿并导入 URDF。";
    if (robotJointMappings.isPending) return "正在加载关节映射…";
    if (robotJointMappings.isError) return "URDF 已找到，但关节映射加载失败。";
    return undefined;
  }, [
    boundVersionId,
    jointAngleStream,
    manifestRobotId,
    robotBootstrap.isError,
    robotBootstrap.isPending,
    robotJointMappings.isError,
    robotJointMappings.isPending,
    robotModelAssets.isError,
    robotModelAssets.isPending,
    robotModelVersion.data?.lifecycle,
    robotModelVersion.isError,
    robotModelVersion.isPending,
    urdfAsset,
  ]);
  const baseline = bundle
    ? `${bundle.task.etag}:${mode === "annotation" ? (bundle.draft?.revision ?? -1) : (resolveReviewRevision(bundle)?.revision ?? -1)}`
    : null;
  const recoveryIdentity = useMemo<TagDraftRecoveryIdentity | null>(() => {
    if (
      !bundle ||
      !scope ||
      !principal ||
      mode !== "annotation" ||
      !bundle.draft
    )
      return null;
    return {
      principalId: principal.actorId,
      projectId: scope.projectId,
      taskId: bundle.task.task_id,
      taskEtag: bundle.task.etag,
      revision: bundle.draft.revision,
    };
  }, [bundle, mode, principal, scope]);

  useEffect(() => {
    if (!bundle || !baseline || initializedBaseline.current === baseline)
      return;
    initializedBaseline.current = baseline;
    setTags(tagsForBundle(bundle, mode));
    setDirty(false);
    setRecoveryTags(
      recoveryIdentity ? loadTagDraftRecovery(recoveryIdentity) : null,
    );
  }, [baseline, bundle, mode, recoveryIdentity]);

  useEffect(() => {
    if (!dirty || !recoveryIdentity) return;
    const timer = window.setTimeout(
      () => saveTagDraftRecovery(recoveryIdentity, tags),
      750,
    );
    return () => window.clearTimeout(timer);
  }, [dirty, recoveryIdentity, tags]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  if (capabilities.loading || query.isLoading)
    return <WorkbenchState kind="first-loading" />;
  if (capabilities.failed || !canRead) {
    return (
      <WorkbenchState
        kind="forbidden"
        detail="标注读取权限已撤销；页面不会保留可操作的旧数据。"
      />
    );
  }
  if (!scope || !taskId)
    return (
      <WorkbenchState
        kind="feature-unavailable"
        detail="需要有效的项目、Region 和任务 ID。"
      />
    );
  if (query.error) {
    return (
      <WorkbenchState
        detail={query.error.message}
        kind={workbenchErrorKind(query.error)}
        requestId={
          isDomainError(query.error)
            ? (query.error.requestId ?? undefined)
            : undefined
        }
        problemCode={
          isDomainError(query.error)
            ? (query.error.problemCode ?? undefined)
            : undefined
        }
        retryable={
          isDomainError(query.error) ? query.error.retryable : undefined
        }
        onRetry={() => void query.refetch()}
      />
    );
  }
  if (!bundle) {
    return (
      <WorkbenchState
        kind="contract-mismatch"
        detail="正式 API 未返回当前标注任务事实。"
      />
    );
  }

  const commands = createRuntimeAnnotationCommands(scope);
  const permissions = permissionSnapshot({
    mode,
    bundle,
    principalId: principal?.actorId,
    dirty,
    has: capabilities.has,
  });
  const canReportDataIssue =
    capabilities.has("manual_issue.create") &&
    capabilities.has(
      mode === "tag-review" ? "annotation.review" : "annotation.edit",
    );
  const save = async () => {
    if (!permissions.canSave) throw new Error("当前只读状态禁止保存标注修改。");
    if (!bundle.draft) throw new Error("当前没有可保存的服务端草稿。");
    await commands.saveDraft(bundle.task, bundle.draft, tags);
    if (recoveryIdentity) clearTagDraftRecovery(recoveryIdentity);
    setRecoveryTags(null);
    setDirty(false);
    await query.refetch();
  };
  const restoreRevision = async (targetRevision: number) => {
    if (!permissions.canRevise)
      throw new Error("当前只读状态禁止写入数据修订。");
    await commands.restoreRevision(bundle.task, targetRevision);
    if (recoveryIdentity) clearTagDraftRecovery(recoveryIdentity);
    setRecoveryTags(null);
    setDirty(false);
    await query.refetch();
  };
  const submit = async () => {
    if (!permissions.canSubmit)
      throw new Error("当前状态不允许提交审核，请先保存所有修改。");
    if (!bundle.draft) throw new Error("当前没有可提交的服务端草稿。");
    await commands.submit(bundle.task, bundle.draft.revision);
    if (recoveryIdentity) clearTagDraftRecovery(recoveryIdentity);
    setRecoveryTags(null);
    await query.refetch();
    void navigate(
      annotationRoutes.tagReviewTask.build({ taskId: bundle.task.task_id }),
    );
  };
  const review = async (decision: RuntimeReviewDecision, comment: string) => {
    if (!permissions.canReview)
      throw new Error("当前账号或任务状态不允许创建 Tag 审核决定。");
    const submission = resolveReviewSubmission(bundle);
    if (!submission) throw new Error("正式 API 未返回可审核的固定提交版本。");
    await commands.review(bundle.task, submission, decision, comment);
    await query.refetch();
  };
  const reportDataIssue = async (input: DataIssueReportInput) => {
    if (!canReportDataIssue)
      throw new Error("当前账号没有登记问题数据的权限。");
    await createAnnotationManualIssueCommand({
      organizationId: scope.organizationId,
      projectId: scope.projectId,
      regionCode: scope.regionCode,
      annotationTaskId: bundle.task.task_id,
      streamRef: input.streamRef,
      relativeStartNs: input.relativeStartNs,
      relativeEndNs: input.relativeEndNs,
      discoverySource: mode === "tag-review" ? "REVIEWER" : "ANNOTATOR",
      issueType: input.issueType,
      severity: input.severity,
      note: input.note,
      idempotencyKey: createClientMutationId("report-data-issue"),
    });
    await queryClient.invalidateQueries({
      predicate: (candidate) => candidate.queryKey[0] === "manual-issues",
    });
  };
  const createAnnotation = async () => {
    if (!permissions.canCreate)
      throw new Error(
        permissions.createUnavailableReason ?? "当前不能创建标注草稿。",
      );
    try {
      await commands.claim(bundle.task);
    } catch (error) {
      const concurrent =
        isDomainError(error) &&
        (error.httpStatus === 409 ||
          error.code === "VERSION_CONFLICT" ||
          error.code === "PRECONDITION_FAILED");
      if (!concurrent) throw error;
      const refreshed = await query.refetch();
      if (refreshed.error) throw refreshed.error;
      if (
        refreshed.data?.draft &&
        refreshed.data.task.assignee_id === principal?.actorId
      )
        return;
      throw error;
    }
    const refreshed = await query.refetch();
    if (refreshed.error) throw refreshed.error;
    if (
      !refreshed.data?.draft ||
      refreshed.data.task.assignee_id !== principal?.actorId
    )
      throw new Error("任务已领取，但标注草稿尚未就绪，请重试刷新。");
  };
  const selectTask = (selectedTaskId: string) => {
    const target =
      mode === "tag-review"
        ? annotationRoutes.tagReviewTask.build({ taskId: selectedTaskId })
        : annotationRoutes.task.build(
            { taskId: selectedTaskId },
            { returnTo: `${location.pathname}${location.search}` },
          );
    if (
      dirty &&
      !window.confirm(
        "有尚未保存的 Tag 修改，确认切换任务并保留本会话恢复稿吗？",
      )
    )
      return;
    void navigate(target);
  };
  const switchMode = (nextMode: AnnotationWorkbenchMode) => {
    if (nextMode === mode) return;
    void navigate(
      nextMode === "tag-review"
        ? annotationRoutes.tagReviewTask.build({ taskId: bundle.task.task_id })
        : annotationRoutes.task.build({ taskId: bundle.task.task_id }),
    );
  };
  const openRevisions = () => {
    if (
      dirty &&
      !window.confirm(
        "有尚未保存的 Tag 修改，确认查看数据修订并保留本会话恢复稿吗？",
      )
    )
      return;
    void navigate(annotationRoutes.revisions.pattern);
  };

  return (
    <AnnotationWorkbenchView
      key={bundle.task.task_id}
      bundle={bundle}
      autoAnnotationPanel={
        mode === "annotation" ? (
          <RuntimeAutoAnnotationPanel
            key={bundle.task.task_id}
            canUse={permissions.canSave && capabilities.has("annotation.write")}
            dirty={dirty}
            scope={scope}
            task={bundle.task}
            onApplied={async () => {
              await query.refetch();
            }}
          />
        ) : undefined
      }
      dirty={dirty}
      {...(jointAngleStream ? { jointAngleStream } : {})}
      mode={mode}
      canReportDataIssue={canReportDataIssue}
      permissions={permissions}
      reportDataIssueUnavailableReason="当前账号没有登记问题数据的权限。"
      recoveryAvailable={recoveryTags !== null}
      {...(robotScene ? { robotScene } : {})}
      {...(robotSceneUnavailableReason ? { robotSceneUnavailableReason } : {})}
      scope={scope}
      tags={tags}
      onDiscardRecovery={() => {
        if (recoveryIdentity) clearTagDraftRecovery(recoveryIdentity);
        setRecoveryTags(null);
      }}
      onCreateAnnotation={createAnnotation}
      onDiscardChanges={() => {
        setTags(tagsForBundle(bundle, mode));
        setDirty(false);
        if (recoveryIdentity) clearTagDraftRecovery(recoveryIdentity);
        setRecoveryTags(null);
      }}
      onRecover={() => {
        if (!recoveryTags) return;
        setTags(recoveryTags);
        setDirty(true);
        setRecoveryTags(null);
      }}
      onReportDataIssue={reportDataIssue}
      onReview={review}
      onOpenRevisions={openRevisions}
      onRestoreRevision={restoreRevision}
      onSave={save}
      onSelectTask={selectTask}
      onSubmit={submit}
      onSwitchMode={switchMode}
      onTagsChange={(next) => {
        if (!permissions.canEdit) return;
        setTags(next);
        setDirty(true);
      }}
    />
  );
}

export function AnnotationTaskPage(): JSX.Element {
  return <RuntimeAnnotationTaskPage mode="annotation" />;
}

export function TagReviewTaskPage(): JSX.Element {
  return <RuntimeAnnotationTaskPage mode="tag-review" />;
}
