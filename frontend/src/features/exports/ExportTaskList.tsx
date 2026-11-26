import { Button, Card, Progress, Space, Tag } from "antd";
import type { Scope } from "../../shared/scope/model";
import { OssExportResult } from "./ExportControls";
import { cancelExport, clearExportTask, pauseExport, resumeExport, useExportTasks } from "./export-tasks";
import styles from "./ExportTaskList.module.css";

const states = {
  running: "进行中", pausing: "正在暂停", paused: "已暂停", saving: "正在保存",
  finished: "已完成", failed: "失败", cancelled: "已取消",
};

export function ExportTaskList({ scope }: { scope: Scope | null }) {
  const tasks = useExportTasks(scope);
  const visible = tasks.filter(t => !(t.uri && t.hiddenReceipt));
  const active = tasks.filter(t => ["running", "pausing", "paused", "saving"].includes(t.status)).length;
  return (
    <Card size="small" title="下载与转换任务" extra={`${active} 个当前任务`}>
      {visible.length === 0 && <span className={styles.empty}>暂无当前任务</span>}
      <div className={styles.list}>
        {visible.map(task => {
          const local = task.destination === "zip";
          const waiting = task.status === "paused" || task.status === "pausing";
          const cancellable = task.status === "running" || waiting;
          return <div key={task.id} className={styles.row} role="group" aria-label={`任务 ${task.id}`}>
            <div className={styles.info}>
              <strong>#{task.id} · {task.label} · {local ? "ZIP 下载" : "导出到 OSS"}</strong>
              {task.uri
                ? <OssExportResult uri={task.uri} onClose={() => clearExportTask(task.id)} />
                : <span className={styles.detail}>{task.message}</span>}
              {cancellable && task.progress !== undefined &&
                <Progress size="small" percent={task.progress} status="normal" aria-label="当前阶段进度" />}
            </div>
            <Space wrap>
              <Tag color={task.status === "failed" ? "error" : task.status === "running" ? "processing" :
                task.status === "finished" ? "success" : undefined}>{states[task.status]}</Tag>
              {local && cancellable &&
                <Button size="small" onClick={() => waiting ? resumeExport(task.id) : pauseExport(task.id)}>
                  {waiting ? "继续下载" : "暂停下载"}
                </Button>}
              {cancellable && <Button size="small" onClick={() => cancelExport(task.id)}>
                {local ? "取消下载" : "取消导出请求"}
              </Button>}
              {!cancellable && task.status !== "saving" && !task.uri &&
                <Button type="text" size="small" onClick={() => clearExportTask(task.id)}>移除记录</Button>}
            </Space>
          </div>;
        })}
      </div>
    </Card>
  );
}
