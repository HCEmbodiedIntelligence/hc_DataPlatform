import { readableFallback } from "../../../shared/lib/resource-labels";
import type { RuntimeAnnotationTask } from "./runtime-annotation-adapter";

const labelOrder = new Intl.Collator("zh-CN", { numeric: true });
const idOrder = new Intl.Collator("en", { numeric: true });

/** Match the visible task and Episode numbers, independently of processing or edit times. */
export function compareAnnotationTasks(
  left: RuntimeAnnotationTask,
  right: RuntimeAnnotationTask,
  leftLabel?: string,
  rightLabel?: string,
): number {
  return (
    labelOrder.compare(
      leftLabel ?? readableFallback("rollout", left.rollout_id),
      rightLabel ?? readableFallback("rollout", right.rollout_id),
    ) ||
    (left.created_at ?? "").localeCompare(right.created_at ?? "") ||
    idOrder.compare(left.task_id, right.task_id)
  );
}
