import { useInfiniteQuery } from "@tanstack/react-query";
import { History, RefreshCw, ShieldCheck, Tags } from "lucide-react";
import { useState, type JSX } from "react";
import { Link, useSearchParams } from "react-router-dom";
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
  { value: "DRAFT", label: "草稿" },
  { value: "SUBMITTED", label: "待审核" },
  { value: "APPROVED", label: "已通过" },
  { value: "NEEDS_REVISION", label: "需修改" },
  { value: "REJECTED", label: "已拒绝" },
];

const revisionOriginOptions: readonly {
  readonly value: RevisionOriginFilter;
  readonly label: string;
}[] = [
  { value: "ALL", label: "全部来源" },
  { value: "ANNOTATION", label: "标注" },
  { value: "LEGACY_CLEANING", label: "历史清洗" },
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
  const legacy = thread.legacy_draft_id;
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
        <strong>r{thread.latest_revision.revision}</strong>
        <small>
          {thread.latest_revision.origin === "ANNOTATION"
            ? "标注修订"
            : "历史清洗导入"}
        </small>
      </td>
      <td>
        {thread.dataset_id}
        <small>v{thread.dataset_version}</small>
      </td>
      <td>
        {thread.submitted_revision === null ||
        thread.submitted_revision === undefined
          ? "未提交"
          : `已提交 r${thread.submitted_revision}`}
        {thread.approved_revision === null ||
        thread.approved_revision === undefined ? null : (
          <small>已通过 r{thread.approved_revision}</small>
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
        {legacy ? <small>已关联旧草稿 {legacy}</small> : null}
      </td>
    </tr>
  );
}

export function AnnotationRevisionPage(): JSX.Element {
  const capabilities = useCapabilities();
  const shellScope = useShellStore((state) => state.scope);
  const scope = runtimeAnnotationScopeFromShell(shellScope);
  const [status, setStatus] = useState<RevisionStatusFilter>("ALL");
  const [origin, setOrigin] = useState<RevisionOriginFilter>("ALL");
  const [searchParams] = useSearchParams();
  const legacyDraftId = searchParams.get("legacyDraftId")?.trim() || undefined;
  const canRead = capabilities.has("annotation_task.read");
  const canOpenTask = capabilities.has("episode.read");
  const revisions = useInfiniteQuery({
    queryKey: [
      "p08-revision-threads",
      scope?.projectId ?? "disabled",
      scope?.regionCode ?? "disabled",
      status,
      origin,
      legacyDraftId ?? "all-legacy-drafts",
    ],
    queryFn: ({ pageParam, signal }) =>
      listRuntimeAnnotationRevisionThreads(
        scope as RuntimeAnnotationScope,
        {
          status: status === "ALL" ? undefined : status,
          origin: origin === "ALL" ? undefined : origin,
          legacyDraftId,
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
    capabilities.loading ||
    (revisions.isLoading && revisions.data === undefined)
  )
    return <AnnotationPageState kind="first-loading" />;
  if (capabilities.failed || !canRead)
    return <AnnotationPageState kind="forbidden" />;
  if (!scope)
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
      <header className="p08-page-header">
        <div>
          <p className="p08-eyebrow">数据生产 / 数据标注 / 不可变修订</p>
          <h1>数据修订</h1>
          <p>以当前项目和 Region 为界，按最近工作流变化读取不可变修订线索。</p>
        </div>
        <button
          disabled={revisions.isFetching}
          type="button"
          onClick={() => void revisions.refetch()}
        >
          <RefreshCw aria-hidden="true" size={15} />
          {revisions.isFetching ? "刷新中…" : "刷新"}
        </button>
      </header>
      <nav className="p08-mode-tabs" aria-label="数据标注功能模式">
        <Link to={annotationRoutes.annotate.pattern}>
          <Tags aria-hidden="true" size={15} />
          数据标注
        </Link>
        <Link aria-current="page" to={annotationRoutes.revisions.pattern}>
          <History aria-hidden="true" size={15} />
          数据修订
        </Link>
        <Link to={annotationRoutes.tagReview.pattern}>
          <ShieldCheck aria-hidden="true" size={15} />
          Tag 审核
        </Link>
      </nav>
      {legacyDraftId ? (
        <section className="p08-legacy-route-notice" role="status">
          <strong>旧清洗草稿兼容入口</strong>
          <span>
            仅在当前项目和 Region 中查找已安全导入的草稿 {legacyDraftId}；不会跨
            Scope 猜测映射。
          </span>
          <Link to={annotationRoutes.revisions.pattern}>查看全部数据修订</Link>
        </section>
      ) : null}
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
          detail={
            legacyDraftId
              ? `旧草稿 ${legacyDraftId} 尚未在当前项目和 Region 中安全导入；请从数据修订列表继续，不会自动打开其他 Scope 的同名草稿。`
              : "当前 Scope 中没有符合筛选条件的修订线索。"
          }
        />
      ) : (
        <div className="p08-table-wrap">
          <table>
            <caption>当前修订线程（按最近变更排序）</caption>
            <thead>
              <tr>
                <th>Rollout / 任务</th>
                <th>状态</th>
                <th>最新修订</th>
                <th>Dataset</th>
                <th>提交 / 审核</th>
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
