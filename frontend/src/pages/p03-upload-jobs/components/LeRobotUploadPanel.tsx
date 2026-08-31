import { Alert, Button, Progress } from "antd";
import { Database, RotateCcw } from "lucide-react";
import type { UploadFlowState } from "../upload-flow";
import { formatBytes } from "../upload-contract";
import styles from "../styles.module.css";

type LeRobotState = Extract<
  UploadFlowState,
  {
    readonly phase:
      | "lerobot_uploading"
      | "lerobot_failed"
      | "lerobot_completed";
  }
>;

export function LeRobotUploadPanel(props: {
  readonly state: LeRobotState;
  readonly onRetry: () => void;
  readonly onBack: () => void;
  readonly onContinue: () => void;
  readonly onViewRecords: () => void;
}) {
  if (props.state.phase === "lerobot_uploading") {
    const progress = props.state.progress;
    const percent =
      progress.totalBytes > 0
        ? Math.min(
            100,
            Math.round((progress.uploadedBytes / progress.totalBytes) * 100),
          )
        : 0;
    return (
      <section className={styles.uploadPanel} aria-live="polite">
        <header className={styles.selectionHeading}>
          <span className={styles.stepBadge} aria-hidden="true">
            2
          </span>
          <div>
            <h2>
              {progress.stage === "committing"
                ? "正在校验 Raw 清单"
                : "正在上传原始 LeRobot"}
            </h2>
            <p>
              源文件按原路径直接写入 Raw；这里不会生成 MCAP，也不会重新编码
              Parquet 或 MP4。
            </p>
          </div>
        </header>
        <Progress percent={percent} status="active" />
        <p>
          {formatBytes(progress.uploadedBytes)} /{" "}
          {formatBytes(progress.totalBytes)} · {progress.completedFiles}/
          {progress.totalFiles} 个文件
        </p>
        {progress.currentPath ? <code>{progress.currentPath}</code> : null}
      </section>
    );
  }

  if (props.state.phase === "lerobot_failed") {
    return (
      <section className={styles.uploadPanel}>
        <Alert
          type="error"
          showIcon
          title={props.state.problem.title}
          description={props.state.problem.detail}
        />
        <div className={styles.panelActions}>
          <Button icon={<RotateCcw size={15} />} onClick={props.onRetry}>
            重新上传
          </Button>
          <Button onClick={props.onBack}>重新选择</Button>
        </div>
      </section>
    );
  }

  return (
    <section className={styles.uploadPanel} aria-live="polite">
      <header className={styles.selectionHeading}>
        <span className={styles.stepBadge} aria-hidden="true">
          <Database size={16} />
        </span>
        <div>
          <h2>原始 LeRobot 已写入 Raw</h2>
          <p>
            平台已记录每个源对象的大小和 SHA-256，并按 episode 创建 LeRobot
            Adapter 处理任务。
          </p>
        </div>
      </header>
      <dl className={styles.confirmFacts}>
        <div>
          <dt>上传 ID</dt>
          <dd>
            <code>{props.state.result.import_id}</code>
          </dd>
        </div>
        <div>
          <dt>原始文件</dt>
          <dd>{props.state.result.source_file_count} 个</dd>
        </div>
        <div>
          <dt>Episodes</dt>
          <dd>{props.state.result.episode_count}</dd>
        </div>
        <div>
          <dt>处理任务</dt>
          <dd>{props.state.result.episode_task_count} 个</dd>
        </div>
        <div>
          <dt>Raw 格式</dt>
          <dd>LeRobot v3.0 原始对象（未转 MCAP）</dd>
        </div>
      </dl>
      <div className={styles.panelActions}>
        <Button type="primary" onClick={props.onContinue}>
          继续上传
        </Button>
        <Button onClick={props.onViewRecords}>查看上传记录</Button>
      </div>
    </section>
  );
}
