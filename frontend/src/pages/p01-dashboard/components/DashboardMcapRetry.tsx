import { FailedMcapRetry } from "../../../features/collection-tasks/FailedMcapRetry";
import type { DashboardDataIssue, DashboardScope, DashboardTaskListItem } from "../../../features/dashboard/types";
import { useCapabilities } from "../../../shared/auth/use-capabilities";

export function DashboardMcapRetry({ scope, issues, tasks }: {
  scope?: DashboardScope;
  issues: readonly DashboardDataIssue[];
  tasks: readonly DashboardTaskListItem[];
}) {
  const canManage = useCapabilities().has("upload.manage");
  if (!scope || !canManage) return null;
  const taskKeys = new Map<string, Set<string>>();
  for (const issue of issues) {
    if (issue.category !== "TECHNICAL" && issue.category !== "QUALITY") continue;
    const keys = taskKeys.get(issue.task_id) ?? new Set<string>();
    keys.add(issue.data_package_id);
    if (issue.recording_id) keys.add(issue.recording_id);
    taskKeys.set(issue.task_id, keys);
  }
  if (!taskKeys.size) return null;
  const targets = Array.from(taskKeys, ([taskId, issueKeys]) => {
    const task = tasks.find(item => item.taskId === taskId);
    return { taskId, name: task ? `${task.taskCode} · ${task.name}` : taskId, issueKeys };
  });
  return <FailedMcapRetry scope={scope} targets={targets} />;
}
