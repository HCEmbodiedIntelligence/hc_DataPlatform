import { Button, Select } from "antd";
import type { ColumnDef } from "@tanstack/react-table";
import { RefreshCw, TrendingUp } from "lucide-react";
import { useEffect, useMemo, useState, type CSSProperties } from "react";
import {
  useCapacityPortfolio,
  useCapacityHistory,
  useCapacitySnapshot,
  type CapacityPortfolio,
  type CapacityHistory,
  type CapacityScope,
} from "../../features/storage-overview/capacity-api";
import { isDomainError } from "../../shared/api/domain-error";
import type { components } from "../../shared/api/generated/platform";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  DataTable,
  PageState,
  StatusTag,
  type PageStateKind,
} from "../../shared/ui";
import { ManagedStorageObjectsPanel } from "./components/ManagedStorageObjectsPanel";
import { CapacityInventoryPanel } from "./components/CapacityInventoryPanel";
import styles from "./styles.module.css";

type BusinessCapacityCategory =
  components["schemas"]["BusinessCapacityCategory"];
export type CapacitySnapshot = components["schemas"]["CapacitySnapshot"];

const categoryLabels: Record<BusinessCapacityCategory, string> = {
  RAW: "Raw",
  ANNOTATION_COMPLETE: "标注完成",
  PENDING_ANNOTATION: "待标注",
  ISSUE_DATA: "问题数据",
};

const categoryOrder: readonly BusinessCapacityCategory[] = [
  "RAW",
  "ANNOTATION_COMPLETE",
  "PENDING_ANNOTATION",
  "ISSUE_DATA",
];

export function formatCapacityBytes(value: string): string {
  const bytes = BigInt(value);
  if (bytes === 0n) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"] as const;
  let unit = 0;
  let divisor = 1n;
  while (unit < units.length - 1 && bytes >= divisor * 1024n) {
    divisor *= 1024n;
    unit += 1;
  }
  const tenths = (bytes * 10n + divisor / 2n) / divisor;
  const whole = tenths / 10n;
  const fraction = tenths % 10n;
  return `${new Intl.NumberFormat("zh-CN").format(whole)}${fraction ? `.${fraction}` : ""} ${units[unit]}`;
}

function formatPercent(value: string, total: string): string {
  const denominator = BigInt(total);
  if (denominator === 0n) return "0%";
  const tenths = (BigInt(value) * 1_000n + denominator / 2n) / denominator;
  return `${tenths / 10n}.${tenths % 10n}%`;
}

function stateFromError(error: unknown): PageStateKind {
  if (!isDomainError(error)) return "contract-mismatch";
  if (
    error.code === "NOT_FOUND" &&
    error.problemCode === "CAPACITY_SNAPSHOT_NOT_FOUND"
  )
    return "empty";
  switch (String(error.code)) {
    case "AUTHENTICATION_REQUIRED":
    case "CAPABILITY_REQUIRED":
    case "PROJECT_SCOPE_DENIED":
    case "SERVICE_SCOPE_REQUIRED":
    case "FORBIDDEN":
    case "UNAUTHENTICATED":
      return "forbidden";
    case "NOT_FOUND":
      return "not-found";
    case "GONE":
      return "gone";
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

function isCapacitySnapshotMissing(error: unknown): boolean {
  return (
    isDomainError(error) &&
    error.code === "NOT_FOUND" &&
    error.problemCode === "CAPACITY_SNAPSHOT_NOT_FOUND"
  );
}

export function CapacityErrorState({
  error,
  label,
  onRetry,
}: Readonly<{
  error: unknown;
  label: string;
  onRetry?: () => void;
}>) {
  const waitingForFirstInventory = isCapacitySnapshotMissing(error);
  return (
    <PageState
      state={stateFromError(error)}
      label={label}
      title={waitingForFirstInventory ? "暂无容量快照" : undefined}
      description={
        waitingForFirstInventory
          ? "等待首次存储盘点完成后，这里将显示容量事实。"
          : undefined
      }
      requestId={requestId(error)}
      onRetry={onRetry}
    />
  );
}

function requestId(error: unknown): string | null {
  return isDomainError(error) ? error.requestId : null;
}

type DonutStyle = CSSProperties &
  Readonly<{
    "--raw-end": string;
    "--complete-end": string;
    "--pending-end": string;
  }>;

function donutStyle(snapshot: CapacitySnapshot): DonutStyle {
  const total = BigInt(snapshot.candidate_business_total_bytes);
  const values = categoryOrder.map((category) =>
    BigInt(
      snapshot.categories.find((item) => item.category === category)
        ?.candidate_bytes ?? "0",
    ),
  );
  const end = (index: number) => {
    if (total === 0n) return "0%";
    const cumulative = values
      .slice(0, index + 1)
      .reduce((sum, value) => sum + value, 0n);
    return `${Number((cumulative * 10_000n) / total) / 100}%`;
  };
  return {
    "--raw-end": end(0),
    "--complete-end": end(1),
    "--pending-end": end(2),
  };
}

export function CapacityOverview({
  snapshot,
}: Readonly<{ snapshot: CapacitySnapshot }>) {
  return (
    <section
      className={styles.overviewCard}
      aria-labelledby="capacity-overview-title"
    >
      <div className={styles.cardHeading}>
        <div>
          <h3 id="capacity-overview-title">容量总览</h3>
        </div>
        <StatusTag
          status={snapshot.reconciliation.balanced ? "BALANCED" : "UNBALANCED"}
          label={snapshot.reconciliation.balanced ? "对账一致" : "对账异常"}
          tone={snapshot.reconciliation.balanced ? "success" : "danger"}
        />
      </div>

      <div className={styles.overviewMain}>
        <figure
          className={styles.capacityFigure}
          aria-labelledby="capacity-distribution-title"
        >
          <div
            className={styles.capacityDonut}
            data-empty={
              snapshot.candidate_business_total_bytes === "0" || undefined
            }
            style={donutStyle(snapshot)}
            aria-hidden="true"
          >
            <span>
              <small>业务口径</small>
              <strong>
                {formatCapacityBytes(snapshot.candidate_business_total_bytes)}
              </strong>
              <small>
                {snapshot.candidate_logical_object_count.toLocaleString(
                  "zh-CN",
                )}{" "}
                个对象
              </small>
            </span>
          </div>
          <figcaption
            id="capacity-distribution-title"
            className={styles.srOnly}
          >
            候选业务容量按
            Raw、标注完成、待标注、问题数据四类互斥归类；下方图例提供精确值。
          </figcaption>
        </figure>

        <dl className={styles.totalFacts}>
          <div>
            <dt>对象存储物理总量</dt>
            <dd>{formatCapacityBytes(snapshot.physical_total_bytes)}</dd>
            <dd className={styles.factMeta}>
              {snapshot.physical_instance_count.toLocaleString("zh-CN")}{" "}
              个物理实例
            </dd>
          </div>
          <div>
            <dt>候选业务口径总量</dt>
            <dd>
              {formatCapacityBytes(snapshot.candidate_business_total_bytes)}
            </dd>
            <dd className={styles.factMeta}>四类逻辑对象互斥</dd>
          </div>
        </dl>
      </div>

      <ul className={styles.categoryLegend} aria-label="容量四类精确值">
        {snapshot.categories.map((entry) => (
          <li key={entry.category} data-category={entry.category}>
            <span className={styles.categoryLabel}>
              <i aria-hidden="true" />
              {categoryLabels[entry.category]}
            </span>
            <strong>{formatCapacityBytes(entry.candidate_bytes)}</strong>
            <small>
              {formatPercent(
                entry.candidate_bytes,
                snapshot.candidate_business_total_bytes,
              )}{" "}
              · {entry.logical_object_count} 个
            </small>
          </li>
        ))}
      </ul>

      <div className={styles.reconciliation} aria-label="容量对账公式">
        <span>
          <strong>物理总量</strong>
          {formatCapacityBytes(snapshot.physical_total_bytes)}
        </span>
        <b aria-hidden="true">=</b>
        <span>
          <strong>业务候选</strong>
          {formatCapacityBytes(snapshot.candidate_business_total_bytes)}
        </span>
        <b aria-hidden="true">+</b>
        <span>
          <strong>副本开销</strong>
          {formatCapacityBytes(snapshot.reconciliation.replica_overhead_bytes)}
        </span>
        <b aria-hidden="true">+</b>
        <span>
          <strong>临时对象</strong>
          {formatCapacityBytes(snapshot.reconciliation.temporary_bytes)}
        </span>
        <small>
          重复盘点行已忽略{" "}
          {snapshot.reconciliation.duplicate_inventory_rows_ignored.toLocaleString(
            "zh-CN",
          )}{" "}
          条；两套总量不得相加。
        </small>
      </div>
    </section>
  );
}

function signedCapacity(value: string): string {
  const bytes = BigInt(value);
  return `${bytes > 0n ? "+" : bytes < 0n ? "−" : ""}${formatCapacityBytes(
    bytes < 0n ? (-bytes).toString() : value,
  )}`;
}

function trendHeight(value: string, largest: bigint): CSSProperties {
  if (largest === 0n) return { height: "8%" };
  const percent = Number((BigInt(value) * 100n) / largest);
  return { height: `${Math.max(percent, 8)}%` };
}

export function CapacityTrend({
  history,
  days,
  isPending,
  error,
  onRetry,
  onRangeChange,
}: Readonly<{
  history: CapacityHistory | undefined;
  days: 7 | 30;
  isPending: boolean;
  error: unknown;
  onRetry: () => void;
  onRangeChange: (days: 7 | 30) => void;
}>) {
  const largest = history?.items.reduce(
    (current, item) =>
      BigInt(item.candidate_business_total_bytes) > current
        ? BigInt(item.candidate_business_total_bytes)
        : current,
    0n,
  );
  return (
    <figure
      className={styles.trendCard}
      aria-labelledby="capacity-trend-title"
      aria-describedby="capacity-trend-summary"
    >
      <div className={styles.cardHeading}>
        <div>
          <h3 id="capacity-trend-title">增长趋势</h3>
        </div>
        <div
          className={styles.trendRange}
          role="group"
          aria-label="容量趋势范围"
        >
          <Button
            size="small"
            type={days === 7 ? "primary" : "default"}
            onClick={() => onRangeChange(7)}
          >
            近 7 天
          </Button>
          <Button
            size="small"
            type={days === 30 ? "primary" : "default"}
            onClick={() => onRangeChange(30)}
          >
            近 30 天
          </Button>
        </div>
      </div>
      {isPending ? (
        <PageState state="loading" label="容量趋势" />
      ) : error ? (
        <CapacityErrorState error={error} label="容量趋势" onRetry={onRetry} />
      ) : !history?.items.length ? (
        <PageState
          state="empty"
          label="容量趋势"
          description="所选时间范围内没有已封存的容量记录。"
        />
      ) : (
        <>
          <ol className={styles.trendPlot} aria-label={`近 ${days} 天容量记录`}>
            {history.items.map((item) => (
              <li key={item.snapshot_id} className={styles.trendColumn}>
                <strong>
                  {formatCapacityBytes(item.candidate_business_total_bytes)}
                </strong>
                <span
                  className={styles.trendBar}
                  style={trendHeight(
                    item.candidate_business_total_bytes,
                    largest ?? 0n,
                  )}
                  aria-hidden="true"
                />
                <time dateTime={item.observed_at}>
                  {new Date(item.observed_at).toLocaleDateString("zh-CN", {
                    month: "numeric",
                    day: "numeric",
                  })}
                </time>
              </li>
            ))}
          </ol>
          <figcaption
            id="capacity-trend-summary"
            className={styles.trendSummary}
          >
            <TrendingUp aria-hidden="true" size={15} />
            <span>
              {history.growth ? (
                <>
                  候选业务口径变化
                  <strong>
                    {signedCapacity(history.growth.candidate_change_bytes)}
                  </strong>
                  ，平均
                  <strong>
                    {signedCapacity(history.growth.candidate_bytes_per_day)}/日
                  </strong>
                  。
                </>
              ) : (
                "仅有一条可用记录，尚无法计算增长率。"
              )}
            </span>
          </figcaption>
        </>
      )}
    </figure>
  );
}

interface ProjectCapacityRow {
  projectId: string;
  physical: string;
  candidate: string;
  categories: Readonly<Record<BusinessCapacityCategory, string>>;
  balanced: boolean;
}

const projectColumns: readonly ColumnDef<ProjectCapacityRow, unknown>[] = [
  {
    id: "project",
    header: "项目",
    size: 142,
    cell: ({ row }) => (
      <code className={styles.projectIdentity} tabIndex={0}>
        {row.original.projectId}
      </code>
    ),
  },
  {
    id: "candidate",
    header: "业务口径",
    size: 82,
    cell: ({ row }) => formatCapacityBytes(row.original.candidate),
  },
  {
    id: "raw",
    header: "Raw",
    size: 72,
    cell: ({ row }) => formatCapacityBytes(row.original.categories.RAW),
  },
  {
    id: "complete",
    header: "标注完成",
    size: 82,
    cell: ({ row }) =>
      formatCapacityBytes(row.original.categories.ANNOTATION_COMPLETE),
  },
  {
    id: "pending",
    header: "待标注",
    size: 76,
    cell: ({ row }) =>
      formatCapacityBytes(row.original.categories.PENDING_ANNOTATION),
  },
  {
    id: "issue",
    header: "问题数据",
    size: 78,
    cell: ({ row }) => formatCapacityBytes(row.original.categories.ISSUE_DATA),
  },
  {
    id: "physical",
    header: "物理总量",
    size: 82,
    cell: ({ row }) => formatCapacityBytes(row.original.physical),
  },
  {
    id: "health",
    header: "健康",
    size: 76,
    cell: ({ row }) => (
      <StatusTag
        status={row.original.balanced ? "BALANCED" : "UNBALANCED"}
        label={row.original.balanced ? "一致" : "异常"}
        tone={row.original.balanced ? "success" : "danger"}
      />
    ),
  },
];

export function ProjectCapacityTable({
  portfolio,
  snapshot,
  projectOptions = [],
  selectedProjectIds = snapshot ? [snapshot.project_id] : [],
  onProjectsChange = () => undefined,
  loading = false,
}: Readonly<{
  portfolio?: CapacityPortfolio;
  snapshot?: CapacitySnapshot;
  projectOptions?: readonly { value: string; label: string }[];
  selectedProjectIds?: readonly string[];
  onProjectsChange?: (projectIds: string[]) => void;
  loading?: boolean;
}>) {
  const resolvedPortfolio = useMemo<CapacityPortfolio | undefined>(
    () =>
      portfolio ??
      (snapshot
        ? {
            project_ids: [snapshot.project_id],
            physical_total_bytes: snapshot.physical_total_bytes,
            candidate_business_total_bytes:
              snapshot.candidate_business_total_bytes,
            categories: snapshot.categories,
            items: [
              {
                project_id: snapshot.project_id,
                snapshot_id: snapshot.snapshot_id,
                observed_at: snapshot.observed_at,
                physical_total_bytes: snapshot.physical_total_bytes,
                candidate_business_total_bytes:
                  snapshot.candidate_business_total_bytes,
                categories: snapshot.categories,
                balanced: snapshot.reconciliation.balanced,
              },
            ],
          }
        : undefined),
    [portfolio, snapshot],
  );
  const rows = useMemo<readonly ProjectCapacityRow[]>(
    () =>
      (resolvedPortfolio?.items ?? []).map((item) => ({
        projectId: item.project_id,
        physical: item.physical_total_bytes,
        candidate: item.candidate_business_total_bytes,
        categories: Object.fromEntries(
          categoryOrder.map((category) => [
            category,
            item.categories.find((entry) => entry.category === category)
              ?.candidate_bytes ?? "0",
          ]),
        ) as Readonly<Record<BusinessCapacityCategory, string>>,
        balanced: item.balanced,
      })),
    [resolvedPortfolio?.items],
  );

  return (
    <section
      className={styles.projectCard}
      aria-labelledby="capacity-project-title"
    >
      <div className={styles.cardHeading}>
        <div>
          <h3 id="capacity-project-title">项目容量明细</h3>
          <p>
            已选 {selectedProjectIds.length} 个授权项目 · 汇总业务口径{" "}
            {resolvedPortfolio
              ? formatCapacityBytes(
                  resolvedPortfolio.candidate_business_total_bytes,
                )
              : "—"}
          </p>
        </div>
        <Select
          mode="multiple"
          size="small"
          maxTagCount="responsive"
          aria-label="选择容量汇总项目"
          value={[...selectedProjectIds]}
          options={[...projectOptions]}
          loading={loading}
          className={styles.projectSelector}
          onChange={onProjectsChange}
        />
      </div>
      {rows.length ? (
        <DataTable
          data={rows}
          columns={projectColumns}
          getRowId={(item) => item.projectId}
          caption="授权项目容量明细"
        />
      ) : (
        <PageState
          state={loading ? "loading" : "empty"}
          label="跨项目容量汇总"
          description="所选项目还没有已封存的容量记录。"
        />
      )}
    </section>
  );
}

export function CapacitySnapshotMeta({
  snapshot,
  isStale,
}: Readonly<{
  snapshot: CapacitySnapshot;
  isStale: boolean;
}>) {
  return (
    <span
      className={styles.snapshotTime}
      data-cache-state={isStale ? "stale" : "current"}
    >
      截至{" "}
      <time dateTime={snapshot.observed_at}>
        {new Date(snapshot.observed_at).toLocaleTimeString("zh-CN", {
          hour: "2-digit",
          minute: "2-digit",
        })}
      </time>
      {isStale ? <em>客户端缓存待刷新</em> : null}
    </span>
  );
}

export function CapacityPane() {
  const shellScope = useShellStore((state) => state.scope);
  const sessionScopes = useShellStore((state) => state.sessionScopes);
  const scope = shellScope?.projectId ? (shellScope as CapacityScope) : null;
  const projectId = scope?.projectId ?? null;
  const capabilities = useCapabilities();
  const canRead = capabilities.has("storage.overview.read");
  const [trendDays, setTrendDays] = useState<7 | 30>(30);
  const availableProjectIds = useMemo(() => {
    const projectIds = sessionScopes
      .filter(
        (grant) =>
          grant.organizationId === scope?.organizationId &&
          (grant.capabilities.includes("storage.overview.read") ||
            grant.capabilities.includes("project.access.manage")),
      )
      .map((grant) => grant.projectId);
    if (projectId && !projectIds.includes(projectId))
      projectIds.unshift(projectId);
    return [...new Set(projectIds)];
  }, [projectId, scope?.organizationId, sessionScopes]);
  const [selectedProjectIds, setSelectedProjectIds] = useState<
    readonly string[]
  >(() => (projectId ? [projectId] : []));
  useEffect(() => {
    if (!projectId) {
      setSelectedProjectIds([]);
      return;
    }
    setSelectedProjectIds((current) => {
      const retained = current.filter((candidate) =>
        availableProjectIds.includes(candidate),
      );
      return retained.includes(projectId) ? retained : [projectId, ...retained];
    });
  }, [availableProjectIds, projectId]);
  const [trendAnchor, setTrendAnchor] = useState(() =>
    new Date().toISOString(),
  );
  const trendWindow = useMemo(() => {
    const end = new Date(trendAnchor);
    const start = new Date(end);
    start.setUTCDate(start.getUTCDate() - trendDays);
    return { from: start.toISOString(), to: end.toISOString() };
  }, [trendAnchor, trendDays]);
  const capacity = useCapacitySnapshot(scope, canRead);
  const history = useCapacityHistory(scope, trendWindow, canRead);
  const portfolio = useCapacityPortfolio(
    scope,
    selectedProjectIds,
    canRead && selectedProjectIds.length > 0,
  );
  const pageState: PageStateKind | "ready" = capabilities.loading
    ? "loading"
    : capabilities.failed || !canRead
      ? "forbidden"
      : !projectId
        ? "feature-unavailable"
        : capacity.isPending
          ? "loading"
          : capacity.isError
            ? stateFromError(capacity.error)
            : capacity.data
              ? "ready"
              : "empty";

  const content =
    pageState === "ready" && capacity.data ? (
      <div className={styles.capacityBody}>
        <CapacityOverview snapshot={capacity.data} />
        <CapacityTrend
          history={history.data}
          days={trendDays}
          isPending={history.isPending}
          error={history.error}
          onRetry={() => void history.refetch()}
          onRangeChange={(days) => {
            setTrendDays(days);
            setTrendAnchor(new Date().toISOString());
          }}
        />
        <ProjectCapacityTable
          portfolio={portfolio.data}
          projectOptions={availableProjectIds.map((value) => ({
            value,
            label: value,
          }))}
          selectedProjectIds={selectedProjectIds}
          loading={portfolio.isFetching}
          onProjectsChange={(next) => {
            if (projectId && next.includes(projectId) && next.length > 0)
              setSelectedProjectIds(next);
          }}
        />
        <CapacityInventoryPanel
          key={capacity.data.snapshot_id}
          scope={scope!}
          snapshotId={capacity.data.snapshot_id}
          enabled={canRead}
        />
        <ManagedStorageObjectsPanel scope={scope} enabled={canRead} />
      </div>
    ) : capacity.isError ? (
      <CapacityErrorState
        error={capacity.error}
        label="容量管理"
        onRetry={() => void capacity.refetch()}
      />
    ) : (
      <PageState
        state={pageState === "ready" ? "empty" : pageState}
        label="容量管理"
      />
    );

  return (
    <section
      className={styles.capacityPane}
      aria-labelledby="capacity-pane-title"
    >
      <header className={styles.paneHeader}>
        <div>
          <h1 id="capacity-pane-title">容量管理</h1>
        </div>
        <div className={styles.capacityHeaderActions}>
          {capacity.data ? (
            <CapacitySnapshotMeta
              snapshot={capacity.data}
              isStale={capacity.isStale}
            />
          ) : null}
          <Button
            size="small"
            type="text"
            icon={<RefreshCw aria-hidden="true" size={15} />}
            aria-label="刷新容量数据"
            disabled={!canRead || !projectId}
            loading={capacity.isFetching || history.isFetching}
            onClick={() => {
              setTrendAnchor(new Date().toISOString());
              void capacity.refetch();
            }}
          />
        </div>
      </header>
      {capacity.isFetching && capacity.data ? (
        <PageState state="refreshing" label="容量数据">
          {content}
        </PageState>
      ) : (
        content
      )}
    </section>
  );
}

export function StorageOverviewPage() {
  return (
    <div className={styles.page} data-page-id="P12">
      <CapacityPane />
    </div>
  );
}

export const Component = StorageOverviewPage;
export default StorageOverviewPage;
