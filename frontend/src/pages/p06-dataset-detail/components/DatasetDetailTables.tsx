import type { ColumnDef } from "@tanstack/react-table";
import { Button, Space } from "antd";
import { Eye } from "lucide-react";
import { useMemo } from "react";
import type { DatasetVersion } from "../../../entities/dataset-version";
import type {
  CursorPageVm,
  EpisodeListItemVm,
  SourceProvenanceVm,
} from "../../../features/datasets/api";
import { CursorPager, DataTable, StatusTag } from "../../../shared/ui";
import {
  episodeInclusionLabel,
  episodeReviewPresentation,
  episodeSuccessPresentation,
} from "../episode-presentation";
import styles from "../styles.module.css";

function versionTone(status: DatasetVersion["status"]) {
  if (status === "READY") return "success" as const;
  if (status === "RETURNED") return "danger" as const;
  if (status === "REVIEWING") return "info" as const;
  return "warning" as const;
}

export function VersionTable({
  items,
  onOpen,
}: Readonly<{
  items: readonly DatasetVersion[];
  onOpen: (version: DatasetVersion) => void;
}>) {
  const columns = useMemo<readonly ColumnDef<DatasetVersion, unknown>[]>(
    () => [
      {
        id: "identity",
        header: "版本",
        cell: ({ row }) => (
          <span className={styles.identity}>
            <strong>{row.original.displayVersion}</strong>
            <code>{row.original.id}</code>
          </span>
        ),
      },
      { id: "kind", header: "类型", cell: ({ row }) => row.original.kind },
      {
        id: "status",
        header: "状态",
        cell: ({ row }) => (
          <StatusTag
            status={row.original.status}
            tone={versionTone(row.original.status)}
            known={row.original.status !== "UNKNOWN"}
          />
        ),
      },
      {
        id: "createdAt",
        header: "创建时间",
        cell: ({ row }) => (
          <time dateTime={row.original.createdAt}>
            {new Date(row.original.createdAt).toLocaleString()}
          </time>
        ),
      },
      {
        id: "actions",
        header: "操作",
        cell: ({ row }) => (
          <Button type="link" onClick={() => onOpen(row.original)}>
            打开
          </Button>
        ),
      },
    ],
    [onOpen],
  );

  return (
    <DataTable
      data={items}
      columns={columns}
      getRowId={(item) => item.id}
      caption="数据集版本"
    />
  );
}

export function EpisodeTable({
  items,
  onInspect,
  onOpenViewer,
  selectedEpisodeId,
}: Readonly<{
  items: readonly EpisodeListItemVm[];
  onInspect: (episode: EpisodeListItemVm) => void;
  onOpenViewer: (episode: EpisodeListItemVm) => void;
  selectedEpisodeId?: string;
}>) {
  const columns = useMemo<readonly ColumnDef<EpisodeListItemVm, unknown>[]>(
    () => [
      {
        id: "episode",
        header: "Episode",
        size: 256,
        cell: ({ row }) => (
          <Button
            type="link"
            className={styles.identityButton}
            onClick={() => onInspect(row.original)}
          >
            <span className={styles.identity}>
              <strong>#{row.original.ordinal + 1}</strong>
              <code title={row.original.episodeId}>
                {row.original.episodeId}
              </code>
            </span>
          </Button>
        ),
      },
      {
        id: "revision",
        header: "Revision / 纳入",
        size: 190,
        cell: ({ row }) => (
          <span className={styles.tableCellStack}>
            <code title={row.original.selectedRevisionId}>
              {row.original.selectedRevisionId}
            </code>
            <StatusTag
              status={row.original.included ? "INCLUDED" : "EXCLUDED"}
              label={episodeInclusionLabel(row.original.included)}
              tone={row.original.included ? "info" : "neutral"}
            />
          </span>
        ),
      },
      {
        id: "context",
        header: "任务 / 机器人",
        size: 200,
        cell: ({ row }) => (
          <span className={styles.tableCellStack}>
            <strong title={row.original.task ?? undefined}>
              {row.original.task ?? "—"}
            </strong>
            <code title={row.original.robotId ?? undefined}>
              {row.original.robotId ?? "—"}
            </code>
          </span>
        ),
      },
      {
        id: "storageRegion",
        header: "存储区域",
        size: 100,
        cell: ({ row }) => (
          <code title={row.original.storageRegionCode ?? undefined}>
            {row.original.storageRegionCode ?? "—"}
          </code>
        ),
      },
      {
        id: "quality",
        header: "处理 / 复核",
        size: 170,
        cell: ({ row }) => {
          const success = episodeSuccessPresentation(row.original.successState);
          const review = episodeReviewPresentation(
            row.original.reviewStatus,
            row.original.reviewFindingCount,
          );
          return (
            <span className={`${styles.tableCellStack} ${styles.qualityCell}`}>
              <StatusTag
                status={row.original.successState}
                label={success.label}
                tone={success.tone}
                known={success.known}
              />
              <StatusTag
                status={row.original.reviewStatus}
                label={review.label}
                tone={review.tone}
                known={review.known}
              />
            </span>
          );
        },
      },
      {
        id: "actions",
        header: "操作",
        size: 92,
        cell: ({ row }) => (
          <Space size="small">
            <Button
              type="link"
              icon={<Eye aria-hidden="true" size={15} />}
              onClick={() => onOpenViewer(row.original)}
            >
              查看
            </Button>
          </Space>
        ),
      },
    ],
    [onInspect, onOpenViewer],
  );

  return (
    <DataTable
      data={items}
      columns={columns}
      getRowId={(item) => item.episodeId}
      caption="固定版本 Episodes"
      columnLayout="stable"
      rowInteraction={{
        activeRowId: selectedEpisodeId ?? null,
        onActivate: onInspect,
        getActivationLabel: (item) => `查看 Episode #${item.ordinal + 1} 详情`,
      }}
    />
  );
}

export function SourceTable({
  items,
}: Readonly<{ items: readonly SourceProvenanceVm[] }>) {
  const columns = useMemo<readonly ColumnDef<SourceProvenanceVm, unknown>[]>(
    () => [
      {
        id: "source",
        header: "来源",
        cell: ({ row }) => (
          <span className={styles.identity}>
            <strong>{row.original.sourceDisplayName ?? "已脱敏"}</strong>
            <code>{row.original.sourceId ?? "—"}</code>
          </span>
        ),
      },
      {
        id: "upload",
        header: "Upload",
        cell: ({ row }) => <code>{row.original.uploadId}</code>,
      },
      {
        id: "manifest",
        header: "数据清单",
        cell: ({ row }) => <code>{row.original.sourceManifestId}</code>,
      },
      {
        id: "storageRegion",
        header: "存储区域",
        cell: ({ row }) => <code>{row.original.storageRegionCode ?? "—"}</code>,
      },
      {
        id: "registeredAt",
        header: "注册时间",
        cell: ({ row }) => (
          <time dateTime={row.original.registeredAt}>
            {new Date(row.original.registeredAt).toLocaleString()}
          </time>
        ),
      },
    ],
    [],
  );

  return (
    <DataTable
      data={items}
      columns={columns}
      getRowId={(item) => item.provenanceId}
      caption="安全来源证据"
    />
  );
}

export function DatasetCursorPager({
  page,
  busy,
  onChange,
}: Readonly<{
  page: CursorPageVm<unknown>;
  busy?: boolean;
  onChange: (cursor: { before?: string; after?: string }) => void;
}>) {
  return (
    <CursorPager
      pageInfo={{
        startCursor: page.pageInfo.before,
        endCursor: page.pageInfo.after,
        hasPreviousPage: page.pageInfo.hasPreviousPage,
        hasNextPage: page.pageInfo.hasNextPage,
      }}
      busy={busy}
      windowLabel={`当前窗口 ${page.items.length} 条`}
      onChange={onChange}
    />
  );
}
