import type { ColumnDef } from '@tanstack/react-table';
import { Typography } from 'antd';
import { useMemo } from 'react';
import type { DashboardPendingItem } from '../../../features/dashboard/types';
import { DataTable, StatusTag } from '../../../shared/ui';
import styles from '../styles.module.css';

export function DashboardPendingList({ items }: Readonly<{ items: readonly DashboardPendingItem[] }>) {
  const columns = useMemo<readonly ColumnDef<DashboardPendingItem, unknown>[]>(
    () => [
      {
        id: 'kind',
        header: '类型',
        size: 150,
        cell: ({ row }) => row.original.wireType,
      },
      {
        id: 'detail',
        header: '详情',
        size: 360,
        cell: ({ row }) => (
          <span className={styles.pendingDetail}>
            <Typography.Text strong>{row.original.title}</Typography.Text>
            {row.original.summary ? (
              <Typography.Text type="secondary">{row.original.summary}</Typography.Text>
            ) : null}
          </span>
        ),
      },
      {
        id: 'status',
        header: '状态',
        cell: ({ row }) => (
          <StatusTag
            status={row.original.status}
            label={row.original.status}
            known={!row.original.hasUnknownEnum}
            tone="warning"
          />
        ),
      },
      { id: 'priority', header: '优先级', cell: ({ row }) => row.original.priority },
      {
        id: 'updatedAt',
        header: '更新时间',
        cell: ({ row }) => <time dateTime={row.original.updatedAt}>{row.original.updatedAt}</time>,
      },
      {
        id: 'target',
        header: '目标',
        cell: ({ row }) => row.original.clickable ? (
          <Typography.Text type="secondary" title="目标页 builder 待 Owner 交付">
            暂不可用
          </Typography.Text>
        ) : '—',
      },
    ],
    [],
  );

  return (
    <DataTable
      data={items}
      columns={columns}
      getRowId={(item) => item.itemId}
      caption="待办事项"
    />
  );
}
