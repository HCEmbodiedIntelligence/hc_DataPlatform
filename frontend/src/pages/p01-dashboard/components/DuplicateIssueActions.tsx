import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Modal, Space } from "antd";
import { useEffect, useState } from "react";
import type {
  DashboardDataIssue,
  DashboardScope,
} from "../../../features/dashboard/types";
import type { components } from "../../../shared/api/generated/platform";
import { request } from "../../../shared/api/http-client";
import { useCapabilities } from "../../../shared/auth/use-capabilities";
import { nativeRoot } from "../../p03-upload-jobs/lerobot-processing";

type Resolution = components["schemas"]["EpisodeResolution"];
type Action = "REPROCESS" | "DISCARD";
const statusLabels: Record<string, string> = {
  PENDING: "已提交，等待重新处理",
  RUNNING: "正在重新处理并入库",
  SUCCEEDED: "已重新处理并入库，可前往数据集查看",
  FAILED: "本次处理未完成，可查看错误原因后再次处理",
  DISCARDED: "已移除这条未入库记录，原始文件与历史处理结果已保留",
};

export default function DuplicateIssueActions({
  scope,
  issue,
}: Readonly<{
  scope: DashboardScope;
  issue: DashboardDataIssue;
}>) {
  const client = useQueryClient();
  const capabilities = useCapabilities();
  const [choice, setChoice] = useState<{
    action: Action;
    requestId: string;
  } | null>(null);
  const path = `${nativeRoot(scope)}/${encodeURIComponent(issue.source_import_id!)}/episodes/${issue.source_episode_index}/resolution`;
  const queryKey = [
    "episode-resolution",
    scope.organizationId,
    scope.projectId,
    scope.regionCode,
    issue.rollout_id,
  ];
  const progress = useQuery({
    queryKey,
    queryFn: ({ signal }) =>
      request<Resolution>({ method: "GET", path, scope, signal }),
    refetchInterval: (query) =>
      ["PENDING", "RUNNING"].includes(query.state.data?.status ?? "")
        ? 2_000
        : false,
    retry: false,
  });
  const mutation = useMutation({
    mutationFn: (command: { action: Action; requestId: string }) =>
      request<Resolution>({
        method: "POST",
        path,
        scope,
        body: {
          action: command.action,
          request_id: command.requestId,
          expected_attempt_id: issue.alignment_attempt_id,
        },
      }),
    onSuccess: (result) => {
      client.setQueryData(queryKey, result);
      setChoice(null);
      void client.invalidateQueries({ queryKey: ["native-processing"] });
    },
    onError: () => {
      void progress.refetch();
      void client.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
  const result = progress.data;
  useEffect(() => {
    if (result?.status && result.status !== "UNRESOLVED") {
      void client.invalidateQueries({ queryKey: ["dashboard"] });
    }
  }, [client, result?.status, result?.updated_at]);
  const busy =
    mutation.isPending || ["PENDING", "RUNNING"].includes(result?.status ?? "");
  const disabled =
    busy || !result?.available || !capabilities.has("upload.manage");
  const error = mutation.error ?? progress.error;
  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      <strong>解决这条数据的处理冲突</strong>
      <p style={{ margin: 0 }}>
        重新入库会为当前 Episode
        生成新的处理结果，保留原始文件和历史结果。移除表示放弃这条数据的本次入库。
      </p>
      {result && result.status !== "UNRESOLVED" ? (
        <Alert
          showIcon
          type={
            result.status === "FAILED"
              ? "error"
              : ["SUCCEEDED", "DISCARDED"].includes(result.status)
                ? "success"
                : "info"
          }
          title={statusLabels[result.status]}
          description={
            result.error_code ? `错误码：${result.error_code}` : undefined
          }
        />
      ) : null}
      {error ? (
        <Alert
          type="error"
          showIcon
          title="操作未完成"
          description={
            error instanceof Error ? error.message : "请刷新处理状态后重试。"
          }
        />
      ) : null}
      {!capabilities.has("upload.manage") ? (
        <span>当前账户没有数据处理权限。</span>
      ) : result?.unavailable_reason &&
        !busy &&
        !["SUCCEEDED", "DISCARDED"].includes(result.status) ? (
        <span>{result.unavailable_reason}</span>
      ) : null}
      <Space wrap>
        <Button
          type="primary"
          disabled={disabled}
          onClick={() => {
            mutation.reset();
            setChoice({ action: "REPROCESS", requestId: crypto.randomUUID() });
          }}
        >
          重新处理并入库
        </Button>
        <Button
          danger
          disabled={disabled}
          onClick={() => {
            mutation.reset();
            setChoice({ action: "DISCARD", requestId: crypto.randomUUID() });
          }}
        >
          移除这条数据
        </Button>
        <Button
          loading={progress.isFetching}
          onClick={() => {
            void progress.refetch();
          }}
        >
          刷新处理状态
        </Button>
      </Space>
      <Modal
        open={choice !== null}
        title={
          choice?.action === "DISCARD"
            ? "移除这条未入库的数据？"
            : "重新处理并入库？"
        }
        okText={choice?.action === "DISCARD" ? "确认移除" : "确认重新入库"}
        cancelText="取消"
        okButtonProps={{
          danger: choice?.action === "DISCARD",
          disabled: disabled,
        }}
        confirmLoading={mutation.isPending}
        closable={!mutation.isPending}
        cancelButtonProps={{ disabled: mutation.isPending }}
        onCancel={() => {
          if (!mutation.isPending) setChoice(null);
        }}
        onOk={() => {
          if (choice) mutation.mutate(choice);
        }}
      >
        <p>Episode {issue.source_episode_index}</p>
        <p>
          {choice?.action === "DISCARD"
            ? "确认后，这条记录会从待处理问题中移除，不再继续入库。原始文件和历史结果会保留，同批其他数据不受影响。"
            : "只重新处理当前 Episode。通过质检后写入数据集；原始文件和历史处理结果会保留，不会新增一份重复的原始数据。"}
        </p>
        {mutation.error ? (
          <Alert
            type="error"
            showIcon
            title="操作未完成"
            description={mutation.error.message}
          />
        ) : null}
      </Modal>
    </Space>
  );
}
