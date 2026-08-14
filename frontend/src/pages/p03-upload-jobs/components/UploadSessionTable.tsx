import type { ColumnDef } from '@tanstack/react-table';
import { Button, Progress, Space, Typography } from 'antd';
import { ArrowRight, Eye, Pause, RotateCcw } from 'lucide-react';
import { useMemo } from 'react';
import type { UploadSession } from '../../../features/ingest/upload/model';
import { useThrottledValue } from '../../../features/ingest/upload/use-throttled-value';
import { useAsyncJob } from '../../../shared/jobs/use-async-job';
import { DataTable, StatusTag, type StatusTone } from '../../../shared/ui';

function percent(received: string, expected: string | null): number | null {
  if (!expected || expected === '0') return null;
  return Number((BigInt(received) * 10_000n) / BigInt(expected)) / 100;
}

function byteSize(value: string | null): string {
  if (value === null) return '—';
  const bytes = BigInt(value);
  const units = [
    { unit: 'TB', divisor: 1_099_511_627_776n },
    { unit: 'GB', divisor: 1_073_741_824n },
    { unit: 'MB', divisor: 1_048_576n },
  ] as const;
  const selected = units.find(({ divisor }) => bytes >= divisor);
  if (!selected) return `${value} B`;
  const tenths = (bytes * 10n) / selected.divisor;
  return `${tenths / 10n}.${tenths % 10n} ${selected.unit}`;
}

function duration(seconds: string | null): string {
  if (seconds === null) return '—';
  const total = BigInt(seconds);
  const hours = total / 3600n;
  const minutes = (total % 3600n) / 60n;
  const remaining = total % 60n;
  return [hours, minutes, remaining].map((value) => value.toString().padStart(2, '0')).join(':');
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
      {job.connectionStatus === 'polling' ? (
        <Typography.Text type="secondary">轮询</Typography.Text>
      ) : null}
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
      <Progress
        percent={value}
        size="small"
        format={(current) => `${Number(current).toFixed(1)}%`}
      />
    </div>
  );
}

export function UploadSessionTable(props: {
  readonly items: readonly UploadSession[];
  readonly selected: ReadonlySet<string>;
  readonly focusedUploadId?: string;
  readonly onSelectedChange: (uploadIds: readonly string[]) => void;
  readonly onFocus: (uploadId: string) => void;
  readonly onOpen: (uploadId: string) => void;
}) {
  const columns = useMemo<readonly ColumnDef<UploadSession, unknown>[]>(
    () => [
      {
        id: 'identity',
        size: 160,
        header: '任务 ID',
        cell: ({ row }) => (
          <Space orientation="vertical" size={0}>
            <Button
              type="link"
              className="upload-session-focus"
              aria-pressed={props.focusedUploadId === row.original.uploadId}
              onClick={() => props.onFocus(row.original.uploadId)}
            >
              <Typography.Text code>{row.original.uploadId}</Typography.Text>
            </Button>
            <Typography.Text type="secondary">
              <time dateTime={row.original.createdAt}>{row.original.createdAt}</time>
            </Typography.Text>
          </Space>
        ),
      },
      {
        id: 'target',
        size: 150,
        header: '数据集 / 数据源',
        cell: ({ row }) => (
          <Space orientation="vertical" size={0}>
            <span>{row.original.targetDataset?.name ?? '未指定 Dataset'}</span>
            <Typography.Text type="secondary">{row.original.dataSource.name}</Typography.Text>
          </Space>
        ),
      },
      { id: 'format', size: 90, header: '源格式', cell: ({ row }) => row.original.sourceFormat },
      {
        id: 'objects',
        size: 65,
        header: '文件数',
        cell: ({ row }) => row.original.progress.totalObjects,
      },
      {
        id: 'bytes',
        size: 80,
        header: '总大小',
        cell: ({ row }) => byteSize(row.original.progress.expectedBytes),
      },
      {
        id: 'progress',
        size: 125,
        header: '进度',
        cell: ({ row }) => <UploadProgressCell session={row.original} />,
      },
      {
        id: 'throughput',
        size: 90,
        header: '实时速度',
        cell: ({ row }) =>
          row.original.progress.throughputBytesPerSecond
            ? `${byteSize(row.original.progress.throughputBytesPerSecond)}/s`
            : '—',
      },
      {
        id: 'eta',
        size: 80,
        header: '预计剩余',
        cell: ({ row }) => duration(row.original.progress.estimatedRemainingSeconds),
      },
      {
        id: 'status',
        size: 135,
        header: '任务 / 校验状态',
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
                {typeof row.original.lifecycleStatus === 'string' ? null : (
                  <Typography.Text>{lifecycle}</Typography.Text>
                )}
              </Space>
              <Space size={2}>
                <StatusTag
                  status={verification}
                  label={verification}
                  known={typeof row.original.verificationStatus === 'string'}
                  tone={statusTone(verification)}
                />
                {typeof row.original.verificationStatus === 'string' ? null : (
                  <Typography.Text>{verification}</Typography.Text>
                )}
              </Space>
            </Space>
          );
        },
      },
      {
        id: 'job',
        size: 85,
        header: '异步任务',
        cell: ({ row }) =>
          row.original.activeJobIds[0] ? (
            <UploadJobStatus jobId={row.original.activeJobIds[0]} />
          ) : (
            '—'
          ),
      },
      {
        id: 'creator',
        size: 90,
        header: '创建人',
        cell: ({ row }) => row.original.createdBy.displayName,
      },
      {
        id: 'actions',
        size: 150,
        header: '操作',
        cell: ({ row }) => (
          <Space size={2} wrap>
            <Button
              aria-label="查看详情"
              type="link"
              icon={<Eye aria-hidden="true" size={14} />}
              onClick={() => props.onOpen(row.original.uploadId)}
            >
              查看
            </Button>
            {row.original.allowedActions.includes('PAUSE') ? (
              <Button type="link" icon={<Pause aria-hidden="true" size={14} />} disabled>
                暂停
              </Button>
            ) : row.original.allowedActions.includes('RETRY_UPLOAD') ? (
              <Button type="link" icon={<RotateCcw aria-hidden="true" size={14} />} disabled>
                重试
              </Button>
            ) : null}
          </Space>
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

export function UploadSessionFacts({
  session,
  onOpen,
}: Readonly<{
  session: UploadSession;
  onOpen: (uploadId: string) => void;
}>) {
  const lifecycle = enumText(session.lifecycleStatus);
  const verification = enumText(session.verificationStatus);
  const progress = percent(
    session.progress.confirmedReceivedBytes,
    session.progress.expectedBytes,
  );

  return (
    <section aria-labelledby="upload-session-facts-title">
      <header>
        <div>
          <span>当前查看会话</span>
          <h2 id="upload-session-facts-title">{session.uploadId}</h2>
        </div>
        <Button
          type="primary"
          icon={<ArrowRight aria-hidden="true" size={15} />}
          onClick={() => onOpen(session.uploadId)}
        >
          打开固定详情
        </Button>
      </header>
      <dl>
        <div><dt>Dataset</dt><dd>{session.targetDataset?.name ?? '未指定 Dataset'}</dd></div>
        <div><dt>数据源</dt><dd>{session.dataSource.name}</dd></div>
        <div><dt>格式 / Adapter</dt><dd>{session.sourceFormat} / {session.adapterVersion}</dd></div>
        <div><dt>对象 / 总大小</dt><dd>{session.progress.totalObjects} / {byteSize(session.progress.expectedBytes)}</dd></div>
        <div><dt>进度 / 吞吐</dt><dd>{progress === null ? '—' : `${progress.toFixed(1)}%`} / {session.progress.throughputBytesPerSecond ? `${byteSize(session.progress.throughputBytesPerSecond)}/s` : '—'}</dd></div>
        <div><dt>预计剩余</dt><dd>{duration(session.progress.estimatedRemainingSeconds)}</dd></div>
        <div><dt>任务状态</dt><dd><StatusTag status={lifecycle} label={lifecycle} known={typeof session.lifecycleStatus === 'string'} tone={statusTone(lifecycle)} /></dd></div>
        <div><dt>校验状态</dt><dd><StatusTag status={verification} label={verification} known={typeof session.verificationStatus === 'string'} tone={statusTone(verification)} /></dd></div>
        <div><dt>最近更新</dt><dd><time dateTime={session.updatedAt}>{session.updatedAt}</time></dd></div>
        <div><dt>资源版本</dt><dd><code>{session.resourceVersion}</code></dd></div>
      </dl>
    </section>
  );
}
