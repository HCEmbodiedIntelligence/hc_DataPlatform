import { readableFallback } from "../../../shared/lib/resource-labels";
import type { RuntimeAnnotationTask } from "./runtime-annotation-adapter";

/** Match the visible task and Episode numbers, independently of processing or edit times. */
export function compareAnnotationTasks(
  left: RuntimeAnnotationTask,
  right: RuntimeAnnotationTask,
  leftLabel?: string,
  rightLabel?: string,
): number {
  return (
    (leftLabel ?? readableFallback("rollout", left.rollout_id)).localeCompare(
      rightLabel ?? readableFallback("rollout", right.rollout_id),
      "zh-CN",
      { numeric: true },
    ) ||
    (left.created_at ?? "").localeCompare(right.created_at ?? "") ||
    left.task_id.localeCompare(right.task_id, "en", { numeric: true })
  );
}
