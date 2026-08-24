import { Button, Select } from "antd";
import { Clock3, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import {
  useDashboardActivity,
  useDashboardPending,
  useDashboardPendingPage,
  useDashboardSnapshot,
} from "../../features/dashboard/api/queries";
import {
  DASHBOARD_PROJECT_TIMEZONE_ASSUMPTION,
  type DashboardScope,
} from "../../features/dashboard/types";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  PageState,
  StandardPageScaffold,
  StatusTag,
  type PageStateKind,
} from "../../shared/ui";
import { AssetCapacityBoard } from "./components/AssetCapacityBoard";
import { DashboardActivityList } from "./components/DashboardActivityList";
import { DashboardPendingDrawer } from "./components/DashboardPendingDrawer";
import { DashboardPendingList } from "./components/DashboardPendingList";
import {
  DashboardSectionNotice,
  sectionLabel,
  sectionTone,
} from "./components/DashboardSectionNotice";
import { dashboardQueryCodec, type DashboardRange } from "./query-codec";
import styles from "./styles.module.css";

function stateFromError(error: unknown): PageStateKind {
  if (!isDomainError(error)) return "contract-mismatch";
  switch (error.code) {
    case "FORBIDDEN":
    case "UNAUTHENTICATED":
      return "forbidden";
    case "NOT_FOUND":
      return "not-found";
    case "GONE":
      return "gone";
    case "VERSION_CONFLICT":
    case "PRECONDITION_FAILED":
      return "conflict";
    case "RATE_LIMITED":
      return "rate-limited";
    case "NETWORK_ERROR":
      return "offline";
    case "CONTRACT_MISMATCH":
      return "contract-mismatch";
    default:
      return "error";
  }
}

function requestId(error: unknown): string | null {
  return isDomainError(error) ? error.requestId : null;
}

function queryState(
  query: Readonly<{
    data?: unknown;
    isPending: boolean;
    isFetching: boolean;
    error: unknown;
  }>,
): PageStateKind | "ready" {
  if (query.isPending && query.data === undefined) return "loading";
  if (query.error && query.data !== undefined) return "partial";
  if (query.error) return stateFromError(query.error);
  if (query.isFetching && query.data !== undefined) return "refreshing";
  return "ready";
}

function rangeWindow(
  range: DashboardRange,
  from: string | undefined,
  to: string | undefined,
  anchor: Date,
) {
  if (range === "custom" && from && to) return { from, to };
  const hours = range === "7d" ? 7 * 24 : range === "30d" ? 30 * 24 : 24;
  return {
    from: new Date(anchor.getTime() - hours * 60 * 60 * 1_000).toISOString(),
    to: anchor.toISOString(),
  };
}

function formatAsOf(value: string | undefined): string {
  if (!value) return "数据时间待返回";
  return `数据截至 ${new Intl.DateTimeFormat("zh-CN", {
    timeZone: DASHBOARD_PROJECT_TIMEZONE_ASSUMPTION,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value))}`;
}

function fullDateTime(value: string | undefined): string | undefined {
  if (!value) return undefined;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: DASHBOARD_PROJECT_TIMEZONE_ASSUMPTION,
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(new Date(value));
}

function renderRegion(
  state: PageStateKind | "ready",
  label: string,
  error: unknown,
  content: ReactNode,
  onRetry: () => void,
): ReactNode {
  if (state === "ready") return content;
  if (state === "refreshing") {
    return (
      <PageState state="refreshing" label={label}>
        {content}
      </PageState>
    );
  }
  if (state === "partial") {
    return (
      <PageState
        state="partial"
        label={label}
        description="刷新失败，当前保留上次成功返回的数据。"
        onRetry={onRetry}
      >
        {content}
      </PageState>
    );
  }
  return (
    <div className={styles.regionState}>
      <PageState
        state={state}
        label={label}
        requestId={requestId(error)}
        onRetry={state === "loading" ? undefined : onRetry}
      />
    </div>
  );
}

export function DashboardPage() {
  const shellScope = useShellStore((state) => state.scope);
  const scopeKey = useShellStore((state) => state.scopeKey);
  const scope: DashboardScope | null =
    shellScope?.projectId && shellScope.regionCode
      ? {
          organizationId: shellScope.organizationId,
          projectId: shellScope.projectId,
          regionCode: shellScope.regionCode,
          timezone: DASHBOARD_PROJECT_TIMEZONE_ASSUMPTION,
        }
      : null;
  const capabilities = useCapabilities();
  const dashboardReadGranted = useShellStore((state) =>
    new Set<string>(state.authorization?.capabilities ?? []).has(
      "dashboard.read",
    ),
  );
  const [searchParams, setSearchParams] = useSearchParams();
  const search = dashboardQueryCodec.parse(searchParams);
  const [anchor, setAnchor] = useState(
    () => new Date(Math.floor(Date.now() / 1_000) * 1_000),
  );
  const [pendingOpen, setPendingOpen] = useState(false);
  const [pendingCursor, setPendingCursor] = useState<
    Readonly<{
      after?: string;
      before?: string;
    }>
  >({});
  const previousScopeKey = useRef(scopeKey);
  const scopeChanged = previousScopeKey.current !== scopeKey;

  useEffect(() => {
    if (previousScopeKey.current === scopeKey) return;
    previousScopeKey.current = scopeKey;
    setPendingOpen(false);
    setPendingCursor({});
  }, [scopeKey]);

  const browserFixtureEnabled =
    import.meta.env.MODE !== "test" &&
    import.meta.env.VITE_MOCK_MODE === "browser";
  const routeAllowed =
    !capabilities.loading &&
    !capabilities.failed &&
    (dashboardReadGranted || browserFixtureEnabled);
  const window = useMemo(
    () => rangeWindow(search.range, search.from, search.to, anchor),
    [anchor, search.from, search.range, search.to],
  );
  const activity = useDashboardActivity(scope, window, routeAllowed);
  const snapshot = useDashboardSnapshot(scope, window, routeAllowed);
  const pending = useDashboardPending(scope, window, routeAllowed);
  const pendingPageInput = useMemo(
    () => ({ limit: 50 as const, ...pendingCursor }),
    [pendingCursor],
  );
  const pendingPage = useDashboardPendingPage(
    scope,
    window,
    pendingPageInput,
    routeAllowed && pendingOpen && !scopeChanged,
  );

  const queries = [activity, snapshot, pending] as const;
  const fatal = queries.every(
    (query) => query.error && query.data === undefined,
  );
  const pageState: PageStateKind | "ready" = capabilities.loading
    ? "loading"
    : capabilities.failed || !routeAllowed
      ? "forbidden"
      : !scope
        ? "feature-unavailable"
        : fatal
          ? stateFromError(
              activity.error ?? snapshot.error ?? pending.error,
            )
          : "ready";
  const pendingState = queryState(pending);
  const activityState = queryState(activity);
  const snapshotState = queryState(snapshot);
  const pendingPageState = queryState(pendingPage);

  const pendingContent = pending.data ? (
    <section
      className={`${styles.panel} ${styles.pendingPanel}`}
      aria-labelledby="dashboard-pending-title"
    >
      <div className={styles.panelHeading}>
        <div>
          <h2 id="dashboard-pending-title">我的待办</h2>
          <p>仅展示当前能力与项目范围允许处理的事项</p>
        </div>
        <div className={styles.panelHeadingActions}>
          <StatusTag
            status={pending.data.section.status}
            label={sectionLabel(pending.data.section.status)}
            known
            tone={sectionTone(pending.data.section.status)}
          />
          {pending.data.pageInfo?.hasNextPage ? (
            <Button type="link" onClick={() => setPendingOpen(true)}>
              查看全部
            </Button>
          ) : null}
        </div>
      </div>
      <DashboardSectionNotice
        section={pending.data.section}
        label="我的待办"
        onRetry={() => void pending.refetch()}
      />
      {pending.data.items.length === 0 ? (
        <div className={styles.compactState}>
          <PageState state="empty" label="我的待办" title="当前时段暂无待办" />
        </div>
      ) : (
        <DashboardPendingList items={pending.data.items} />
      )}
    </section>
  ) : null;

  const activityContent = activity.data ? (
    <DashboardActivityList
      activity={activity.data}
      onRetry={() => void activity.refetch()}
    />
  ) : null;

  const readyContent = (
    <div className={styles.contentStack}>
      {renderRegion(
        snapshotState,
        "信号轨道",
        snapshot.error,
        snapshot.data ? (
          <AssetCapacityBoard snapshot={snapshot.data} pending={pending.data} />
        ) : null,
        () => void snapshot.refetch(),
      )}
      <div className={styles.lowerGrid}>
        {renderRegion(
          pendingState,
          "我的待办",
          pending.error,
          pendingContent,
          () => void pending.refetch(),
        )}
        <aside className={styles.sideColumn} aria-label="最近活动">
          {renderRegion(
            activityState,
            "最近活动",
            activity.error,
            activityContent,
            () => void activity.refetch(),
          )}
        </aside>
      </div>
    </div>
  );

  const firstError = queries.find((query) => query.error)?.error;
  const blockingState: PageStateKind =
    pageState === "ready" ? "error" : pageState;
  const asOf = snapshot.data?.asOf ?? activity.data?.asOf ?? pending.data?.asOf;
  const refreshing = queries.some(
    (query) => query.isFetching && query.data !== undefined,
  );

  return (
    <section className={styles.page} data-page-id="P01">
      <StandardPageScaffold
        header={{
          title: "工作台",
          breadcrumbs: [{ key: "dashboard", label: "工作台" }],
          actions: (
            <>
              <label className={styles.rangeControl}>
                <Clock3 aria-hidden="true" size={16} />
                <span className={styles.srOnly}>时间范围</span>
                <Select
                  aria-label="时间范围"
                  value={search.range}
                  options={[
                    { value: "24h", label: "最近 24 小时" },
                    { value: "7d", label: "最近 7 天" },
                    { value: "30d", label: "最近 30 天" },
                    ...(search.range === "custom"
                      ? [
                          {
                            value: "custom",
                            label: "自定义时段",
                            disabled: true,
                          },
                        ]
                      : []),
                  ]}
                  onChange={(range: DashboardRange) => {
                    setSearchParams(dashboardQueryCodec.build({ range }));
                    setAnchor(new Date(Math.floor(Date.now() / 1_000) * 1_000));
                  }}
                />
              </label>
              {browserFixtureEnabled ? (
                <span className={styles.fixtureBadge}>测试数据</span>
              ) : null}
              <time
                className={styles.asOf}
                dateTime={asOf}
                title={fullDateTime(asOf)}
              >
                {formatAsOf(asOf)}
              </time>
              <Button
                icon={<RefreshCw aria-hidden="true" size={16} />}
                aria-label="刷新工作台"
                loading={refreshing}
                onClick={() => {
                  setAnchor(new Date(Math.floor(Date.now() / 1_000) * 1_000));
                  void Promise.all(queries.map((query) => query.refetch()));
                }}
              >
                刷新
              </Button>
            </>
          ),
        }}
        state={
          pageState === "ready" ? (
            readyContent
          ) : (
            <PageState
              state={blockingState}
              label="工作台"
              layout="dashboard"
              requestId={requestId(firstError)}
              onRetry={
                fatal
                  ? () =>
                      void Promise.all(queries.map((query) => query.refetch()))
                  : undefined
              }
            />
          )
        }
      />

      <DashboardPendingDrawer
        open={pendingOpen}
        page={pendingPage.data}
        state={pendingPageState}
        requestId={requestId(pendingPage.error)}
        onRetry={
          pendingPage.isError ? () => void pendingPage.refetch() : undefined
        }
        onCursorChange={setPendingCursor}
        onClose={() => setPendingOpen(false)}
      />
    </section>
  );
}

export default DashboardPage;
