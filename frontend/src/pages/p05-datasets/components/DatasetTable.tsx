import type { ColumnDef } from '@tanstack/react-table';
import { Button, Space } from 'antd';
import { Eye } from 'lucide-react';
import { useMemo } from 'react';
import type { DatasetId } from '../../../entities/dataset';
import type { CursorPageVm, DatasetListItemVm } from '../../../features/datasets/api';
import { DataTable, StatusTag } from '../../../shared/ui';
import styles from '../styles.module.css';

function actionAllowed(item: DatasetListItemVm, action: string): boolean {
  return item.allowedActions.some((candidate) => candidate.action === action && candidate.allowed);
}

export function DatasetTable({
  page,
  selectedDatasetId,
  canReadEpisodes,
  onSelect,
  onOpen,
  onOpenEpisodes,
}: Readonly<{
  page: CursorPageVm<DatasetListItemVm>;
  selectedDatasetId: DatasetId;
  canReadEpisodes: boolean;
  onSelect: (datasetId: DatasetId) => void;
  onOpen: (datasetId: DatasetId) => void;
  onOpenEpisodes: (item: DatasetListItemVm) => void;
}>) {
  const columns = useMemo<readonly ColumnDef<DatasetListItemVm, unknown>[]>(
    () => [
      {
        id: 'identity',
        header: '数据集',
        cell: ({ row }) => (
          <Button
            type="link"
            className={styles.identityButton}
            disabled={!actionAllowed(row.original, 'OPEN_DATASET')}
            onClick={() => onOpen(row.original.datasetId)}
          >
            <span className={styles.identity}>
              <strong>{row.original.name}</strong>
              <code>{row.original.datasetId}</code>
            </span>
          </Button>
        ),
      },
      {
        id: 'currentVersion',
        header: '当前 Ready',
        cell: ({ row }) => {
          const current = row.original.currentVersion;
          if (!current) {
            return <StatusTag status="PENDING_INGEST" label="待导入" tone="warning" known />;
          }
          const known = current.kind !== 'UNKNOWN';
          return (
            <span className={styles.versionCell}>
              <StatusTag
                status={current.kind}
                label={known ? current.displayVersion : `未知类型 · ${current.displayVersion}`}
                tone={known ? 'success' : 'warning'}
                known={known}
              />
              <code>{current.versionId}</code>
            </span>
          );
        },
      },
      { id: 'episodes', header: 'Episodes', cell: ({ row }) => row.original.episodeCount },
      {
        id: 'pendingReview',
        header: '待复核',
        cell: ({ row }) => row.original.pendingReviewVersionCount,
      },
      {
        id: 'returned',
        header: '已退回',
        cell: ({ row }) => row.original.returnedVersionCount,
      },
      {
        id: 'drafts',
        header: '可处理草稿',
        cell: ({ row }) => row.original.actionableDraftCount,
      },
      {
        id: 'activityAt',
        header: '活动时间',
        cell: ({ row }) => (
          <time dateTime={row.original.datasetActivityAt}>
            {new Date(row.original.datasetActivityAt).toLocaleString()}
          </time>
        ),
      },
      {
        id: 'actions',
        header: '操作',
        cell: ({ row }) => (
          <Space size="small" wrap>
            <Button
              type="link"
              icon={<Eye aria-hidden="true" size={14} />}
              className={styles.inspectButton}
              aria-pressed={row.original.datasetId === selectedDatasetId}
              data-row-selected={row.original.datasetId === selectedDatasetId}
              onClick={() => onSelect(row.original.datasetId)}
            >
              摘要
            </Button>
            <Button
              type="link"
              disabled={!actionAllowed(row.original, 'OPEN_DATASET')}
              onClick={() => onOpen(row.original.datasetId)}
            >
              打开
            </Button>
            {row.original.currentVersion &&
            canReadEpisodes &&
            actionAllowed(row.original, 'OPEN_EPISODE') ? (
              <Button type="link" onClick={() => onOpenEpisodes(row.original)}>
                Episodes
              </Button>
            ) : null}
          </Space>
        ),
      },
    ],
    [canReadEpisodes, onOpen, onOpenEpisodes, onSelect, selectedDatasetId],
  );

  return (
    <DataTable
      data={page.items}
      columns={columns}
      getRowId={(item) => item.datasetId}
      caption="数据集结果"
    />
  );
}
