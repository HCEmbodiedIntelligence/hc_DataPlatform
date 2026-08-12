import type { ColumnDef } from '@tanstack/react-table';
import { Button, Progress, Space, Typography } from 'antd';
import { Eye } from 'lucide-react';
import { useMemo } from 'react';
import type { UploadSession } from '../../../features/ingest/upload/model';
import { useThrottledValue } from '../../../features/ingest/upload/use-throttled-value';
import { useAsyncJob } from '../../../shared/jobs/use-async-job';
import { DataTable, StatusTag, type StatusTone } from '../../../shared/ui';

function percent(received: string, expected: string | null): number | null {
  if (!expected || expected === '0') return null;
  return Number((BigInt(received) * 10_000n) / BigInt(expected)) / 100;
}

function enumText(value: string | { readonly raw: string }): string {
  return typeof value === 'string' ? value : `UNKNOWN (${value.raw})`;
}

function statusTone(status: string): StatusTone {
  if (['AVAILABLE', 'PASSED'].includes(status)) return 'success';
  if (['FAILED', 'QUARANTINED', 'CANCELLED', 'EXPIRED'].includes(status)) return 'danger';
  if (['UPLOADING', 'VERIFYING', 'RUNNING'].includes(status)) return 'info';
  if (['PAUSED', 'PENDING_VERIFY', 'QUEUED'].includes(status)) return 'warning';
  return 'neutral';
}

function UploadJobStatus({ jobId }: { readonly jobId: string }) {
  const job = useAsyncJob(jobId);
  const status = job.data?.status ?? 'QUEUED';
  return (
    <Space orientation="vertical" size={0} aria-live="polite">
      <StatusTag status={status} tone={statusTone(status)} />
      {job.connectionStatus === 'polling' ? <Typography.Text type="secondary">轮询</Typography.Text> : null}
    </Space>
  );
}

function UploadProgressCell({ session }: { readonly session: UploadSession }) {
  const visible = useThrottledValue(
    {
      received: session.progress.confirmedReceivedBytes,
      expected: session.progress.expectedBytes,
      phase: typeof session.lifecycleStatus === 'string' ? session.lifecycleStatus : 'UNKNOWN',
    },
    100,
  );
  const value = percent(visible.received, visible.expected);
  return value === null ? (
    <Typography.Text>{visible.received} 字节</Typography.Text>
  ) : (
    <div aria-label={`${session.uploadId} 上传进度`}>
      <Progress percent={value} size="small" format={(current) => `${Number(current).toFixed(1)}%`} />
    </div>
  );
}

export function UploadSessionTable(props: {
  readonly items: readonly UploadSession[];
  readonly selected: ReadonlySet<string>;
  readonly onSelectedChange: (uploadIds: readonly string[]) => void;
  readonly onOpen: (uploadId: string) => void;
}) {
  const columns = useMemo<readonly ColumnDef<UploadSession, unknown>[]>(
    () => [
      {
        id: 'identity',
        header: '任务',
        cell: ({ row }) => (
          <Space orientation="vertical" size={0}>
            <Typography.Text code>{row.original.uploadId}</Typography.Text>
            <Typography.Text type="secondary">
              <time dateTime={row.original.createdAt}>{row.original.createdAt}</time>
            </Typography.Text>
          </Space>
        ),
      },
      {
        id: 'target',
        header: '数据源 / Dataset',
        cell: ({ row }) => (
          <Space orientation="vertical" size={0}>
            <span>{row.original.dataSource.name}</span>
            <Typography.Text type="secondary">{row.original.targetDataset?.name ?? '未指定 Dataset'}</Typography.Text>
          </Space>
        ),
      },
      { id: 'progress', header: '进度', cell: ({ row }) => <UploadProgressCell session={row.original} /> },
      {
        id: 'status',
        header: '会话状态',
        cell: ({ row }) => {
          const lifecycle = enumText(row.original.lifecycleStatus);
          const verification = enumText(row.original.verificationStatus);
          return (
            <Space wrap size={[4, 4]}>
              <Space size={2}>
                <StatusTag
                  status={lifecycle}
                  label={lifecycle}
                  known={typeof row.original.lifecycleStatus === 'string'}
                  tone={statusTone(lifecycle)}
                />
                {typeof row.original.lifecycleStatus === 'string' ? null : <Typography.Text>{lifecycle}</Typography.Text>}
              </Space>
              <Space size={2}>
                <StatusTag
                  status={verification}
                  label={verification}
                  known={typeof row.original.verificationStatus === 'string'}
                  tone={statusTone(verification)}
                />
                {typeof row.original.verificationStatus === 'string' ? null : <Typography.Text>{verification}</Typography.Text>}
              </Space>
            </Space>
          );
        },
      },
      {
        id: 'job',
        header: '异步任务',
        cell: ({ row }) => row.original.activeJobIds[0] ? <UploadJobStatus jobId={row.original.activeJobIds[0]} /> : '—',
      },
      {
        id: 'actions',
        header: '操作',
        cell: ({ row }) => (
          <Button icon={<Eye aria-hidden="true" size={16} />} onClick={() => props.onOpen(row.original.uploadId)}>
            查看详情
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
      getRowId={(session) => session.uploadId}
      caption="上传会话任务列表"
      selection={{
        selectedRowIds: [...props.selected],
        onSelectedRowIdsChange: props.onSelectedChange,
        getRowSelectionLabel: (session) => `选择 ${session.uploadId}`,
        selectAllLabel: '选择当前上传任务窗口全部行',
      }}
    />
  );
}
