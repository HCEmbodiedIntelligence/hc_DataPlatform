import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Space } from "antd";
import { useEffect } from "react";
import type { DashboardScope } from "../../../features/dashboard/types";
import { request } from "../../../shared/api/http-client";
import { useCapabilities } from "../../../shared/auth/use-capabilities";
import {
  nativeRoot,
  type NativeProgress,
} from "../../p03-upload-jobs/lerobot-processing";

export default function ResumeProcessingAction({
  scope,
  importId,
}: Readonly<{
  scope: DashboardScope;
  importId: string;
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
    enabled: canManage,
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
      void client.invalidateQueries({ queryKey: ["native-processing"] });
    }
  }, [
    client,
    result?.updated_at,
    result?.status,
    result?.ready,
    result?.failed,
  ]);
  const busy = ["PENDING", "RUNNING"].includes(result?.status ?? "");
  const retryable = ["FAILED", "PARTIALLY_FAILED", "CANCELLED"].includes(
    result?.status ?? "",
  );
  const error = retry.error ?? progress.error;
  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      <p style={{ margin: 0 }}>
        直接重试这批原始数据中未完成的
        Episode，已入库的数据会跳过。处理冲突可使用对应记录的单条重新处理入口。
      </p>
      {busy ? <Alert type="info" title="已提交，正在继续处理" /> : null}
      {result?.status === "SUCCEEDED" ? (
        <Alert type="success" title="本批次处理已完成" />
      ) : null}
      {result ? (
        <span>
          已就绪 {result.ready} · 处理未完成 {result.failed}
        </span>
      ) : null}
      {error ? (
        <Alert
          type="error"
          title="操作未完成"
          description={
            error instanceof Error ? error.message : "请刷新后重试。"
          }
        />
      ) : null}
      {!canManage ? <span>当前账户没有数据处理权限。</span> : null}
      <Space>
        <Button
          type="primary"
          loading={retry.isPending}
          disabled={!canManage || busy || !retryable || progress.isPending}
          onClick={() => retry.mutate()}
        >
          重试本批次未完成处理
        </Button>
        <Button
          disabled={!canManage}
          loading={progress.isFetching}
          onClick={() => void progress.refetch()}
        >
          刷新处理状态
        </Button>
      </Space>
    </Space>
  );
}
