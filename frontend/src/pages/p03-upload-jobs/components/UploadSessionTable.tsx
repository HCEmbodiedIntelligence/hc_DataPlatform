import { useAsyncJob } from '../../../shared/jobs/use-async-job';
import type { UploadSession } from '../../../features/ingest/upload/model';
import { useThrottledValue } from '../../../features/ingest/upload/use-throttled-value';

function percent(received: string, expected: string | null): number | null {
  if (!expected || expected === '0') return null;
  return Number((BigInt(received) * 10_000n) / BigInt(expected)) / 100;
}

function UploadJobStatus({ jobId }: { readonly jobId: string }) {
  const job = useAsyncJob(jobId);
  return <span aria-live="polite">{job.data?.status ?? 'QUEUED'}{job.connectionStatus === 'polling' ? '（轮询）' : ''}</span>;
}

function UploadProgressCell({ session }: { readonly session: UploadSession }) {
  const visible = useThrottledValue({
    received: session.progress.confirmedReceivedBytes,
    expected: session.progress.expectedBytes,
    phase: typeof session.lifecycleStatus === 'string' ? session.lifecycleStatus : 'UNKNOWN',
  }, 100);
  const value = percent(visible.received, visible.expected);
  return (
    <div aria-live="off">
      <progress max={100} value={value ?? undefined} aria-label={`${session.uploadId} 上传进度`} />
      <span>{value === null ? `${visible.received} 字节` : `${value.toFixed(1)}%`}</span>
    </div>
  );
}

export function UploadSessionTable(props: {
  readonly items: readonly UploadSession[];
  readonly selected: ReadonlySet<string>;
  readonly onToggle: (uploadId: string) => void;
  readonly onOpen: (uploadId: string) => void;
}) {
  return (
    <div className="ingest-table-scroll">
      <table>
        <caption>上传会话任务列表</caption>
        <thead><tr><th scope="col">选择</th><th scope="col">任务</th><th scope="col">数据源 / Dataset</th><th scope="col">进度</th><th scope="col">会话状态</th><th scope="col">异步任务</th><th scope="col">操作</th></tr></thead>
        <tbody>{props.items.map((session) => <tr key={session.uploadId}>
          <td><input type="checkbox" checked={props.selected.has(session.uploadId)} onChange={() => props.onToggle(session.uploadId)} aria-label={`选择 ${session.uploadId}`} /></td>
          <th scope="row"><code>{session.uploadId}</code><time dateTime={session.createdAt}>{session.createdAt}</time></th>
          <td>{session.dataSource.name}<br />{session.targetDataset?.name ?? '未指定 Dataset'}</td>
          <td><UploadProgressCell session={session} /></td>
          <td>{typeof session.lifecycleStatus === 'string' ? session.lifecycleStatus : `UNKNOWN (${session.lifecycleStatus.raw})`} / {typeof session.verificationStatus === 'string' ? session.verificationStatus : 'UNKNOWN'}</td>
          <td>{session.activeJobIds[0] ? <UploadJobStatus jobId={session.activeJobIds[0]} /> : '—'}</td>
          <td><button type="button" onClick={() => props.onOpen(session.uploadId)}>查看详情</button></td>
        </tr>)}</tbody>
      </table>
    </div>
  );
}
