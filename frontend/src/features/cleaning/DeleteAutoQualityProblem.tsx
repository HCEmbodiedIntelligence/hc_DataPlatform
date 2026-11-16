import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Modal, Space } from "antd";
import { useState } from "react";
import type { IngestScope } from "../../entities/data-source";
import type { AutoQualityProblem } from "./api";
import { discardAutoQualityProblem } from "./api/auto-quality-processing";

export function DeleteAutoQualityProblem({
  scope,
  problem,
  onDeleted,
  autoOpen = false,
  onClosed,
}: {
  scope: IngestScope;
  problem: AutoQualityProblem;
  onDeleted?: () => void;
  autoOpen?: boolean;
  onClosed?: () => void;
}) {
  const client = useQueryClient();
  const [requestId, setRequestId] = useState<string | null>(() =>
    autoOpen ? crypto.randomUUID() : null,
  );
  const mutation = useMutation({
    mutationFn: () => discardAutoQualityProblem(scope, problem, requestId!),
    onSuccess: () => {
      setRequestId(null);
      for (const key of [
        "quality-problems",
        "auto-quality-processing",
        "continuous-recording-processing",
        "native-processing",
        "dashboard",
        "capture-progress",
        "collection-task-progress",
      ])
        void client.invalidateQueries({ queryKey: [key] });
      onDeleted?.();
      onClosed?.();
    },
  });
  return (
    <>
      {!autoOpen && (
        <Button
          danger
          onClick={() => {
            mutation.reset();
            setRequestId(crypto.randomUUID());
          }}
        >
          丢弃
        </Button>
      )}
      <Modal
        title="丢弃这条问题数据？"
        open={Boolean(requestId)}
        okText="确认丢弃"
        cancelText="取消"
        confirmLoading={mutation.isPending}
        okButtonProps={{ danger: true }}
        closable={!mutation.isPending}
        keyboard={!mutation.isPending}
        mask={{ closable: !mutation.isPending }}
        cancelButtonProps={{ disabled: mutation.isPending }}
        onCancel={() => {
          if (!mutation.isPending) {
            setRequestId(null);
            onClosed?.();
          }
        }}
        onOk={() => mutation.mutate()}
      >
        <Space orientation="vertical">
          <strong>
            {problem.sourceFileName ??
              problem.recordingEpisodeId ??
              (problem.sourceEpisodeIndex != null
                ? `Episode ${problem.sourceEpisodeIndex}`
                : "整段录制")}
          </strong>
          <code>{problem.recordingId ?? problem.sourceImportId}</code>
          <p>
            丢弃后，这条数据
            将停止后续处理和入库。原始文件会保留，可在问题数据的“已丢弃”中追溯。
          </p>
          {mutation.isError && (
            <Alert
              type="error"
              title="丢弃未完成"
              description={mutation.error.message}
            />
          )}
        </Space>
      </Modal>
    </>
  );
}
