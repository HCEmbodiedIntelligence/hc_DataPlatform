import type { ColumnDef } from '@tanstack/react-table';
import { Button } from 'antd';
import { useMemo } from 'react';
import type { DataSourceSummary, UnknownEnum } from '../../../entities/data-source';
import { credentialDisplay } from '../../../features/ingest/connectors/credential-boundary';
import { DataTable, StatusTag } from '../../../shared/ui';
import styles from '../styles.module.css';

function label(value: string | UnknownEnum): string {
  return typeof value === 'string' ? value : `未知（${value.raw}）`;
}

function connectivityTone(value: string | UnknownEnum) {
  if (typeof value !== 'string') return 'warning' as const;
  if (value === 'ONLINE') return 'success' as const;
  if (value === 'DEGRADED') return 'warning' as const;
  if (value === 'OFFLINE' || value === 'AUTH_FAILED' || value === 'CONFIG_ERROR') {
    return 'danger' as const;
  }
  return 'neutral' as const;
}

export function DataSourceTable(props: {
  readonly items: readonly DataSourceSummary[];
  readonly selectedId?: string;
  readonly onSelect: (id: string) => void;
}) {
  const columns = useMemo<readonly ColumnDef<DataSourceSummary, unknown>[]>(
    () => [
      {
        id: 'identity',
        header: '数据源',
        size: 175,
        cell: ({ row }) => (
          <span className={styles.identity}>
            <strong>{row.original.name}</strong>
            <code>{row.original.id}</code>
          </span>
        ),
      },
      {
        id: 'sourceType',
        header: '连接器',
        size: 110,
        cell: ({ row }) => label(row.original.sourceType),
      },
      {
        id: 'connectivity',
        header: '连接状态',
        size: 110,
        meta: { responsive: ['sm'] },
        cell: ({ row }) => {
          const value = row.original.connectivity.state;
          return (
            <StatusTag
              status={typeof value === 'string' ? value : value.raw}
              label={label(value)}
              tone={connectivityTone(value)}
              known={typeof value === 'string' && value !== 'UNKNOWN'}
            />
          );
        },
      },
      {
        id: 'credential',
        header: '凭据',
        size: 160,
        meta: { responsive: ['lg'] },
        cell: ({ row }) =>
          credentialDisplay(row.original.credential.maskedHint, row.original.credential.configured),
      },
      {
        id: 'lastUpload',
        header: '最近上传',
        size: 190,
        meta: { responsive: ['xl'] },
        cell: ({ row }) =>
          row.original.lastUpload ? (
            <time dateTime={row.original.lastUpload.completedAt}>
              {row.original.lastUpload.completedAt}
            </time>
          ) : (
            '暂无上传'
          ),
      },
      {
        id: 'actions',
        header: '操作',
        size: 72,
        meta: { responsive: ['md'] },
        cell: ({ row }) => (
          <Button
            type="link"
            aria-label={`查看 ${row.original.name}`}
            aria-pressed={row.original.id === props.selectedId}
            onClick={() => props.onSelect(row.original.id)}
          >
            查看
          </Button>
        ),
      },
    ],
    [props],
  );

  return (
    <DataTable
      data={props.items}
      columns={columns}
      getRowId={(source) => source.id}
      caption="数据源连接器列表"
      columnLayout="stable"
    />
  );
}
