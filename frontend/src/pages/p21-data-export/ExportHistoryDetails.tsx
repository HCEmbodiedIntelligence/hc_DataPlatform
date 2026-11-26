import { useState } from "react";
import { Button, Space, Typography } from "antd";
import {
  decodedFileName,
  readableFallback,
  ResourceLabel,
} from "../../shared/lib/resource-labels";
import type { Scope } from "../../shared/scope/model";
import type {
  ExportItem,
  ExportRecord,
} from "../../features/exports/export-history";
export function ExportContents({
  items,
  raw,
  scope,
}: {
  items: ExportItem[];
  raw?: boolean;
  scope: Scope;
}) {
  const [open, setOpen] = useState(false);
  const [limit, setLimit] = useState(25);
  const datasets = [...new Set(items.map((item) => item.datasetId))];
  const batches = [
    ...new Set(items.flatMap((item) => item.batches ?? [])),
  ].sort((a, b) => a - b);
  return (
    <div style={{ minWidth: 0, overflowWrap: "anywhere" }}>
      <span>数据集 · </span>
      {datasets.slice(0, 3).map((id, index) => (
        <span key={id}>
          {index > 0 && "、"}
          <ResourceLabel kind="dataset" id={id} scope={scope} fallback={id} />
        </span>
      ))}
      {datasets.length > 3 && ` 等 ${datasets.length} 个`}
      {batches.length > 0 && (
        <span>
          {" "}
          · 上传批次 {batches.slice(0, 8).join("、")}
          {batches.length > 8 && ` 等 ${batches.length} 个`}
        </span>
      )}
      <details onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary style={{ cursor: "pointer" }}>
          {raw ? "查看原始批次明细" : "查看 Episode 明细"}（{items.length}）
        </summary>
        {open && (
          <>
            <ul>
              {items.slice(0, limit).map((item) => (
                <li
                  key={JSON.stringify([item.datasetId, item.id, item.version])}
                >
                  {raw ? (
                    item.id
                  ) : (
                    <ResourceLabel
                      kind="episode"
                      id={item.id}
                      datasetId={item.datasetId}
                      fallback={
                        readableFallback("episode", item.id) === "Episode"
                          ? item.id
                          : undefined
                      }
                      scope={scope}
                    />
                  )}
                  {" · "}
                  <ResourceLabel
                    kind="dataset"
                    id={item.datasetId}
                    fallback={item.datasetId}
                    scope={scope}
                  />
                  {item.version !== undefined && ` · 数据 v${item.version}`}
                  {item.frames !== undefined && ` · ${item.frames} 帧`}
                  {item.sourceFormat && ` · ${item.sourceFormat.toUpperCase()}`}
                  {item.episodes !== undefined &&
                    ` · ${item.episodes} 条 Episode`}
                  {item.files !== undefined && ` · ${item.files} 个文件`}
                  {item.batches?.length
                    ? ` · 上传批次 ${item.batches.join("、")}`
                    : null}
                  {item.taskId && (
                    <>
                      {" "}
                      · 采集任务{" "}
                      <ResourceLabel
                        kind="collection_task"
                        id={item.taskId}
                        fallback={item.taskId}
                        scope={scope}
                      />
                    </>
                  )}
                  {item.annotationRevision !== undefined &&
                    ` · 标注 v${item.annotationRevision}`}
                </li>
              ))}
            </ul>
            {items.length > limit && (
              <Button size="small" onClick={() => setLimit(limit + 50)}>
                查看更多明细
              </Button>
            )}
          </>
        )}
      </details>
    </div>
  );
}

export function ExportReceipt({
  record,
  scope,
  showContents = true,
}: {
  record: ExportRecord;
  scope: Scope;
  showContents?: boolean;
}) {
  const { uri, created, items, filename, format, selectedBatches } = record;
  const raw = record.source === "raw";
  return (
    <div style={{ minWidth: 0, overflowWrap: "anywhere" }}>
      {showContents && items && (
        <strong>
          {format} · {items.length} {raw ? "个原始批次" : "条 Episode"}
        </strong>
      )}
      <div>
        <Space wrap size="small">
          <time dateTime={new Date(created).toISOString()}>
            {new Date(created).toLocaleString("zh-CN")}
          </time>
          <span title={uri}>{filename ?? (uri && decodedFileName(uri))}</span>
          {uri ? (
            <Typography.Text
              copyable={{ text: uri, tooltips: ["复制 OSS 地址", "已复制"] }}
            >
              OSS 地址
            </Typography.Text>
          ) : (
            <span>下载 ZIP</span>
          )}
        </Space>
      </div>
      {selectedBatches?.length ? (
        <div>筛选批次 · {selectedBatches.join("、")}</div>
      ) : null}
      {showContents && items && <ExportContents items={items} raw={raw} scope={scope} />}
    </div>
  );
}
