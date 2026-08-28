import {
  Alert,
  Avatar,
  Button,
  Collapse,
  Descriptions,
  Dropdown,
  Form,
  Grid,
  Input,
  Modal,
  Select,
  Space,
  Tooltip,
  Typography,
  type MenuProps,
} from "antd";
import type { ColumnDef } from "@tanstack/react-table";
import {
  CheckCircle2,
  CircleDotDashed,
  ListChecks,
  LocateFixed,
  MoreHorizontal,
  Wrench,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { isDatasetId } from "../../entities/dataset";
import { isDatasetVersionId } from "../../entities/dataset-version";
import { isEpisodeId } from "../../entities/episode";
import type {
  ManualIssue,
  ManualIssueDiscoverySource,
  ManualIssueListItem,
  ManualIssueSeverity,
  ManualIssueType,
} from "../../entities/manual-issue";
import {
  type AutoQualityProblem,
  useAutoQualityProblems,
  useCreateDraftFromManualIssue,
  useManualIssue,
  useManualIssues,
  useManualIssuesPage,
  useResolveManualIssue,
  useTriageManualIssue,
} from "../../features/cleaning/api";
import {
  manualIssuesQueryCodec,
  routes as cleaningRoutes,
} from "../../features/cleaning/routing";
import { routes as datasetRoutes } from "../../features/datasets/routing";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import {
  formatEffectiveDuration,
  formatTimeRange,
} from "../../shared/lib/metric-presentation";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  DataCursorPager,
  DataTable,
  EntityDrawer,
  FilterToolbar,
  PageState,
  StandardPageScaffold,
  StatusTag,
  type PageStateKind,
} from "../../shared/ui";
import styles from "./styles.module.css";

type DialogState =
  | { readonly kind: "triage"; readonly issue: ManualIssueListItem }
  | { readonly kind: "resolve"; readonly issue: ManualIssueListItem }
  | null;

type WorkView = "untriaged" | "working" | "resolved" | "all";

type ProblemDataRow = {
  readonly kind: "manual" | "auto";
  readonly id: string;
  readonly issueType: ManualIssueType;
  readonly severity: ManualIssueSeverity;
  readonly status: ManualIssueListItem["status"];
  readonly assignee: ManualIssueListItem["assignee"];
  readonly startNs: string;
  readonly endNs: string;
  readonly updatedAt: string;
  readonly discoverySource: ManualIssueDiscoverySource | "AUTO_QC";
  readonly sourceTitle: string;
  readonly sourceSubtitle: string;
  readonly manualIssue: ManualIssueListItem | null;
  readonly autoProblem: AutoQualityProblem | null;
};

const ISSUE_TYPE_LABELS: Readonly<
  Record<ManualIssueListItem["issueType"], string>
> = {
  POSE_JITTER: "姿态抖动",
  TIMESTAMP_DRIFT: "时间偏移",
  MISSING_FRAME: "画面缺帧",
  STREAM_GAP: "数据流中断",
  CALIBRATION_MISMATCH: "标定不匹配",
  INVALID_MASK: "无效区间",
  OTHER: "其他问题",
};

const SEVERITY_LABELS: Readonly<Record<ManualIssueSeverity, string>> = {
  CRITICAL: "紧急",
  HIGH: "高",
  MEDIUM: "中",
  LOW: "低",
};

const DISCOVERY_SOURCE_LABELS: Readonly<
  Record<ProblemDataRow["discoverySource"], string>
> = {
  AUTO_QC: "自动质检",
  DATA_VIEWER: "数据查看",
  ANNOTATOR: "标注员上报",
  REVIEWER: "审核员上报",
};

function issueTypeFromQuality(problem: AutoQualityProblem): ManualIssueType {
  const codes = problem.findingCodes.join(" ");
  if (/MISSING|REQUIRED_TOPIC/u.test(codes)) return "MISSING_FRAME";
  if (/GAP/u.test(codes)) return "STREAM_GAP";
  if (/TIMESTAMP|FREQUENCY|COVERAGE|OFFSET|STEP_RATIO/u.test(codes))
    return "TIMESTAMP_DRIFT";
  return "OTHER";
}

function manualProblemRow(issue: ManualIssueListItem): ProblemDataRow {
  return {
    kind: "manual",
    id: issue.id,
    issueType: issue.issueType,
    severity: issue.severity,
    status: issue.status,
    assignee: issue.assignee,
    startNs: issue.source.startNs,
    endNs: issue.source.endNs,
    updatedAt: issue.updatedAt,
    discoverySource: issue.discoverySource,
    sourceTitle: `采集条目 ${issue.source.episodeId}`,
    sourceSubtitle: `数据集 ${issue.source.datasetId}`,
    manualIssue: issue,
    autoProblem: null,
  };
}

function autoProblemRow(problem: AutoQualityProblem): ProblemDataRow {
  return {
    kind: "auto",
    id: problem.id,
    issueType: issueTypeFromQuality(problem),
    severity: problem.severity,
    status: { kind: "known", value: "OPEN" },
    assignee: null,
    startNs: problem.startNs,
    endNs: problem.endNs,
    updatedAt: problem.updatedAt,
    discoverySource: "AUTO_QC",
    sourceTitle: problem.dataPackageId
      ? `采集包 ${problem.dataPackageId}`
      : `Rollout ${problem.rolloutId}`,
    sourceSubtitle: `${problem.status} · ${problem.findingCount} 项质检发现`,
    manualIssue: null,
    autoProblem: problem,
  };
}

function statusLabel(issue: Pick<ManualIssueListItem, "status">): string {
  if (issue.status.kind === "unknown") return "新状态（只读）";
  if (issue.status.value === "OPEN") return "待分诊";
  if (issue.status.value === "IN_PROGRESS") return "处理中";
  return "已完成";
}

function severityTone(
  severity: ManualIssueSeverity,
): "neutral" | "danger" | "warning" {
  if (severity === "CRITICAL") return "danger";
  if (severity === "HIGH") return "warning";
  return "neutral";
}

function formatNanoseconds(value: string): string {
  try {
    const totalMs = BigInt(value) / 1_000_000n;
    const hours = totalMs / 3_600_000n;
    const minutes = (totalMs % 3_600_000n) / 60_000n;
    const seconds = (totalMs % 60_000n) / 1_000n;
    const milliseconds = totalMs % 1_000n;
    const prefix = hours > 0n ? `${hours.toString().padStart(2, "0")}:` : "";
    return `${prefix}${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}.${milliseconds.toString().padStart(3, "0")}`;
  } catch {
    return value;
  }
}

function formatDuration(startNs: string, endNs: string): string {
  try {
    return formatEffectiveDuration(BigInt(endNs) - BigInt(startNs));
  } catch {
    return "时长未知";
  }
}

function formatUpdatedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function filteredAutoProblems(
  problems: readonly AutoQualityProblem[],
  search: ReturnType<typeof manualIssuesQueryCodec.parse>,
  options: { readonly includeCursorPage: boolean },
): readonly AutoQualityProblem[] {
  if (!options.includeCursorPage && (search.after || search.before)) return [];
  if (search.datasetId || search.versionId || search.episodeId) return [];
  if (search.assigneeId) return [];
  if (
    search.discoverySource?.length &&
    !search.discoverySource.includes("AUTO_QC")
  )
    return [];
  if (search.status?.length && !search.status.includes("OPEN")) return [];
  return problems.filter((problem) => {
    const issueType = issueTypeFromQuality(problem);
    if (search.issueType?.length && !search.issueType.includes(issueType))
      return false;
    if (search.severity?.length && !search.severity.includes(problem.severity))
      return false;
    if (search.q) {
      const haystack = [
        problem.dataPackageId,
        problem.rolloutId,
        problem.message,
        ...problem.findingCodes,
        ...problem.topics,
      ]
        .filter(Boolean)
        .join(" ")
        .toLocaleLowerCase("zh-CN");
      if (!haystack.includes(search.q.toLocaleLowerCase("zh-CN"))) return false;
    }
    return true;
  });
}

function severityRank(value: ManualIssueSeverity): number {
  return { CRITICAL: 4, HIGH: 3, MEDIUM: 2, LOW: 1 }[value];
}

function sortProblemRows(
  rows: readonly ProblemDataRow[],
  sort: ReturnType<typeof manualIssuesQueryCodec.parse>["sort"],
): readonly ProblemDataRow[] {
  return [...rows].sort((left, right) => {
    if (sort === "severityDesc") {
      const severity =
        severityRank(right.severity) - severityRank(left.severity);
      if (severity) return severity;
    }
    const time =
      new Date(left.updatedAt).getTime() - new Date(right.updatedAt).getTime();
    if (time) return sort === "updatedAtAsc" ? time : -time;
    return left.id.localeCompare(right.id, "en");
  });
}

function autoProblemHref(
  problem: AutoQualityProblem,
  returnTo: string,
): string | null {
  if (!problem.sessionId) return null;
  return cleaningRoutes.manualIssueRawDiagnostic.build({
    uploadId: problem.sessionId,
    returnTo,
  });
}

function activeWorkView(
  search: ReturnType<typeof manualIssuesQueryCodec.parse>,
): WorkView | null {
  const statuses = search.status ?? [];
  if (!search.assigneeId && statuses.length === 1 && statuses[0] === "OPEN")
    return "untriaged";
  if (
    !search.assigneeId &&
    statuses.length === 1 &&
    statuses[0] === "IN_PROGRESS"
  )
    return "working";
  if (!search.assigneeId && statuses.length === 1 && statuses[0] === "RESOLVED")
    return "resolved";
  if (!search.assigneeId && statuses.length === 0) return "all";
  return null;
}

function mutationKey(): string {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `manual-issue-${Date.now().toString(36)}`
  );
}

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

function statusTone(
  issue: Pick<ManualIssueListItem, "status">,
): "neutral" | "info" | "success" | "warning" {
  if (issue.status.kind === "unknown") return "warning";
  if (issue.status.value === "RESOLVED") return "success";
  if (issue.status.value === "IN_PROGRESS") return "info";
  return "neutral";
}

function viewerHref(
  issue: Pick<ManualIssueListItem, "source">,
  returnTo: string,
): string | null {
  const source = issue.source;
  if (
    !isDatasetId(source.datasetId) ||
    !isDatasetVersionId(source.versionId) ||
    !isEpisodeId(source.episodeId)
  ) {
    return null;
  }
  return datasetRoutes.episodeViewer.build({
    datasetId: source.datasetId,
    versionId: source.versionId,
    episodeId: source.episodeId,
    returnTo,
  });
}

function IssueDetail({
  issue,
  returnTo,
}: Readonly<{ issue: ManualIssue; returnTo: string }>) {
  const href = viewerHref(issue, returnTo);
  return (
    <Space
      orientation="vertical"
      size="middle"
      className={styles.drawerContent}
    >
      <section className={styles.detailSummary} aria-label="问题摘要">
        <div>
          <Typography.Text type="secondary">
            {ISSUE_TYPE_LABELS[issue.issueType]}
          </Typography.Text>
          <Typography.Title level={3}>
            {issue.note || ISSUE_TYPE_LABELS[issue.issueType]}
          </Typography.Title>
        </div>
        <Space wrap size="small">
          <StatusTag
            status={statusLabel(issue)}
            tone={statusTone(issue)}
            known={issue.status.kind === "known"}
          />
          <StatusTag
            status={`严重程度：${SEVERITY_LABELS[issue.severity]}`}
            tone={severityTone(issue.severity)}
          />
          <StatusTag
            status={DISCOVERY_SOURCE_LABELS[issue.discoverySource]}
            tone="info"
          />
        </Space>
      </section>

      <section className={styles.evidenceCard} aria-label="问题证据">
        <div className={styles.evidenceIcon}>
          <LocateFixed aria-hidden="true" size={20} />
        </div>
        <div>
          <strong>已绑定到具体数据段</strong>
          <span>
            {formatNanoseconds(issue.source.startNs)}–
            {formatNanoseconds(issue.source.endNs)}
            {" · "}
            {formatDuration(issue.source.startNs, issue.source.endNs)}
          </span>
          <small>
            当前接口不返回安全缩略图，请到数据工作台查看相机画面、曲线和上下文。
          </small>
        </div>
        {href ? (
          <Button type="primary" href={href}>
            在工作台定位
          </Button>
        ) : (
          <Typography.Text type="warning">来源定位信息不可用</Typography.Text>
        )}
      </section>

      <Descriptions
        className={styles.businessFacts}
        bordered
        column={1}
        size="small"
      >
        <Descriptions.Item label="数据位置">
          数据集{" "}
          <Typography.Text code>{issue.source.datasetId}</Typography.Text>
          {" · "}采集条目{" "}
          <Typography.Text code>{issue.source.episodeId}</Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="处理人">
          {issue.assignee?.displayName ?? "尚未分派"}
        </Descriptions.Item>
        <Descriptions.Item label="最近更新">
          <time dateTime={issue.updatedAt}>
            {formatUpdatedAt(issue.updatedAt)}
          </time>
        </Descriptions.Item>
        <Descriptions.Item label="关联清洗任务">
          {issue.relatedDrafts.length
            ? issue.relatedDrafts.map((draft) => (
                <Link
                  key={draft.draftId}
                  to={cleaningRoutes.cleaningWorkbench.build({
                    draftId: draft.draftId,
                  })}
                >
                  {draft.status === "EDITING" ? "继续清洗" : "查看清洗结果"}
                </Link>
              ))
            : "尚未创建清洗任务"}
        </Descriptions.Item>
      </Descriptions>

      {issue.blockedReasons.map((reason) => (
        <Alert
          key={reason.code}
          type="warning"
          showIcon
          title={reason.code}
          description={reason.message}
        />
      ))}

      <Collapse
        ghost
        className={styles.technicalDetails}
        items={[
          {
            key: "technical",
            label: "技术详情",
            children: (
              <Descriptions bordered column={1} size="small">
                <Descriptions.Item label="问题 ID">
                  <Typography.Text code copyable>
                    {issue.id}
                  </Typography.Text>
                </Descriptions.Item>
                <Descriptions.Item label="数据版本 / 修订 / 通道">
                  <Typography.Text code>
                    {issue.source.versionId}
                  </Typography.Text>{" "}
                  /{" "}
                  <Typography.Text code>
                    {issue.source.revisionId}
                  </Typography.Text>{" "}
                  /{" "}
                  <Typography.Text code>
                    {issue.source.streamId}
                  </Typography.Text>
                </Descriptions.Item>
                <Descriptions.Item label="精确范围">
                  {formatTimeRange(issue.source.startNs, issue.source.endNs)}
                </Descriptions.Item>
                {issue.annotationTaskId ? (
                  <Descriptions.Item label="发现于标注任务">
                    <Typography.Text code copyable>
                      {issue.annotationTaskId}
                    </Typography.Text>
                  </Descriptions.Item>
                ) : null}
              </Descriptions>
            ),
          },
        ]}
      />
    </Space>
  );
}

export function ManualIssuesPage() {
  const screens = Grid.useBreakpoint();
  const desktopInspector = Boolean(screens.xxl);
  const capabilities = useCapabilities();
  const unscopedAccount = useShellStore(
    (state) =>
      state.bootstrapLoaded &&
      !state.authorizationFailed &&
      state.scope === null,
  );
  const canRead =
    !capabilities.loading &&
    !capabilities.failed &&
    capabilities.has("manual_issue.read");
  const [params, setParams] = useSearchParams();
  const search = manualIssuesQueryCodec.parse(params);
  const summarySearch = useMemo(
    () =>
      manualIssuesQueryCodec.parse(
        manualIssuesQueryCodec.build({
          ...search,
          status: undefined,
          assigneeId: undefined,
          after: undefined,
          before: undefined,
        }),
      ),
    [search],
  );
  const manualSourceAllowed =
    !search.discoverySource?.length ||
    search.discoverySource.some((source) => source !== "AUTO_QC");
  const manualSummarySourceAllowed =
    !summarySearch.discoverySource?.length ||
    summarySearch.discoverySource.some((source) => source !== "AUTO_QC");
  const autoSourceAllowed =
    !search.discoverySource?.length ||
    search.discoverySource.includes("AUTO_QC");
  const list = useManualIssues(search, canRead && manualSourceAllowed);
  const summary = useManualIssuesPage(
    summarySearch,
    canRead && manualSummarySourceAllowed,
  );
  const autoProblems = useAutoQualityProblems(canRead && autoSourceAllowed);
  const detail = useManualIssue(search.issueId, canRead);
  const navigate = useNavigate();
  const triage = useTriageManualIssue();
  const resolve = useResolveManualIssue();
  const createDraft = useCreateDraftFromManualIssue();
  const [dialog, setDialog] = useState<DialogState>(null);
  const [reason, setReason] = useState("");
  const [assigneeId, setAssigneeId] = useState("");
  const [severity, setSeverity] = useState<ManualIssueSeverity>("MEDIUM");
  const [resolutionVersionId, setResolutionVersionId] = useState("");
  const [resolutionNote, setResolutionNote] = useState("");
  const [selection, setSelection] = useState<Awaited<
    ReturnType<typeof createDraft.mutateAsync>
  > | null>(null);

  const filtered = Boolean(
    search.q ||
      search.datasetId ||
      search.versionId ||
      search.episodeId ||
      search.status?.length ||
      search.issueType?.length ||
      search.severity?.length ||
      search.discoverySource?.length ||
      search.assigneeId,
  );
  const currentReturn = cleaningRoutes.manualIssues.build(search);
  const changing =
    triage.isPending || resolve.isPending || createDraft.isPending;
  const visibleAutoProblems = useMemo(
    () =>
      filteredAutoProblems(autoProblems.data?.items ?? [], search, {
        includeCursorPage: false,
      }),
    [autoProblems.data?.items, search],
  );
  const summaryAutoProblems = useMemo(
    () =>
      filteredAutoProblems(autoProblems.data?.items ?? [], summarySearch, {
        includeCursorPage: true,
      }),
    [autoProblems.data?.items, summarySearch],
  );
  const rows = useMemo(
    () =>
      sortProblemRows(
        [
          ...(list.data?.items ?? []).map(manualProblemRow),
          ...visibleAutoProblems.map(autoProblemRow),
        ],
        search.sort,
      ),
    [list.data?.items, search.sort, visibleAutoProblems],
  );
  const currentView = activeWorkView(search);

  const countWithAutomatic = (
    value: string | undefined,
    automaticCount: number,
  ): string | number | undefined => {
    if (
      value === undefined &&
      !unscopedAccount &&
      ((manualSummarySourceAllowed && summary.isPending) ||
        (autoSourceAllowed && autoProblems.isPending))
    )
      return undefined;
    try {
      return (BigInt(value ?? 0) + BigInt(automaticCount)).toString();
    } catch {
      return automaticCount;
    }
  };

  const update = (
    changes: Parameters<typeof manualIssuesQueryCodec.withChanges>[1],
  ) => {
    setParams(
      manualIssuesQueryCodec.build(
        manualIssuesQueryCodec.withChanges(search, changes),
      ),
    );
  };

  const resetFilters = () => {
    setParams(manualIssuesQueryCodec.build({ returnTo: search.returnTo }));
  };

  const selectWorkView = (view: WorkView) => {
    if (view === "untriaged") {
      update({ status: ["OPEN"], assigneeId: undefined });
      return;
    }
    if (view === "working") {
      update({ status: ["IN_PROGRESS"], assigneeId: undefined });
      return;
    }
    if (view === "resolved") {
      update({ status: ["RESOLVED"], assigneeId: undefined });
      return;
    }
    update({ status: undefined, assigneeId: undefined });
  };

  useEffect(() => {
    if (!desktopInspector || !search.issueId) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setParams((currentParams) => {
        const current = manualIssuesQueryCodec.parse(currentParams);
        return manualIssuesQueryCodec.build(
          manualIssuesQueryCodec.withChanges(current, { issueId: undefined }),
        );
      });
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [desktopInspector, search.issueId, setParams]);

  const performCreateDraft = (issue: ManualIssueListItem) => {
    createDraft.mutate(
      {
        manualIssueId: issue.id,
        expectedVersion: issue.etag,
        idempotencyKey: mutationKey(),
      },
      {
        onSuccess(result) {
          if (result.disposition === "SELECTION_REQUIRED") {
            setSelection(result);
            return;
          }
          setSelection(null);
          void navigate(
            cleaningRoutes.cleaningWorkbench.build({ draftId: result.draftId }),
          );
        },
      },
    );
  };

  const columns: ColumnDef<ProblemDataRow, unknown>[] = [
    {
      id: "issue",
      header: "问题摘要",
      size: 190,
      cell: ({ row }) => {
        const href = row.original.autoProblem
          ? autoProblemHref(row.original.autoProblem, currentReturn)
          : null;
        return (
          <Button
            className={styles.issueButton}
            type="link"
            {...(row.original.manualIssue
              ? { onClick: () => update({ issueId: row.original.id }) }
              : href
                ? { href }
                : { disabled: true })}
          >
            <strong>{ISSUE_TYPE_LABELS[row.original.issueType]}</strong>
            <small>
              {row.original.autoProblem?.message ?? "查看问题证据与处置记录"}
            </small>
          </Button>
        );
      },
    },
    {
      id: "source",
      header: "数据位置",
      size: 175,
      cell: ({ row }) => (
        <div className={styles.locationCell}>
          <Tooltip title={row.original.sourceTitle}>
            <strong>{row.original.sourceTitle}</strong>
          </Tooltip>
          <Tooltip title={row.original.sourceSubtitle}>
            <small>{row.original.sourceSubtitle}</small>
          </Tooltip>
        </div>
      ),
    },
    {
      id: "range",
      header: "问题时间段",
      size: 165,
      cell: ({ row }) => (
        <div className={styles.rangeCell}>
          <strong>
            {formatNanoseconds(row.original.startNs)}–
            {formatNanoseconds(row.original.endNs)}
          </strong>
          <small>
            {formatDuration(row.original.startNs, row.original.endNs)}
          </small>
        </div>
      ),
    },
    {
      id: "state",
      header: "优先级与进度",
      size: 155,
      cell: ({ row }) => (
        <Space orientation="vertical" size={4} className={styles.stateCell}>
          <StatusTag
            status={statusLabel(row.original)}
            tone={statusTone(row.original)}
            known={row.original.status.kind === "known"}
          />
          <StatusTag
            status={`严重程度：${SEVERITY_LABELS[row.original.severity]}`}
            tone={severityTone(row.original.severity)}
          />
          <StatusTag
            status={DISCOVERY_SOURCE_LABELS[row.original.discoverySource]}
            tone={row.original.kind === "auto" ? "warning" : "info"}
          />
        </Space>
      ),
    },
    {
      id: "assignee",
      header: "处理人",
      size: 140,
      meta: { responsive: ["xl"] },
      cell: ({ row }) =>
        row.original.assignee ? (
          <div className={styles.assigneeCell}>
            <Avatar size={28}>
              {row.original.assignee.displayName.trim().slice(0, 1)}
            </Avatar>
            <span>{row.original.assignee.displayName}</span>
          </div>
        ) : (
          <Typography.Text type="secondary">尚未分派</Typography.Text>
        ),
    },
    {
      id: "updatedAt",
      header: "最近更新",
      size: 120,
      meta: { responsive: ["xxl"] },
      cell: ({ row }) => (
        <time dateTime={row.original.updatedAt}>
          {formatUpdatedAt(row.original.updatedAt)}
        </time>
      ),
    },
    {
      id: "actions",
      header: "下一步",
      size: 172,
      cell: ({ row }) => {
        if (row.original.autoProblem) {
          const href = autoProblemHref(row.original.autoProblem, currentReturn);
          return href ? (
            <Button
              type="primary"
              href={href}
              icon={<LocateFixed aria-hidden="true" size={16} />}
            >
              查看 Raw 诊断
            </Button>
          ) : (
            <Tooltip title="该历史质检记录未关联可定位的上传会话。">
              <Button disabled>Raw 入口不可用</Button>
            </Tooltip>
          );
        }
        const issue = row.original.manualIssue;
        if (!issue) return null;
        const known = issue.status.kind === "known";
        const mutable = known && issue.status.value !== "RESOLVED";
        const href = viewerHref(issue, currentReturn);
        const canTriage =
          mutable &&
          capabilities.has("manual_issue.triage") &&
          issue.allowedActions.includes("TRIAGE") &&
          !list.isFetching;
        const canResolve =
          mutable &&
          capabilities.has("manual_issue.resolve") &&
          issue.allowedActions.includes("RESOLVE") &&
          !list.isFetching;
        const canDraft =
          mutable &&
          capabilities.has("cleaning.create") &&
          (issue.allowedActions.includes("CREATE_DRAFT") ||
            issue.allowedActions.includes("CONTINUE_DRAFT")) &&
          !list.isFetching;
        const menuItems: MenuProps["items"] = [
          {
            key: "triage",
            label:
              issue.status.kind === "known" &&
              issue.status.value === "IN_PROGRESS"
                ? "调整分派"
                : "分诊与分派",
            disabled: !canTriage || changing,
          },
          {
            key: "draft",
            label: issue.allowedActions.includes("CONTINUE_DRAFT")
              ? "继续清洗任务"
              : "创建清洗任务",
            disabled: !canDraft || changing,
          },
          { type: "divider" },
          {
            key: "resolve",
            label: "标记已完成",
            danger: true,
            disabled: !canResolve || changing,
          },
        ];

        const onMenuClick: MenuProps["onClick"] = ({ key }) => {
          if (key === "triage") {
            setDialog({ kind: "triage", issue });
            setSeverity(issue.severity);
            setAssigneeId(issue.assignee?.id ?? "");
            setReason("");
          } else if (key === "draft") {
            performCreateDraft(issue);
          } else if (key === "resolve") {
            setDialog({ kind: "resolve", issue });
            setResolutionVersionId("");
            setResolutionNote("");
          }
        };

        return (
          <Space size="small" className={styles.rowActions}>
            {issue.allowedActions.includes("CONTINUE_DRAFT") && canDraft ? (
              <Button
                type="primary"
                onClick={() => performCreateDraft(issue)}
                icon={<Wrench aria-hidden="true" size={16} />}
              >
                继续处理
              </Button>
            ) : href ? (
              <Button
                type="primary"
                href={href}
                icon={<LocateFixed aria-hidden="true" size={16} />}
              >
                定位处理
              </Button>
            ) : (
              <Button disabled>无法定位</Button>
            )}
            <Dropdown
              menu={{ items: menuItems, onClick: onMenuClick }}
              trigger={["click"]}
            >
              <Button
                className={styles.moreButton}
                aria-label={`更多操作：${ISSUE_TYPE_LABELS[issue.issueType]}`}
                icon={<MoreHorizontal aria-hidden="true" size={18} />}
              />
            </Dropdown>
          </Space>
        );
      },
    },
  ];
  const tableColumns =
    search.issueId && desktopInspector
      ? columns.filter(
          (column) => column.id !== "assignee" && column.id !== "updatedAt",
        )
      : columns;

  const dataPending =
    (manualSourceAllowed && list.isPending) ||
    (autoSourceAllowed && autoProblems.isPending);
  const dataError =
    (manualSourceAllowed ? list.error : null) ??
    (autoSourceAllowed ? autoProblems.error : null);
  const summaryError =
    (manualSummarySourceAllowed ? summary.error : null) ??
    (autoSourceAllowed ? autoProblems.error : null);
  const dataFetching =
    (manualSourceAllowed && list.isFetching) ||
    (autoSourceAllowed && autoProblems.isFetching);

  const listState: PageStateKind | "ready" = unscopedAccount
    ? "ready"
    : capabilities.loading
      ? "loading"
      : !canRead
        ? "forbidden"
        : dataPending
          ? "loading"
          : dataError
            ? stateFromError(dataError)
            : rows.length === 0
              ? filtered
                ? "filtered-empty"
                : "empty"
              : dataFetching
                ? "refreshing"
                : "ready";

  const table = (
    <DataTable
      data={rows}
      columns={tableColumns}
      getRowId={(issue) => issue.id}
      caption="统一问题数据：自动质检发现与人工上报"
    />
  );
  const emptyTitles: Readonly<Record<WorkView, string>> = {
    untriaged: "当前没有待分诊的问题",
    working: "当前没有正在处理的问题",
    resolved: "当前还没有已完成的问题",
    all: "当前范围没有问题数据",
  };
  const emptyTitle = currentView
    ? emptyTitles[currentView]
    : "当前筛选没有匹配的问题";
  const content =
    listState === "ready" ? (
      table
    ) : listState === "refreshing" ? (
      <PageState state="refreshing" label="问题数据列表">
        {table}
      </PageState>
    ) : (
      <PageState
        state={listState}
        label="问题数据列表"
        title={
          listState === "empty" || listState === "filtered-empty"
            ? emptyTitle
            : undefined
        }
        description={
          listState === "empty"
            ? "自动质检发现与标注、审核、数据查看中的人工上报都会进入这里，等待分诊、清洗和闭环。"
            : listState === "filtered-empty"
              ? "可以切换工作视图，或清除筛选条件后查看其他问题。"
              : undefined
        }
        requestId={requestId(dataError)}
        onRetry={
          dataError
            ? () => {
                if (manualSourceAllowed) void list.refetch();
                if (autoSourceAllowed) void autoProblems.refetch();
              }
            : undefined
        }
        action={
          listState === "filtered-empty" ? (
            <Button onClick={resetFilters}>清除筛选</Button>
          ) : undefined
        }
      />
    );

  return (
    <main className={styles.page} data-page-id="P09">
      <StandardPageScaffold
        header={{
          title: "问题数据",
          description:
            "统一处理自动质检发现与人工上报的问题：定位证据、分诊、清洗并完成闭环。",
          breadcrumbs: [
            { key: "production", label: "数据生产" },
            { key: "issues", label: "问题数据" },
          ],
          actions: (
            <Link
              className={styles.headerAction}
              to={cleaningRoutes.cleaningDrafts.build({})}
            >
              <Wrench aria-hidden="true" size={16} />
              清洗任务
            </Link>
          ),
        }}
        summary={
          summaryError ? (
            <PageState
              state={stateFromError(summaryError)}
              label="问题统计"
              requestId={requestId(summaryError)}
              onRetry={() => {
                if (manualSummarySourceAllowed) void summary.refetch();
                if (autoSourceAllowed) void autoProblems.refetch();
              }}
            />
          ) : (
            <div className={styles.summaryStack}>
              <nav className={styles.workViews} aria-label="问题工作视图">
                {(
                  [
                    {
                      key: "untriaged",
                      label: "待分诊",
                      description: "等待确认与分派",
                      count: countWithAutomatic(
                        summary.data?.counts.open,
                        summaryAutoProblems.length,
                      ),
                      icon: CircleDotDashed,
                    },
                    {
                      key: "working",
                      label: "处理中",
                      description: "清洗或补充证据",
                      count: countWithAutomatic(
                        summary.data?.counts.inProgress,
                        0,
                      ),
                      icon: Wrench,
                    },
                    {
                      key: "resolved",
                      label: "已完成",
                      description: "已关联解决结果",
                      count: countWithAutomatic(
                        summary.data?.counts.resolved,
                        0,
                      ),
                      icon: CheckCircle2,
                    },
                    {
                      key: "all",
                      label: "全部问题",
                      description: "完整问题记录",
                      count: countWithAutomatic(
                        summary.data?.counts.total,
                        summaryAutoProblems.length,
                      ),
                      icon: ListChecks,
                    },
                  ] as const
                ).map((view) => {
                  const Icon = view.icon;
                  return (
                    <button
                      key={view.key}
                      type="button"
                      className={styles.workView}
                      data-active={currentView === view.key || undefined}
                      aria-pressed={currentView === view.key}
                      onClick={() => selectWorkView(view.key)}
                    >
                      <span className={styles.workViewIcon}>
                        <Icon aria-hidden="true" size={18} />
                      </span>
                      <span className={styles.workViewCopy}>
                        <strong>{view.label}</strong>
                        <small>{view.description}</small>
                      </span>
                      {view.count !== undefined ? <b>{view.count}</b> : null}
                    </button>
                  );
                })}
              </nav>
            </div>
          )
        }
        filters={
          canRead || unscopedAccount ? (
            <FilterToolbar
              label="问题数据筛选"
              onReset={filtered ? resetFilters : undefined}
              disabled={dataFetching}
            >
              <label className={styles.filterField}>
                搜索
                <Input
                  value={search.q ?? ""}
                  onChange={(event) =>
                    update({ q: event.target.value || undefined })
                  }
                  placeholder="问题类型 / 采集条目 / 说明"
                  allowClear
                />
              </label>
              <label className={styles.filterField}>
                进度
                <Select
                  value={search.status?.length === 1 ? search.status[0] : ""}
                  onChange={(value) =>
                    update({
                      status: value
                        ? [value as "OPEN" | "IN_PROGRESS" | "RESOLVED"]
                        : undefined,
                    })
                  }
                  options={[
                    { value: "", label: "全部进度" },
                    { value: "OPEN", label: "待分诊" },
                    { value: "IN_PROGRESS", label: "处理中" },
                    { value: "RESOLVED", label: "已完成" },
                  ]}
                />
              </label>
              <label className={styles.filterField}>
                严重程度
                <Select
                  value={
                    search.severity?.length === 1 ? search.severity[0] : ""
                  }
                  onChange={(value) =>
                    update({
                      severity: value
                        ? [value as ManualIssueSeverity]
                        : undefined,
                    })
                  }
                  options={[
                    { value: "", label: "全部级别" },
                    ...(["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const).map(
                      (value) => ({ value, label: SEVERITY_LABELS[value] }),
                    ),
                  ]}
                />
              </label>
              <label className={styles.filterField}>
                发现来源
                <Select
                  value={
                    search.discoverySource?.length === 1
                      ? search.discoverySource[0]
                      : ""
                  }
                  onChange={(value) =>
                    update({
                      discoverySource: value
                        ? [
                            value as
                              | "AUTO_QC"
                              | "DATA_VIEWER"
                              | "ANNOTATOR"
                              | "REVIEWER",
                          ]
                        : undefined,
                    })
                  }
                  options={[
                    { value: "", label: "全部来源" },
                    { value: "AUTO_QC", label: "自动质检" },
                    { value: "DATA_VIEWER", label: "数据查看" },
                    { value: "ANNOTATOR", label: "标注员上报" },
                    { value: "REVIEWER", label: "审核员上报" },
                  ]}
                />
              </label>
              <label className={styles.filterField}>
                处理人
                <Select
                  showSearch
                  optionFilterProp="label"
                  value={search.assigneeId ?? ""}
                  onChange={(value) =>
                    update({ assigneeId: value || undefined })
                  }
                  options={[
                    { value: "", label: "全部人员" },
                    ...(summary.data?.facets.assignees ?? []).map(
                      (assignee) => ({
                        value: assignee.id,
                        label: assignee.display_name,
                      }),
                    ),
                  ]}
                />
              </label>
              <label className={styles.filterField}>
                排序
                <Select
                  value={search.sort}
                  onChange={(value) => update({ sort: value })}
                  options={[
                    { value: "updatedAtDesc", label: "最近更新" },
                    { value: "updatedAtAsc", label: "最早更新" },
                    { value: "severityDesc", label: "严重程度从高到低" },
                    { value: "createdAtDesc", label: "最近创建" },
                  ]}
                />
              </label>
              <label className={styles.filterField}>
                每页数量
                <Select
                  value={search.limit}
                  onChange={(value) => update({ limit: value })}
                  options={([20, 50, 100] as const).map((value) => ({
                    value,
                    label: `${value} 条`,
                  }))}
                />
              </label>
            </FilterToolbar>
          ) : undefined
        }
        pagination={
          list.data ? (
            <DataCursorPager
              pageInfo={{
                startCursor: list.data.pageInfo.before,
                endCursor: list.data.pageInfo.after,
                hasPreviousPage: list.data.pageInfo.hasPrevious,
                hasNextPage: list.data.pageInfo.hasNext,
              }}
              busy={dataFetching}
              windowLabel={`本页 ${rows.length} 个问题`}
              onChange={(request) =>
                update(
                  "before" in request
                    ? { before: request.before, after: undefined }
                    : { after: request.after, before: undefined },
                )
              }
            />
          ) : undefined
        }
      >
        <div
          className={
            search.issueId && desktopInspector
              ? styles.tableInspectorLayout
              : undefined
          }
        >
          <div className={styles.tableRegion}>{content}</div>
          {search.issueId && desktopInspector ? (
            <aside className={styles.desktopInspector} aria-label="问题详情">
              <header>
                <Typography.Title level={2}>问题详情</Typography.Title>
                <Button
                  type="text"
                  onClick={() => update({ issueId: undefined })}
                >
                  关闭
                </Button>
              </header>
              {detail.isPending ? (
                <PageState state="loading" label="问题详情" />
              ) : detail.error ? (
                <PageState
                  state={stateFromError(detail.error)}
                  label="问题详情"
                  requestId={requestId(detail.error)}
                  onRetry={() => void detail.refetch()}
                />
              ) : detail.data ? (
                <IssueDetail issue={detail.data} returnTo={currentReturn} />
              ) : null}
            </aside>
          ) : null}
        </div>
      </StandardPageScaffold>

      <EntityDrawer
        open={Boolean(search.issueId) && !desktopInspector}
        title="问题详情"
        loading={detail.isPending}
        onClose={() => update({ issueId: undefined })}
      >
        {detail.error ? (
          <PageState
            state={stateFromError(detail.error)}
            label="问题详情"
            requestId={requestId(detail.error)}
            onRetry={() => void detail.refetch()}
          />
        ) : detail.data ? (
          <IssueDetail issue={detail.data} returnTo={currentReturn} />
        ) : null}
      </EntityDrawer>

      <Modal
        open={dialog?.kind === "triage"}
        title="分诊问题数据"
        footer={null}
        destroyOnHidden
        onCancel={() => {
          if (!triage.isPending) setDialog(null);
        }}
      >
        {dialog?.kind === "triage" ? (
          <Form
            layout="vertical"
            onFinish={() => {
              const returningToTriage =
                dialog.issue.status.kind === "known" &&
                dialog.issue.status.value === "IN_PROGRESS";
              triage.mutate(
                {
                  manualIssueId: dialog.issue.id,
                  expectedVersion: dialog.issue.etag,
                  idempotencyKey: mutationKey(),
                  targetStatus: returningToTriage ? "OPEN" : "IN_PROGRESS",
                  severity,
                  assigneeId: returningToTriage ? null : assigneeId || null,
                  reason,
                },
                { onSuccess: () => setDialog(null) },
              );
            }}
          >
            <Typography.Paragraph>
              <strong>{ISSUE_TYPE_LABELS[dialog.issue.issueType]}</strong> ·{" "}
              {dialog.issue.source.episodeId}
            </Typography.Paragraph>
            <Form.Item label="严重程度">
              <Select
                aria-label="严重程度"
                value={severity}
                onChange={setSeverity}
                options={(["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const).map(
                  (value) => ({ value, label: SEVERITY_LABELS[value] }),
                )}
              />
            </Form.Item>
            {dialog.issue.status.kind === "known" &&
            dialog.issue.status.value === "IN_PROGRESS" ? null : (
              <Form.Item label="处理人" required>
                <Select
                  showSearch
                  optionFilterProp="label"
                  aria-label="处理人"
                  value={assigneeId || undefined}
                  onChange={setAssigneeId}
                  placeholder="选择处理人"
                  options={(summary.data?.facets.assignees ?? []).map(
                    (assignee) => ({
                      value: assignee.id,
                      label: assignee.display_name,
                    }),
                  )}
                />
              </Form.Item>
            )}
            <Form.Item
              label={
                dialog.issue.status.kind === "known" &&
                dialog.issue.status.value === "IN_PROGRESS"
                  ? "退回待分诊的原因"
                  : "分诊说明"
              }
              required
            >
              <Input.TextArea
                aria-label={
                  dialog.issue.status.kind === "known" &&
                  dialog.issue.status.value === "IN_PROGRESS"
                    ? "退回待分诊的原因"
                    : "分诊说明"
                }
                rows={4}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </Form.Item>
            {triage.error ? (
              <Alert
                type="error"
                showIcon
                title={
                  isDomainError(triage.error)
                    ? triage.error.message
                    : "分诊失败；输入已保留。"
                }
              />
            ) : null}
            <Space className={styles.modalActions}>
              <Button
                disabled={triage.isPending}
                onClick={() => setDialog(null)}
              >
                取消
              </Button>
              <Button
                type="primary"
                htmlType="submit"
                loading={triage.isPending}
                disabled={
                  !reason.trim() ||
                  (dialog.issue.status.kind === "known" &&
                    dialog.issue.status.value !== "IN_PROGRESS" &&
                    !assigneeId)
                }
              >
                确认
              </Button>
            </Space>
          </Form>
        ) : null}
      </Modal>

      <Modal
        open={dialog?.kind === "resolve"}
        title="标记问题已完成"
        footer={null}
        destroyOnHidden
        onCancel={() => {
          if (!resolve.isPending) setDialog(null);
        }}
      >
        {dialog?.kind === "resolve" ? (
          <Form
            layout="vertical"
            onFinish={() => {
              resolve.mutate(
                {
                  manualIssueId: dialog.issue.id,
                  expectedVersion: dialog.issue.etag,
                  idempotencyKey: mutationKey(),
                  resolutionVersionId,
                  resolutionNote,
                },
                { onSuccess: () => setDialog(null) },
              );
            }}
          >
            <Typography.Paragraph>
              <strong>{ISSUE_TYPE_LABELS[dialog.issue.issueType]}</strong> ·
              只有关联了经审核的解决结果后才能完成问题。
            </Typography.Paragraph>
            <Form.Item
              label="解决结果版本 ID"
              help="填写清洗审核通过后生成的精确数据版本。"
              required
            >
              <Input
                aria-label="解决结果版本 ID"
                value={resolutionVersionId}
                onChange={(event) => setResolutionVersionId(event.target.value)}
              />
            </Form.Item>
            <Form.Item label="解决说明" required>
              <Input.TextArea
                aria-label="解决说明"
                rows={4}
                value={resolutionNote}
                onChange={(event) => setResolutionNote(event.target.value)}
              />
            </Form.Item>
            {resolve.error ? (
              <Alert
                type="error"
                showIcon
                title={
                  isDomainError(resolve.error)
                    ? resolve.error.message
                    : "提交失败；输入已保留。"
                }
              />
            ) : null}
            <Space className={styles.modalActions}>
              <Button
                disabled={resolve.isPending}
                onClick={() => setDialog(null)}
              >
                取消
              </Button>
              <Button
                type="primary"
                htmlType="submit"
                loading={resolve.isPending}
                disabled={!resolutionVersionId.trim() || !resolutionNote.trim()}
              >
                确认完成
              </Button>
            </Space>
          </Form>
        ) : null}
      </Modal>

      <Modal
        open={selection?.disposition === "SELECTION_REQUIRED"}
        title="选择要继续的清洗任务"
        footer={<Button onClick={() => setSelection(null)}>取消</Button>}
        onCancel={() => setSelection(null)}
      >
        {selection?.disposition === "SELECTION_REQUIRED"
          ? selection.candidates.map((candidate) => (
              <Typography.Paragraph key={candidate.draftId}>
                <Typography.Text code>{candidate.draftId}</Typography.Text> ·{" "}
                {candidate.updatedAt}
              </Typography.Paragraph>
            ))
          : null}
      </Modal>
      {createDraft.error ? (
        <Alert
          className={styles.operationAlert}
          type="error"
          showIcon
          title={
            isDomainError(createDraft.error)
              ? createDraft.error.message
              : "创建清洗任务失败。"
          }
        />
      ) : null}
    </main>
  );
}

export const Component = ManualIssuesPage;
export default ManualIssuesPage;
