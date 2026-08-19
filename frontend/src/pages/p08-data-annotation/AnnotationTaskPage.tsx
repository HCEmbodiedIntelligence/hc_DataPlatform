import { useEffect, useMemo, useRef, useState } from "react";
import type { JSX } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, LockKeyhole, RefreshCw } from "lucide-react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { isDomainError } from "../../shared/api/domain-error";
import { useShellStore } from "../../shared/scope/shell-store";
import { AnnotationPageState } from "../../features/annotation";
import {
  createPlaybackClock,
  DataVisualizationWorkbench,
} from "../../features/viewer";
import { AnnotationWorkbenchView } from "./AnnotationWorkbenchView";
import type { AnnotationWorkbenchPermissions } from "./AnnotationWorkbenchView";
import {
  createRuntimeAnnotationCommands,
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
    <main className={styles.page}>
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
    </main>
  );
}

function tagsForBundle(
  bundle: RuntimeAnnotationBundle,
  mode: AnnotationWorkbenchMode,
): readonly RuntimeAnnotationTag[] {
  if (mode === "annotation") return bundle.draft?.tags ?? [];
  return resolveReviewRevision(bundle)?.tags ?? [];
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
  const editableStatus =
    task.status === "DRAFT" || task.status === "NEEDS_REVISION";
  const canEdit =
    input.mode === "annotation" &&
    editableStatus &&
    assignedToCurrent &&
    input.has("annotation.edit") &&
    input.has("annotation_draft.edit");
  const canSave = canEdit && input.has("annotation.save");
  const canSubmit = canEdit && !input.dirty && input.has("annotation.submit");
  const selfReview =
    !!input.principalId && task.submitted_by === input.principalId;
  const canReview =
    input.mode === "tag-review" &&
    task.status === "SUBMITTED" &&
    !selfReview &&
    input.has("annotation.review");
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
    canEdit,
    canSave,
    canSubmit,
    canReview,
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
  if (!bundle || (mode === "annotation" && !bundle.draft)) {
    return (
      <WorkbenchState
        kind="contract-mismatch"
        detail="正式 API 未返回当前模式所需的固定草稿。"
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
  const save = async () => {
    if (!bundle.draft) throw new Error("当前没有可保存的服务端草稿。");
    await commands.saveDraft(bundle.task, bundle.draft, tags);
    if (recoveryIdentity) clearTagDraftRecovery(recoveryIdentity);
    setRecoveryTags(null);
    setDirty(false);
    await query.refetch();
  };
  const submit = async () => {
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
    const submission = resolveReviewSubmission(bundle);
    if (!submission) throw new Error("正式 API 未返回可审核的不可变提交快照。");
    await commands.review(bundle.task, submission, decision, comment);
    await query.refetch();
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
    if (
      dirty &&
      !window.confirm(
        "有尚未保存的 Tag 修改，确认切换模式并保留本会话恢复稿吗？",
      )
    )
      return;
    void navigate(
      nextMode === "tag-review"
        ? annotationRoutes.tagReviewTask.build({ taskId: bundle.task.task_id })
        : annotationRoutes.task.build({ taskId: bundle.task.task_id }),
    );
  };

  return (
    <AnnotationWorkbenchView
      bundle={bundle}
      dirty={dirty}
      mode={mode}
      permissions={permissions}
      recoveryAvailable={recoveryTags !== null}
      scope={scope}
      tags={tags}
      onDiscardRecovery={() => {
        if (recoveryIdentity) clearTagDraftRecovery(recoveryIdentity);
        setRecoveryTags(null);
      }}
      onRecover={() => {
        if (!recoveryTags) return;
        setTags(recoveryTags);
        setDirty(true);
        setRecoveryTags(null);
      }}
      onReview={review}
      onSave={save}
      onSelectTask={selectTask}
      onSubmit={submit}
      onSwitchMode={switchMode}
      onTagsChange={(next) => {
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
