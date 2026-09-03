import type { ColumnDef } from '@tanstack/react-table';
import { Button, Typography } from 'antd';
import { useMemo } from 'react';
import type { AuditEventView } from '../../../features/audit/types';
import { DataTable, StatusTag } from '../../../shared/ui';
import styles from '../styles.module.css';

function outcomeTone(status: AuditEventView['outcome']['status']) {
  if (status === 'SUCCEEDED') return 'success' as const;
  if (status === 'DENIED' || status === 'FAILED') return 'danger' as const;
  return 'warning' as const;
}

function riskTone(risk: AuditEventView['risk']['level']) {
  if (risk === 'LOW') return 'success' as const;
  if (risk === 'MEDIUM') return 'warning' as const;
  return 'danger' as const;
}

export function AuditEventTable({
  events,
  selectedEventId,
  onOpen,
}: Readonly<{
  events: readonly AuditEventView[];
  selectedEventId?: string;
  onOpen: (eventId: string) => void;
}>) {
  const columns = useMemo<readonly ColumnDef<AuditEventView, unknown>[]>(
    () => [
      { id: 'occurredAt', header: '时间', size: 190, cell: ({ row }) => <time dateTime={row.original.occurredAt}>{row.original.occurredAt}</time> },
      {
        id: 'eventName',
        header: '事件',
        size: 280,
        cell: ({ row }) => (
          <Button
            id={`audit-event-trigger-${row.original.eventId}`}
            type="link"
            className={styles.identityButton}
            aria-current={selectedEventId === row.original.eventId ? 'true' : undefined}
            onClick={() => onOpen(row.original.eventId)}
          >
            <span className={styles.identity}>
              <strong>{row.original.eventNameKnown ? row.original.eventName : '未登记事件'}</strong>
              <Typography.Text code>{row.original.eventId}</Typography.Text>
            </span>
          </Button>
        ),
      },
      { id: 'actor', header: 'Actor', cell: ({ row }) => row.original.actor.displayName },
      { id: 'resource', header: '目标', cell: ({ row }) => row.original.resource.displayName },
      {
        id: 'outcome',
        header: '结果',
        cell: ({ row }) => (
          <StatusTag
            status={row.original.outcome.status}
            label={row.original.outcome.status}
            known={row.original.outcome.status !== 'UNKNOWN'}
            tone={outcomeTone(row.original.outcome.status)}
          />
        ),
      },
      {
        id: 'risk',
        header: '风险',
        cell: ({ row }) => (
          <StatusTag
            status={row.original.risk.level}
            label={row.original.risk.level}
            known={row.original.risk.level !== 'UNKNOWN'}
            tone={riskTone(row.original.risk.level)}
          />
        ),
      },
      { id: 'requestId', header: '请求', cell: ({ row }) => <code>{row.original.request.requestId}</code> },
    ],
    [onOpen, selectedEventId],
  );

  return (
    <DataTable
      data={events}
      columns={columns}
      getRowId={(event) => event.eventId}
      caption="按 occurred_at DESC、event_id DESC 稳定排序"
    />
  );
}
