import { describeEpisodeRobotPreview } from "../../features/datasets/episodes/robot-preview-provenance";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { JSX } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, LockKeyhole, RefreshCw } from "lucide-react";
import { useLocation, useMatch, useNavigate, useParams } from "react-router-dom";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { isDomainError } from "../../shared/api/domain-error";
import { useShellStore } from "../../shared/scope/shell-store";
import { AnnotationPageState } from "../../features/annotation";
import { createAnnotationManualIssueCommand } from "../../features/cleaning/api";
import {
  createPlaybackClock,
  DataVisualizationWorkbench,
} from "../../features/viewer";
import { useLogicalRobotScene } from "../../features/datasets/episodes/use-logical-robot-scene";
import { AnnotationWorkbenchView } from "./AnnotationWorkbenchView";
import type {
  AnnotationWorkbenchPermissions,
  DataIssueReportInput,
} from "./AnnotationWorkbenchView";
import { RuntimeAutoAnnotationPanel } from "./RuntimeAutoAnnotationPanel";
import { AnnotationRevisionFilesPanel } from "./AnnotationRevisionFilesPanel";
import { AnnotationDeliveryPanel } from "./AnnotationDeliveryPanel";
import { TagEpisodePanel } from "./TagEpisodePanel";
import {
  createRuntimeAnnotationCommands,
  normalizeEpisodeOutcome,
  createClientMutationId,
  loadRuntimeAnnotationBundle,
  refreshRuntimeAnnotationBundle,
  resolveReviewRevision,
  resolveReviewSubmission,
  runtimeAnnotationScopeFromShell,
} from "../../features/annotation/runtime/runtime-annotation-adapter";
import type {
  AnnotationWorkbenchMode,
  RuntimeAnnotationBundle,
  RuntimeAnnotationScope,
  RuntimeAnnotationTag,
  RuntimeEpisodeOutcome,
  RuntimeReviewDecision,
} from "../../features/annotation/runtime/runtime-annotation-adapter";
import {
  clearTagDraftRecovery,
  loadAnnotationDraftRecovery,
  saveTagDraftRecovery,
} from "./tag-draft-recovery";
import type { TagDraftRecoveryIdentity, StoredRecovery } from "./tag-draft-recovery";
import { annotationRoutes } from "../../features/annotation/routing";
import { buildRuntimeJointAngleStream } from "../../features/annotation/runtime/joint-angle-stream";
import { useScopeQueryRevision } from "../../shared/api/use-scoped-query";
import styles from "./workbench.module.css";
import { defaultAnnotationNavigatorPreferences } from "./AnnotationNavigator";
import { annotationTaskListKey, synchronizeAnnotationBundle, taskQueryKey, taskQueryOptions } from "../../features/annotation/runtime/task-preload";

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
            readOnly: true,
            clock,
            cameraStreams: [],
            cameraEmptyMessage: props.kind === "first-loading" ? "" : "数据加载失败，请重试。",
            robotSceneUnavailableReason: props.kind === "first-loading" ? "" : "数据加载失败，请重试。",
            collectionItems: [],
            findings: [],
            timelineTracks: [],
            actions: [],
            ...(props.kind === "first-loading" ? {} : { banner: {
              label: "加载失败",
              title: "标注数据暂时不可用",
              description: props.detail ?? "请稍后重试或返回任务队列。",
              tone: "warning",
            } }),
          }}
          slots={{
            inspector: () => props.kind === "first-loading" ? null : (
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
            actionDock: () => props.kind === "first-loading" ? null : (
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

function outcomeForBundle(
  bundle: RuntimeAnnotationBundle, mode: AnnotationWorkbenchMode,
): RuntimeEpisodeOutcome {
  const revision = mode === "annotation"
    ? bundle.draft ?? bundle.history.revisions.at(-1)
    : resolveReviewRevision(bundle) ?? bundle.history.revisions.at(-1);
  return normalizeEpisodeOutcome(revision?.episode_outcome);
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
  const canReopen =
    task.status === "APPROVED" &&
    task.approved_revision === task.current_revision &&
    assignedToCurrent &&
    input.has("annotation.edit") &&
    input.has("annotation_draft.edit") &&
    input.has("annotation.save");
  const reopenUnavailableReason = !assignedToCurrent
    ? "请由该任务的标注人员修改标注。"
    : "当前账号没有修改已完成标注的权限。";
  const canSubmit = canEdit && !input.dirty && input.has("annotation.submit");
  const canCreate =
    input.mode === "annotation" &&
    !hasAnnotationDraft &&
    task.assignee_id === null &&
    task.status === "DRAFT" &&
    input.has("annotation_task.claim") &&
    input.has("annotation.edit") &&
    input.has("annotation_draft.edit");
  const reviewSubmission = resolveReviewSubmission(input.bundle);
  const canReview =
    task.status === "SUBMITTED" &&
    reviewSubmission !== null &&
    input.has("annotation.review");
  const canRevise =
    canSave &&
    input.bundle.history.revisions.some(
      (revision) => revision.revision < task.current_revision,
    );
  const createUnavailableReason = !input.has("annotation_task.claim")
    ? "当前账号没有领取待标注任务的权限。"
    : task.assignee_id && !assignedToCurrent
      ? "该标注任务已由其他标注人员领取。"
      : task.status !== "DRAFT"
        ? `任务状态为 ${task.status}，当前不能进入标注。`
        : "当前账号没有编辑标注的权限。";
  const annotationUnavailableReason = canReopen
    ? "点击“修改标注”后可继续编辑。"
    : canCreate
    ? "正在准备标注，完成后即可直接编辑。"
    : !hasAnnotationDraft
    ? createUnavailableReason
    : !editableStatus
      ? `任务状态为 ${task.status}，标注编辑已锁定。`
      : !canEdit
        ? "当前账号没有编辑标注草稿的权限。"
        : undefined;
  const reviewUnavailableReason = !input.has("annotation.review")
    ? "当前账号没有 Tag 审核权限。"
    : reviewSubmission === null
      ? task.status === "DRAFT" || task.status === "NEEDS_REVISION"
        ? "任务仍为草稿，标注尚未提交。"
        : "当前没有有效的待审核提交。"
      : undefined;
  const revisionUnavailableReason = !hasAnnotationDraft
    ? "标注草稿就绪后可查看数据修订。"
    : !canSave
      ? "当前账号或任务状态不允许写入数据修订。"
      : !canRevise
        ? "当前没有可用于数据修订的历史版本。"
        : undefined;
  let readOnlyReason: string | undefined;
  if (canReopen)
    readOnlyReason = "标注已完成，可点击“修改标注”创建新草稿；修改后需重新提交审核。";
  else if (input.mode === "tag-review" && task.status !== "SUBMITTED")
    readOnlyReason = `任务状态为 ${task.status}，没有待审核提交。`;
  else if (input.mode === "tag-review" && !input.has("annotation.review"))
    readOnlyReason = "当前授权仅允许查看，不允许创建审核决定。";
  else if (input.mode === "annotation" && !assignedToCurrent && !canCreate)
    readOnlyReason = createUnavailableReason;
  else if (input.mode === "annotation" && !editableStatus)
    readOnlyReason = `任务状态为 ${task.status}，当前修订只读。`;
  else if (input.mode === "annotation" && !canEdit && !canCreate)
    readOnlyReason = "编辑权限已撤销或当前授权不包含标注写能力。";
  return {
    hasAnnotationDraft,
    canCreate,
    canEdit,
    canSave,
    canSubmit,
    canReview,
    canRevise,
    canReopen,
    reopenUnavailableReason,
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
  const scopeRevision = useScopeQueryRevision();
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
    capabilities.has("episode.read") &&
    capabilities.has("dataset_version.read");
  const query = useQuery({
    queryKey:
      scope && taskId
        ? taskQueryKey(scope, taskId, mode, scopeRevision)
        : ["p08-runtime-annotation", "disabled"],
    queryFn: ({ signal }) =>
      loadRuntimeAnnotationBundle(scope!, taskId!, mode, signal,
        queryClient.getQueryData(annotationTaskListKey(scope!, scopeRevision))),
    enabled:
      !!scope &&
      !!taskId &&
      canRead &&
      !capabilities.loading &&
      !capabilities.failed,
    placeholderData: (previous, previousQuery) => {
      const oldKey = previousQuery?.queryKey;
      if (!scope || !oldKey || !canRead) return;
      const nextKey = taskQueryKey(scope, taskId!, mode, scopeRevision);
      // Annotation and review share one authorized workspace. Keep the current
      // picture while the next task loads, with writes disabled below.
      return [0, 1, 2, 3, 4].every(index => oldKey[index] === nextKey[index]) ? previous : undefined;
    },
    staleTime: 15_000,
    gcTime: 120_000,
    retry: false,
  });
  const [tags, setTags] = useState<readonly RuntimeAnnotationTag[]>([]);
  const [episodeOutcome, setEpisodeOutcome] = useState<RuntimeEpisodeOutcome>("success");
  const [dirty, setDirty] = useState(false);
  const [navigatorPreferences, setNavigatorPreferences] = useState(
    defaultAnnotationNavigatorPreferences,
  );
  const [recoveryDraft, setRecoveryDraft] = useState<StoredRecovery | null>(null);
  const initializedBaseline = useRef<string | null>(null);
  const bundle = query.data;
  const changingTask = query.isPlaceholderData || bundle?.task.task_id !== taskId;
  useEffect(() => {
    if (!bundle || !scope || changingTask || query.isFetching || !canRead || capabilities.failed) return;
    synchronizeAnnotationBundle(queryClient, scope, scopeRevision, mode, bundle);
  }, [bundle, scope, scopeRevision, mode, changingTask, query.isFetching, canRead, capabilities.failed, queryClient]);
  useEffect(() => {
    if (
      !bundle || !scope || changingTask || query.isFetching || !canRead ||
      capabilities.loading || capabilities.failed
    ) return;
    if (mode === "tag-review" && (bundle.task.status === "DRAFT" || bundle.task.status === "NEEDS_REVISION"))
      void navigate(annotationRoutes.task.build({ taskId: bundle.task.task_id }), { replace: true });
    else if (mode === "annotation" && bundle.task.status === "SUBMITTED") {
      // Carry the current submission across modes so a cached draft cannot
      // redirect the review page back to annotation.
      queryClient.setQueryData(
        taskQueryKey(scope, bundle.task.task_id, "tag-review", scopeRevision),
        { ...bundle, draft: null },
      );
      void navigate(annotationRoutes.tagReviewTask.build({ taskId: bundle.task.task_id }), { replace: true });
    }
  }, [mode, bundle, scope, scopeRevision, changingTask, query.isFetching, canRead, capabilities.loading, capabilities.failed, navigate, queryClient]);
  const prepareTask = useCallback(async (selectedTaskId: string) => {
    if (!scope || !canRead) return;
    const tasks = queryClient.getQueryData<RuntimeAnnotationBundle["tasks"]>(annotationTaskListKey(scope, scopeRevision));
    const selected = tasks?.find(task => task.task_id === selectedTaskId);
    const nextMode = selected?.status === "SUBMITTED" ? "tag-review"
      : selected?.status === "DRAFT" || selected?.status === "NEEDS_REVISION" ? "annotation" : mode;
    let loaded = await queryClient.fetchQuery(taskQueryOptions(scope, selectedTaskId, nextMode, scopeRevision, tasks));
    // The task may have changed since its navigator row was loaded.
    const actualMode = loaded.task.status === "SUBMITTED" ? "tag-review"
      : loaded.task.status === "DRAFT" || loaded.task.status === "NEEDS_REVISION" ? "annotation" : nextMode;
    if (actualMode === "annotation" && nextMode !== actualMode)
      loaded = await queryClient.fetchQuery(taskQueryOptions(scope, selectedTaskId, actualMode, scopeRevision, tasks));
    synchronizeAnnotationBundle(queryClient, scope, scopeRevision, actualMode, loaded);
  }, [scope, canRead, mode, scopeRevision, queryClient]);
  const jointAngleStream = useMemo(
    () =>
      bundle && scope ? buildRuntimeJointAngleStream({ bundle, scope }) : null,
    [bundle, scope],
  );
  const { robotScene, robotSceneUnavailableReason } = useLogicalRobotScene(
    canRead ? (bundle?.logical ?? null) : null,
    jointAngleStream,
    bundle?.manifest?.robot_id ?? null,
    describeEpisodeRobotPreview(bundle?.manifest?.robot_id ?? null),
  );
  const baseline = bundle
    ? `${scope?.organizationId}:${scope?.projectId}:${scope?.regionCode}:${scopeRevision}:${bundle.task.task_id}:${bundle.task.etag}:${mode === "annotation" ? (bundle.draft?.revision ?? -1) : (resolveReviewRevision(bundle)?.revision ?? -1)}`
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

  useLayoutEffect(() => {
    if (!bundle || !baseline || initializedBaseline.current === baseline)
      return;
    initializedBaseline.current = baseline;
    setTags(tagsForBundle(bundle, mode));
    setEpisodeOutcome(outcomeForBundle(bundle, mode));
    setDirty(mode === "annotation" && !!bundle.draft && !["success", "fail"].includes(bundle.draft.episode_outcome));
    setRecoveryDraft(
      recoveryIdentity ? loadAnnotationDraftRecovery(recoveryIdentity) : null,
    );
  }, [baseline, bundle, mode, recoveryIdentity]);

  useEffect(() => {
    if (!dirty || !recoveryIdentity) return;
    const timer = window.setTimeout(
      () => saveTagDraftRecovery(recoveryIdentity, tags, Date.now(), episodeOutcome),
      750,
    );
    return () => window.clearTimeout(timer);
  }, [dirty, recoveryIdentity, tags, episodeOutcome]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  if (capabilities.loading) return <WorkbenchState kind="first-loading" />;
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
  if (query.isPending) return <WorkbenchState kind="first-loading" />;
  if (!bundle) {
    return (
      <WorkbenchState
        kind="contract-mismatch"
        detail="正式 API 未返回当前标注任务事实。"
      />
    );
  }

  const commands = createRuntimeAnnotationCommands(scope);
  const availablePermissions = permissionSnapshot({
    mode,
    bundle,
    principalId: principal?.actorId,
    dirty,
    has: capabilities.has,
  });
  const permissions = changingTask ? {
    ...availablePermissions, canCreate: false, canEdit: false, canSave: false,
    canSubmit: false, canReview: false, canRevise: false, canReopen: false,
    readOnlyReason: "正在切换数据，加载完成后即可继续操作。",
  } : availablePermissions;
  const canReportDataIssue =
    !changingTask &&
    capabilities.has("manual_issue.create") &&
    capabilities.has(
      mode === "tag-review" ? "annotation.review" : "annotation.edit",
    );
  const updateBundle = (updated: RuntimeAnnotationBundle) => {
    synchronizeAnnotationBundle(queryClient, scope, scopeRevision, mode, updated);
  };
  const cancelTaskReads = async () => {
    // Keep neighboring tasks warm. Cache synchronization merges their navigator
    // snapshots by state_version, so they cannot undo this task's mutation.
    await Promise.all([
      queryClient.cancelQueries({ queryKey: taskQueryKey(scope, bundle.task.task_id, mode, scopeRevision).slice(0, 6) }),
      queryClient.cancelQueries({ queryKey: annotationTaskListKey(scope, scopeRevision) }),
    ]);
  };
  const refreshTask = async () => {
    await cancelTaskReads();
    const refreshed = await refreshRuntimeAnnotationBundle(scope, bundle, mode);
    if (dirty && recoveryIdentity && refreshed.draft) {
      saveTagDraftRecovery({ ...recoveryIdentity, taskEtag: refreshed.task.etag, revision: refreshed.draft.revision }, tags, Date.now(), episodeOutcome);
    }
    updateBundle(refreshed);
  };
  const save = async () => {
    if (!permissions.canSave) throw new Error("当前只读状态禁止保存标注修改。");
    if (!bundle.draft) throw new Error("当前没有可保存的服务端草稿。");
    await cancelTaskReads();
    await commands.saveDraft(bundle.task, bundle.draft, tags, episodeOutcome);
    const saved = await refreshRuntimeAnnotationBundle(scope, bundle);
    updateBundle(saved);
    if (recoveryIdentity) clearTagDraftRecovery(recoveryIdentity);
    setRecoveryDraft(null);
    setDirty(false);
  };
  const restoreRevision = async (targetRevision: number) => {
    if (!permissions.canRevise)
      throw new Error("当前只读状态禁止写入数据修订。");
    await cancelTaskReads();
    await commands.restoreRevision(bundle.task, targetRevision);
    updateBundle(await refreshRuntimeAnnotationBundle(scope, bundle));
    if (recoveryIdentity) clearTagDraftRecovery(recoveryIdentity);
    setRecoveryDraft(null);
    setDirty(false);
  };
  const submit = async () => {
    if (!permissions.canSubmit)
      throw new Error("当前状态不允许提交审核，请先保存所有修改。");
    if (!bundle.draft) throw new Error("当前没有可提交的服务端草稿。");
    await cancelTaskReads();
    await commands.submit(bundle.task, bundle.draft.revision);
    updateBundle(await refreshRuntimeAnnotationBundle(scope, bundle, "tag-review"));
    if (recoveryIdentity) clearTagDraftRecovery(recoveryIdentity);
    setRecoveryDraft(null);
    void navigate(
      annotationRoutes.tagReviewTask.build({ taskId: bundle.task.task_id }),
    );
  };
  const review = async (decision: RuntimeReviewDecision, comment: string) => {
    if (!permissions.canReview)
      throw new Error("当前账号或任务状态不允许创建 Tag 审核决定。");
    const submission = resolveReviewSubmission(bundle);
    if (!submission) throw new Error("正式 API 未返回可审核的固定提交版本。");
    await cancelTaskReads();
    let reviewedTask;
    try {
      reviewedTask = await commands.review(bundle.task, submission, decision, comment);
    } catch (error) {
      if (isDomainError(error) && (error.problemCode === "ANNOTATION_INVALID_STATE" ||
          error.code === "VERSION_CONFLICT" || error.code === "PRECONDITION_FAILED")) {
        // Never replay an old review. Read the current result and preserve the
        // original error/request ID if refreshing is temporarily unavailable.
        try {
          updateBundle(await refreshRuntimeAnnotationBundle(scope, bundle, "tag-review"));
        } catch { /* The workbench offers an explicit refresh retry. */ }
      }
      throw error;
    }
    // Disable the old submission immediately, even if fetching history fails.
    const reviewed = { ...bundle, task: reviewedTask, draft: null,
      history: { ...bundle.history, task: reviewedTask } };
    updateBundle(reviewed);
    updateBundle(await refreshRuntimeAnnotationBundle(scope, reviewed, "tag-review"));
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
        permissions.createUnavailableReason ?? "当前不能进入标注。",
      );
    await cancelTaskReads();
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
      if (refreshed.data) updateBundle(refreshed.data);
      if (
        refreshed.data?.draft &&
        refreshed.data.task.assignee_id === principal?.actorId
      )
        return;
      throw error;
    }
    const refreshed = await refreshRuntimeAnnotationBundle(scope, bundle);
    if (
      !refreshed.draft ||
      refreshed.task.assignee_id !== principal?.actorId
    )
      throw new Error("标注草稿尚未就绪，请重试进入标注。");
    updateBundle(refreshed);
  };
  const reopenAnnotation = async () => {
    if (!permissions.canReopen)
      throw new Error(permissions.reopenUnavailableReason);
    await cancelTaskReads();
    await commands.reopen(bundle.task);
    if (recoveryIdentity) clearTagDraftRecovery(recoveryIdentity);
    setRecoveryDraft(null);
    setDirty(false);
    await queryClient.invalidateQueries({
      queryKey: annotationTaskListKey(scope, scopeRevision),
      refetchType: "all",
    });
    await queryClient.invalidateQueries({
      queryKey: taskQueryKey(scope, bundle.task.task_id, mode, scopeRevision).slice(0, 6),
      refetchType: "none",
    });
    const refreshed = await query.refetch();
    if (refreshed.error) throw refreshed.error;
    if (refreshed.data) updateBundle(refreshed.data);
    if (refreshed.data?.task.status !== "DRAFT")
      throw new Error("标注任务状态已变化，请刷新后重试。");
  };
  const selectTask = (selectedTaskId: string) => {
    const selectedTask = selectedTaskId === bundle.task.task_id ? bundle.task : bundle.tasks.find((task) => task.task_id === selectedTaskId);
    const needsAnnotation = selectedTask?.status === "DRAFT" || selectedTask?.status === "NEEDS_REVISION";
    const needsReview = selectedTask?.status === "SUBMITTED";
    if (selectedTaskId === bundle.task.task_id && !(needsReview && mode !== "tag-review")) return;
    const target =
      needsReview || (mode === "tag-review" && !needsAnnotation)
        ? annotationRoutes.tagReviewTask.build({ taskId: selectedTaskId })
        : annotationRoutes.task.build(
            { taskId: selectedTaskId },
            {
              returnTo:
                new URLSearchParams(location.search).get("returnTo") ??
                annotationRoutes.annotate.pattern,
            },
          );
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
      key={`${scope.organizationId}:${scope.projectId}:${scope.regionCode}:${scopeRevision}`}
      bundle={bundle}
      principalId={principal?.actorId}
      navigatorPreferences={navigatorPreferences}
      onNavigatorChange={setNavigatorPreferences}
      revisionFilesPanel={
        <>
          <AnnotationRevisionFilesPanel
            scope={scope}
            history={bundle.history}
            dirty={dirty}
            canRetry={!changingTask && capabilities.has("annotation.save")}
            canDownload={capabilities.has("export.download")}
          />
          {bundle.task.data_ref && (
            <AnnotationDeliveryPanel
              key={`${scope.organizationId}/${scope.projectId}/${scope.regionCode}/${bundle.task.task_id}`}
              scope={scope}
              history={bundle.history}
              dirty={dirty}
              canCreate={!changingTask && capabilities.has("export.create")}
              canDownload={capabilities.has("export.download")}
            />
          )}
          {bundle.task.data_ref && (
            <TagEpisodePanel
              key={`tag/${scope.organizationId}/${scope.projectId}/${scope.regionCode}/${bundle.task.task_id}`}
              scope={scope}
              history={bundle.history}
              dirty={dirty}
              canCreate={!changingTask && capabilities.has("upload.manage")}
              canRead={[
                "dataset_version.read",
                "annotation_task.read",
                "export.download",
              ].every((c) => capabilities.has(c))}
            />
          )}
        </>
      }
      autoAnnotationPanel={
        mode === "annotation" ? (
          <RuntimeAutoAnnotationPanel
            key={bundle.task.task_id}
            canUse={permissions.canSave && capabilities.has("annotation.edit")}
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
      switchingTask={changingTask}
      {...(jointAngleStream ? { jointAngleStream } : {})}
      mode={mode}
      canReportDataIssue={canReportDataIssue}
      permissions={permissions}
      reportDataIssueUnavailableReason="当前账号没有登记问题数据的权限。"
      recoveryAvailable={recoveryDraft !== null}
      {...(robotScene ? { robotScene } : {})}
      {...(robotSceneUnavailableReason ? { robotSceneUnavailableReason } : {})}
      scope={scope}
      tags={tags}
      episodeOutcome={episodeOutcome}
      onEpisodeOutcomeChange={(next) => {
        if (!permissions.canEdit) return;
        setEpisodeOutcome(next);
        setDirty(true);
      }}
      onDiscardRecovery={() => {
        if (recoveryIdentity) clearTagDraftRecovery(recoveryIdentity);
        setRecoveryDraft(null);
      }}
      onCreateAnnotation={createAnnotation}
      onReopenAnnotation={reopenAnnotation}
      onDiscardChanges={() => {
        setTags(tagsForBundle(bundle, mode));
        setEpisodeOutcome(outcomeForBundle(bundle, mode));
        setDirty(false);
        if (recoveryIdentity) clearTagDraftRecovery(recoveryIdentity);
        setRecoveryDraft(null);
      }}
      onRecover={() => {
        if (!recoveryDraft) return;
        setTags(recoveryDraft.tags);
        setEpisodeOutcome(normalizeEpisodeOutcome(recoveryDraft.episodeOutcome ?? outcomeForBundle(bundle, mode)));
        setDirty(true);
        setRecoveryDraft(null);
      }}
      onReportDataIssue={reportDataIssue}
      onReview={review}
      onRefresh={refreshTask}
      onOpenRevisions={openRevisions}
      onRestoreRevision={restoreRevision}
      onSave={save}
      onPrepareTask={prepareTask}
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
  const review = useMatch(annotationRoutes.tagReviewTask.pattern);
  return <RuntimeAnnotationTaskPage mode={review ? "tag-review" : "annotation"} />;
}

// Identical component type preserves the workspace and navigator across routes.
export { AnnotationTaskPage as TagReviewTaskPage };
