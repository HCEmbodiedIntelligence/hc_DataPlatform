import { useInfiniteQuery } from "@tanstack/react-query";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { useState, type JSX } from "react";
import { Link } from "react-router-dom";
import { AnnotationPageState } from "../../features/annotation";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  annotationTaskStatusLabel,
  listRuntimeAnnotationRevisionThreads,
  runtimeAnnotationScopeFromShell,
} from "./runtime-annotation-adapter";
import type {
  RuntimeAnnotationRevisionThread,
  RuntimeAnnotationScope,
} from "./runtime-annotation-adapter";
import { annotationRoutes } from "./routes";
import "./p08.css";

type RevisionStatusFilter = RuntimeAnnotationRevisionThread["status"] | "ALL";
type RevisionOriginFilter =
  | RuntimeAnnotationRevisionThread["latest_revision"]["origin"]
  | "ALL";

const revisionStatusOptions: readonly {
  readonly value: RevisionStatusFilter;
  readonly label: string;
}[] = [
  { value: "ALL", label: "全部状态" },
  { value: "DRAFT", label: "待标注" },
  { value: "SUBMITTED", label: "待审核" },
  { value: "APPROVED", label: "标注完成" },
  { value: "NEEDS_REVISION", label: "需修改" },
  { value: "REJECTED", label: "已拒绝" },
];

const revisionOriginOptions: readonly {
  readonly value: RevisionOriginFilter;
  readonly label: string;
}[] = [
  { value: "ALL", label: "全部来源" },
  { value: "ANNOTATION", label: "标注" },
  { value: "ANNOTATION_RESTORE", label: "恢复修订" },
];

function revisionErrorKind(
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

function displayTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function ThreadRow({
  thread,
  canOpenTask,
}: {
  readonly thread: RuntimeAnnotationRevisionThread;
  readonly canOpenTask: boolean;
}): JSX.Element {
  return (
    <tr>
      <th scope="row">
        <strong>{thread.rollout_id}</strong>
        <small>{thread.task_id}</small>
      </th>
      <td>
        <span
          className={`p08-status p08-status--${thread.status.toLowerCase()}`}
        >
          {annotationTaskStatusLabel(thread.status)}
        </span>
      </td>
      <td>
        <strong>草稿 r{thread.latest_revision.revision}</strong>
        <small>
          {thread.latest_revision.origin === "ANNOTATION"
            ? "标注修订"
            : "恢复修订"}
        </small>
      </td>
      <td>
        {thread.dataset_id}
        <small>v{thread.dataset_version}</small>
      </td>
      <td>
        {thread.current_episode_version === null ||
        thread.current_episode_version === undefined
          ? "未提交"
          : `Episode v${thread.current_episode_version}`}
        {thread.approved_revision === null ||
        thread.approved_revision === undefined ? null : (
          <small>已通过 · 待数据集发布定版</small>
        )}
      </td>
      <td>
        <time dateTime={thread.updated_at}>
          {displayTime(thread.updated_at)}
        </time>
        <small>{thread.latest_revision.author_id}</small>
      </td>
      <td>
        {canOpenTask ? (
          <Link
            className="p08-row-link"
            to={annotationRoutes.task.build({ taskId: thread.task_id })}
          >
            打开任务
          </Link>
        ) : (
          <span className="p08-muted-action">缺少任务详情权限</span>
        )}
      </td>
    </tr>
  );
}

export function AnnotationRevisionPage(): JSX.Element {
  const capabilities = useCapabilities();
  const shellScope = useShellStore((state) => state.scope);
  const unscopedAccount = useShellStore(
    (state) =>
      state.bootstrapLoaded &&
      !state.authorizationFailed &&
      state.scope === null,
  );
  const scope = runtimeAnnotationScopeFromShell(shellScope);
  const [status, setStatus] = useState<RevisionStatusFilter>("ALL");
  const [origin, setOrigin] = useState<RevisionOriginFilter>("ALL");
  const canRead = capabilities.has("annotation_task.read");
  const canOpenTask = capabilities.has("episode.read");
  const revisions = useInfiniteQuery({
    queryKey: [
      "p08-revision-threads",
      scope?.projectId ?? "disabled",
      scope?.regionCode ?? "disabled",
      status,
      origin,
    ],
    queryFn: ({ pageParam, signal }) =>
      listRuntimeAnnotationRevisionThreads(
        scope as RuntimeAnnotationScope,
        {
          status: status === "ALL" ? undefined : status,
          origin: origin === "ALL" ? undefined : origin,
          ...(pageParam === null ? {} : { after: pageParam }),
        },
        signal,
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.page_info.end_cursor ?? undefined,
    enabled:
      scope !== null &&
      canRead &&
      !capabilities.loading &&
      !capabilities.failed,
    retry: false,
    staleTime: 30_000,
  });
  const threads = revisions.data?.pages.flatMap((page) => page.items) ?? [];

  if (
    !unscopedAccount &&
    (capabilities.loading ||
      (revisions.isLoading && revisions.data === undefined))
  )
    return <AnnotationPageState kind="first-loading" />;
  if (!unscopedAccount && (capabilities.failed || !canRead))
    return <AnnotationPageState kind="forbidden" />;
  if (!scope && !unscopedAccount)
    return (
      <AnnotationPageState
        kind="feature-unavailable"
        detail="需要先选择有效的项目和 Region。"
      />
    );
  if (revisions.error)
    return (
      <AnnotationPageState
        kind={revisionErrorKind(revisions.error)}
        detail={revisions.error.message}
        problemCode={
          isDomainError(revisions.error)
            ? (revisions.error.problemCode ?? undefined)
            : undefined
        }
        requestId={
          isDomainError(revisions.error)
            ? (revisions.error.requestId ?? undefined)
            : undefined
        }
        retryable={
          isDomainError(revisions.error) ? revisions.error.retryable : undefined
        }
        onRetry={() => void revisions.refetch()}
      />
    );

  return (
    <main className="p08-page p08-revision-page">
      <header className="p08-page-header p08-page-header--plain">
        <div>
          <h1>Episode 版本与草稿修订</h1>
        </div>
        <div className="p08-header-actions">
          <Link
            className="p08-secondary-action"
            to={annotationRoutes.annotate.pattern}
          >
            <ArrowLeft aria-hidden="true" size={16} />
            返回任务队列
          </Link>
          <button
            className="p08-secondary-action"
            disabled={!scope || revisions.isFetching}
            type="button"
            onClick={() => {
              if (scope) void revisions.refetch();
            }}
          >
            <RefreshCw aria-hidden="true" size={16} />
            {revisions.isFetching ? "刷新中…" : "刷新"}
          </button>
        </div>
      </header>
      <section className="p08-revision-toolbar" aria-label="修订筛选">
        <label>
          <span>工作流状态</span>
          <select
            aria-label="工作流状态"
            value={status}
            onChange={(event) =>
              setStatus(event.target.value as RevisionStatusFilter)
            }
          >
            {revisionStatusOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>修订来源</span>
          <select
            aria-label="修订来源"
            value={origin}
            onChange={(event) =>
              setOrigin(event.target.value as RevisionOriginFilter)
            }
          >
            {revisionOriginOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <output aria-live="polite">
          {threads.length} 条已加载 · 单页由服务端签名游标推进
        </output>
      </section>
      {threads.length === 0 ? (
        <AnnotationPageState
          kind="empty"
          detail="当前 Scope 中没有符合筛选条件的修订线索。"
        />
      ) : (
        <div
          aria-label="修订记录表，可横向滚动"
          className="p08-table-wrap"
          role="region"
          tabIndex={0}
        >
          <table>
            <caption>当前修订线程（按最近变更排序）</caption>
            <thead>
              <tr>
                <th>Rollout / 任务</th>
                <th>状态</th>
                <th>最新草稿修订</th>
                <th>Dataset</th>
                <th>Episode 版本 / 审核</th>
                <th>最近变更</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {threads.map((thread) => (
                <ThreadRow
                  canOpenTask={canOpenTask}
                  key={thread.task_id}
                  thread={thread}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {revisions.hasNextPage ? (
        <div className="p08-revision-more">
          <button
            disabled={revisions.isFetchingNextPage}
            type="button"
            onClick={() => void revisions.fetchNextPage()}
          >
            {revisions.isFetchingNextPage ? "加载中…" : "加载下一页"}
          </button>
        </div>
      ) : null}
    </main>
  );
}
