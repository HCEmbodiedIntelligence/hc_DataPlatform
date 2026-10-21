import {
  captureProblemLabels,
  captureError,
} from "../../capture-schemes/presentation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Space } from "antd";
import { useEffect } from "react";
import type { IngestScope } from "../../../entities/data-source";
import { request } from "../../../shared/api/http-client";
import { useCapabilities } from "../../../shared/auth/use-capabilities";
import { nativeRoot, type NativeProgress } from "./lerobot-processing";
import { McapPreparationProgress } from "./components/McapPreparation";

export default function ResumeProcessingAction({
  scope,
  importId,
}: Readonly<{
  scope: IngestScope;
  importId: string;
  robotId?: string;
}>) {
  const client = useQueryClient();
  const capabilities = useCapabilities();
  const canManage = capabilities.has("upload.manage");
  const path = `${nativeRoot(scope)}/${encodeURIComponent(importId)}`;
  const queryKey = [
    "native-processing-detail",
    scope.organizationId,
    scope.projectId,
    scope.regionCode,
    importId,
  ];
  const progress = useQuery({
    queryKey,
    queryFn: ({ signal }) =>
      request<NativeProgress>({
        method: "GET",
        path: `${path}/processing`,
        scope,
        signal,
      }),
    enabled: capabilities.has("upload.read"),
    refetchInterval: (query) =>
      ["PENDING", "RUNNING"].includes(query.state.data?.status ?? "")
        ? 2_000
        : false,
    retry: false,
  });
  const retry = useMutation({
    mutationFn: () =>
      request<NativeProgress>({ method: "POST", path: `${path}:retry`, scope }),
    onSuccess: (result) => client.setQueryData(queryKey, result),
    onError: () => {
      void progress.refetch();
    },
  });
  const result = progress.data;
  useEffect(() => {
    if (result?.updated_at) {
      void client.invalidateQueries({ queryKey: ["dashboard"] });
      for (const key of [
        "native-processing",
        "capture-progress",
        "continuous-recordings",
        "collection-task-progress",
      ])
        void client.invalidateQueries({ queryKey: [key] });
    }
  }, [
    client,
    result?.updated_at,
    result?.status,
    result?.ready,
    result?.failed,
  ]);
  const isMcap = result?.source_format === "MCAP";
  const recordingPreparation =
    result?.source_format === "CAPTURE_BUNDLE" || isMcap;
  const busy = ["PENDING", "RUNNING"].includes(result?.status ?? "");
  const retryable = ["FAILED", "PARTIALLY_FAILED", "CANCELLED"].includes(
    result?.status ?? "",
  );
  const error = retry.error ?? progress.error;
  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      <p style={{ margin: 0 }}>
        {recordingPreparation
          ? "从平台已接收的原始文件重新准备录制，完成后即可进入录制切片。无需重新上传。"
          : "直接重试这批原始数据中未完成的 Episode，已入库的数据会跳过。处理冲突可使用对应记录的单条重新处理入口。"}
      </p>
      {busy ? <Alert type="info" title="已提交，正在继续处理" /> : null}
      {result?.status === "SUCCEEDED" ? (
        <Alert
          type="success"
          title={
            recordingPreparation
              ? "录制已准备完成，可进入切片"
              : "本批次处理已完成"
          }
        />
      ) : null}
      {result && !recordingPreparation ? (
        <span>
          已就绪 {result.ready} · 处理未完成 {result.failed}
        </span>
      ) : null}
      {isMcap && <McapPreparationProgress scope={scope} importId={importId} />}
      {result?.last_error_code &&
        captureProblemLabels[result.last_error_code] && (
          <Alert
            type="warning"
            title={captureProblemLabels[result.last_error_code]}
            description="原始数据保留。请检查采集方案及映射；标注配置独立处理。"
          />
        )}
      {error ? (
        <Alert
          type="error"
          title="操作未完成"
          description={captureError(error)}
        />
      ) : null}
      {!canManage ? <span>当前账户没有数据处理权限。</span> : null}
      <Space>
        {!isMcap && (
          <Button
            type="primary"
            loading={retry.isPending}
            disabled={!canManage || busy || !retryable || progress.isPending}
            onClick={() => retry.mutate()}
          >
            {recordingPreparation ? "重试录制准备" : "重试本批次未完成处理"}
          </Button>
        )}
        <Button
          disabled={!capabilities.has("upload.read")}
          loading={progress.isFetching}
          onClick={() => void progress.refetch()}
        >
          刷新处理状态
        </Button>
      </Space>
    </Space>
  );
}
