import { Button } from "antd";
import type { ColumnDef } from "@tanstack/react-table";
import { Database, RefreshCw, TrendingUp } from "lucide-react";
import { useMemo, type CSSProperties } from "react";
import {
  useCapacitySnapshot,
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
import { LifecyclePane } from "../p13-storage-lifecycle/page";
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
  switch (String(error.code)) {
    case "AUTHENTICATION_REQUIRED":
    case "CAPABILITY_REQUIRED":
    case "PROJECT_SCOPE_DENIED":
    case "SERVICE_SCOPE_REQUIRED":
    case "FORBIDDEN":
    case "UNAUTHENTICATED":
      return "forbidden";
    case "CAPACITY_SNAPSHOT_NOT_FOUND":
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
          <p>四类互斥业务口径 · 单位：IEC 字节</p>
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

export function CapacityTrend({
  snapshot,
}: Readonly<{ snapshot: CapacitySnapshot }>) {
  return (
    <figure
      className={styles.trendCard}
      aria-labelledby="capacity-trend-title"
      aria-describedby="capacity-trend-summary"
    >
      <div className={styles.cardHeading}>
        <div>
          <h3 id="capacity-trend-title">增长趋势</h3>
          <p>近 30 天 · 单位：IEC 字节</p>
        </div>
        <span className={styles.unavailableBadge}>历史序列未开放</span>
      </div>
      <div className={styles.trendPlot} aria-hidden="true">
        <span className={styles.trendStart}>30 天前</span>
        <span className={styles.trendLine} />
        <span className={styles.trendPoint} />
        <span className={styles.trendEnd}>当前快照</span>
      </div>
      <figcaption id="capacity-trend-summary" className={styles.trendSummary}>
        <TrendingUp aria-hidden="true" size={15} />
        <span>
          正式 API 仅返回当前快照，不能计算增长率。当前业务口径为
          <strong>
            {" "}
            {formatCapacityBytes(snapshot.candidate_business_total_bytes)}
          </strong>
          ， 截至{" "}
          <time dateTime={snapshot.observed_at}>
            {new Date(snapshot.observed_at).toLocaleString("zh-CN")}
          </time>
          。
        </span>
      </figcaption>
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
  snapshot,
}: Readonly<{ snapshot: CapacitySnapshot }>) {
  const row = useMemo<ProjectCapacityRow>(
    () => ({
      projectId: snapshot.project_id,
      physical: snapshot.physical_total_bytes,
      candidate: snapshot.candidate_business_total_bytes,
      categories: Object.fromEntries(
        snapshot.categories.map((entry) => [
          entry.category,
          entry.candidate_bytes,
        ]),
      ) as Readonly<Record<BusinessCapacityCategory, string>>,
      balanced: snapshot.reconciliation.balanced,
    }),
    [snapshot],
  );

  return (
    <section
      className={styles.projectCard}
      aria-labelledby="capacity-project-title"
    >
      <div className={styles.cardHeading}>
        <div>
          <h3 id="capacity-project-title">项目容量明细</h3>
          <p>当前授权项目 · 表格内横向滚动</p>
        </div>
        <span className={styles.unavailableBadge}>跨项目汇总未开放</span>
      </div>
      <DataTable
        data={[row]}
        columns={projectColumns}
        getRowId={(item) => item.projectId}
        caption="当前项目容量明细"
      />
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
  const scope = shellScope?.projectId ? (shellScope as CapacityScope) : null;
  const projectId = scope?.projectId ?? null;
  const capabilities = useCapabilities();
  const canRead = capabilities.has("storage.overview.read");
  const capacity = useCapacitySnapshot(scope, canRead);
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
        <CapacityTrend snapshot={capacity.data} />
        <ProjectCapacityTable snapshot={capacity.data} />
      </div>
    ) : (
      <PageState
        state={pageState === "ready" ? "empty" : pageState}
        label="容量管理"
        requestId={requestId(capacity.error)}
        onRetry={capacity.isError ? () => void capacity.refetch() : undefined}
      />
    );

  return (
    <section
      className={styles.capacityPane}
      aria-labelledby="capacity-pane-title"
    >
      <header className={styles.paneHeader}>
        <div>
          <h2 id="capacity-pane-title">容量管理</h2>
          <p>按业务状态核对所选项目的对象存储容量</p>
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
            aria-label="刷新容量快照"
            disabled={!canRead || !projectId}
            loading={capacity.isFetching}
            onClick={() => void capacity.refetch()}
          />
        </div>
      </header>
      {capacity.isFetching && capacity.data ? (
        <PageState state="refreshing" label="容量快照">
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
    <div className={styles.page} data-page-id="P12-P13">
      <h1 className={styles.srOnly}>容量与生命周期治理</h1>
      <div className={styles.governanceGrid}>
        <CapacityPane />
        <LifecyclePane />
      </div>
      <p className={styles.pageFootnote}>
        <Database aria-hidden="true" size={13} />
        页面只消费当前作用域的正式存储合同；真实 API 失败会保留错误状态，不回退
        Browser Mock。
      </p>
    </div>
  );
}

export const Component = StorageOverviewPage;
export default StorageOverviewPage;
