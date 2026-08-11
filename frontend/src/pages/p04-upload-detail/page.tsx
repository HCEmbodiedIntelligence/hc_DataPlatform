import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { useAsyncJob } from '../../shared/jobs/use-async-job';
import { IngestRegion } from '../../features/ingest/region-state';
import { routes } from '../../features/ingest/routing';
import { useRetryUploadVerification, useUploadBootstrap, useUploadEvents, useUploadObjects, useVerificationRuns } from '../../features/ingest/api';
import { useIngestScope } from '../../features/ingest/use-ingest-scope';
import { canRequestQuarantineRelease, validatePipeline } from '../../features/ingest/validation-pipeline';
import { createMutationIntentKey } from '../../features/ingest/mutation-machine';
import { ingestUploadAuthorizationVault } from '../../features/ingest/upload/authorization-vault';
import { updateUploadDetailSearch, uploadDetailQueryCodec } from './query-codec';
import { UploadObjectsTable } from './components/UploadObjectsTable';
import { ValidationPipeline } from './components/ValidationPipeline';
import { QuarantinePanel } from './components/QuarantinePanel';
import { DangerousUploadActionDialog } from './components/DangerousUploadActionDialog';
import { UploadEventTimeline } from './components/UploadEventTimeline';
import '../../features/ingest/styles.css';
import './styles.css';

function ActiveJob({ jobId }: { readonly jobId: string }) {
  const job = useAsyncJob(jobId);
  return <li><code>{jobId}</code>：<span aria-live="polite">{job.data?.status ?? 'QUEUED'}</span>（{job.connectionStatus}）</li>;
}

export default function UploadDetailPage() {
  const { uploadId = '' } = useParams();
  const stableId = uploadId && !['latest', 'current'].includes(uploadId) ? uploadId : null;
  const scope = useIngestScope();
  const capabilities = useCapabilities();
  const [params, setParams] = useSearchParams();
  const search = useMemo(() => uploadDetailQueryCodec.parse(params), [params]);
  const bootstrap = useUploadBootstrap(scope, stableId, capabilities.has('upload.read'));
  const objects = useUploadObjects(scope, stableId, { after: search.after, before: search.before, limit: 50, sort: 'relativePath:asc' }, capabilities.has('upload.read'));
  const runs = useVerificationRuns(scope, stableId, { limit: 50 }, capabilities.has('upload.read'));
  const events = useUploadEvents(scope, stableId, { eventLevel: search.eventLevel, limit: 50 }, capabilities.has('upload.read'));
  const retry = useRetryUploadVerification();
  const [confirmRetry, setConfirmRetry] = useState(false);
  const scopeKey = scope ? `${scope.organizationId}/${scope.projectId}/${scope.regionCode}` : null;
  const previousScopeKey = useRef<string | null | undefined>(undefined);
  const previousUploadId = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    ingestUploadAuthorizationVault.bindScope(scopeKey);
    if (previousScopeKey.current !== undefined && previousScopeKey.current !== scopeKey) {
      setParams(uploadDetailQueryCodec.build(updateUploadDetailSearch(search, { objectId: undefined }, true)), { replace: true });
      setConfirmRetry(false);
    }
    previousScopeKey.current = scopeKey;
  }, [scopeKey, search, setParams]);
  useEffect(() => {
    if (previousUploadId.current !== undefined && previousUploadId.current !== stableId) {
      setParams(uploadDetailQueryCodec.build(updateUploadDetailSearch(search, { objectId: undefined }, true)), { replace: true });
      setConfirmRetry(false);
    }
    previousUploadId.current = stableId;
  }, [search, setParams, stableId]);
  const latestRun = bootstrap.data?.latestVerificationRun ?? null;
  const pipelineErrors = latestRun ? validatePipeline(latestRun.stages) : [];

  if (!stableId) return <IngestRegion label="上传详情" state="not-found" />;
  if (!scope) return <IngestRegion label="上传详情" state="feature-unavailable" />;
  if (!capabilities.has('upload.read') && !capabilities.loading) return <IngestRegion label="上传详情" state="forbidden" />;
  if (bootstrap.isPending) return <main className="ingest-page"><IngestRegion label="上传详情" state="first-loading" /></main>;
  if (bootstrap.isError || !bootstrap.data) return <main className="ingest-page"><IngestRegion label="上传详情" state="fatal-error" onRetry={() => void bootstrap.refetch()} /></main>;
  const session = bootstrap.data.session;
  const quarantine = bootstrap.data.latestQuarantine;
  const visibleJobIds = [...new Set([...session.activeJobIds, ...(retry.data?.jobId ? [retry.data.jobId] : [])])];
  const canRetry = capabilities.has('upload.manage') && session.allowedActions.includes('RETRY_VERIFY') && quarantine !== null && canRequestQuarantineRelease(quarantine.disposition) && pipelineErrors.every((item) => item !== 'UNKNOWN_VALIDATION_STAGE');
  const apply = (next: Partial<typeof search>) => setParams(uploadDetailQueryCodec.build({ ...search, ...next }, search));

  return <main className="ingest-page p04-page"><nav className="breadcrumbs" aria-label="面包屑"><Link to={routes.uploadJobs.build()}>上传任务</Link><span>/</span><code>{session.uploadId}</code></nav><header className="page-header"><div><p className="eyebrow">上传详情</p><h1>{session.uploadId}</h1><p>{session.dataSource.name} · {session.sourceFormat} · {typeof session.lifecycleStatus === 'string' ? session.lifecycleStatus : 'UNKNOWN'}</p></div><div className="header-actions"><Link to={routes.uploadJobs.build()}>返回任务</Link></div></header><section className="detail-summary" aria-label="概要"><h2>概要</h2><dl><dt>目标 Dataset</dt><dd>{session.targetDataset?.name ?? '未指定'}</dd><dt>已确认字节</dt><dd>{session.progress.confirmedReceivedBytes} / {session.progress.expectedBytes ?? '?'}</dd><dt>对象</dt><dd>{session.progress.completedObjects} / {session.progress.totalObjects}</dd><dt>ETag</dt><dd><code>{session.etag}</code>（资源并发版本，不是内容 SHA-256）</dd></dl>{session.result ? <p>结果 Version：<code>{session.result.datasetVersionId}</code>（{session.result.datasetVersionStatus}）</p> : null}</section><section aria-labelledby="objects-title"><div className="section-heading"><h2 id="objects-title">对象清单</h2><span>大表 · 游标分页 · 稳定排序</span></div><IngestRegion label="对象清单" state={objects.isPending ? 'first-loading' : objects.isError ? 'partial-error' : objects.data?.items.length ? 'ready' : 'empty'} onRetry={() => void objects.refetch()}><UploadObjectsTable objects={objects.data?.items ?? bootstrap.data.objects} /></IngestRegion><div className="cursor-bar"><button type="button" disabled={!objects.data?.pageInfo.has_previous_page} onClick={() => apply({ before: objects.data?.pageInfo.start_cursor ?? undefined, after: undefined })}>上一组</button><button type="button" disabled={!objects.data?.pageInfo.has_next_page} onClick={() => apply({ after: objects.data?.pageInfo.end_cursor ?? undefined, before: undefined })}>下一组</button></div></section><section aria-labelledby="pipeline-title"><h2 id="pipeline-title">校验流水线阶段</h2>{pipelineErrors.length ? <p role="alert">流水线合同异常：{pipelineErrors.join('、')}，相关写操作已禁用。</p> : null}<ValidationPipeline run={latestRun} /></section><section aria-labelledby="quarantine-title"><h2 id="quarantine-title">隔离区</h2><QuarantinePanel quarantine={quarantine} canRequestRelease={canRetry} onRequestRelease={() => setConfirmRetry(true)} /></section><section aria-labelledby="retry-history-title"><h2 id="retry-history-title">重试历史</h2><IngestRegion label="重试历史" state={runs.isPending ? 'first-loading' : runs.isError ? 'partial-error' : runs.data?.items.length ? 'ready' : 'empty'}><ol className="retry-history">{runs.data?.items.map((run) => <li key={run.verificationRunId}><code>{run.verificationRunId}</code><span>{typeof run.status === 'string' ? run.status : 'UNKNOWN'}</span>{run.supersedesRunId ? <small>替代运行：{run.supersedesRunId}</small> : <small>首次运行</small>}</li>)}</ol></IngestRegion></section><section aria-labelledby="audit-title"><h2 id="audit-title">审计摘要</h2><p>下列是安全资源事件时间线，不是 P19 正式审计投影；仅用于 requestId/jobId 关联诊断，不推导审计成功。</p><dl><dt>Bootstrap 请求 ID</dt><dd><code>{bootstrap.data.requestId}</code></dd><dt>合同版本</dt><dd>{bootstrap.data.contractVersion}</dd></dl><ul>{visibleJobIds.map((id) => <ActiveJob key={id} jobId={id} />)}</ul><IngestRegion label="安全资源事件" state={events.isPending ? 'first-loading' : events.isError ? 'partial-error' : events.data?.items.length ? 'ready' : 'empty'} onRetry={() => void events.refetch()}><UploadEventTimeline items={events.data?.items ?? []} /></IngestRegion></section><DangerousUploadActionDialog open={confirmRetry} title="确认复验并申请释放隔离" uploadId={session.uploadId} impact="追加新的 VerificationRun；旧运行、Finding 与隔离事实保持不可变。仅当复验和原子可用性提交成功后，服务端才释放隔离。" blockedReasons={session.blockedReasons} pending={retry.isPending} onClose={() => setConfirmRetry(false)} onConfirm={(reason) => { if (!latestRun) return; retry.mutate({ scope, uploadId: session.uploadId, etag: session.etag, failedVerificationRunId: latestRun.verificationRunId, expectedObjectSetHash: latestRun.objectSetHash, expectedManifestSha256: latestRun.manifestSha256, reason, idempotencyKey: createMutationIntentKey() }, { onSuccess: () => setConfirmRetry(false) }); }} /></main>;
}
