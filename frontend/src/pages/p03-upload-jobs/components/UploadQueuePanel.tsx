import { Button, Popconfirm, Progress, Tag } from "antd";
import {
  CircleAlert,
  CircleCheck,
  Pause,
  RotateCcw,
  Trash2,
  UploadCloud,
  WifiOff,
  X,
} from "lucide-react";
import type { UploadQueueItem } from "../upload-queue-store";
import { formatBytes, formatDuration } from "../upload-contract";
import styles from "../styles.module.css";

const queueStatusTagStyle = {
  backgroundColor: "var(--hc-color-surface)",
} as const;

const transferLabels: Readonly<
  Record<UploadQueueItem["transferStatus"], string>
> = {
  preparing: "正在建立会话",
  uploading: "正在上传",
  pausing: "正在暂停传输",
  paused: "传输已暂停",
  offline: "断网，传输已停止",
  failed: "传输失败",
  finalizing: "正在提交 Manifest",
  committed: "Raw 已提交",
  cancelled: "上传已取消",
  "needs-file": "等待重新选择原文件",
};

function statusTone(status: UploadQueueItem["transferStatus"]) {
  if (status === "committed") return "success";
  if (status === "failed" || status === "cancelled") return "error";
  if (status === "paused" || status === "offline" || status === "needs-file")
    return "warning";
  return "processing";
}

function QueueItemCard(props: {
  readonly item: UploadQueueItem;
  readonly canManage: boolean;
  readonly onPause: () => void;
  readonly onResume: () => void;
  readonly onRetry: () => void;
  readonly onCancel: () => void;
  readonly onReattach: (file: File) => void;
}) {
  const item = props.item;
  const percent =
    item.totalBytes > 0
      ? Math.min(100, (item.uploadedBytes / item.totalBytes) * 100)
      : 0;
  const cancellationBlocked = ["preparing", "pausing", "finalizing"].includes(
    item.transferStatus,
  );
  const mayCancel =
    !["committed", "cancelled"].includes(item.transferStatus) &&
    item.sessionId !== null;

  return (
    <article
      className={styles.queueItem}
      data-transfer-status={item.transferStatus}
      aria-label={`${item.dataPackageId} 上传传输`}
    >
      <header>
        <span className={styles.queueFileIcon} aria-hidden="true">
          <UploadCloud size={17} />
        </span>
        <div>
          <strong title={item.fileName}>{item.fileName}</strong>
          <span title={item.dataPackageId}>{item.dataPackageId}</span>
        </div>
        <Tag
          variant="outlined"
          color={statusTone(item.transferStatus)}
          style={queueStatusTagStyle}
        >
          {transferLabels[item.transferStatus]}
        </Tag>
      </header>
      <Progress
        aria-label={`${item.fileName} 上传进度`}
        percent={Number(percent.toFixed(1))}
        status={
          item.transferStatus === "failed"
            ? "exception"
            : item.transferStatus === "committed"
              ? "success"
              : "active"
        }
        size="small"
        format={(value) => `${Number(value).toFixed(1)}%`}
      />
      <dl className={styles.queueMetrics}>
        <div>
          <dt>分片</dt>
          <dd>
            {item.completedParts} / {item.totalParts}
          </dd>
        </div>
        <div>
          <dt>速度</dt>
          <dd>
            {item.speedBytesPerSecond
              ? `${formatBytes(item.speedBytesPerSecond)}/s`
              : "—"}
          </dd>
        </div>
        <div>
          <dt>剩余</dt>
          <dd>{formatDuration(item.remainingSeconds)}</dd>
        </div>
        <div>
          <dt>已确认</dt>
          <dd>{formatBytes(item.uploadedBytes)}</dd>
        </div>
      </dl>

      {item.transferStatus === "offline" ? (
        <p className={styles.queueNotice}>
          <WifiOff size={14} aria-hidden="true" />{" "}
          网络恢复后将核对服务端分片并继续，不会从零开始。
        </p>
      ) : item.transferStatus === "paused" ? (
        <p className={styles.queueNotice}>
          <Pause size={14} aria-hidden="true" />{" "}
          上传传输已暂停；采集任务状态没有改变。
        </p>
      ) : item.failureMessage ? (
        <div className={styles.queueFailure} role="alert">
          <CircleAlert size={14} aria-hidden="true" />
          <div>
            <span>{item.failureMessage}</span>
            {item.failureCode ? (
              <code translate="no">{item.failureCode}</code>
            ) : null}
            {item.requestId ? (
              <small>
                请求 ID：<code translate="no">{item.requestId}</code>
              </small>
            ) : null}
          </div>
        </div>
      ) : null}

      {item.failedParts.length > 0 ? (
        <div className={styles.failedParts} aria-label="失败分片">
          <span>失败分片</span>
          {item.failedParts.slice(0, 8).map((part) => (
            <code key={part} translate="no">
              #{part}
            </code>
          ))}
          {item.failedParts.length > 8 ? (
            <small>+{item.failedParts.length - 8}</small>
          ) : null}
        </div>
      ) : null}

      {item.transferStatus === "needs-file" ||
      (item.transferStatus === "failed" &&
        item.failureCode === "LOCAL_FILE_SIZE_MISMATCH") ? (
        <div className={styles.reattachFile}>
          <input
            id={`reattach-${item.id}`}
            name={`reattach-upload-file-${item.id}`}
            className={styles.visuallyHidden}
            type="file"
            accept=".mcap,application/octet-stream"
            disabled={!props.canManage}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) props.onReattach(file);
            }}
          />
          <label
            htmlFor={`reattach-${item.id}`}
            aria-disabled={!props.canManage}
          >
            重新选择同一原文件并续传
          </label>
        </div>
      ) : null}

      <footer>
        {item.transferStatus === "uploading" ? (
          <Button
            size="small"
            icon={<Pause aria-hidden="true" size={13} />}
            disabled={!props.canManage}
            onClick={props.onPause}
          >
            暂停传输
          </Button>
        ) : ["paused", "offline"].includes(item.transferStatus) ? (
          <Button
            size="small"
            icon={<RotateCcw aria-hidden="true" size={13} />}
            disabled={!props.canManage}
            onClick={props.onResume}
          >
            继续传输
          </Button>
        ) : item.transferStatus === "failed" && item.failedParts.length > 0 ? (
          <Button
            size="small"
            icon={<RotateCcw aria-hidden="true" size={13} />}
            disabled={!props.canManage}
            onClick={props.onRetry}
          >
            重试失败分片
          </Button>
        ) : item.transferStatus === "failed" &&
          item.sourceType === "OBJECT_STORAGE_REFERENCE" ? (
          <Button
            size="small"
            icon={<RotateCcw aria-hidden="true" size={13} />}
            disabled={!props.canManage}
            onClick={props.onResume}
          >
            重试提交
          </Button>
        ) : null}
        {mayCancel ? (
          <Popconfirm
            title="取消此次上传？"
            description="已上传分片将由服务端中止；此操作不影响采集任务。"
            okText="取消上传"
            cancelText="返回"
            onConfirm={props.onCancel}
          >
            <Button
              size="small"
              danger
              icon={<X aria-hidden="true" size={13} />}
              disabled={!props.canManage || cancellationBlocked}
            >
              取消
            </Button>
          </Popconfirm>
        ) : null}
        {item.transferStatus === "committed" ? (
          <span className={styles.queueCommitted}>
            <CircleCheck size={14} aria-hidden="true" /> 已进入摄取工作流
          </span>
        ) : null}
      </footer>
    </article>
  );
}

export function UploadQueuePanel(props: {
  readonly items: readonly UploadQueueItem[];
  readonly recovering: boolean;
  readonly canManage: boolean;
  readonly onPause: (id: string) => void;
  readonly onResume: (id: string) => void;
  readonly onRetry: (id: string) => void;
  readonly onCancel: (id: string) => void;
  readonly onReattach: (id: string, file: File) => void;
  readonly onClearSettled: () => void;
}) {
  const settled = props.items.some((item) =>
    ["committed", "cancelled"].includes(item.transferStatus),
  );
  return (
    <section
      className={`${styles.uploadPanel} ${styles.queuePanel}`}
      aria-labelledby="upload-queue-heading"
    >
      <header className={styles.panelHeader}>
        <span className={styles.stepBadge} aria-hidden="true">
          4
        </span>
        <div>
          <h2 id="upload-queue-heading">
            上传队列 <small>（{props.items.length}）</small>
          </h2>
        </div>
      </header>
      <div className={styles.queueList} aria-live="polite">
        {props.items.map((item) => (
          <QueueItemCard
            key={item.id}
            item={item}
            canManage={props.canManage}
            onPause={() => props.onPause(item.id)}
            onResume={() => props.onResume(item.id)}
            onRetry={() => props.onRetry(item.id)}
            onCancel={() => props.onCancel(item.id)}
            onReattach={(file) => props.onReattach(item.id, file)}
          />
        ))}
        {props.items.length === 0 ? (
          <div className={styles.queueEmpty}>
            <UploadCloud size={30} aria-hidden="true" />
            <strong>
              {props.recovering ? "正在恢复上传队列" : "队列为空"}
            </strong>
            <span>
              {props.recovering
                ? "正在核对服务端已确认分片…"
                : "预检通过后点击“开始上传”。"}
            </span>
          </div>
        ) : null}
      </div>
      {settled ? (
        <Button
          className={styles.clearQueueButton}
          type="text"
          size="small"
          icon={<Trash2 aria-hidden="true" size={13} />}
          onClick={props.onClearSettled}
        >
          清除已完成
        </Button>
      ) : null}
    </section>
  );
}
