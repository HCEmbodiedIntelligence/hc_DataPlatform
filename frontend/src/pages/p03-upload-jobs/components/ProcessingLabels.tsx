import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Input, Space } from "antd";
import { useState } from "react";
import type { IngestScope } from "../../../entities/data-source";
import { request } from "../../../shared/api/http-client";
import { nativeRoot } from "../lerobot-processing";

export const processingConfigurationKey = (
  scope: IngestScope,
  datasetId: string,
) => [
  "native-configuration",
  scope.organizationId,
  scope.projectId,
  scope.regionCode,
  datasetId,
];

export function useProcessingConfiguration(
  scope: IngestScope,
  datasetId: string,
  enabled: boolean,
) {
  return useQuery({
    queryKey: processingConfigurationKey(scope, datasetId),
    enabled: enabled && Boolean(datasetId),
    queryFn: ({ signal }) =>
      request<{ configured: boolean }>({
        method: "GET",
        path: `${nativeRoot(scope)}/configuration`,
        scope,
        signal,
        query: { dataset_id: datasetId },
      }),
  });
}

export function ProcessingLabels({
  scope,
  datasetId,
}: {
  readonly scope: IngestScope;
  readonly datasetId: string;
}) {
  const client = useQueryClient();
  const [labels, setLabels] = useState("");
  const configuration = useProcessingConfiguration(scope, datasetId, true);
  const save = useMutation({
    mutationFn: () =>
      request({
        method: "POST",
        path: `${nativeRoot(scope)}/configuration`,
        scope,
        body: {
          dataset_id: datasetId,
          labels: labels
            .split(/[\n,，]/u)
            .map((v) => v.trim())
            .filter(Boolean),
        },
      }),
    onSuccess: () =>
      client.invalidateQueries({
        queryKey: processingConfigurationKey(scope, datasetId),
      }),
  });
  if (!datasetId) return null;
  if (configuration.data?.configured)
    return <p>已关联标注规则，处理完成后自动创建标注任务。</p>;
  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      <p>请先配置该数据集的标注标签，或在数据规则管理中关联已有规则。</p>
      <Input.TextArea
        aria-label="标注标签"
        value={labels}
        onChange={(e) => setLabels(e.target.value)}
        placeholder="每行一个标签"
      />
      <Button
        loading={save.isPending}
        disabled={!labels.trim()}
        onClick={() => save.mutate()}
      >
        保存并发布标注规则
      </Button>
      {configuration.isError || save.isError ? (
        <Alert
          type="error"
          title="标注规则配置失败"
          description={(configuration.error ?? save.error)?.message}
        />
      ) : null}
    </Space>
  );
}
