import { useEffect, useMemo, useRef, useState } from 'react';
import type { JSX } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import type { AnnotationEntryWire } from '../../entities/annotation-draft';
import { annotationEntryWireSchema } from '../../entities/annotation-draft';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import { isDomainError } from '../../shared/api/domain-error';
import { useShellStore } from '../../shared/scope/shell-store';
import {
  AnnotationPageState,
  DangerousActionDialog,
  SchemaDrivenAnnotationForm,
  buildManualIssuesLink,
  clearLocalAnnotationDraft,
  loadLocalAnnotationDraft,
  createIdempotencyKey,
  decideAnnotationCapability,
  reportManualIssueFromAnnotation,
  useAnnotationTask,
  useLocalAnnotationDraftAutosave,
  usePreflightAnnotationSubmission,
  useRebaseAnnotationTask,
  useReviewAnnotationTask,
  useSaveAnnotationDraft,
  useSubmitAnnotationTask,
  useUnsavedChangesWarning,
} from '../../features/annotation';
import { annotationCapabilities } from '../../features/annotation/capabilities';
import type { AnnotationTaskDetail } from '../../features/annotation/api/adapter';
import { createPlaybackClock, EpisodeWorkbenchCore } from '../../features/viewer';
import { annotationRoutes } from './routes';
import { annotationTaskQueryCodec } from './query-codec';
import './p08.css';

type DialogKind = 'submit' | 'review-approve' | 'review-return' | 'rebase' | null;

const supportedFieldTypes = new Set(['string', 'number', 'boolean', 'enum', 'multi-enum', 'time-point', 'time-range']);

function relativeSecondsToNs(value: string): bigint {
  const [seconds = '0', fraction = ''] = value.split('.', 2);
  return BigInt(seconds) * 1_000_000_000n + BigInt(fraction.padEnd(9, '0'));
}

function entryValues(detail: AnnotationTaskDetail): Record<string, unknown> {
  const first = detail.draft.entries.find((entry): entry is AnnotationEntryWire => 'semantic_type' in entry);
  if (!first) return { semanticType: 'PHASE', labelCode: '', startNs: detail.task.source.startNs, endNs: detail.task.source.endNs };
  const result: Record<string, unknown> = { semanticType: first.semantic_type, labelCode: first.label_code, ...first.attributes };
  if (first.anchor.anchor_type === 'TIME_RANGE') { result.startNs = first.anchor.start_ns; result.endNs = first.anchor.end_ns; }
  else if (first.anchor.anchor_type === 'TIME_POINT') result.atNs = first.anchor.at_ns;
  else { result.startNs = first.anchor.start_ns; result.endNs = first.anchor.end_ns; }
  return result;
}

function buildEntry(detail: AnnotationTaskDetail, values: Readonly<Record<string, unknown>>): AnnotationEntryWire {
  const semanticTypeValue = typeof values.semanticType === 'string' ? values.semanticType : '';
  const semanticType = ['ACTION', 'PHASE', 'OBJECT', 'EVENT', 'KEYFRAME'].includes(semanticTypeValue) ? semanticTypeValue : 'PHASE';
  const labelCode = typeof values.labelCode === 'string' && values.labelCode ? values.labelCode : 'unlabeled';
  const startNs = typeof values.startNs === 'string' ? values.startNs : detail.task.source.startNs;
  const endNs = typeof values.endNs === 'string' ? values.endNs : detail.task.source.endNs;
  const atNs = typeof values.atNs === 'string' ? values.atNs : startNs;
  const attributes = Object.fromEntries(Object.entries(values).filter(([key, value]) => !['semanticType', 'labelCode', 'startNs', 'endNs', 'atNs'].includes(key) && (value === null || ['string','number','boolean'].includes(typeof value) || Array.isArray(value))));
  const existing = detail.draft.entries.find((entry): entry is AnnotationEntryWire => 'semantic_type' in entry);
  const annotationId = existing?.annotation_id ?? `${detail.task.id}:entry-1`;
  const base = { annotation_id: annotationId, label_code: labelCode, attributes };
  if (semanticType === 'ACTION' || semanticType === 'PHASE') return annotationEntryWireSchema.parse({ ...base, semantic_type: semanticType, anchor: { anchor_type: 'TIME_RANGE', coordinate_system: 'REVISION_TIME_NS', start_ns: startNs, end_ns: endNs } });
  if (semanticType === 'EVENT' || semanticType === 'KEYFRAME') return annotationEntryWireSchema.parse({ ...base, semantic_type: semanticType, anchor: { anchor_type: 'TIME_POINT', coordinate_system: 'REVISION_TIME_NS', at_ns: atNs } });
  const streamId = detail.task.source.streamIds[0];
  return annotationEntryWireSchema.parse({ ...base, semantic_type: 'OBJECT', anchor: { anchor_type: 'OBJECT_TRACK', coordinate_system: 'REVISION_TIME_NS', stream_id: streamId, start_ns: startNs, end_ns: endNs, observations: [{ at_ns: startNs, geometry: { geometry_type: 'BBOX_2D_NORMALIZED', x: 0, y: 0, width: 0.1, height: 0.1 }, occluded: false }] } });
}

function buildDraftEntries(detail: AnnotationTaskDetail, values: Readonly<Record<string, unknown>>): readonly AnnotationEntryWire[] {
  if (detail.draft.hasUnsupportedEntries) throw new Error('草稿包含当前客户端未知的标注类型，不能安全保存。');
  const replacement = buildEntry(detail, values);
  let replaced = false;
  const entries = detail.draft.entries.map((entry) => {
    if (!('semantic_type' in entry)) throw new Error('草稿包含当前客户端未知的标注类型，不能安全保存。');
    if (!replaced) {
      replaced = true;
      return replacement;
    }
    return entry;
  });
  return replaced ? entries : [replacement];
}

function errorState(error: unknown): Parameters<typeof AnnotationPageState>[0]['kind'] {
  if (!isDomainError(error)) return 'fatal-error';
  if (error.code === 'FORBIDDEN') return 'forbidden';
  if (error.code === 'NOT_FOUND' || error.code === 'GONE') return 'not-found-gone';
  if (error.code === 'VERSION_CONFLICT' || error.code === 'PRECONDITION_FAILED') return 'conflict';
  if (error.code === 'RATE_LIMITED') return 'rate-limited';
  if (error.code === 'NETWORK_ERROR') return 'offline-reconnecting';
  if (error.code === 'CONTRACT_MISMATCH') return 'contract-mismatch';
  return 'fatal-error';
}

export function AnnotationTaskPage(): JSX.Element {
  const { taskId } = useParams<{ taskId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const search = useMemo(() => annotationTaskQueryCodec.parse(location.search), [location.search]);
  const shellScope = useShellStore((state) => state.scope);
  const capabilities = useCapabilities();
  const scope = useMemo(() => shellScope?.projectId && shellScope.regionCode
    ? { projectId: shellScope.projectId, regionCode: shellScope.regionCode }
    : null, [shellScope?.projectId, shellScope?.regionCode]);
  const activeScope = scope ?? { projectId: 'unavailable', regionCode: 'unavailable' };
  const safeTaskId = taskId ?? 'unavailable';
  const canRead = capabilities.has('annotation_task.read') && capabilities.has('episode.read');
  const detailQuery = useAnnotationTask(scope, taskId, canRead);
  const save = useSaveAnnotationDraft(activeScope, safeTaskId);
  const preflight = usePreflightAnnotationSubmission(activeScope, safeTaskId);
  const submit = useSubmitAnnotationTask(activeScope, safeTaskId);
  const review = useReviewAnnotationTask(activeScope, safeTaskId);
  const rebase = useRebaseAnnotationTask(activeScope, safeTaskId);
  const [values, setValues] = useState<Readonly<Record<string, unknown>>>({});
  const [dirty, setDirty] = useState(false);
  const [dialog, setDialog] = useState<DialogKind>(null);
  const [preflightResult, setPreflightResult] = useState<Awaited<ReturnType<typeof preflight.mutateAsync>> | null>(null);
  const [acceptedJob, setAcceptedJob] = useState<{ readonly id: string; readonly status: string } | null>(null);
  const [summaryErrors, setSummaryErrors] = useState<readonly string[]>([]);
  const [serverErrors, setServerErrors] = useState<readonly { path: string; code: string; message: string }[]>([]);
  const [issueNote, setIssueNote] = useState('');
  const [issueLink, setIssueLink] = useState<string | null>(null);
  const [issuePending, setIssuePending] = useState(false);
  const [recoveredLocalDraft, setRecoveredLocalDraft] = useState(false);
  const initializedBaseline = useRef<string | null>(null);

  const detail = detailQuery.data;
  const clockTaskId = detail?.task.id;
  const clockStartNs = detail?.task.source.startNs;
  const clockEndNs = detail?.task.source.endNs;
  const clock = useMemo(() => clockTaskId && clockStartNs && clockEndNs ? createPlaybackClock({ startNs: clockStartNs, endNs: clockEndNs }) : null, [clockEndNs, clockStartNs, clockTaskId]);
  useEffect(() => () => clock?.dispose(), [clock]);
  useEffect(() => {
    if (!clock || !search.t) return;
    clock.seek((BigInt(clock.startNs) + relativeSecondsToNs(search.t)).toString());
  }, [clock, search.t]);
  const localIdentity = useMemo(() => detail && scope ? {
    projectId: scope.projectId,
    regionCode: scope.regionCode,
    taskId: detail.task.id,
    taskEtag: detail.task.etag,
    baselineRevision: detail.draft.revision,
  } : null, [detail, scope]);
  const baselineIdentity = localIdentity
    ? `${localIdentity.projectId}:${localIdentity.regionCode}:${localIdentity.taskId}:${localIdentity.taskEtag}:${localIdentity.baselineRevision}`
    : null;
  useEffect(() => {
    if (!detail || !localIdentity || !baselineIdentity) return;
    if (initializedBaseline.current === baselineIdentity) return;
    initializedBaseline.current = baselineIdentity;
    const recovered = loadLocalAnnotationDraft(localIdentity);
    setValues({ ...entryValues(detail), ...recovered });
    setDirty(recovered !== null && Object.keys(recovered).length > 0);
    setRecoveredLocalDraft(recovered !== null && Object.keys(recovered).length > 0);
    setPreflightResult(null);
    setDialog(null);
  }, [baselineIdentity, detail, localIdentity]);

  const granted = useMemo(() => new Set(annotationCapabilities.filter((capability) => capabilities.has(capability))), [capabilities]);
  const editDecision = decideAnnotationCapability(granted, 'annotation.edit');
  const draftDecision = decideAnnotationCapability(granted, 'annotation_draft.edit');
  const saveDecision = decideAnnotationCapability(granted, 'annotation.save');
  const submitDecision = decideAnnotationCapability(granted, 'annotation.submit');
  const reviewDecision = decideAnnotationCapability(granted, 'annotation.review');
  const rebaseDecision = decideAnnotationCapability(granted, 'annotation_task.rebase');

  useLocalAnnotationDraftAutosave(localIdentity, detail?.formDefinition ?? null, values, dirty);
  useUnsavedChangesWarning(dirty);

  if (capabilities.loading || detailQuery.isLoading) return <AnnotationPageState kind="first-loading" />;
  if (capabilities.failed || !canRead) return <AnnotationPageState kind="forbidden" />;
  if (!scope || !taskId) return <AnnotationPageState kind="feature-unavailable" detail="缺少项目、Region 或任务 ID。" />;
  if (detailQuery.error) return <AnnotationPageState kind={errorState(detailQuery.error)} requestId={isDomainError(detailQuery.error) ? detailQuery.error.requestId ?? undefined : undefined} onRetry={() => void detailQuery.refetch()} />;
  if (!detail || !clock) return <AnnotationPageState kind="fatal-error" />;

  const task = detail.task;
  const editorReady = initializedBaseline.current === baselineIdentity;
  const stale = task.sourceStatus === 'STALE';
  const unsupportedSchema = detail.formDefinition?.fields.some((field) => !supportedFieldTypes.has(field.type)) ?? true;
  const unknown = task.workflowStatus === 'UNKNOWN' || task.sourceStatus === 'UNKNOWN' || detail.draft.state === 'UNKNOWN' || detail.draft.hasUnsupportedEntries || unsupportedSchema;
  const mutationPending = save.isPending || submit.isPending || review.isPending || rebase.isPending || acceptedJob !== null;
  const canEdit = editorReady && !stale && !unknown && !mutationPending && editDecision.state === 'allowed' && task.allowedActions.has('EDIT_DRAFT');
  const canSave = canEdit && dirty && draftDecision.state === 'allowed' && saveDecision.state === 'allowed' && task.allowedActions.has('SAVE_DRAFT');
  const canSubmit = !dirty
    && !stale
    && !unknown
    && editorReady
    && !mutationPending
    && detail.draft.state === 'ACTIVE'
    && submitDecision.state === 'allowed'
    && task.allowedActions.has('PREFLIGHT_SUBMIT')
    && task.allowedActions.has('SUBMIT')
    && (detail.submissionGate.status === 'PASS' || detail.submissionGate.status === 'ADVISORY');
  const canReview = !mutationPending && reviewDecision.state === 'allowed' && task.allowedActions.has('REVIEW') && task.workflowStatus === 'SUBMITTED';
  const canRebase = !mutationPending && stale && rebaseDecision.state === 'allowed' && task.allowedActions.has('REBASE');
  const canReportIssue = capabilities.has('manual_issue.create') && task.allowedActions.has('REPORT_ISSUE') && !stale && !unknown;

  const leave = () => {
    if (dirty && !window.confirm('有尚未保存的标注修改，确认离开并保留短期恢复摘要吗？')) return;
    void navigate(search.returnTo ?? annotationRoutes.queue.pattern);
  };

  const captureMutationError = (error: unknown) => {
    if (isDomainError(error)) {
      setServerErrors(error.fieldErrors);
      setSummaryErrors([
        ...error.operationErrors.map((item) => item.message),
        ...error.blockedReasons.map((item) => item.message),
        ...(error.fieldErrors.length ? [] : [error.message]),
      ]);
    } else {
      setServerErrors([]);
      setSummaryErrors([error instanceof Error ? error.message : '操作失败，请刷新后重试。']);
    }
  };

  const doSave = async () => {
    try {
      setServerErrors([]);
      setSummaryErrors([]);
      const entries = buildDraftEntries(detail, values);
      await save.mutateAsync({ ...scope, taskId: task.id, etag: task.etag, idempotencyKey: createIdempotencyKey(), expectedRevision: detail.draft.revision, clientMutationId: createIdempotencyKey(), entries });
      if (localIdentity) clearLocalAnnotationDraft(localIdentity);
      setDirty(false);
      setRecoveredLocalDraft(false);
    } catch (error) {
      captureMutationError(error);
    }
  };

  const openSubmit = async () => {
    try {
      setServerErrors([]);
      setSummaryErrors([]);
      const result = await preflight.mutateAsync({ ...scope, taskId: task.id, etag: task.etag, idempotencyKey: createIdempotencyKey(), expectedRevision: detail.draft.revision, expectedContentHash: detail.draft.contentHash });
      if (!result.valid || result.validation_errors.length) {
        setServerErrors(result.validation_errors);
        setSummaryErrors(result.submission_gate.blocked_reasons);
        return;
      }
      setPreflightResult(result);
      setDialog('submit');
    } catch (error) { captureMutationError(error); }
  };

  const confirmSubmit = async () => {
    if (!preflightResult) return;
    try {
      const result = await submit.mutateAsync({ ...scope, taskId: task.id, etag: task.etag, idempotencyKey: createIdempotencyKey(), expectedRevision: detail.draft.revision, expectedContentHash: detail.draft.contentHash, preflightId: preflightResult.preflight_id });
      if (result.kind === 'accepted-job') setAcceptedJob({ id: result.jobId, status: result.status });
      setDialog(null);
    } catch (error) { setDialog(null); captureMutationError(error); }
  };

  const confirmReview = async () => {
    const returned = dialog === 'review-return';
    try {
      await review.mutateAsync({ ...scope, taskId: task.id, etag: task.etag, idempotencyKey: createIdempotencyKey(), decision: returned ? 'RETURNED' : 'APPROVED', reasonCodes: returned ? ['ANNOTATION_CORRECTION_REQUIRED'] : [], comment: returned ? '请按结构化反馈修订后重新提交。' : null });
      setDialog(null);
    } catch (error) { setDialog(null); captureMutationError(error); }
  };

  const confirmRebase = async () => {
    const targetRevisionId = detail.staleInfo?.replacementRevisionId ?? '';
    if (!targetRevisionId) { setSummaryErrors(['服务端未返回权威替代 Revision，不能 Rebase。']); return; }
    try {
      const result = await rebase.mutateAsync({ ...scope, taskId: task.id, etag: task.etag, idempotencyKey: createIdempotencyKey(), targetRevisionId, reason: '用户确认在权威替代 Revision 上创建空白任务', successorAssigneeId: task.assigneeId });
      void navigate(annotationRoutes.task.build({ taskId: result.successorTask.id }));
    } catch (error) { setDialog(null); captureMutationError(error); }
  };

  const reportIssue = async () => {
    const streamId = task.source.streamIds[0];
    if (!streamId || !shellScope?.organizationId) return;
    setIssuePending(true);
    try {
      const current = BigInt(clock.currentNs());
      const issueEnd = current + 1_000_000_000n < BigInt(task.source.endNs) ? current + 1_000_000_000n : BigInt(task.source.endNs);
      const issue = await reportManualIssueFromAnnotation({ organizationId: shellScope.organizationId, projectId: scope.projectId, regionCode: scope.regionCode, datasetId: task.source.datasetId, versionId: task.source.datasetVersionId, episodeId: task.source.episodeId, revisionId: task.source.baseRevisionId, streamId, startNs: current.toString(), endNs: issueEnd.toString(), issueType: 'OTHER', severity: 'MEDIUM', note: issueNote, idempotencyKey: createIdempotencyKey() });
      setIssueLink(buildManualIssuesLink({ datasetId: task.source.datasetId, versionId: task.source.datasetVersionId, episodeId: task.source.episodeId, issueId: issue.id, returnTo: `${location.pathname}${location.search}` }));
      setIssueNote('');
      void detailQuery.refetch();
    } catch (error) { setSummaryErrors([error instanceof Error ? error.message : '人工问题创建失败']); }
    finally { setIssuePending(false); }
  };

  const pendingCapabilityReason = [draftDecision, rebaseDecision].find((decision) => decision.state === 'feature-unavailable')?.reason;
  return (
    <main className="p08-page p08-task-page">
      <header className="p08-task-header"><button type="button" onClick={leave}>← 返回任务</button><div><p className="p08-eyebrow">数据标注 / {task.id}</p><h1>任务 {task.id}</h1><p>{task.source.datasetId} / {task.source.datasetVersionId} / {task.source.episodeId} / {task.source.baseRevisionId}</p></div><span className={`p08-badge p08-badge--${task.displayState.toLowerCase()}`}>{task.displayState}</span><div className="p08-primary-actions"><button type="button" disabled={!canSave || save.isPending} onClick={() => void doSave()}>{save.isPending ? '保存中…' : '保存标注'}</button><button type="button" disabled={!canSubmit || preflight.isPending} onClick={() => void openSubmit()}>提交复核</button>{canReview ? <><button type="button" onClick={() => setDialog('review-approve')}>通过标注</button><button type="button" onClick={() => setDialog('review-return')}>退回修改</button></> : null}</div></header>
      {detailQuery.isFetching ? <AnnotationPageState kind="refreshing" /> : null}
      {acceptedJob ? <section role="status" className="p08-stale-banner"><h2>提交任务已接受</h2><p>异步 Job <code>{acceptedJob.id}</code> 当前为 {acceptedJob.status}；完成前不会乐观显示为已提交。</p></section> : null}
      {recoveredLocalDraft ? <section role="status" className="p08-recovery-banner"><p>已恢复本任务在当前服务端版本上的短期本地工作副本；保存前请复核。</p></section> : null}
      {stale ? <section className="p08-stale-banner" role="alert"><h2>基础数据已更新，任务已 STALE</h2><p>旧草稿保持只读且不会静默迁移。Rebase 将在权威替代 Revision 上创建空白任务。</p><button type="button" disabled={!canRebase} onClick={() => setDialog('rebase')}>在新修订上重建任务</button></section> : null}
      {unknown ? <AnnotationPageState kind="unknown-enum" /> : null}
      {pendingCapabilityReason ? <AnnotationPageState kind="feature-unavailable" detail={pendingCapabilityReason} /> : null}
      {summaryErrors.length ? <section role="alert" className="p08-error-summary"><h2>需要处理的问题</h2><ul>{summaryErrors.map((error) => <li key={error}>{error}</li>)}</ul></section> : null}
      <div className="p08-workbench-grid">
        <aside className="p08-tool-rail" aria-label="标注工具"><h2>工具</h2>{['动作','阶段','对象','事件','关键帧'].map((tool) => <button type="button" key={tool} disabled={!canEdit} title={`${tool}标注`}>{tool}</button>)}</aside>
        <section className="p08-viewer-area"><EpisodeWorkbenchCore episodeId={task.source.episodeId} datasetId={task.source.datasetId} versionId={task.source.datasetVersionId} clock={clock} mode="annotate" streams={detail.streams} onResourceError={(error) => setSummaryErrors([`局部资源失败：${error.message}`])} /></section>
        <aside className="p08-inspector">
          {detail.formDefinition && editorReady ? <SchemaDrivenAnnotationForm key={`${task.id}:${detail.draft.revision}`} definition={detail.formDefinition} defaultValues={values} disabled={!canEdit} serverErrors={serverErrors} onChange={(next, changed) => { setValues(next); setDirty(changed); if (changed) { setPreflightResult(null); if (dialog === 'submit') setDialog(null); } }} onSubmit={(next) => { setValues(next); setDirty(true); setPreflightResult(null); }} onFormErrorSummary={setSummaryErrors} /> : detail.formDefinition ? <AnnotationPageState kind="first-loading" /> : <AnnotationPageState kind="feature-unavailable" detail="Bootstrap 未返回可验证的 Schema 表单定义。" />}
          <section><h2>数据问题（只读）</h2>{detail.manualIssues.length ? <ul>{detail.manualIssues.map((issue) => <li key={issue.id}><strong>{issue.impact}</strong> {issue.summary}</li>)}</ul> : <p>当前投影无问题。</p>}
            {canReportIssue ? <><label>报告问题说明<textarea value={issueNote} onChange={(event) => setIssueNote(event.target.value)} /></label><button type="button" disabled={!issueNote.trim() || issuePending} onClick={() => void reportIssue()}>{issuePending ? '提交中…' : '报告数据问题'}</button></> : null}{issueLink && capabilities.has('manual_issue.read') ? <a href={issueLink}>在人工问题中查看</a> : null}
          </section>
        </aside>
      </div>
      <DangerousActionDialog open={dialog === 'submit'} title="确认提交标注复核" stableResourceId={task.id} impact={[`冻结 Draft revision ${detail.draft.revision}`, '创建不可变 AnnotationSubmission 与 AnnotationSet', '任务进入 SUBMITTED，编辑器切换为只读']} blockedReasons={preflightResult?.submission_gate.blocked_reasons} pending={submit.isPending} confirmLabel="确认提交" onCancel={() => setDialog(null)} onConfirm={() => void confirmSubmit()} />
      <DangerousActionDialog open={dialog === 'review-approve' || dialog === 'review-return'} title={dialog === 'review-return' ? '确认退回标注' : '确认通过标注'} stableResourceId={task.id} impact={dialog === 'review-return' ? ['创建不可变 RETURNED 复核记录', '任务返回标注员继续修订，旧提交快照保持不变'] : ['创建不可变 APPROVED 复核记录', '任务进入完成状态，提交快照不可修改']} pending={review.isPending} confirmLabel={dialog === 'review-return' ? '确认退回' : '确认通过'} onCancel={() => setDialog(null)} onConfirm={() => void confirmReview()} />
      <DangerousActionDialog open={dialog === 'rebase'} title="确认在新修订上重建任务" stableResourceId={task.id} impact={['旧任务与旧草稿永久保持 STALE 只读', '创建新的 taskId 与 revision-0 空白草稿', '不会复制任何标签、锚点或 annotation ID']} pending={rebase.isPending} confirmLabel="创建空白后继任务" onCancel={() => setDialog(null)} onConfirm={() => void confirmRebase()} />
    </main>
  );
}
