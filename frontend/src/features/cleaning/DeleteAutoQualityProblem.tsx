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
          删除
        </Button>
      )}
      <Modal
        title="删除这条问题数据？"
        open={Boolean(requestId)}
        okText="确认删除"
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
            {problem.recordingEpisodeId ??
              `Episode ${problem.sourceEpisodeIndex}`}
          </strong>
          <code>{problem.recordingId ?? problem.sourceImportId}</code>
          <p>
            删除后，这条 Episode
            将退出问题待处理列表，不再参与处理和入库。原始文件和操作记录会保留，同一录制或上传批次中的其他
            Episode 不受影响。
          </p>
          {mutation.isError && (
            <Alert
              type="error"
              title="删除未完成"
              description={mutation.error.message}
            />
          )}
        </Space>
      </Modal>
    </>
  );
}
