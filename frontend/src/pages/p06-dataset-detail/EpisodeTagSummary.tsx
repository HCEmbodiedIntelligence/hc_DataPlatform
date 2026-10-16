import { Alert, Button, Space, Tag } from "antd";
import { Link } from "react-router-dom";
import { annotationRoutes } from "../../features/annotation/routing";
import type { EpisodeAnnotations } from "./use-episode-annotations";

export function EpisodeTagSummary({
  annotations,
}: {
  annotations: EpisodeAnnotations;
}) {
  if (!annotations.dataBound) return null;
  if (!annotations.allowed) return <span role="status">无标注读取权限</span>;
  if (annotations.isPending) return <span role="status">正在加载标注…</span>;
  if (annotations.error)
    return (
      <Alert
        type="warning"
        title="标注加载失败"
        description={annotations.error.message}
        action={
          <Button size="small" onClick={() => void annotations.refetch()}>
            重试标注
          </Button>
        }
      />
    );
  const annotation = annotations.annotation;
  if (!annotation) return null;
  if (annotation.availability === "FORBIDDEN")
    return <span role="status">无标注读取权限</span>;
  if (annotation.availability === "NOT_CREATED")
    return <span role="status">当前 Episode 尚无标注任务</span>;
  return (
    <Space wrap aria-label="Episode 标注摘要">
      {annotation.task_options.length > 1 ||
      annotation.availability === "SELECTION_REQUIRED" ||
      annotations.selectedTaskId ? (
        <label>
          标注任务{" "}
          <select
            aria-label="选择标注任务"
            value={annotation.task_id ?? ""}
            onChange={(event) => annotations.selectTask(event.target.value)}
          >
            <option value="">请选择标注任务</option>
            {annotations.selectedTaskId &&
              !annotation.task_options.some(
                (task) => task.task_id === annotations.selectedTaskId,
              ) && (
                <option value={annotations.selectedTaskId}>
                  已选标注 · 修订 {annotation.current_revision?.revision}
                </option>
              )}
            {annotation.task_options.map((task, index) => (
              <option key={task.task_id} value={task.task_id}>
                标注任务 {index + 1} · 修订 {task.current_revision}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {annotation.current_revision ? (
        <span>
          标注修订 {annotation.current_revision.revision} ·{" "}
          {annotations.tagCount} 个 Tag
        </span>
      ) : null}
      {annotation.current_approved_review_id ? (
        <Tag color="success">审核通过</Tag>
      ) : annotation.current_revision ? (
        <Tag>{annotation.status === "SUBMITTED" ? "待审核" : "未审核"}</Tag>
      ) : null}
      {annotation.task_id ? (
        <Link to={annotationRoutes.task.build({ taskId: annotation.task_id })}>
          查看标注
        </Link>
      ) : null}
    </Space>
  );
}
