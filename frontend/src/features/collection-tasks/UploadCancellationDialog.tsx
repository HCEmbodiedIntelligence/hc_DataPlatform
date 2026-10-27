import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Alert, Modal } from "antd";
import { useState } from "react";
import { request } from "../../shared/api/http-client";
import type { CollectionTaskScope } from "./api";

export interface CancellableUpload {
  readonly upload_id: string;
  readonly source_path?: string | null;
}

export function UploadCancellationDialog({
  scope,
  items,
  onClose,
  onCompleted,
}: {
  readonly scope: CollectionTaskScope;
  readonly items: readonly CancellableUpload[];
  readonly onClose: () => void;
  readonly onCompleted: (count: number) => void;
}) {
  const client = useQueryClient();
  const [remaining, setRemaining] = useState(items);
  const [failures, setFailures] = useState<string[]>([]);
  const mutation = useMutation({
    mutationFn: async () => {
      const failed: CancellableUpload[] = [],
        messages: string[] = [];
      let completed = 0;
      for (const item of remaining) {
        try {
          await request({
            method: "POST",
            path: `/projects/${encodeURIComponent(scope.projectId)}/robot-ingest/uploads/${encodeURIComponent(item.upload_id)}:cancel`,
            scope,
            scopeMode: "explicit",
            cache: "no-store",
          });
          completed++;
        } catch (error) {
          failed.push(item);
          messages.push(
            `${item.source_path ?? item.upload_id}：${error instanceof Error ? error.message : "取消失败，请重试"}`,
          );
        }
      }
      setRemaining(failed);
      setFailures(messages);
      await Promise.all(
        [
          "capture-progress",
          "dashboard",
          "collection-task-progress",
          "robot-ingest",
        ].map((key) => client.invalidateQueries({ queryKey: [key] })),
      );
      if (completed) onCompleted(completed);
      if (!failed.length) onClose();
    },
  });
  return (
    <Modal
      open
      title={`取消 ${remaining.length} 条未完成上传？`}
      okText={failures.length ? "重试取消" : "确认取消上传"}
      cancelText="暂不取消"
      okButtonProps={{ danger: true }}
      confirmLoading={mutation.isPending}
      cancelButtonProps={{ disabled: mutation.isPending }}
      closable={!mutation.isPending}
      mask={{ closable: !mutation.isPending }}
      onCancel={() => {
        if (!mutation.isPending) onClose();
      }}
      onOk={() => mutation.mutate()}
    >
      <p>
        取消后将停止继续上传和提交，并清理未完成的上传分片。需要重新上传时，请创建新的上传。
      </p>
      <ul>
        {remaining.map((item) => (
          <li key={item.upload_id}>{item.source_path ?? item.upload_id}</li>
        ))}
      </ul>
      {failures.length > 0 && (
        <Alert
          type="error"
          showIcon
          title="部分上传未能取消"
          description={failures.map((message) => (
            <p key={message}>{message}</p>
          ))}
        />
      )}
    </Modal>
  );
}
