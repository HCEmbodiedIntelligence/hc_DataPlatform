import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Empty, Select, Space } from "antd";
import { useState } from "react";
import type { IngestScope } from "../../../entities/data-source";
import { request } from "../../../shared/api/http-client";
import { useScopeQueryRevision } from "../../../shared/api/use-scoped-query";
import {
  nativeRoot,
  type NativeProgress,
} from "../processing/lerobot-processing";
import { formatBytes } from "../upload/upload-contract";
import OriginalFileExplorer from "./OriginalFileExplorer";

export default function DatasetSourceFiles({
  scope,
  datasetId,
  initialImportId,
  recordingId,
}: {
  scope: IngestScope;
  datasetId?: string;
  initialImportId?: string;
  recordingId?: string;
}) {
  const [selected, setSelected] = useState(initialImportId);
  const [offset, setOffset] = useState(0);
  const revision = useScopeQueryRevision();
  const original = useQuery({
    queryKey: ["recording-source-files", revision, scope, recordingId],
    queryFn: ({ signal }) =>
      request<NativeProgress[]>({
        method: "GET",
        path: nativeRoot(scope),
        scope,
        scopeMode: "explicit",
        signal,
        cache: "no-store",
        query: {
          recording_id: recordingId,
          include_all_sources: true,
          limit: 50,
        },
      }),
    enabled: Boolean(recordingId) && !initialImportId,
    retry: false,
  });
  const activeId =
    selected ??
    (original.data?.length === 1 ? original.data[0]?.import_id : undefined);
  const query = useQuery({
    queryKey: ["dataset-source-files", revision, scope, datasetId, offset],
    queryFn: ({ signal }) =>
      request<NativeProgress[]>({
        method: "GET",
        path: nativeRoot(scope),
        scope,
        scopeMode: "explicit",
        signal,
        cache: "no-store",
        query: {
          dataset_id: datasetId,
          include_all_sources: true,
          offset,
          limit: 50,
        },
      }),
    retry: false,
  });
  return (
    <div style={{ display: "grid", gap: 16, minWidth: 0 }}>
      <p style={{ margin: 0, color: "var(--hc-color-text-muted)" }}>
        选择原始批次，浏览上传时的文件夹、文件和存储位置。
      </p>
      {recordingId && !initialImportId && original.isError && (
        <Alert
          type="warning"
          title="当前录制的原始批次读取失败"
          action={<Button onClick={() => void original.refetch()}>重试</Button>}
        />
      )}
      {query.isError && (
        <Alert
          type="error"
          title="原始批次读取失败"
          action={<Button onClick={() => void query.refetch()}>重试</Button>}
        />
      )}
      <Select
        aria-label="选择原始批次"
        placeholder="选择要查看的原始批次"
        loading={query.isPending}
        value={activeId}
        onChange={setSelected}
        style={{ width: "100%" }}
        options={[
          ...(activeId &&
          !query.data?.some((item) => item.import_id === activeId)
            ? [{ value: activeId, label: "当前原始批次" }]
            : []),
          ...(query.data ?? []).map((item) => ({
            value: item.import_id,
            label: `${item.source_format} · ${new Date(item.updated_at).toLocaleString("zh-CN", { hour12: false })} · ${item.file_count} 个文件 · ${formatBytes(item.total_bytes)}`,
          })),
        ]}
      />
      {(offset > 0 || (query.data?.length ?? 0) === 50) && (
        <Space>
          <Button
            disabled={offset === 0 || query.isFetching}
            onClick={() => setOffset(offset - 50)}
          >
            上一页批次
          </Button>
          <Button
            disabled={(query.data?.length ?? 0) < 50 || query.isFetching}
            onClick={() => setOffset(offset + 50)}
          >
            下一页批次
          </Button>
        </Space>
      )}
      {activeId ? (
        <OriginalFileExplorer scope={scope} importId={activeId} />
      ) : (
        !query.isPending &&
        !query.isError && (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              query.data?.length
                ? "选择批次后查看原始文件"
                : "暂无已提交的原始批次"
            }
          />
        )
      )}
    </div>
  );
}
