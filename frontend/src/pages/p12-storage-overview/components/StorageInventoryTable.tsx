import type { ColumnDef } from '@tanstack/react-table';
import { Button, Typography } from 'antd';
import { useMemo } from 'react';
import type { StorageInventoryFact } from '../../../entities/storage-inventory';
import { formatByteString } from '../../../features/storage-overview/metrics-contract';
import { DataTable, StatusTag } from '../../../shared/ui';
import { objectRoleLabel, objectStatusLabel, storageClassLabel } from '../display-labels';
import styles from '../styles.module.css';

export function StorageInventoryTable({
  items,
  onOpen,
}: Readonly<{
  items: readonly StorageInventoryFact[];
  onOpen: (objectId: string) => void;
}>) {
  const columns = useMemo<readonly ColumnDef<StorageInventoryFact, unknown>[]>(
    () => [
      {
        id: 'object',
        header: '对象路径',
        size: 240,
        cell: ({ row }) => (
          <Button
            type="link"
            className={styles.identityButton}
            onClick={() => onOpen(row.original.objectId)}
          >
            <span className={styles.identity}>
              <strong>{row.original.displayKey}</strong>
              <Typography.Text code>{row.original.objectId}</Typography.Text>
            </span>
          </Button>
        ),
      },
      {
        id: 'role',
        header: '角色',
        cell: ({ row }) => (
          <StatusTag
            status={row.original.objectRole}
            label={objectRoleLabel(row.original.objectRole)}
            known={row.original.objectRole !== 'UNKNOWN'}
          />
        ),
      },
      {
        id: 'storageClass',
        header: '层级',
        cell: ({ row }) => (
          <StatusTag
            status={row.original.storageClass}
            label={storageClassLabel(row.original.storageClass)}
            known={row.original.storageClass !== 'UNKNOWN'}
            tone="info"
          />
        ),
      },
      {
        id: 'physicalBytes',
        header: '占用空间',
        cell: ({ row }) => formatByteString(row.original.physicalBytes),
      },
      {
        id: 'referenceCount',
        header: '引用次数',
        cell: ({ row }) => row.original.referenceCount,
      },
      {
        id: 'status',
        header: '状态',
        cell: ({ row }) => (
          <StatusTag
            status={row.original.status}
            label={objectStatusLabel(row.original.status)}
            tone={row.original.status === 'AVAILABLE' ? 'success' : 'warning'}
          />
        ),
      },
    ],
    [onOpen],
  );

  return (
    <DataTable
      data={items}
      columns={columns}
      getRowId={(item) => item.objectId}
      caption="同一次盘点中的存储对象"
    />
  );
}
