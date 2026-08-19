import type { ColumnDef } from '@tanstack/react-table';
import { useMemo } from 'react';
import type { StorageMultipartFact } from '../../../features/storage-overview/types';
import { formatByteString } from '../../../features/storage-overview/metrics-contract';
import { DataTable, StatusTag } from '../../../shared/ui';
import { formatChineseDateTime, multipartStatusLabel } from '../display-labels';

export function StorageMultipartTable({ items }: Readonly<{ items: readonly StorageMultipartFact[] }>) {
  const columns = useMemo<readonly ColumnDef<StorageMultipartFact, unknown>[]>(
    () => [
      { id: 'multipartId', header: '分片记录编号', size: 210, cell: ({ row }) => <code>{row.original.multipartId}</code> },
      { id: 'uploadId', header: '上传任务编号', size: 190, cell: ({ row }) => row.original.uploadId ?? '未关联' },
      {
        id: 'status',
        header: '状态',
        cell: ({ row }) => (
          <StatusTag
            status={row.original.status}
            label={multipartStatusLabel(row.original.status)}
            known={row.original.statusKnown}
            tone="warning"
          />
        ),
      },
      { id: 'receivedBytes', header: '已上传容量', cell: ({ row }) => formatByteString(row.original.receivedBytes) },
      {
        id: 'expectedBytes',
        header: '文件总容量',
        cell: ({ row }) => row.original.expectedBytes === null ? '未知' : formatByteString(row.original.expectedBytes),
      },
      { id: 'partCount', header: '分片数量', cell: ({ row }) => row.original.partCount },
      {
        id: 'lastActivityAt',
        header: '最后上传时间',
        size: 190,
        cell: ({ row }) => row.original.lastActivityAt
          ? formatChineseDateTime(row.original.lastActivityAt)
          : '未提供',
      },
    ],
    [],
  );

  return (
    <DataTable
      data={items}
      columns={columns}
      getRowId={(item) => item.multipartId}
      caption="未完成分片上传的只读诊断"
    />
  );
}
