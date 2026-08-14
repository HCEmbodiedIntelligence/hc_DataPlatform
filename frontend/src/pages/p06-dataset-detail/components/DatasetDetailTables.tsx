import type { ColumnDef } from '@tanstack/react-table';
import { Button, Space } from 'antd';
import { useMemo } from 'react';
import type { DatasetVersion } from '../../../entities/dataset-version';
import type {
  CursorPageVm,
  EpisodeListItemVm,
  SourceProvenanceVm,
} from '../../../features/datasets/api';
import { DataCursorPager, DataTable, StatusTag } from '../../../shared/ui';
import styles from '../styles.module.css';

function versionTone(status: DatasetVersion['status']) {
  if (status === 'READY') return 'success' as const;
  if (status === 'RETURNED') return 'danger' as const;
  if (status === 'REVIEWING') return 'info' as const;
  return 'warning' as const;
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
        id: 'identity',
        header: '版本',
        cell: ({ row }) => (
          <span className={styles.identity}>
            <strong>{row.original.displayVersion}</strong>
            <code>{row.original.id}</code>
          </span>
        ),
      },
      { id: 'kind', header: '类型', cell: ({ row }) => row.original.kind },
      {
        id: 'status',
        header: '状态',
        cell: ({ row }) => (
          <StatusTag
            status={row.original.status}
            tone={versionTone(row.original.status)}
            known={row.original.status !== 'UNKNOWN'}
          />
        ),
      },
      {
        id: 'createdAt',
        header: '创建时间',
        cell: ({ row }) => (
          <time dateTime={row.original.createdAt}>
            {new Date(row.original.createdAt).toLocaleString()}
          </time>
        ),
      },
      {
        id: 'actions',
        header: '操作',
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
}: Readonly<{
  items: readonly EpisodeListItemVm[];
  onInspect: (episode: EpisodeListItemVm) => void;
  onOpenViewer: (episode: EpisodeListItemVm) => void;
}>) {
  const columns = useMemo<readonly ColumnDef<EpisodeListItemVm, unknown>[]>(
    () => [
      {
        id: 'episode',
        header: 'Episode',
        cell: ({ row }) => (
          <Button type="link" className={styles.identityButton} onClick={() => onInspect(row.original)}>
            <span className={styles.identity}>
              <strong>#{row.original.ordinal + 1}</strong>
              <code>{row.original.episodeId}</code>
            </span>
          </Button>
        ),
      },
      {
        id: 'revision',
        header: 'Revision / Included',
        cell: ({ row }) => <span className={styles.tableCellStack}><code>{row.original.selectedRevisionId}</code><StatusTag status={row.original.included ? 'INCLUDED' : 'EXCLUDED'} tone={row.original.included ? 'info' : 'neutral'} /></span>,
      },
      {
        id: 'context',
        header: '任务 / 机器人',
        cell: ({ row }) => <span className={styles.tableCellStack}><strong>{row.original.task ?? '—'}</strong><code>{row.original.robotId ?? '—'}</code></span>,
      },
      {
        id: 'quality',
        header: '质量状态',
        cell: ({ row }) => (
          <span className={styles.tableCellStack}>
            <StatusTag
              status={row.original.successState}
              tone={row.original.successState === 'SUCCEEDED' ? 'success' : row.original.successState === 'FAILED' ? 'danger' : 'warning'}
              known={row.original.successState !== 'UNKNOWN'}
            />
            <StatusTag
              status={row.original.reviewStatus}
              label={`${row.original.reviewStatus} · ${row.original.reviewFindingCount}`}
              tone={row.original.reviewStatus === 'HAS_FINDING' ? 'warning' : row.original.reviewStatus === 'ACCEPTED' ? 'success' : 'neutral'}
              known={row.original.reviewStatus !== 'UNKNOWN'}
            />
          </span>
        ),
      },
      {
        id: 'actions',
        header: '操作',
        cell: ({ row }) => (
          <Space size="small">
            <Button type="link" onClick={() => onOpenViewer(row.original)}>
              只读 Viewer
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
    />
  );
}

export function SourceTable({ items }: Readonly<{ items: readonly SourceProvenanceVm[] }>) {
  const columns = useMemo<readonly ColumnDef<SourceProvenanceVm, unknown>[]>(
    () => [
      {
        id: 'source',
        header: '来源',
        cell: ({ row }) => (
          <span className={styles.identity}>
            <strong>{row.original.sourceDisplayName ?? '已脱敏'}</strong>
            <code>{row.original.sourceId ?? '—'}</code>
          </span>
        ),
      },
      { id: 'upload', header: 'Upload', cell: ({ row }) => <code>{row.original.uploadId}</code> },
      {
        id: 'manifest',
        header: 'Manifest',
        cell: ({ row }) => <code>{row.original.sourceManifestId}</code>,
      },
      {
        id: 'registeredAt',
        header: '注册时间',
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
    <DataCursorPager
      pageInfo={{
        startCursor: page.pageInfo.before,
        endCursor: page.pageInfo.after,
        hasPreviousPage: page.pageInfo.hasPreviousPage,
        hasNextPage: page.pageInfo.hasNextPage,
      }}
      busy={busy}
      windowLabel={`当前窗口 ${page.items.length} 条 · 快照 ${page.snapshotAt}`}
      onChange={onChange}
    />
  );
}
