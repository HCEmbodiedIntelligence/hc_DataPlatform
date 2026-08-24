import type { ColumnDef } from "@tanstack/react-table";
import { Button, Progress, Space, Tooltip } from "antd";
import { Ban, CircleAlert, Pencil, RotateCcw, XCircle } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";
import { routes } from "../../../features/datasets/routing";
import { isDomainError } from "../../../shared/api/domain-error";
import { DataTable } from "../../../shared/ui/data/DataTable";
import { PageState } from "../../../shared/ui/state/PageState";
import { StatusTag } from "../../../shared/ui/state/StatusTag";
import type { CollectionTask, CollectionTaskProgress } from "../api";
import styles from "../styles.module.css";

export interface TaskProgressState {
  readonly data?: CollectionTaskProgress;
  readonly pending: boolean;
  readonly error?: unknown;
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

const numberFormat = new Intl.NumberFormat("zh-CN");
const percentFormat = new Intl.NumberFormat("zh-CN", {
  style: "percent",
  maximumFractionDigits: 1,
});
const dateTimeFormat = new Intl.DateTimeFormat("zh-CN", {
  dateStyle: "medium",
  timeStyle: "short",
});

function TaskIdentity({ task }: Readonly<{ task: CollectionTask }>) {
  return (
    <Link
      aria-label={`查看采集任务 ${task.name}（${task.task_code}）的数据`}
      className={styles.taskIdentity}
      to={routes.datasets.build({
        collectionTaskId: task.collection_task_id,
      })}
    >
      <strong title={task.name}>{task.name}</strong>
      <code translate="no">{task.task_code}</code>
    </Link>
  );
}

function PackageProgress({
  progress,
  task,
}: Readonly<{
  progress: TaskProgressState | undefined;
  task: CollectionTask;
}>) {
  if (progress?.pending) {
    return (
      <span className={styles.progressPending} role="status">
        正在提取…
      </span>
    );
  }
  if (progress?.error || !progress?.data) {
    const detail = isDomainError(progress?.error)
      ? `${progress.error.message}${progress.error.problemCode ? ` 问题代码：${progress.error.problemCode}。` : ""}${progress.error.requestId ? ` 请求 ID：${progress.error.requestId}。` : ""}${progress.error.retryable ? " 服务端允许重试。" : ""}`
      : "进度暂不可用；任务定义仍可查看。";
    return (
      <Tooltip title={detail}>
        <span aria-label={detail} className={styles.progressError}>
          <CircleAlert aria-hidden="true" size={14} />
          暂不可用
        </span>
      </Tooltip>
    );
  }
  const received = progress.data.received_package_count;
  const target = task.target?.package_count;
  const determinate = typeof target === "number" && target > 0;
  const percent = determinate
    ? Math.min(100, Math.max(0, (received / target) * 100))
    : 0;
  return (
    <span className={styles.packageProgress}>
      <Progress
        aria-label="数据包接收进度"
        percent={percent}
        showInfo={false}
        size="small"
        status="normal"
      />
      <small>
        {numberFormat.format(received)}
        {typeof target === "number"
          ? ` / ${numberFormat.format(target)}`
          : " 个已接收"}
      </small>
      {progress.data.attainment.status !== "NOT_CONFIGURED" ? (
        <small className={styles.attainmentValue}>
          {progress.data.attainment.status === "ATTAINED"
            ? "目标已达成"
            : progress.data.attainment.status === "EXCEEDED"
              ? "目标已超出"
              : "目标进行中"}
        </small>
      ) : null}
    </span>
  );
}

function QualityProgress({
  progress,
}: Readonly<{ progress: TaskProgressState | undefined }>) {
  if (progress?.pending)
    return <span className={styles.mutedValue}>提取中</span>;
  if (progress?.error || !progress?.data) {
    return <span className={styles.mutedValue}>不可用</span>;
  }
  const rate = progress.data.qc.pass_rate.value;
  if (rate == null) return <span className={styles.mutedValue}>待评估</span>;
  return (
    <span className={styles.qualityValue}>
      {percentFormat.format(rate)}
      <small>
        {numberFormat.format(progress.data.qc.pass_count)} /{" "}
        {numberFormat.format(progress.data.qc.evaluated_count)} 通过
      </small>
    </span>
  );
}

function ProgressTime({
  progress,
}: Readonly<{ progress: TaskProgressState | undefined }>) {
  if (progress?.pending)
    return <span className={styles.mutedValue}>提取中</span>;
  if (progress?.error || !progress?.data) {
    return <span className={styles.mutedValue}>不可用</span>;
  }
  return (
    <time dateTime={progress.data.as_of}>
      {dateTimeFormat.format(new Date(progress.data.as_of))}
    </time>
  );
}

export function CollectionTaskTable({
  canManage,
  filtered,
  loading = false,
  onCancel,
  onClose,
  onEdit,
  onReopen,
  progressByTaskId,
  tasks,
}: Readonly<CollectionTaskTableProps>) {
  const columns = useMemo<readonly ColumnDef<CollectionTask, unknown>[]>(
    () => [
      {
        id: "identity",
        header: "任务名称 / 编号",
        size: 190,
        cell: ({ row }) => <TaskIdentity task={row.original} />,
      },
      {
        id: "status",
        header: "状态",
        size: 92,
        cell: ({ row }) => (
          <StatusTag
            label={
              row.original.status === "ACTIVE"
                ? "进行中"
                : row.original.status === "CLOSED"
                  ? "已关闭"
                  : "已取消"
            }
            status={row.original.status}
            tone={
              row.original.status === "ACTIVE"
                ? "success"
                : row.original.status === "CANCELLED"
                  ? "danger"
                  : "neutral"
            }
          />
        ),
      },
      {
        id: "project",
        header: "项目",
        size: 130,
        meta: { responsive: ["xl"] },
        cell: ({ row }) => (
          <code
            className={styles.projectId}
            title={row.original.project_id}
            translate="no"
          >
            {row.original.project_id}
          </code>
        ),
      },
      {
        id: "type",
        header: "类型",
        size: 112,
        cell: ({ row }) => (
          <span className={styles.typeValue} title={row.original.type}>
            {row.original.type}
          </span>
        ),
      },
      {
        id: "packages",
        header: "数据包进度",
        size: 148,
        cell: ({ row }) => (
          <PackageProgress
            progress={progressByTaskId.get(row.original.collection_task_id)}
            task={row.original}
          />
        ),
      },
      {
        id: "quality",
        header: "质量通过率",
        size: 120,
        meta: { responsive: ["lg"] },
        cell: ({ row }) => (
          <QualityProgress
            progress={progressByTaskId.get(row.original.collection_task_id)}
          />
        ),
      },
      {
        id: "asOf",
        header: "最近提取时间",
        size: 150,
        meta: { responsive: ["xl"] },
        cell: ({ row }) => (
          <ProgressTime
            progress={progressByTaskId.get(row.original.collection_task_id)}
          />
        ),
      },
      {
        id: "actions",
        header: "操作",
        size: 92,
        cell: ({ row }) =>
          canManage && row.original.status === "ACTIVE" ? (
            <Space orientation="vertical" size={0}>
              <Button
                icon={<Pencil aria-hidden="true" size={14} />}
                size="small"
                type="link"
                onClick={() => onEdit(row.original)}
              >
                编辑任务
              </Button>
              <Button
                danger
                icon={<XCircle aria-hidden="true" size={14} />}
                size="small"
                type="link"
                onClick={() => onClose(row.original)}
              >
                关闭任务
              </Button>
              <Button
                danger
                icon={<Ban aria-hidden="true" size={14} />}
                size="small"
                type="link"
                onClick={() => onCancel(row.original)}
              >
                取消任务
              </Button>
            </Space>
          ) : canManage ? (
            <Button
              icon={<RotateCcw aria-hidden="true" size={14} />}
              size="small"
              type="link"
              onClick={() => onReopen(row.original)}
            >
              重新开启
            </Button>
          ) : (
            <span className={styles.mutedValue}>—</span>
          ),
      },
    ],
    [canManage, onCancel, onClose, onEdit, onReopen, progressByTaskId],
  );

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
