import type { ColumnDef } from '@tanstack/react-table';
import { Button } from 'antd';
import { useMemo } from 'react';
import type {
  CursorPageVm,
  EpisodeListItemVm,
  EpisodeRevisionWire,
  OperationalInventoryItemVm,
  RequiredStorageItemVm,
  VersionManifestWire,
  VersionSchemaVm,
} from '../../../features/datasets/api';
import { DataCursorPager, DataTable, StatusTag } from '../../../shared/ui';
import styles from '../styles.module.css';

type RevisionStream = EpisodeRevisionWire['streams'][number];
type ManifestEntry = VersionManifestWire['items'][number];
type SchemaChannel = VersionSchemaVm['channels'][number];

export function EpisodeRevisionTable({
  items,
  onInspect,
  onOpenViewer,
}: Readonly<{
  items: readonly EpisodeListItemVm[];
  onInspect: (item: EpisodeListItemVm) => void;
  onOpenViewer: (item: EpisodeListItemVm) => void;
}>) {
  const columns = useMemo<readonly ColumnDef<EpisodeListItemVm, unknown>[]>(
    () => [
      { id: 'episode', header: 'Episode', cell: ({ row }) => <code className={styles.tableCode} title={row.original.episodeId}>{row.original.episodeId}</code> },
      {
        id: 'revision',
        header: 'Revision',
        cell: ({ row }) => <Button className={styles.tableLink} type="link" onClick={() => onInspect(row.original)} title={row.original.selectedRevisionId}>{row.original.selectedRevisionId}</Button>,
      },
      { id: 'included', header: 'Included', cell: ({ row }) => <StatusTag status={row.original.included ? 'INCLUDED' : 'EXCLUDED'} tone={row.original.included ? 'info' : 'neutral'} /> },
      { id: 'review', header: 'Review', cell: ({ row }) => <StatusTag status={row.original.reviewStatus} label={`${row.original.reviewStatus} · ${row.original.reviewFindingCount}`} tone={row.original.reviewStatus === 'HAS_FINDING' ? 'warning' : row.original.reviewStatus === 'ACCEPTED' ? 'success' : 'neutral'} known={row.original.reviewStatus !== 'UNKNOWN'} /> },
      { id: 'actions', header: '操作', cell: ({ row }) => <Button type="link" onClick={() => onOpenViewer(row.original)}>只读 Viewer</Button> },
    ],
    [onInspect, onOpenViewer],
  );
  return <DataTable data={items} columns={columns} getRowId={(item) => item.episodeId} caption="版本 Episodes 与 Revisions" />;
}

export function RevisionStreamTable({ items }: Readonly<{ items: readonly RevisionStream[] }>) {
  const columns = useMemo<readonly ColumnDef<RevisionStream, unknown>[]>(() => [
    { id: 'stream', header: 'Stream', cell: ({ row }) => <code>{row.original.episode_stream_id}</code> },
    { id: 'channel', header: 'Channel', cell: ({ row }) => row.original.channel_path },
    { id: 'kind', header: 'Kind', cell: ({ row }) => row.original.kind },
    { id: 'range', header: 'Range ns', cell: ({ row }) => `${row.original.t_start_ns}–${row.original.t_end_ns}` },
  ], []);
  return <DataTable data={items} columns={columns} getRowId={(item) => item.episode_stream_id} caption="Revision Streams" />;
}

export function ManifestTable({ items }: Readonly<{ items: readonly ManifestEntry[] }>) {
  const columns = useMemo<readonly ColumnDef<ManifestEntry, unknown>[]>(() => [
    { id: 'entry', header: 'Entry', cell: ({ row }) => row.original.entry_id },
    { id: 'episode', header: 'Episode', cell: ({ row }) => row.original.episode_id },
    { id: 'role', header: 'Role', cell: ({ row }) => row.original.role },
    { id: 'bytes', header: 'Bytes', cell: ({ row }) => row.original.size_bytes },
    { id: 'sha', header: 'SHA-256', cell: ({ row }) => <code>{row.original.sha256.slice(0, 16)}…</code> },
  ], []);
  return <DataTable data={items} columns={columns} getRowId={(item) => item.entry_id} caption="Version Manifest 条目" />;
}

export function SchemaChannelTable({ items }: Readonly<{ items: readonly SchemaChannel[] }>) {
  const columns = useMemo<readonly ColumnDef<SchemaChannel, unknown>[]>(() => [
    { id: 'id', header: 'Channel ID', cell: ({ row }) => row.original.id },
    { id: 'name', header: 'Name', cell: ({ row }) => row.original.name },
    { id: 'dataType', header: 'Data type', cell: ({ row }) => row.original.dataType },
    { id: 'unit', header: 'Unit', cell: ({ row }) => row.original.unit ?? '—' },
  ], []);
  return <DataTable data={items} columns={columns} getRowId={(item) => item.id} caption="Schema Channels" />;
}

export function RequiredStorageTable({ items }: Readonly<{ items: readonly RequiredStorageItemVm[] }>) {
  const columns = useMemo<readonly ColumnDef<RequiredStorageItemVm, unknown>[]>(() => [
    { id: 'object', header: 'Object', cell: ({ row }) => row.original.objectId },
    { id: 'role', header: 'Role', cell: ({ row }) => row.original.role },
    { id: 'bytes', header: 'Bytes', cell: ({ row }) => row.original.sizeBytes },
    { id: 'reuse', header: 'Reuse', cell: ({ row }) => row.original.reuse },
    { id: 'protection', header: 'Protection', cell: ({ row }) => row.original.protection },
  ], []);
  return <DataTable data={items} columns={columns} getRowId={(item) => item.objectId} caption="Required storage" />;
}

export function InventoryTable({ items }: Readonly<{ items: readonly OperationalInventoryItemVm[] }>) {
  const columns = useMemo<readonly ColumnDef<OperationalInventoryItemVm, unknown>[]>(() => [
    { id: 'inventory', header: 'Inventory', cell: ({ row }) => row.original.inventoryId },
    { id: 'kind', header: 'Kind', cell: ({ row }) => row.original.kind },
    { id: 'status', header: 'Status', cell: ({ row }) => <StatusTag status={row.original.status} tone={row.original.status === 'SUCCEEDED' ? 'success' : row.original.status === 'FAILED' ? 'danger' : 'info'} known /> },
    { id: 'bytes', header: 'Bytes', cell: ({ row }) => row.original.sizeBytes },
    { id: 'revision', header: 'Revision', cell: ({ row }) => row.original.operationalRevision },
  ], []);
  return <DataTable data={items} columns={columns} getRowId={(item) => item.inventoryId} caption="Operational inventory" />;
}

export function VersionCursorPager({
  page,
  busy,
  onChange,
}: Readonly<{
  page: CursorPageVm<unknown>;
  busy?: boolean;
  onChange: (cursor: { before?: string; after?: string }) => void;
}>) {
  return <DataCursorPager pageInfo={{ startCursor: page.pageInfo.before, endCursor: page.pageInfo.after, hasPreviousPage: page.pageInfo.hasPreviousPage, hasNextPage: page.pageInfo.hasNextPage }} busy={busy} windowLabel={`当前窗口 ${page.items.length} 条 · 快照 ${page.snapshotAt}`} onChange={onChange} />;
}

export function ManifestCursorPager({
  pageInfo,
  count,
  snapshotAt,
  busy,
  onChange,
}: Readonly<{
  pageInfo: VersionManifestWire['page_info'];
  count: number;
  snapshotAt: string;
  busy?: boolean;
  onChange: (cursor: { before?: string; after?: string }) => void;
}>) {
  return <DataCursorPager pageInfo={{ startCursor: pageInfo.before, endCursor: pageInfo.after, hasPreviousPage: pageInfo.has_previous, hasNextPage: pageInfo.has_next }} busy={busy} windowLabel={`当前窗口 ${count} 条 · 快照 ${snapshotAt}`} onChange={onChange} />;
}
