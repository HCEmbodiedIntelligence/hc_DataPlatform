import type { ColumnDef } from "@tanstack/react-table";
import { Button, Dropdown, Progress, Tooltip, type MenuProps } from "antd";
import {
  ArrowUpRight,
  Ban,
  CircleAlert,
  MoreHorizontal,
  Pencil,
  RotateCcw,
  XCircle,
} from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { DatasetId } from "../../../entities/dataset";
import { routes } from "../../../features/datasets/routing";
import { isDomainError } from "../../../shared/api/domain-error";
import { DataTable } from "../../../shared/ui/data/DataTable";
import { useCompactLayout } from "../../../shared/ui/layout/responsive";
import { PageState } from "../../../shared/ui/state/PageState";
import { StatusTag } from "../../../shared/ui/state/StatusTag";
import type { CollectionTask, CollectionTaskProgress } from "../api";
import styles from "../styles.module.css";

export interface TaskProgressState {
  readonly data?: CollectionTaskProgress;
  readonly pending: boolean;
  readonly stale?: boolean;
  readonly error?: unknown;
  readonly retry?: () => void;
}

export interface CollectionTaskTableProps {
  readonly tasks: readonly CollectionTask[];
  readonly progressByTaskId: ReadonlyMap<string, TaskProgressState>;
  readonly loading?: boolean;
  readonly canManage: boolean;
  readonly filtered: boolean;
  readonly onEdit: (task: CollectionTask) => void;
  readonly onClose: (task: CollectionTask) => void;
  readonly onCancel: (task: CollectionTask) => void;
  readonly onReopen: (task: CollectionTask) => void;
}

type TargetMetric = NonNullable<
  CollectionTaskProgress["attainment"]["package_count"]
>;
type QualityStatus = CollectionTaskProgress["attainment"]["quality_status"];

const numberFormat = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 1,
});
const percentFormat = new Intl.NumberFormat("zh-CN", {
  style: "percent",
  maximumFractionDigits: 1,
});
const compactDateTimeFormat = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const fullDateTimeFormat = new Intl.DateTimeFormat("zh-CN", {
  dateStyle: "long",
  timeStyle: "medium",
});

function taskStatusCopy(status: CollectionTask["status"]): string {
  if (status === "ACTIVE") return "进行中";
  if (status === "CLOSED") return "已关闭";
  return "已取消";
}

function formatTaskCode(taskCode: string): string {
  return taskCode.replace(/^(\d{4})(\d{4})$/u, "$1 $2");
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${numberFormat.format(seconds)} 秒`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) {
    return minutes > 0 ? `${hours} 小时 ${minutes} 分` : `${hours} 小时`;
  }
  return `${minutes} 分钟`;
}

function targetMetricStatusCopy(status: TargetMetric["status"]): string {
  if (status === "IN_PROGRESS") return "进行中";
  if (status === "MET") return "已达成";
  if (status === "EXCEEDED") return "已超出";
  return "状态未知";
}

function attainmentStatusCopy(
  status: CollectionTaskProgress["attainment"]["status"],
): string {
  if (status === "IN_PROGRESS") return "目标进行中";
  if (status === "ATTAINED") return "目标已达成";
  if (status === "EXCEEDED") return "目标已超出";
  return "未配置目标";
}

function qualityStatusCopy(status: QualityStatus): string {
  if (status === "PENDING_QC") return "存在未出质检包";
  if (status === "NOT_MET") return "未达到阈值";
  if (status === "MET") return "达到阈值";
  return "未配置";
}

function qualityToneClass(status: QualityStatus): string | undefined {
  if (status === "MET") return styles.qualityMet;
  if (status === "NOT_MET") return styles.qualityNotMet;
  if (status === "PENDING_QC") return styles.qualityPending;
  return styles.qualityUnconfigured;
}

function progressErrorDetail(error: unknown): string {
  if (!isDomainError(error)) {
    return "进度暂不可用；任务定义仍可查看。请重试这一行的进度。";
  }
  return `${error.message}${error.problemCode ? ` 问题代码：${error.problemCode}。` : ""}${error.requestId ? ` 请求 ID：${error.requestId}。` : ""}${error.retryable ? " 服务端允许重试。" : " 请核对任务或作用域后重试。"}`;
}

function TaskIdentity({ task }: Readonly<{ task: CollectionTask }>) {
  return (
    <div className={styles.taskIdentity}>
      <Link
        aria-label={`查看采集任务 ${task.name}（${task.task_code}）的数据集`}
        className={styles.taskNameLink}
        to={routes.datasetDetail.build({
          datasetId: task.dataset_id as DatasetId,
        })}
      >
        <strong title={task.name}>{task.name}</strong>
      </Link>
      <code
        className={styles.taskCode}
        title={`原始任务码：${task.task_code}`}
        translate="no"
      >
        {formatTaskCode(task.task_code)}
      </code>
      <span
        className={styles.taskDataset}
        title={`关联数据集：${task.dataset_id}`}
      >
        <span>数据集</span>
        <code translate="no">{task.dataset_id}</code>
      </span>
      <span
        className={styles.taskMetadata}
        title={`类型：${task.type}；场景：${task.scenario}`}
      >
        <span>类型：{task.type}</span>
        <span aria-hidden="true">·</span>
        <span>场景：{task.scenario}</span>
      </span>
    </div>
  );
}

function ProgressUnavailable({
  progress,
}: Readonly<{ progress: TaskProgressState | undefined }>) {
  const detail = progressErrorDetail(progress?.error);
  return (
    <span className={styles.progressUnavailable}>
      <Tooltip title={detail}>
        <span aria-label={detail} className={styles.progressError}>
          <CircleAlert aria-hidden="true" size={15} />
          进度不可用
        </span>
      </Tooltip>
      {progress?.retry ? (
        <Button size="small" type="link" onClick={progress.retry}>
          重试
        </Button>
      ) : null}
    </span>
  );
}

function MetricProgress({
  actual,
  ariaLabel,
  label,
  metric,
  target,
  unit,
}: Readonly<{
  actual: number | null;
  ariaLabel: string;
  label: string;
  metric: TargetMetric | null;
  target: number | null;
  unit: "包" | "duration";
}>) {
  const formattedActual =
    actual === null
      ? unit === "duration"
        ? "时长待汇总"
        : "数量未知"
      : unit === "duration"
        ? formatDuration(actual)
        : `${numberFormat.format(actual)} 包`;
  const formattedTarget =
    target === null
      ? "未设置目标"
      : unit === "duration"
        ? formatDuration(target)
        : `${numberFormat.format(target)} 包`;
  const percent =
    metric?.progress == null
      ? null
      : Math.min(100, Math.max(0, metric.progress * 100));

  return (
    <div className={styles.metricProgress}>
      <span className={styles.metricHeading}>
        <b>{label}</b>
        {target !== null ? (
          <small>
            {metric ? targetMetricStatusCopy(metric.status) : "状态未知"}
          </small>
        ) : null}
      </span>
      <span className={styles.metricValue}>
        {formattedActual}
        {target !== null ? ` / ${formattedTarget}` : ` · ${formattedTarget}`}
      </span>
      {target !== null && percent !== null ? (
        <Progress
          aria-label={ariaLabel}
          percent={percent}
          showInfo={false}
          size="small"
          status="normal"
        />
      ) : null}
    </div>
  );
}

function ReceivedProgress({
  progress,
  task,
}: Readonly<{
  progress: TaskProgressState | undefined;
  task: CollectionTask;
}>) {
  if (progress?.pending && !progress.data) {
    return (
      <span className={styles.progressPending} role="status">
        正在加载进度…
      </span>
    );
  }
  if (!progress?.data) return <ProgressUnavailable progress={progress} />;

  const data = progress.data;
  const packageTarget = task.target?.package_count ?? null;
  const durationTarget = task.target?.duration_seconds ?? null;
  const stale = progress.stale || progress.error !== undefined;

  return (
    <div className={styles.receivedProgress}>
      <span
        className={styles.attainmentStatus}
        data-status={data.attainment.status}
      >
        {attainmentStatusCopy(data.attainment.status)}
      </span>
      <MetricProgress
        actual={data.received_package_count}
        ariaLabel="数据包接收目标进度"
        label="数据包"
        metric={data.attainment.package_count ?? null}
        target={packageTarget}
        unit="包"
      />
      <MetricProgress
        actual={data.captured_duration_seconds ?? null}
        ariaLabel="采集时长目标进度"
        label="采集时长"
        metric={data.attainment.duration_seconds ?? null}
        target={durationTarget}
        unit="duration"
      />
      {data.duration_unknown_package_count > 0 ? (
        <span className={styles.durationWarning}>
          <CircleAlert aria-hidden="true" size={13} />
          {numberFormat.format(data.duration_unknown_package_count)} 包时长未知
        </span>
      ) : null}
      {stale ? (
        <small className={styles.staleValue}>当前显示可能已陈旧</small>
      ) : null}
    </div>
  );
}

function QualityProgress({
  progress,
}: Readonly<{ progress: TaskProgressState | undefined }>) {
  if (progress?.pending && !progress.data) {
    return <span className={styles.mutedValue}>质检加载中…</span>;
  }
  if (!progress?.data) {
    return <span className={styles.mutedValue}>质量结果不可用</span>;
  }

  const data = progress.data;
  const { qc } = data;
  const qualityStatus = data.attainment.quality_status;
  const passRate = qc.evaluated_count > 0 ? (qc.pass_rate.value ?? null) : null;
  const threshold = data.attainment.quality_threshold ?? null;

  return (
    <div className={styles.qualityProgress}>
      <div
        aria-label={`Pass ${qc.pass_count}，Risk ${qc.risk_count}，Reject ${qc.reject_count}，未出质检 ${qc.pending_count}`}
        className={styles.qcCounts}
      >
        <span className={styles.qcPass}>
          P {numberFormat.format(qc.pass_count)}
        </span>
        <span className={styles.qcRisk}>
          R {numberFormat.format(qc.risk_count)}
        </span>
        <span className={styles.qcReject}>
          X {numberFormat.format(qc.reject_count)}
        </span>
        <span className={styles.qcPending}>
          未出质检 {numberFormat.format(qc.pending_count)}
        </span>
      </div>
      {passRate !== null ? (
        <strong
          className={`${styles.passRate} ${qualityToneClass(qualityStatus)}`}
        >
          通过率 {percentFormat.format(passRate)}
        </strong>
      ) : (
        <span className={styles.mutedValue}>暂无已评估数据</span>
      )}
      <small className={styles.qualityRequirement}>
        {threshold === null
          ? "未配置质量阈值"
          : `阈值 ${percentFormat.format(threshold)}`}
        <span aria-hidden="true"> · </span>
        <b className={qualityToneClass(qualityStatus)}>
          {qualityStatusCopy(qualityStatus)}
        </b>
      </small>
    </div>
  );
}

function sourceSummary(progress: CollectionTaskProgress): {
  compact: string;
  detail: string;
} {
  const sources = progress.observed_sources;
  const total =
    sources.device_ids.length +
    sources.camera_ids.length +
    sources.topic_names.length;
  if (total === 0) {
    return {
      compact: "未识别来源",
      detail: "当前进度尚未识别设备、相机或 Topic 来源。",
    };
  }
  const parts = [
    sources.device_ids.length > 0 ? `${sources.device_ids.length} 设备` : null,
    sources.camera_ids.length > 0 ? `${sources.camera_ids.length} 相机` : null,
    sources.topic_names.length > 0
      ? `${sources.topic_names.length} Topic`
      : null,
  ].filter((part): part is string => part !== null);
  const detailParts = [
    sources.device_ids.length > 0
      ? `设备：${sources.device_ids.join("、")}`
      : null,
    sources.camera_ids.length > 0
      ? `相机：${sources.camera_ids.join("、")}`
      : null,
    sources.topic_names.length > 0
      ? `Topic：${sources.topic_names.join("、")}`
      : null,
  ].filter((part): part is string => part !== null);
  return { compact: parts.join(" · "), detail: detailParts.join("；") };
}

function ProgressTime({
  progress,
}: Readonly<{ progress: TaskProgressState | undefined }>) {
  if (progress?.pending && !progress.data) {
    return <span className={styles.mutedValue}>时间加载中…</span>;
  }
  if (!progress?.data) {
    return <span className={styles.mutedValue}>数据截至不可用</span>;
  }

  const date = new Date(progress.data.as_of);
  if (Number.isNaN(date.valueOf())) {
    return <span className={styles.progressError}>数据截至时间不可用</span>;
  }
  const sources = sourceSummary(progress.data);
  const stale = progress.stale || progress.error !== undefined;

  return (
    <div className={styles.progressTime}>
      <time
        dateTime={progress.data.as_of}
        title={fullDateTimeFormat.format(date)}
      >
        {compactDateTimeFormat.format(date)}
      </time>
      <small title={sources.detail}>{sources.compact}</small>
      {stale ? <small className={styles.staleValue}>数据可能陈旧</small> : null}
    </div>
  );
}

function dropdownContainer(): HTMLElement {
  return document.body;
}

function TaskActions({
  canManage,
  onCancel,
  onClose,
  onEdit,
  onReopen,
  task,
}: Readonly<{
  canManage: boolean;
  task: CollectionTask;
  onEdit: (task: CollectionTask) => void;
  onClose: (task: CollectionTask) => void;
  onCancel: (task: CollectionTask) => void;
  onReopen: (task: CollectionTask) => void;
}>) {
  const [menuOpen, setMenuOpen] = useState(false);
  const moreActionsRef = useRef<HTMLAnchorElement | HTMLButtonElement>(null);
  const menuItems = useMemo<MenuProps["items"]>(() => {
    const run = (action: (selectedTask: CollectionTask) => void) => {
      moreActionsRef.current?.focus();
      action(task);
    };
    if (task.status !== "ACTIVE") {
      return [
        {
          key: "reopen",
          icon: <RotateCcw aria-hidden="true" size={16} />,
          label: "重新开启",
          onClick: () => run(onReopen),
        },
      ];
    }
    return [
      {
        key: "edit",
        icon: <Pencil aria-hidden="true" size={16} />,
        label: "编辑任务",
        onClick: () => run(onEdit),
      },
      { type: "divider" },
      {
        key: "close",
        danger: true,
        icon: <XCircle aria-hidden="true" size={16} />,
        label: "关闭任务",
        onClick: () => run(onClose),
      },
      {
        key: "cancel",
        danger: true,
        icon: <Ban aria-hidden="true" size={16} />,
        label: "取消任务",
        onClick: () => run(onCancel),
      },
    ];
  }, [onCancel, onClose, onEdit, onReopen, task]);

  return (
    <div className={styles.taskActions}>
      <Link
        className={styles.viewDataAction}
        to={routes.datasetDetail.build({
          datasetId: task.dataset_id as DatasetId,
        })}
      >
        查看数据集
        <ArrowUpRight aria-hidden="true" size={15} />
      </Link>
      {canManage ? (
        <Tooltip title="更多操作" trigger={["hover", "focus"]}>
          <Dropdown
            autoFocus
            destroyOnHidden
            getPopupContainer={dropdownContainer}
            menu={{
              items: menuItems,
              onKeyDown: (event) => {
                if (event.key !== "Escape") return;
                event.stopPropagation();
                setMenuOpen(false);
                window.setTimeout(() => moreActionsRef.current?.focus(), 0);
              },
            }}
            open={menuOpen}
            placement="bottomRight"
            rootClassName={styles.taskActionDropdown}
            trigger={["click"]}
            onOpenChange={setMenuOpen}
          >
            <Button
              aria-expanded={menuOpen}
              aria-haspopup="menu"
              aria-label="更多操作"
              className={styles.moreTaskActions}
              icon={<MoreHorizontal aria-hidden="true" size={18} />}
              ref={moreActionsRef}
              type="text"
            />
          </Dropdown>
        </Tooltip>
      ) : null}
    </div>
  );
}

function TaskStatus({ task }: Readonly<{ task: CollectionTask }>) {
  return (
    <StatusTag
      label={taskStatusCopy(task.status)}
      status={task.status}
      tone={
        task.status === "ACTIVE"
          ? "success"
          : task.status === "CANCELLED"
            ? "danger"
            : "neutral"
      }
    />
  );
}

function MobileTaskList({
  canManage,
  filtered,
  loading,
  onCancel,
  onClose,
  onEdit,
  onReopen,
  progressByTaskId,
  tasks,
}: Readonly<CollectionTaskTableProps>) {
  if (loading) {
    return <PageState label="采集任务" layout="list" state="loading" />;
  }
  if (tasks.length === 0) {
    return (
      <PageState
        label="采集任务"
        layout="list"
        state={filtered ? "filtered-empty" : "empty"}
        description={
          filtered
            ? "当前数据窗口没有匹配结果，请调整筛选条件。"
            : "当前项目还没有采集任务；有管理权限时可新建任务。"
        }
      />
    );
  }

  return (
    <section aria-label="采集任务" className={styles.mobileTaskList}>
      {tasks.map((task) => {
        const progress = progressByTaskId.get(task.collection_task_id);
        return (
          <article
            className={styles.mobileTaskCard}
            key={task.collection_task_id}
          >
            <TaskIdentity task={task} />
            <div className={styles.mobileTaskSection}>
              <span className={styles.mobileTaskLabel}>状态</span>
              <TaskStatus task={task} />
            </div>
            <div className={styles.mobileTaskSection}>
              <span className={styles.mobileTaskLabel}>接收与目标</span>
              <ReceivedProgress progress={progress} task={task} />
            </div>
            <div className={styles.mobileTaskSection}>
              <span className={styles.mobileTaskLabel}>质量结果</span>
              <QualityProgress progress={progress} />
            </div>
            <div className={styles.mobileTaskSection}>
              <span className={styles.mobileTaskLabel}>数据截至</span>
              <ProgressTime progress={progress} />
            </div>
            <div
              className={`${styles.mobileTaskSection} ${styles.mobileTaskActions}`}
            >
              <span className={styles.mobileTaskLabel}>操作</span>
              <TaskActions
                canManage={canManage}
                task={task}
                onCancel={onCancel}
                onClose={onClose}
                onEdit={onEdit}
                onReopen={onReopen}
              />
            </div>
          </article>
        );
      })}
    </section>
  );
}

export function CollectionTaskTable(props: Readonly<CollectionTaskTableProps>) {
  const {
    canManage,
    filtered,
    loading = false,
    onCancel,
    onClose,
    onEdit,
    onReopen,
    progressByTaskId,
    tasks,
  } = props;
  const compact = useCompactLayout();
  const columns = useMemo<readonly ColumnDef<CollectionTask, unknown>[]>(
    () => [
      {
        id: "identity",
        header: "任务信息",
        size: 250,
        cell: ({ row }) => <TaskIdentity task={row.original} />,
      },
      {
        id: "status",
        header: "状态",
        size: 100,
        cell: ({ row }) => <TaskStatus task={row.original} />,
      },
      {
        id: "received",
        header: "接收与目标",
        size: 220,
        cell: ({ row }) => (
          <ReceivedProgress
            progress={progressByTaskId.get(row.original.collection_task_id)}
            task={row.original}
          />
        ),
      },
      {
        id: "quality",
        header: "质量结果",
        size: 230,
        cell: ({ row }) => (
          <QualityProgress
            progress={progressByTaskId.get(row.original.collection_task_id)}
          />
        ),
      },
      {
        id: "asOf",
        header: "数据截至",
        size: 136,
        cell: ({ row }) => (
          <ProgressTime
            progress={progressByTaskId.get(row.original.collection_task_id)}
          />
        ),
      },
      {
        id: "actions",
        header: "操作",
        size: 156,
        cell: ({ row }) => (
          <TaskActions
            canManage={canManage}
            task={row.original}
            onCancel={onCancel}
            onClose={onClose}
            onEdit={onEdit}
            onReopen={onReopen}
          />
        ),
      },
    ],
    [canManage, onCancel, onClose, onEdit, onReopen, progressByTaskId],
  );

  if (compact) {
    return <MobileTaskList {...props} loading={loading} />;
  }

  return (
    <DataTable
      caption="采集任务"
      columns={columns}
      data={tasks}
      empty={
        <PageState
          label="采集任务"
          state={filtered ? "filtered-empty" : "empty"}
          description={
            filtered
              ? "当前数据窗口没有匹配结果，请调整筛选条件。"
              : "当前项目还没有采集任务；有管理权限时可新建任务。"
          }
        />
      }
      getRowId={(task) => task.collection_task_id}
      state={loading ? "loading" : undefined}
    />
  );
}
