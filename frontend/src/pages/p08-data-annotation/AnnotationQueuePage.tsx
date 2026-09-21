import { useDeferredValue, useMemo, useState } from "react";
import type { JSX } from "react";
import { useQuery } from "@tanstack/react-query";
import { Pagination } from "antd";
import {
  BadgeCheck,
  CircleX,
  ClipboardCheck,
  History,
  PencilLine,
  RefreshCw,
  RotateCcw,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { isDomainError } from "../../shared/api/domain-error";
import { useShellStore } from "../../shared/scope/shell-store";
import { AnnotationPageState } from "../../features/annotation";
import { WorkflowQueuePage } from "../../shared/ui/workflow/WorkflowQueuePage";
import {
  annotationTaskStatusLabel,
  claimRuntimeAnnotationTask,
  listRuntimeAnnotationTasks,
  runtimeAnnotationScopeFromShell,
} from "./runtime-annotation-adapter";
import type {
  AnnotationWorkbenchMode,
  RuntimeAnnotationScope,
  RuntimeAnnotationTask,
} from "./runtime-annotation-adapter";
import {
  annotationQueueQueryCodec,
  type AnnotationQueueStage,
} from "./query-codec";
import { annotationRoutes } from "./routes";
import "./p08.css";

interface QueueStageDefinition {
  readonly status: Exclude<AnnotationQueueStage, "REJECTED">;
  readonly label: string;
  readonly description: string;
  readonly queueDescription: string;
  readonly emptyDescription: string;
  readonly icon: LucideIcon;
}

export const ANNOTATION_QUEUE_STAGES: readonly QueueStageDefinition[] = [
  {
    status: "DRAFT",
    label: "待标注",
    description: "领取任务并开始标注",
    queueDescription: "等待领取或继续编辑的标注任务。",
    emptyDescription: "新任务会在 Lance 与标签结构固定后进入此队列。",
    icon: PencilLine,
  },
  {
    status: "SUBMITTED",
    label: "待审核",
    description: "审核固定提交版本",
    queueDescription: "标注已提交，等待审核固定版本。",
    emptyDescription: "当前没有等待审核的提交；已提交任务会自动进入这里。",
    icon: ClipboardCheck,
  },
  {
    status: "NEEDS_REVISION",
    label: "待修改",
    description: "根据审核意见返工",
    queueDescription: "根据审核意见修改标注并重新提交。",
    emptyDescription: "当前没有被要求修改的任务；审核意见会随任务保留。",
    icon: RotateCcw,
  },
  {
    status: "APPROVED",
    label: "标注完成",
    description: "查看已批准标注结果",
    queueDescription: "审核已通过，批准修订可供发布流程读取。",
    emptyDescription: "当前没有审核通过的标注结果。",
    icon: BadgeCheck,
  },
];

const rejectedStageDefinition = {
  status: "REJECTED",
  label: "已拒绝",
  queueDescription: "审核已拒绝，仅查看驳回事实与提交版本。",
  emptyDescription: "当前没有被拒绝的标注提交。",
  icon: CircleX,
} as const satisfies {
  readonly status: AnnotationQueueStage;
  readonly label: string;
  readonly queueDescription: string;
  readonly emptyDescription: string;
  readonly icon: LucideIcon;
};

const stageDefinitions: Readonly<
  Record<
    AnnotationQueueStage,
    Pick<
      QueueStageDefinition,
      "label" | "queueDescription" | "emptyDescription" | "icon"
    >
  >
> = {
  DRAFT: ANNOTATION_QUEUE_STAGES[0]!,
  SUBMITTED: ANNOTATION_QUEUE_STAGES[1]!,
  NEEDS_REVISION: ANNOTATION_QUEUE_STAGES[2]!,
  APPROVED: ANNOTATION_QUEUE_STAGES[3]!,
  REJECTED: rejectedStageDefinition,
};

function queueErrorKind(
  error: unknown,
): Parameters<typeof AnnotationPageState>[0]["kind"] {
  if (!isDomainError(error)) return "fatal-error";
  if (error.code === "FORBIDDEN" || error.code === "UNAUTHENTICATED")
    return "forbidden";
  if (error.code === "RATE_LIMITED") return "rate-limited";
  if (error.code === "NETWORK_ERROR") return "offline-reconnecting";
  if (error.code === "CONTRACT_MISMATCH") return "contract-mismatch";
  return "fatal-error";
}

function displayTime(value: string | undefined): string {
  if (!value) return "未返回";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function taskTarget(task: RuntimeAnnotationTask, returnTo: string): string {
  if (task.status === "SUBMITTED")
    return annotationRoutes.tagReviewTask.build({ taskId: task.task_id });
  return annotationRoutes.task.build({ taskId: task.task_id }, { returnTo });
}

function QueueRow(props: {
  readonly task: RuntimeAnnotationTask;
  readonly scope: RuntimeAnnotationScope;
  readonly actorId: string | null;
  readonly canClaim: boolean;
  readonly canEdit: boolean;
  readonly canOpen: boolean;
  readonly canReview: boolean;
  readonly returnTo: string;
  readonly onChanged: () => void;
}): JSX.Element {
  const [claiming, setClaiming] = useState(false);
  const [claimError, setClaimError] = useState<string | null>(null);
  const unassignedDraft =
    props.task.status === "DRAFT" && props.task.assignee_id === null;
  const assignedToCurrent =
    props.actorId !== null && props.task.assignee_id === props.actorId;
  const fixedSubmissionExists =
    !!props.task.current_submission_id &&
    props.task.submitted_revision !== null &&
    props.task.submitted_revision !== undefined;
  const canStartReview =
    props.task.status === "SUBMITTED" &&
    props.canReview &&
    fixedSubmissionExists;
  const canModify = assignedToCurrent && props.canEdit;

  const claim = async () => {
    setClaiming(true);
    setClaimError(null);
    try {
      await claimRuntimeAnnotationTask(props.scope, props.task);
      props.onChanged();
    } catch (error) {
      setClaimError(error instanceof Error ? error.message : "领取失败。");
    } finally {
      setClaiming(false);
    }
  };

  const operationLabel =
    props.task.status === "DRAFT"
      ? unassignedDraft
        ? "领取标注"
        : canModify
          ? "继续标注"
          : "打开任务"
      : props.task.status === "SUBMITTED"
        ? canStartReview
          ? "开始审核"
          : "查看提交"
        : props.task.status === "NEEDS_REVISION"
          ? canModify
            ? "修改标注"
            : "打开任务"
          : props.task.status === "REJECTED"
            ? "查看驳回"
            : "查看结果";

  return (
    <tr>
      <th scope="row">
        <span
          className="p08-cell-primary p08-truncate"
          title={props.task.rollout_id}
        >
          {props.task.rollout_id}
        </span>
        <small className="p08-truncate" title={props.task.task_id}>
          {props.task.task_id}
        </small>
      </th>
      <td>
        <span
          className={`p08-status p08-status--${props.task.status.toLowerCase()}`}
        >
          {annotationTaskStatusLabel(props.task.status)}
        </span>
      </td>
      <td>
        <span
          className="p08-cell-primary p08-truncate"
          title={props.task.dataset_id}
        >
          {props.task.dataset_id}
        </span>
        <small>
          Dataset v{props.task.dataset_version} · Lance v
          {props.task.base_lance_version}
        </small>
      </td>
      <td>
        {props.task.base_step_count === null ||
        props.task.base_step_count === undefined
          ? "未返回"
          : `${props.task.base_step_count.toLocaleString("zh-CN")} 步`}
      </td>
      <td>
        <span
          className="p08-truncate"
          title={props.task.assignee_id ?? "未领取"}
        >
          {props.task.assignee_id ?? "未领取"}
        </span>
      </td>
      <td>
        <time dateTime={props.task.updated_at} title={props.task.updated_at}>
          {displayTime(props.task.updated_at)}
        </time>
      </td>
      <td>
        {unassignedDraft ? (
          <button
            className="p08-row-action"
            disabled={!props.canClaim || claiming}
            title={
              props.canClaim ? undefined : "当前账号没有领取标注任务的权限。"
            }
            type="button"
            onClick={() => void claim()}
          >
            {claiming ? "领取中…" : operationLabel}
          </button>
        ) : props.canOpen ? (
          <Link
            className="p08-row-action"
            to={taskTarget(props.task, props.returnTo)}
          >
            {operationLabel}
          </Link>
        ) : (
          <button
            className="p08-row-action"
            disabled
            title="当前账号没有读取任务详情的权限。"
            type="button"
          >
            {operationLabel}
          </button>
        )}
        {claimError ? (
          <small className="p08-row-error" role="alert">
            {claimError}
          </small>
        ) : null}
      </td>
    </tr>
  );
}

function RuntimeAnnotationQueuePage({
  mode,
}: {
  readonly mode: AnnotationWorkbenchMode;
}): JSX.Element {
  const capabilities = useCapabilities();
  const shellScope = useShellStore((state) => state.scope);
  const unscopedAccount = useShellStore(
    (state) =>
      state.bootstrapLoaded &&
      !state.authorizationFailed &&
      state.scope === null,
  );
  const actorId = useShellStore((state) => state.principal?.actorId ?? null);
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const defaultStage: AnnotationQueueStage =
    mode === "tag-review" ? "SUBMITTED" : "DRAFT";
  const queueSearch = annotationQueueQueryCodec.parse(
    searchParams,
    false,
    defaultStage,
  );
  const queryText = queueSearch.q ?? "";
  const deferredQueryText = useDeferredValue(queryText);
  const scope = useMemo<RuntimeAnnotationScope | null>(() => {
    return runtimeAnnotationScopeFromShell(shellScope);
  }, [
    shellScope?.organizationId,
    shellScope?.projectId,
    shellScope?.regionCode,
  ]);
  const canRead = capabilities.has("annotation_task.read");
  const tasks = useQuery({
    queryKey: scope
      ? ["p08-runtime-annotation-list", scope.projectId, scope.regionCode]
      : ["p08-runtime-annotation-list", "disabled"],
    queryFn: ({ signal }) => listRuntimeAnnotationTasks(scope!, signal),
    enabled:
      !!scope && canRead && !capabilities.loading && !capabilities.failed,
    retry: false,
    staleTime: 0,
  });
  const counts = useMemo<Record<AnnotationQueueStage, number>>(() => {
    const next: Record<AnnotationQueueStage, number> = {
      DRAFT: 0,
      SUBMITTED: 0,
      NEEDS_REVISION: 0,
      APPROVED: 0,
      REJECTED: 0,
    };
    for (const task of tasks.data ?? []) next[task.status] += 1;
    return next;
  }, [tasks.data]);
  const visible = useMemo(() => {
    const normalized = deferredQueryText
      .normalize("NFC")
      .trim()
      .toLocaleLowerCase("zh-CN");
    return (tasks.data ?? [])
      .filter((task) => {
        if (task.status !== queueSearch.stage) return false;
        if (!normalized) return true;
        return [
          task.task_id,
          task.rollout_id,
          task.dataset_id,
          task.tag_schema_id,
          task.assignee_id ?? "",
        ].some((value) =>
          value.toLocaleLowerCase("zh-CN").includes(normalized),
        );
      })
      .toSorted((left, right) =>
        (right.updated_at ?? "").localeCompare(left.updated_at ?? ""),
      );
  }, [deferredQueryText, queueSearch.stage, tasks.data]);
  const page = Math.min(
    queueSearch.page,
    Math.max(1, Math.ceil(visible.length / queueSearch.limit)),
  );
  const pageTasks = visible.slice(
    (page - 1) * queueSearch.limit,
    page * queueSearch.limit,
  );
  const currentStage = stageDefinitions[queueSearch.stage];
  const returnTo = `${location.pathname}${location.search}`;

  const stageHref = (stage: AnnotationQueueStage) =>
    annotationRoutes.annotate.build({
      ...queueSearch,
      stage,
      page: 1,
      after: undefined,
      before: undefined,
    });

  const updateQuery = (value: string) => {
    const next = new URLSearchParams(searchParams);
    const normalized = value.normalize("NFC").trim().slice(0, 100);
    if (normalized) next.set("q", normalized);
    else next.delete("q");
    next.delete("after");
    next.delete("before");
    next.delete("page");
    setSearchParams(next, { replace: true });
  };

  const updatePage = (nextPage: number, pageSize: number) => {
    const next = new URLSearchParams(searchParams);
    const limit = pageSize === 50 || pageSize === 100 ? pageSize : 20;
    const targetPage = limit === queueSearch.limit ? nextPage : 1;
    if (targetPage > 1) next.set("page", String(targetPage));
    else next.delete("page");
    if (limit !== 20) next.set("limit", String(limit));
    else next.delete("limit");
    next.delete("after");
    next.delete("before");
    setSearchParams(next);
  };

  if (!unscopedAccount && (capabilities.loading || tasks.isLoading))
    return <AnnotationPageState kind="first-loading" />;
  if (!unscopedAccount && (capabilities.failed || !canRead))
    return <AnnotationPageState kind="forbidden" />;
  if (!scope && !unscopedAccount)
    return (
      <AnnotationPageState
        kind="feature-unavailable"
        detail="需要选择有效项目和 Region。"
      />
    );
  if (tasks.error) {
    return (
      <AnnotationPageState
        kind={queueErrorKind(tasks.error)}
        detail={tasks.error.message}
        problemCode={
          isDomainError(tasks.error)
            ? (tasks.error.problemCode ?? undefined)
            : undefined
        }
        requestId={
          isDomainError(tasks.error)
            ? (tasks.error.requestId ?? undefined)
            : undefined
        }
        retryable={
          isDomainError(tasks.error) ? tasks.error.retryable : undefined
        }
        onRetry={() => void tasks.refetch()}
      />
    );
  }

  return (
    <WorkflowQueuePage
      title="数据标注"
      headerActions={
        <>
          <Link
            className="p08-secondary-action"
            to={annotationRoutes.revisions.pattern}
          >
            <History aria-hidden="true" size={16} />
            修订记录
          </Link>
          <button
            className="p08-secondary-action"
            disabled={!scope || tasks.isFetching}
            type="button"
            onClick={() => {
              if (scope) void tasks.refetch();
            }}
          >
            <RefreshCw aria-hidden="true" size={16} />
            {tasks.isFetching ? "刷新中…" : "刷新"}
          </button>
        </>
      }
      stages={ANNOTATION_QUEUE_STAGES.map((stage) => ({
        key: stage.status,
        label: stage.label,
        description: stage.description,
        queueDescription: stage.queueDescription,
        emptyDescription: stage.emptyDescription,
        icon: stage.icon,
        count: counts[stage.status],
        href: stageHref(stage.status),
      }))}
      selectedStage={queueSearch.stage}
      stageNavigationLabel="标注任务状态"
      visibleCount={visible.length}
      search={{
        id: "p08-task-search",
        name: "annotation-task-search",
        label: "搜索任务、Rollout、Dataset 或数据结构",
        placeholder: "输入关键词…",
        value: queryText,
        meta: `按最近更新时间展示 · ${counts[queueSearch.stage]} 项处于此状态`,
        onChange: updateQuery,
      }}
      tableLabel="任务表"
      table={
        <table>
          <caption>{currentStage.label}任务列表</caption>
          <thead>
            <tr>
              <th>采集条目 / 任务</th>
              <th>当前状态</th>
              <th>数据基线</th>
              <th>对齐范围</th>
              <th>处理人</th>
              <th>最近更新</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {pageTasks.map((task) => (
              <QueueRow
                actorId={actorId}
                canClaim={capabilities.has("annotation_task.claim")}
                canEdit={
                  capabilities.has("annotation.edit") &&
                  capabilities.has("annotation_draft.edit")
                }
                canOpen={capabilities.has("episode.read")}
                canReview={capabilities.has("annotation.review")}
                key={task.task_id}
                returnTo={returnTo}
                scope={scope!}
                task={task}
                onChanged={() => void tasks.refetch()}
              />
            ))}
          </tbody>
        </table>
      }
      pagination={
        <Pagination
          current={page}
          pageSize={queueSearch.limit}
          total={visible.length}
          pageSizeOptions={[20, 50, 100]}
          showSizeChanger
          showTotal={(total, [start, end]) =>
            `第 ${start}–${end} 条，共 ${total} 条`
          }
          onChange={updatePage}
        />
      }
      emptyActionLabel={queryText ? "清除搜索" : "重新检查队列"}
      emptyActionDisabled={!queryText && !scope}
      onEmptyAction={() => {
        if (queryText) updateQuery("");
        else if (scope) void tasks.refetch();
      }}
    />
  );
}

export function AnnotationQueuePage(): JSX.Element {
  return <RuntimeAnnotationQueuePage mode="annotation" />;
}

export function TagReviewQueuePage(): JSX.Element {
  return <RuntimeAnnotationQueuePage mode="tag-review" />;
}
