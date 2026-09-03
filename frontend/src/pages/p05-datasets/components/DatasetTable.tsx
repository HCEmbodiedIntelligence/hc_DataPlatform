import type { ColumnDef } from "@tanstack/react-table";
import { Button, Space } from "antd";
import { useMemo } from "react";
import type { DatasetId } from "../../../entities/dataset";
import type { DatasetListItemVm } from "../../../features/datasets/api";
import { DataTable, StatusTag } from "../../../shared/ui";
import styles from "../styles.module.css";

const datasetActivityFormatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function actionAllowed(item: DatasetListItemVm, action: string): boolean {
  return item.allowedActions.some(
    (candidate) => candidate.action === action && candidate.allowed,
  );
}

export function DatasetTable({
  items,
  selectedDatasetId,
  canReadEpisodes,
  onSelect,
  onOpen,
  onOpenEpisodes,
}: Readonly<{
  items: readonly DatasetListItemVm[];
  selectedDatasetId: DatasetId | null;
  canReadEpisodes: boolean;
  onSelect: (datasetId: DatasetId) => void;
  onOpen: (datasetId: DatasetId) => void;
  onOpenEpisodes: (item: DatasetListItemVm) => void;
}>) {
  const columns = useMemo<readonly ColumnDef<DatasetListItemVm, unknown>[]>(
    () => [
      {
        id: "identity",
        header: "数据集",
        size: 280,
        cell: ({ row }) => (
          <span className={styles.identity}>
            <strong>{row.original.name}</strong>
            <code>{row.original.datasetId}</code>
          </span>
        ),
      },
      {
        id: "currentVersion",
        header: "当前 Ready",
        size: 165,
        cell: ({ row }) => {
          const current = row.original.currentVersion;
          if (!current) {
            return (
              <StatusTag
                status="PENDING_INGEST"
                label="待导入"
                tone="warning"
                known
              />
            );
          }
          const known = current.kind !== "UNKNOWN";
          return (
            <span className={styles.versionCell}>
              <StatusTag
                status={current.kind}
                label={
                  known
                    ? current.displayVersion
                    : `未知类型 · ${current.displayVersion}`
                }
                tone={known ? "success" : "warning"}
                known={known}
              />
            </span>
          );
        },
      },
      {
        id: "episodes",
        header: "Episodes",
        size: 70,
        meta: { responsive: ["md"] },
        cell: ({ row }) => row.original.episodeCount,
      },
      {
        id: "activityAt",
        header: "最近活动",
        size: 155,
        meta: { responsive: ["lg"] },
        cell: ({ row }) => (
          <time dateTime={row.original.datasetActivityAt}>
            {datasetActivityFormatter.format(
              new Date(row.original.datasetActivityAt),
            )}
          </time>
        ),
      },
      {
        id: "actions",
        header: "操作",
        size: 160,
        meta: { responsive: ["md"] },
        cell: ({ row }) => (
          <Space size="small" wrap>
            <Button
              type="link"
              disabled={!actionAllowed(row.original, "OPEN_DATASET")}
              onClick={() => onOpen(row.original.datasetId)}
            >
              打开
            </Button>
            {row.original.currentVersion &&
            canReadEpisodes &&
            actionAllowed(row.original, "OPEN_EPISODE") ? (
              <Button type="link" onClick={() => onOpenEpisodes(row.original)}>
                Episodes
              </Button>
            ) : null}
          </Space>
        ),
      },
    ],
    [canReadEpisodes, onOpen, onOpenEpisodes],
  );

  return (
    <DataTable
      data={items}
      columns={columns}
      getRowId={(item) => item.datasetId}
      caption="数据集结果"
      columnLayout="stable"
      rowInteraction={{
        activeRowId: selectedDatasetId,
        onActivate: (item) => onSelect(item.datasetId),
        getActivationLabel: (item) => `选择 ${item.name} 并查看摘要`,
      }}
    />
  );
}
