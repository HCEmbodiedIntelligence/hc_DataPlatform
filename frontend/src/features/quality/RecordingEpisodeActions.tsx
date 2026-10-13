import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Modal, Space } from "antd";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { components } from "../../shared/api/generated/platform";
import { request } from "../../shared/api/http-client";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import type { CollectionTaskScope } from "../collection-tasks/api";

export function RecordingEpisodeActions({
  scope,
  episode,
}: {
  scope: CollectionTaskScope;
  episode: components["schemas"]["EpisodeProcessing"];
}) {
  const capabilities = useCapabilities();
  const client = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const requestIds = useRef<Partial<Record<"retry" | "discard", string>>>({});
  const blocked =
    episode.status === "PENDING_QC" && Boolean(episode.dispatch_error_code);
  const stopped = blocked || ["QC_FAILED", "FAILED"].includes(episode.status);
  const mutation = useMutation({
    mutationFn: (action: "retry" | "discard") => {
      requestIds.current[action] ??= crypto.randomUUID();
      return request<components["schemas"]["EpisodeProcessing"]>({
        method: "POST",
        scope,
        path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/continuous-recordings/${encodeURIComponent(episode.recording_id)}/episodes/${encodeURIComponent(episode.episode_id)}:${action}`,
        body: {
          request_id: requestIds.current[action],
          expected_workflow_id: episode.workflow_id,
        },
      });
    },
    onSuccess: (_result, action) => {
      delete requestIds.current[action];
      setConfirmDelete(false);
      for (const key of [
        "continuous-recording-processing",
        "dashboard",
        "capture-progress",
        "quality-problems",
        "collection-task-progress",
      ])
        void client.invalidateQueries({ queryKey: [key] });
    },
    onError: () => {
      void client.invalidateQueries({
        queryKey: ["continuous-recording-processing"],
      });
    },
  });
  if (!capabilities.has("upload.manage")) return null;
  return (
    <Space orientation="vertical" style={{ width: "100%", marginTop: 12 }}>
      {blocked && (
        <span>
          修正采集任务与数据集的通道配置后，可重新检查并继续质检入库；原始文件和切片会保留。
        </span>
      )}
      {mutation.isError && (
        <Alert
          type="error"
          title="操作未完成"
          description={mutation.error.message}
        />
      )}
      <Space wrap>
        {stopped && (
          <Button
            type="primary"
            loading={mutation.isPending && mutation.variables === "retry"}
            disabled={mutation.isPending}
            onClick={() => mutation.mutate("retry")}
          >
            {blocked ? "重新检查处理配置" : "重新质检并继续处理"}
          </Button>
        )}
        {blocked && <Link to="/collection-tasks">查看采集任务配置</Link>}
        {stopped && (
          <Button
            danger
            disabled={mutation.isPending}
            onClick={() => setConfirmDelete(true)}
          >
            删除此 Episode
          </Button>
        )}
      </Space>
      <Modal
        open={confirmDelete}
        title={`删除 ${episode.episode_id}？`}
        okText="确认删除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        confirmLoading={mutation.isPending}
        onCancel={() => {
          if (!mutation.isPending) setConfirmDelete(false);
        }}
        onOk={() => mutation.mutate("discard")}
      >
        删除后，此 Episode
        将退出处理和入库。原始录制与操作记录保留，同批次的其他 Episode
        不受影响。
      </Modal>
    </Space>
  );
}
