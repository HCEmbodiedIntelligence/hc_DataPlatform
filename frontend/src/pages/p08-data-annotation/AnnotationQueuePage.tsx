import { useMemo, useState } from "react";
import type { JSX } from "react";
import { useQuery } from "@tanstack/react-query";
import { History, RefreshCw, ShieldCheck, Tags } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
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
import { annotationRoutes } from "./routes";
import "./p08.css";

const taskStatusStyles = {
  DRAFT: { color: "var(--hc-color-info-text)" },
  SUBMITTED: { color: "var(--hc-color-warning-text)" },
  APPROVED: { color: "var(--hc-color-success-text)" },
  NEEDS_REVISION: { color: "var(--hc-color-warning-text)" },
  REJECTED: { color: "var(--hc-color-error-text)" },
} as const;

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

function QueueRow(props: {
  readonly mode: AnnotationWorkbenchMode;
  readonly task: RuntimeAnnotationTask;
  readonly scope: RuntimeAnnotationScope;
  readonly canClaim: boolean;
  readonly onOpen: (taskId: string) => void;
  readonly onChanged: () => void;
}): JSX.Element {
  const [claiming, setClaiming] = useState(false);
  const [claimError, setClaimError] = useState<string | null>(null);
  const canClaim =
    props.mode === "annotation" &&
    props.canClaim &&
    props.task.assignee_id === null &&
    props.task.status === "DRAFT";
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
  return (
    <tr>
      <th scope="row">
        <button
          className="p08-link-button"
          type="button"
          onClick={() => props.onOpen(props.task.task_id)}
        >
          {props.task.rollout_id}
        </button>
        <small>{props.task.task_id}</small>
      </th>
      <td>
        <span
          className={`p08-status p08-status--${props.task.status.toLowerCase()}`}
          style={taskStatusStyles[props.task.status]}
        >
          {annotationTaskStatusLabel(props.task.status)}
        </span>
      </td>
      <td>
        {props.task.dataset_id}
        <small>v{props.task.dataset_version}</small>
      </td>
      <td>
        <span>{props.task.tag_schema_id}</span>
        <small>v{props.task.tag_schema_version}</small>
      </td>
      <td>
        {props.task.base_step_count === null ||
        props.task.base_step_count === undefined
          ? "未返回"
          : `${props.task.base_step_count.toLocaleString("zh-CN")} 步`}
      </td>
      <td>{props.task.assignee_id ?? "未领取"}</td>
      <td>
        {canClaim ? (
          <button
            disabled={claiming}
            type="button"
            onClick={() => void claim()}
          >
            {claiming ? "领取中…" : "领取任务"}
          </button>
        ) : (
          <button
            type="button"
            onClick={() => props.onOpen(props.task.task_id)}
          >
            {props.mode === "tag-review" ? "进入审核" : "打开任务"}
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
  const navigate = useNavigate();
  const capabilities = useCapabilities();
  const shellScope = useShellStore((state) => state.scope);
  const [queryText, setQueryText] = useState("");
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
  const visible = useMemo(() => {
    const normalized = queryText
      .normalize("NFC")
      .trim()
      .toLocaleLowerCase("zh-CN");
    return (tasks.data ?? []).filter((task) => {
      const statusMatches =
        mode === "tag-review"
          ? task.status === "SUBMITTED"
          : task.status === "DRAFT" || task.status === "NEEDS_REVISION";
      if (!statusMatches) return false;
      if (!normalized) return true;
      return [
        task.task_id,
        task.rollout_id,
        task.dataset_id,
        task.tag_schema_id,
        task.assignee_id ?? "",
      ].some((value) => value.toLocaleLowerCase("zh-CN").includes(normalized));
    });
  }, [mode, queryText, tasks.data]);
  const open = (taskId: string) =>
    void navigate(
      mode === "tag-review"
        ? annotationRoutes.tagReviewTask.build({ taskId })
        : annotationRoutes.task.build({ taskId }),
    );

  if (capabilities.loading || tasks.isLoading)
    return <AnnotationPageState kind="first-loading" />;
  if (capabilities.failed || !canRead)
    return <AnnotationPageState kind="forbidden" />;
  if (!scope)
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
      <header className="p08-page-header">
        <div>
          <p className="p08-eyebrow">数据生产 / 数据标注</p>
          <h1>{mode === "tag-review" ? "Tag 审核" : "数据标注"}</h1>
          <p>
            {mode === "tag-review"
              ? "按固定提交快照检查 Tag 层级、边界、属性、冲突与对象关系。"
              : "在固定 Lance 版本上编辑多级 Tag，并通过唯一共享时间轴保存草稿。"}
          </p>
        </div>
        <button
          disabled={tasks.isFetching}
          type="button"
          onClick={() => void tasks.refetch()}
        >
          <RefreshCw aria-hidden="true" size={15} />
          {tasks.isFetching ? "刷新中…" : "刷新"}
        </button>
      </header>
      <nav className="p08-mode-tabs" aria-label="数据标注功能模式">
        <Link
          aria-current={mode === "annotation" ? "page" : undefined}
          to={annotationRoutes.annotate.pattern}
        >
          <Tags aria-hidden="true" size={15} />
          数据标注
        </Link>
        <Link to={annotationRoutes.revisions.pattern}>
          <History aria-hidden="true" size={15} />
          数据修订
        </Link>
        <Link
          aria-current={mode === "tag-review" ? "page" : undefined}
          to={annotationRoutes.tagReview.pattern}
        >
          <ShieldCheck aria-hidden="true" size={15} />
          Tag 审核
        </Link>
      </nav>
      <section className="p08-queue-toolbar" aria-label="任务筛选">
        <label htmlFor="p08-task-search">
          <span>搜索任务、Rollout、Dataset 或 Schema</span>
          <input
            id="p08-task-search"
            name="annotation-task-search"
            autoComplete="off"
            placeholder="输入关键词…"
            value={queryText}
            onChange={(event) => setQueryText(event.target.value)}
          />
        </label>
        <span>
          {visible.length} 项 ·{" "}
          {mode === "tag-review" ? "仅待审核提交" : "草稿 / 需修改"}
        </span>
      </section>
      {!visible.length ? (
        <AnnotationPageState
          kind="empty"
          detail={
            queryText
              ? "当前搜索没有匹配任务。"
              : mode === "tag-review"
                ? "当前没有待审核的 Tag 提交。"
                : "当前没有可编辑的标注任务。"
          }
        />
      ) : (
        <div className="p08-table-wrap">
          <table>
            <caption>
              {mode === "tag-review" ? "待审核 Tag 提交" : "数据标注任务"}
            </caption>
            <thead>
              <tr>
                <th>采集条目 / 任务</th>
                <th>状态</th>
                <th>Dataset</th>
                <th>Tag Schema</th>
                <th>对齐范围</th>
                <th>处理人</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((task) => (
                <QueueRow
                  canClaim={capabilities.has("annotation_task.claim")}
                  key={task.task_id}
                  mode={mode}
                  scope={scope}
                  task={task}
                  onChanged={() => void tasks.refetch()}
                  onOpen={open}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}

export function AnnotationQueuePage(): JSX.Element {
  return <RuntimeAnnotationQueuePage mode="annotation" />;
}

export function TagReviewQueuePage(): JSX.Element {
  return <RuntimeAnnotationQueuePage mode="tag-review" />;
}
