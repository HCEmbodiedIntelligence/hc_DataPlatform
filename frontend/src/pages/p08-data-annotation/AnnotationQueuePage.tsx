import { useDeferredValue, useMemo, useState } from "react";
import type { JSX } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  BadgeCheck,
  Check,
  CircleX,
  ClipboardCheck,
  History,
  PencilLine,
  RefreshCw,
  RotateCcw,
  Search,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { isDomainError } from "../../shared/api/domain-error";
import { useShellStore } from "../../shared/scope/shell-store";
import { AnnotationPageState } from "../../features/annotation";
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
  const selfReview =
    props.actorId !== null && props.task.submitted_by === props.actorId;
  const canStartReview =
    props.task.status === "SUBMITTED" &&
    props.canReview &&
    fixedSubmissionExists &&
    !selfReview;
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
        <span
          className="p08-cell-primary p08-truncate"
          title={props.task.tag_schema_id}
        >
          {props.task.tag_schema_id}
        </span>
        <small>标签结构 v{props.task.tag_schema_version}</small>
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
  const currentStage = stageDefinitions[queueSearch.stage];
  const CurrentStageIcon = currentStage.icon;
  const returnTo = `${location.pathname}${location.search}`;

  const stageHref = (stage: AnnotationQueueStage) =>
    annotationRoutes.annotate.build({
      ...queueSearch,
      stage,
    });

  const updateQuery = (value: string) => {
    const next = new URLSearchParams(searchParams);
    const normalized = value.normalize("NFC").trim().slice(0, 100);
    if (normalized) next.set("q", normalized);
    else next.delete("q");
    next.delete("after");
    next.delete("before");
    setSearchParams(next, { replace: true });
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
    <main className="p08-page p08-queue-page">
      <header className="p08-page-header p08-page-header--plain">
        <div>
          <h1>数据标注</h1>
        </div>
        <div className="p08-header-actions">
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
        </div>
      </header>

      <nav className="p08-stage-grid" aria-label="标注任务状态">
        {ANNOTATION_QUEUE_STAGES.map((stage) => {
          const Icon = stage.icon;
          const selected = queueSearch.stage === stage.status;
          return (
            <Link
              aria-current={selected ? "page" : undefined}
              aria-label={`${stage.label}，${stage.description}，${counts[stage.status]} 项${selected ? "，当前队列" : ""}`}
              className="p08-stage-card"
              key={stage.status}
              to={stageHref(stage.status)}
            >
              <span className="p08-stage-card__icon">
                <Icon aria-hidden="true" size={20} strokeWidth={1.8} />
              </span>
              <span className="p08-stage-card__copy">
                <strong>{stage.label}</strong>
                <span>{stage.description}</span>
              </span>
              <strong className="p08-stage-card__count">
                {counts[stage.status]}
              </strong>
              <span className="p08-stage-card__state" aria-hidden="true">
                {selected ? (
                  <>
                    <Check size={13} strokeWidth={2.2} />
                    当前队列
                  </>
                ) : (
                  <ArrowRight size={15} />
                )}
              </span>
            </Link>
          );
        })}
      </nav>

      <section className="p08-queue-panel" aria-labelledby="p08-queue-title">
        <header className="p08-queue-panel__header">
          <div>
            <p>当前队列</p>
            <h2 id="p08-queue-title">{currentStage.label}</h2>
            <span>{currentStage.queueDescription}</span>
          </div>
          <output aria-live="polite">
            <strong>{visible.length}</strong>
            <span>项结果</span>
          </output>
        </header>
        <div className="p08-queue-toolbar" role="search">
          <label htmlFor="p08-task-search">
            <span>搜索任务、Rollout、Dataset 或数据结构</span>
            <span className="p08-search-field">
              <Search aria-hidden="true" size={17} />
              <input
                autoComplete="off"
                id="p08-task-search"
                name="annotation-task-search"
                placeholder="输入关键词…"
                type="search"
                value={queryText}
                onChange={(event) => updateQuery(event.target.value)}
              />
            </span>
          </label>
          <span>
            按最近更新时间展示 · {counts[queueSearch.stage]} 项处于此状态
          </span>
        </div>
        {visible.length === 0 ? (
          <div className="p08-queue-empty">
            <span className="p08-queue-empty__icon">
              <CurrentStageIcon aria-hidden="true" size={22} />
            </span>
            <h3>
              {queryText ? "没有匹配的任务" : `${currentStage.label}队列为空`}
            </h3>
            <p>
              {queryText
                ? "请缩短关键词或清除搜索，任务状态筛选会继续保留。"
                : currentStage.emptyDescription}
            </p>
            {queryText ? (
              <button type="button" onClick={() => updateQuery("")}>
                清除搜索
              </button>
            ) : (
              <button
                disabled={!scope}
                type="button"
                onClick={() => {
                  if (scope) void tasks.refetch();
                }}
              >
                重新检查队列
              </button>
            )}
          </div>
        ) : (
          <div
            aria-label={`${currentStage.label}任务表，可横向滚动`}
            className="p08-table-wrap p08-queue-table"
            role="region"
            tabIndex={0}
          >
            <table>
              <caption>{currentStage.label}任务列表</caption>
              <thead>
                <tr>
                  <th>采集条目 / 任务</th>
                  <th>当前状态</th>
                  <th>数据基线</th>
                  <th>标签结构</th>
                  <th>对齐范围</th>
                  <th>处理人</th>
                  <th>最近更新</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((task) => (
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
          </div>
        )}
      </section>
    </main>
  );
}

export function AnnotationQueuePage(): JSX.Element {
  return <RuntimeAnnotationQueuePage mode="annotation" />;
}

export function TagReviewQueuePage(): JSX.Element {
  return <RuntimeAnnotationQueuePage mode="tag-review" />;
}
