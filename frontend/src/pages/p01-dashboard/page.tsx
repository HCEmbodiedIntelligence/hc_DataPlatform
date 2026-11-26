import { Button } from "antd";
import { RefreshCw } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import {
  useDashboardAnalytics,
  useDashboardTaskStatus,
} from "../../features/dashboard/api/queries";
import {
  DASHBOARD_PROJECT_TIMEZONE_ASSUMPTION,
  type DashboardScope,
  type DashboardTaskStatus,
} from "../../features/dashboard/types";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  PageState,
  StandardPageScaffold,
  type PageStateKind,
} from "../../shared/ui";
import { AssetCapacityBoard } from "./components/AssetCapacityBoard";
import { AnalyticsPanel } from "./components/AnalyticsPanel";
import { TimeRangeFilter } from "../../shared/ui/TimeRangeFilter";
import { dashboardQueryCodec, type DashboardRange } from "./query-codec";
import styles from "./styles.module.css";

const taskStageOrder = [
  "TASK_EXECUTION",
  "PACKAGE_UPLOAD",
  "RAW_RECEIPT",
  "AUTOMATIC_VALIDATION",
  "STANDARDIZATION",
  "ANNOTATION",
  "REVIEW",
  "PUBLICATION",
] as const;

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
  if (state === "ready" || state === "refreshing" || state === "partial") {
    // Keep the content at the same position while polling. Wrapping it only
    // during a refresh unmounts the board and closes its issue drawer.
    return (
      <>
        {state === "partial" ? (
          <PageState
            state="partial"
            label={label}
            description="刷新失败，当前保留上次成功返回的数据。"
            onRetry={onRetry}
          />
        ) : null}
        {content}
      </>
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
  const unscopedAccount = useShellStore(
    (state) =>
      state.bootstrapLoaded &&
      !state.authorizationFailed &&
      state.scope === null,
  );
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
  const dashboardReadGranted = capabilities.has("dashboard.read");
  const [searchParams, setSearchParams] = useSearchParams();
  const search = dashboardQueryCodec.parse(searchParams);
  const [anchor, setAnchor] = useState(
    // Match the one-minute freshness budget so a quick return can reuse data.
    // Explicit refresh still uses the exact current second below.
    () => new Date(Math.floor(Date.now() / 60_000) * 60_000),
  );
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
  const analytics = useDashboardAnalytics(scope, window, routeAllowed, search.taskId);
  const taskStatus = useDashboardTaskStatus(scope, search.taskId, routeAllowed);
  const unscopedData = useMemo(() => {
    const section = {
      status: "EMPTY" as const,
      asOf: window.to,
      error: null,
    };
    return {
      taskStatus: {
        asOf: window.to,
        section,
        tasks: [],
        pipeline: {
          taskCount: 0,
          packageCount: 0,
          qc: {
            awaitingSlice: 0,
            waiting: 0,
            passed: 0,
            risk: 0,
            rejected: 0,
            unavailable: 0,
          },
          stages: taskStageOrder.map((stage) => ({
            stage,
            notStarted: 0,
            waiting: 0,
            running: 0,
            succeeded: 0,
            risk: 0,
            isolated: 0,
            cancelled: 0,
            blocked: 0,
            failed: 0,
            unavailable: 0,
          })),
          unavailableSources: [],
        },
        selectedTaskId: null,
        selected: null,
      } satisfies DashboardTaskStatus,
    };
  }, [window.to]);
  const taskStatusData = unscopedAccount
    ? unscopedData.taskStatus
    : taskStatus.data;
  const queries = [taskStatus, analytics] as const;
  const fatal = queries.every(
    (query) => query.error && query.data === undefined,
  );
  const pageState: PageStateKind | "ready" = capabilities.loading
    ? "loading"
    : unscopedAccount
      ? "ready"
      : !scope
        ? "feature-unavailable"
        : capabilities.failed || !routeAllowed
          ? "forbidden"
          : fatal
            ? stateFromError(taskStatus.error ?? analytics.error)
            : "ready";
  const taskStatusState = unscopedAccount ? "ready" : queryState(taskStatus);
  const analyticsState = unscopedAccount ? "ready" : queryState(analytics);

  const readyContent = (
    <div className={styles.contentStack}>
      {renderRegion(
        taskStatusState,
        "信号轨道",
        taskStatus.error,
        taskStatusData ? (
          <AssetCapacityBoard
            key={`${scopeKey}:${taskStatusData.selectedTaskId ?? "all"}`}
            scope={scope ?? undefined}
            taskStatus={taskStatusData}
            onTaskChange={(taskId) =>
              setSearchParams(
                dashboardQueryCodec.build({
                  ...search,
                  taskId: taskId ?? undefined,
                }),
              )
            }
          />
        ) : null,
        () => void taskStatus.refetch(),
      )}
      {renderRegion(
        analyticsState,
        "时段统计",
        analytics.error,
        <AnalyticsPanel data={analytics.data?.analytics} window={window} />,
        () => void analytics.refetch(),
      )}
    </div>
  );

  const firstError = queries.find((query) => query.error)?.error;
  const blockingState: PageStateKind =
    pageState === "ready" ? "error" : pageState;
  const asOf = analytics.data?.as_of ?? taskStatusData?.asOf;
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
              <TimeRangeFilter
                label="工作台统计时间段"
                required
                maxDays={31}
                range={window}
                onChange={(range) => {
                  if (range)
                    setSearchParams(
                      dashboardQueryCodec.build({
                        ...search,
                        ...range,
                        range: "custom",
                      }),
                    );
                }}
              />
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
                disabled={!scope}
                loading={refreshing}
                onClick={() => {
                  const next = new Date(Math.floor(Date.now() / 1_000) * 1_000);
                  if (
                    search.range === "custom" ||
                    next.getTime() === anchor.getTime()
                  ) {
                    void Promise.all(queries.map((query) => query.refetch()));
                  } else {
                    // Changing the window starts a new analytics query.
                    setAnchor(next);
                    void taskStatus.refetch();
                  }
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
    </section>
  );
}

export default DashboardPage;
