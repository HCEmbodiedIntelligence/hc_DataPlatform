import { Alert, Button, Form, InputNumber, Select, Space } from "antd";
import { useEffect, useState } from "react";
import { useScopedQuery } from "../../shared/api/use-scoped-query";
import { request } from "../../shared/api/http-client";
import { useIngestScope } from "../ingest/use-ingest-scope";
import {
  nativeRoot,
  type NativeProgress,
} from "../ingest/processing/lerobot-processing";
import { getOriginalFiles } from "../ingest/browser/original-source-api";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import type { Sample, SourceFormat } from "./contracts";
import { captureError } from "./presentation";
import {
  collectionTaskGateway,
  type CollectionTask,
} from "../collection-tasks/api";

export function SamplePicker({
  formats,
  value,
  onChange,
  label = "验证样本",
}: {
  formats: SourceFormat[];
  value: Sample | null;
  onChange: (sample: Sample | null) => void;
  label?: string;
}) {
  const scope = useIngestScope();
  const capabilities = useCapabilities();
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    setOffset(0);
  }, [scope?.organizationId, scope?.projectId, scope?.regionCode]);
  const tasks = useScopedQuery(scope, "capture-samples", "tasks", null, {
    enabled: Boolean(scope) && capabilities.has("upload.read"),
    retry: false,
    queryFn: async ({ signal }) => {
      const items: CollectionTask[] = [];
      let cursor: string | undefined;
      do {
        const page = await collectionTaskGateway.list(
          scope!,
          { limit: 100, cursor },
          signal,
        );
        items.push(...page.items);
        cursor = page.next_cursor ?? undefined;
      } while (cursor);
      return items;
    },
  });
  const taskById = new Map(
    tasks.data?.map((task) => [task.collection_task_id, task]),
  );
  const records = useScopedQuery(
    scope,
    "capture-samples",
    "list",
    { offset },
    {
      enabled: Boolean(scope) && capabilities.has("upload.read"),
      retry: false,
      queryFn: ({ signal }) =>
        request<NativeProgress[]>({
          method: "GET",
          path: nativeRoot(scope!),
          scope: scope!,
          scopeMode: "explicit",
          signal,
          query: { offset, limit: 50 },
        }),
    },
  );
  const selected =
    value &&
    scope &&
    value.project_id === scope.projectId &&
    value.region_code === scope.regionCode
      ? value
      : null;
  const stale = Boolean(
    value && (!selected || !formats.includes(value.source_format)),
  );
  useEffect(() => {
    if (stale) onChange(null);
  }, [stale, onChange]);
  const files = useScopedQuery(
    scope,
    "capture-samples",
    "files",
    selected?.raw_source_id,
    {
      enabled: Boolean(selected?.source_format === "MCAP"),
      retry: false,
      queryFn: async ({ signal }) => {
        const paths: string[] = [];
        let at = 0;
        while (true) {
          const page = await getOriginalFiles(
            scope!,
            selected!.raw_source_id,
            at,
            signal,
          );
          paths.push(
            ...page.files
              .filter((file) => file.path.endsWith(".mcap"))
              .map((file) => file.path),
          );
          at += page.files.length;
          if (at >= page.total || !page.files.length) break;
        }
        return paths;
      },
    },
  );
  if (!scope)
    return (
      <Alert
        type="info"
        title="请选择样本所在项目和 Region，再进行样本验证。"
      />
    );
  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
      <p>
        使用当前项目的已上传原始样本。设备映射验证还会检查样本来自当前实例。范围：
        {scope.projectId} / {scope.regionCode}
      </p>
      {(records.error || files.error || tasks.error) && (
        <Alert
          type="error"
          title={captureError(records.error ?? files.error ?? tasks.error)}
          action={
            <Button
              onClick={() => {
                void records.refetch();
                void tasks.refetch();
                if (selected?.source_format === "MCAP") void files.refetch();
              }}
            >
              重试读取
            </Button>
          }
        />
      )}
      <Form.Item label={label} style={{ width: "100%", marginBottom: 8 }}>
        <Select
          aria-label={label}
          placeholder="搜索任务编号、名称或原始记录 ID"
          style={{ width: "100%" }}
          showSearch
          optionFilterProp="label"
          loading={records.isFetching || tasks.isFetching}
          value={selected?.raw_source_id}
          onChange={(id) => {
            const row = records.data?.find((r) => r.import_id === id);
            if (!row) return;
            onChange({
              raw_source_id: id,
              source_format: row.source_format as SourceFormat,
              source_path:
                row.source_format === "MCAP"
                  ? ""
                  : row.source_format === "CAPTURE_BUNDLE"
                    ? "recording-complete.json"
                    : "meta/info.json",
              project_id: scope.projectId,
              region_code: scope.regionCode,
              episode_index: 0,
            });
          }}
          options={(records.data ?? [])
            .filter((row) =>
              formats.includes(row.source_format as SourceFormat),
            )
            .map((row) => {
              const task = taskById.get(row.collection_task_id ?? "");
              const title = task
                ? `任务${Number(task.task_code) || task.task_code} · ${task.name}`
                : "未关联任务的样本";
              const status = row.failed
                ? `处理失败 ${row.failed} 段`
                : row.ready
                  ? `已就绪 ${row.ready} 段`
                  : ({
                      READY: "已就绪",
                      PROCESSING: "处理中",
                      FAILED: "处理失败",
                      PENDING: "待处理",
                      REGISTERED: "已登记",
                    }[row.status] ?? row.status);
              return {
                value: row.import_id,
                label: `${title} · ${status} · ${row.source_format === "LEROBOT_V3" ? "LeRobot v3" : row.source_format} · ${row.updated_at} · ${row.import_id}`,
              };
            })}
        />
      </Form.Item>
      <Space>
        <Button
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - 50))}
        >
          上一页样本
        </Button>
        <Button
          disabled={(records.data?.length ?? 0) < 50}
          onClick={() => setOffset(offset + 50)}
        >
          下一页样本
        </Button>
      </Space>
      {selected?.source_format === "MCAP" ? (
        <Form.Item label="样本文件">
          <Select
            aria-label="样本文件"
            value={selected.source_path || undefined}
            loading={files.isFetching}
            options={files.data?.map((path) => ({ value: path, label: path }))}
            onChange={(source_path) => onChange({ ...selected, source_path })}
          />
        </Form.Item>
      ) : (
        selected?.source_format === "LEROBOT_V3" && (
          <Form.Item label="样本片段序号">
            <InputNumber
              aria-label="样本片段序号"
              min={0}
              precision={0}
              value={selected.episode_index}
              onChange={(v) => onChange({ ...selected, episode_index: v ?? 0 })}
            />
          </Form.Item>
        )
      )}
    </Space>
  );
}
