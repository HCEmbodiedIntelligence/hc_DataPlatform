import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Checkbox, Space } from "antd";
import { request } from "../../shared/api/http-client";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
export function TaskDataRequirements({ taskId }: { taskId: string }) {
  const scope = useShellStore((s) => s.scope),
    canManage = useCapabilities().has("upload.manage");
  const path = `/projects/${scope?.projectId}/collection-tasks/${encodeURIComponent(taskId)}/data-requirements`;
  const query = useQuery({
    queryKey: ["task-data-requirements", scope, taskId],
    enabled: !!scope,
    queryFn: () =>
      request<{ requires_rgb: boolean; declared: boolean }>({
        method: "GET",
        path,
      }),
  });
  const save = useMutation({
    mutationFn: (requires_rgb: boolean) =>
      request({
        method: "PUT",
        path,
        body: { requires_rgb, allows_derived_action: false },
      }),
    onSuccess: () => query.refetch(),
  });
  return (
    <Space orientation="vertical">
      <Checkbox
        checked={query.data?.requires_rgb ?? false}
        disabled={!canManage || !query.data || save.isPending}
        onChange={(e) => save.mutate(e.target.checked)}
      >
        任务必须包含 RGB 图像（纯关节方案不可用于此任务）
      </Checkbox>
      {query.data && !query.data.declared && (
        <small>尚未声明视觉要求；请按任务实际需要确认。</small>
      )}
      {(query.error || save.error) && (
        <Alert type="error" title={(query.error || save.error)?.message} />
      )}
    </Space>
  );
}
