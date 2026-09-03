import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  ArrowLeft,
  Eye,
  EyeOff,
  GitCompareArrows,
  ListChecks,
  Save,
  Scissors,
  Send,
  ShieldCheck,
} from 'lucide-react';
import { asCleaningDraftId } from '../../entities/cleaning-draft';
import {
  decimalNanoseconds,
  type EdlOperation,
  type EditingDecisionList,
} from '../../entities/edl';
import {
  useCleaningDraftBootstrap,
  useCommitCleaningDraft,
  useCreateCleaningPreview,
  useReadonlyCleaningReviewFindings,
  useSaveCleaningEdl,
  type CleaningReviewFeedback,
  type CleaningWorkbenchModel,
} from '../../features/cleaning/api';
import '../../features/cleaning/cleaning.css';
import { CleaningStatePanel, cleaningStateFromError } from '../../features/cleaning/page-state';
import { routes as cleaningRoutes } from '../../features/cleaning/routing';
import { outputTimeToSource } from '../../features/cleaning/time-mapping';
import { ConfirmDialog } from '../../features/datasets/components/ConfirmDialog';
import {
  createPlaybackClock,
  EpisodeWorkbenchCore,
  type StreamDescriptor,
} from '../../features/viewer';
import { isDomainError } from '../../shared/api/domain-error';
import { useCapabilities } from '../../shared/auth/use-capabilities';
import {
  int64FromBigInt,
  int64String,
  int64ToBigInt,
  compareInt64,
} from '../../shared/lib/bigint-string';
import {
  formatEffectiveDuration,
  formatSignedDuration,
  formatStorageSize,
  formatTimeRange,
} from '../../shared/lib/metric-presentation';
import { PageHeader, StatusBadge } from '../../shared/ui';
import { cleaningWorkbenchQueryCodec } from './query-codec';
import styles from './workbench.module.css';

function intentId(prefix: string): string {
  const suffix =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID().replaceAll('-', '_')
      : `${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
  return `${prefix}_${suffix}`;
}

function validDraftId(value: string | undefined): string | undefined {
  if (!value) return undefined;
  try {
    return asCleaningDraftId(value);
  } catch {
    return undefined;
  }
}

function modality(kind: string): StreamDescriptor['modality'] {
  switch (kind) {
    case 'RGB_VIDEO':
      return 'rgb';
    case 'DEPTH':
    case 'DEPTH_PREVIEW':
      return 'depth';
    case 'POINTCLOUD':
    case 'POINTCLOUD_PREVIEW':
      return 'pointcloud';
    case 'JOINT_STATE':
      return 'joint_state';
    case 'POSE':
      return 'pose';
    case 'IMU':
      return 'imu';
    case 'TACTILE':
      return 'tactile';
    default:
      return 'other';
  }
}

function viewerStreams(model: CleaningWorkbenchModel): readonly StreamDescriptor[] {
  return model.streams.map((stream) => ({
    id: stream.streamId,
    canonicalPath: stream.channelPath,
    displayName: stream.channelPath,
    modality: modality(stream.kind),
    semanticRole: stream.kind,
    schema: { id: `cleaning/${stream.kind.toLowerCase()}`, version: '1' },
    startNs: '0',
    endNs: stream.durationNs,
    availability: 'ready',
  }));
}

function minimumDuration(streams: CleaningWorkbenchModel['streams']): string {
  return streams
    .slice(1)
    .reduce(
      (minimum, stream) =>
        compareInt64(int64String(stream.durationNs), int64String(minimum)) < 0
          ? stream.durationNs
          : minimum,
      streams[0]!.durationNs,
    );
}

function resequence(operations: readonly EdlOperation[]): readonly EdlOperation[] {
  return operations.map((operation, sequenceNo) => ({ ...operation, sequenceNo }));
}

function operationSummary(operation: EdlOperation): string {
  switch (operation.type) {
    case 'TRIM':
      return formatTimeRange(operation.startNs, operation.endNs);
    case 'EXCLUDE_RANGE':
      return `${formatTimeRange(operation.startNs, operation.endNs)} · ${operation.reason ?? '无原因'}`;
    case 'SPLIT':
      return `位置 ${formatEffectiveDuration(operation.atNs)}`;
    case 'TIME_OFFSET':
      return `${operation.episodeStreamId} ${formatSignedDuration(operation.offsetNs)}`;
    case 'DISABLE_CHANNEL':
      return operation.episodeStreamId;
    case 'SET_METADATA':
      return `${Object.keys(operation.patch).length} 个字段`;
    case 'INVALIDATE_EPISODE':
      return operation.reasonCode;
    case 'INVALID_MASK':
      return formatTimeRange(operation.startNs, operation.endNs);
  }
}

function operationRange(
  operation: EdlOperation,
  durationValue: string,
): Readonly<{ left: string; width: string }> | null {
  const duration = BigInt(durationValue);
  if (duration <= 0n) return null;
  const bounds =
    operation.type === 'TRIM' ||
    operation.type === 'EXCLUDE_RANGE' ||
    operation.type === 'INVALID_MASK'
      ? ([BigInt(operation.startNs), BigInt(operation.endNs)] as const)
      : operation.type === 'SPLIT'
        ? ([BigInt(operation.atNs), BigInt(operation.atNs) + duration / 100n] as const)
        : null;
  if (!bounds) return null;
  const low = bounds[0] < 0n ? 0n : bounds[0];
  const high = bounds[1] > duration ? duration : bounds[1];
  const left = Number((low * 10_000n) / duration) / 100;
  const width = Number(((high - low) * 10_000n) / duration) / 100;
  return { left: `${left}%`, width: `${Math.max(width, 1)}%` };
}

function sourceRangeForFinding(
  finding: CleaningReviewFeedback['findings'][number],
  model: CleaningWorkbenchModel,
): readonly [string, string] | null {
  if (finding.outputRevisionId === model.base.revisionId) return [finding.startNs, finding.endNs];
  const mapping = model.preview?.mapping;
  if (!mapping) return null;
  const sourceStart = outputTimeToSource(
    mapping,
    finding.outputRevisionId,
    decimalNanoseconds(finding.startNs),
  );
  const outputEndExclusive = int64ToBigInt(int64String(finding.endNs));
  if (outputEndExclusive <= 0n) return null;
  const outputLast = decimalNanoseconds(int64FromBigInt(outputEndExclusive - 1n));
  const sourceLast = outputTimeToSource(mapping, finding.outputRevisionId, outputLast);
  if (!sourceStart || !sourceLast) return null;
  return [sourceStart, int64FromBigInt(int64ToBigInt(sourceLast) + 1n)];
}

function WorkbenchContent({
  model,
  search,
  feedback,
}: Readonly<{
  model: CleaningWorkbenchModel;
  search: ReturnType<typeof cleaningWorkbenchQueryCodec.parse>;
  feedback: CleaningReviewFeedback | null;
}>) {
  const capabilities = useCapabilities();
  const navigate = useNavigate();
  const streams = useMemo(() => viewerStreams(model), [model]);
  const timelineDuration = useMemo(() => minimumDuration(model.streams), [model.streams]);
  const clock = useMemo(
    () => createPlaybackClock({ startNs: '0', endNs: timelineDuration }),
    [timelineDuration],
  );
  const [baseline, setBaseline] = useState<EditingDecisionList>(model.edl);
  const [operations, setOperations] = useState<readonly EdlOperation[]>(model.edl.operations);
  const [dirty, setDirty] = useState(false);
  const [selectedRange, setSelectedRange] = useState<readonly [string, string] | null>(null);
  const [focusedFinding, setFocusedFinding] = useState<string | null>(search.findingId ?? null);
  const [commitOpen, setCommitOpen] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const initialFindingApplied = useRef(false);
  const save = useSaveCleaningEdl();
  const preview = useCreateCleaningPreview();
  const commit = useCommitCleaningDraft();

  useEffect(() => () => clock.dispose(), [clock]);
  useEffect(() => {
    setBaseline(model.edl);
    if (!dirty) setOperations(model.edl.operations);
  }, [dirty, model.edl]);

  const editable =
    model.draft.status === 'EDITING' &&
    !model.draft.hasUnknownState &&
    model.lease?.readOnly !== true;
  const canSave =
    editable &&
    dirty &&
    capabilities.has('cleaning.edit') &&
    model.allowedActions.includes('SAVE_EDL') &&
    !save.isPending;
  const canPreview =
    editable &&
    !dirty &&
    model.validation.status === 'PASSED' &&
    capabilities.has('cleaning.preview') &&
    model.allowedActions.includes('CREATE_PREVIEW') &&
    !preview.isPending;
  const commitBlockedReasons = useMemo(() => {
    const reasons: { code: string; message: string }[] = [];
    if (!editable)
      reasons.push({
        code: 'DRAFT_NOT_EDITABLE',
        message: '当前 Draft 不是可编辑事实或编辑租约为只读。',
      });
    if (dirty) reasons.push({ code: 'UNSAVED_EDL', message: '存在未保存的 EDL 修改。' });
    if (model.validation.status !== 'PASSED')
      reasons.push({ code: 'EDL_VALIDATION_FAILED', message: '服务端 EDL 校验未通过。' });
    if (model.preview?.status !== 'READY')
      reasons.push({ code: 'PREVIEW_REQUIRED', message: '需要 READY Preview。' });
    if (
      model.preview &&
      (model.preview.edlRevision !== baseline.revision ||
        model.preview.operationHash !== baseline.operationHash)
    )
      reasons.push({
        code: 'PREVIEW_STALE',
        message: 'Preview 与当前保存的 revision/hash 不一致。',
      });
    if (!capabilities.has('cleaning.submit'))
      reasons.push({ code: 'CAPABILITY_MISSING', message: '缺少 cleaning.submit capability。' });
    if (!model.allowedActions.includes('COMMIT'))
      reasons.push({ code: 'ACTION_BLOCKED', message: '服务端 allowed_actions 未允许 COMMIT。' });
    return reasons;
  }, [
    baseline.operationHash,
    baseline.revision,
    capabilities,
    dirty,
    editable,
    model.allowedActions,
    model.preview,
    model.validation.status,
  ]);
  const reportedBlockedReasons =
    commit.error && isDomainError(commit.error) ? commit.error.blockedReasons : [];
  const allCommitBlockedReasons = [...commitBlockedReasons, ...reportedBlockedReasons];

  const updateOperations = (next: readonly EdlOperation[]) => {
    setOperations(resequence(next));
    setDirty(true);
  };
  const performSave = async () => {
    const result = await save.mutateAsync({
      draftId: model.draft.id,
      etag: model.draft.etag,
      expectedRevision: baseline.revision,
      expectedOperationHash: baseline.operationHash,
      operations,
      clientMutationId: intentId('mut_cleaning_save'),
    });
    setBaseline(result.edl);
    setOperations(result.edl.operations);
    setDirty(false);
  };
  const performPreview = () =>
    preview.mutate({
      draftId: model.draft.id,
      etag: model.draft.etag,
      baseRevisionId: model.base.revisionId,
      edlRevision: baseline.revision,
      operationHash: baseline.operationHash,
      idempotencyKey: intentId('idem_cleaning_preview'),
    });
  const performCommit = () => {
    if (!model.preview || commitBlockedReasons.length || !acknowledged) return;
    commit.mutate(
      {
        draftId: model.draft.id,
        etag: model.draft.etag,
        previewId: model.preview.previewId,
        baseRevisionId: model.base.revisionId,
        edlRevision: baseline.revision,
        operationHash: baseline.operationHash,
        successorCompositionHash: model.successorCompositionHash,
        idempotencyKey: intentId('idem_cleaning_commit'),
        confirmed: true,
      },
      { onSuccess: () => setCommitOpen(false) },
    );
  };
  const focusFinding = useCallback(
    (finding: CleaningReviewFeedback['findings'][number]) => {
      const range = sourceRangeForFinding(finding, model);
      setFocusedFinding(finding.id);
      if (range) {
        clock.seek(range[0]);
        setSelectedRange(range);
        void navigate(
          cleaningWorkbenchQueryCodec.build(model.draft.id, {
            ...search,
            findingId: finding.id,
            windowStartNs: range[0],
            windowEndNs: range[1],
          }),
          { replace: true },
        );
      }
    },
    [clock, model, navigate, search],
  );

  const routeFinding = search.findingId
    ? feedback?.findings.find((finding) => finding.id === search.findingId)
    : undefined;
  useEffect(() => {
    if (!routeFinding || initialFindingApplied.current) return;
    initialFindingApplied.current = true;
    focusFinding(routeFinding);
  }, [focusFinding, routeFinding]);

  return (
    <>
      <section className={`cleaning-origin-bar ${styles.originBar}`} aria-label="清洗草稿来源">
        {model.draft.origin.kind === 'ISSUE_DERIVED' ? (
          <p>
            来源：ManualIssue <code>{model.draft.origin.manualIssueIds[0]}</code> · 固定范围{' '}
            {formatTimeRange(model.draft.origin.startNs, model.draft.origin.endNs)}
          </p>
        ) : (
          <p>
            来源：Review Return · 原草稿 <code>{model.draft.origin.supersedesDraftId}</code> ·
            不可变 Decision <code>{model.draft.origin.returnedFromReviewDecisionId}</code>
          </p>
        )}
      </section>
      <section
        className={`cleaning-workbench-toolbar ${styles.toolbar}`}
        aria-label="清洗工作台操作"
      >
        <div className={styles.toolbarFacts}>
          <StatusBadge
            status={model.draft.status}
            tone={model.draft.status === 'EDITING' ? 'info' : 'neutral'}
          />
          <span>
            EDL revision <code>{baseline.revision}</code>
          </span>
          <span className={dirty ? styles.dirty : styles.synced}>
            {dirty ? '有未保存修改' : '已与服务端基线同步'}
          </span>
        </div>
        <div className={styles.toolbarActions}>
          {search.returnTo ? (
            <Link to={search.returnTo}>
              <ArrowLeft aria-hidden="true" size={15} />
              返回来源页
            </Link>
          ) : (
            <Link to={cleaningRoutes.cleaningDrafts.build({})}>
              <ArrowLeft aria-hidden="true" size={15} />
              返回草稿列表
            </Link>
          )}
          <button type="button" disabled={!canSave} onClick={() => void performSave()}>
            <Save aria-hidden="true" size={15} />
            {save.isPending ? '保存中…' : '保存 EDL'}
          </button>
          <button type="button" disabled={!canPreview} onClick={performPreview}>
            <Eye aria-hidden="true" size={15} />
            {preview.isPending ? '请求中…' : '生成 Preview'}
          </button>
          <button
            type="button"
            className={styles.submitButton}
            disabled={model.draft.status !== 'EDITING'}
            onClick={() => {
              setCommitOpen(true);
              setAcknowledged(false);
            }}
          >
            <Send aria-hidden="true" size={15} />
            提交版本
          </button>
        </div>
      </section>
      {save.error || preview.error || commit.error ? (
        <p className="cleaning-error" role="alert">
          {[save.error, preview.error, commit.error]
            .filter(Boolean)
            .map((error) =>
              isDomainError(error) ? error.message : '操作未完成；服务端事实没有被乐观推进。',
            )
            .join('；')}
        </p>
      ) : null}
      {search.findingId && !routeFinding ? (
        <p className="cleaning-warning">
          URL 中的 findingId 不属于当前不可变 ReviewDecision，已忽略定位。
        </p>
      ) : null}
      <div className={`cleaning-workbench-grid ${styles.workbenchGrid}`}>
        <aside className={`cleaning-editor-panel ${styles.operationPanel}`} aria-label="EDL 编辑器">
          <div className={styles.panelTitle}>
            <Scissors aria-hidden="true" size={17} />
            <div>
              <h2>清洗操作</h2>
              <span>按顺序执行 · {operations.length} 项</span>
            </div>
          </div>
          <button
            type="button"
            className={styles.rangeButton}
            disabled={!editable || !selectedRange}
            onClick={() =>
              selectedRange &&
              updateOperations([
                ...operations,
                {
                  id: intentId('operation'),
                  sequenceNo: operations.length,
                  enabled: true,
                  schemaVersion: '1',
                  type: 'EXCLUDE_RANGE',
                  startNs: decimalNanoseconds(selectedRange[0]),
                  endNs: decimalNanoseconds(selectedRange[1]),
                  reason: '人工选择范围',
                },
              ])
            }
          >
            将选区加入 EXCLUDE_RANGE
          </button>
          <ol className="cleaning-operation-list">
            {operations.map((operation) => (
              <li
                className="cleaning-operation"
                key={operation.id}
                aria-current={search.operationId === operation.id ? 'true' : undefined}
              >
                <div className={styles.operationHeading}>
                  <strong>
                    {operation.sequenceNo + 1}. {operation.type}
                  </strong>
                  <label>
                    <input
                      type="checkbox"
                      checked={operation.enabled}
                      disabled={!editable}
                      onChange={(event) =>
                        updateOperations(
                          operations.map((item) =>
                            item.id === operation.id
                              ? { ...item, enabled: event.target.checked }
                              : item,
                          ),
                        )
                      }
                    />{' '}
                    启用
                  </label>
                </div>
                <code>{operation.id}</code>
                <span>{operationSummary(operation)}</span>
                <button
                  type="button"
                  disabled={!editable}
                  onClick={() =>
                    updateOperations(operations.filter((item) => item.id !== operation.id))
                  }
                >
                  移除
                </button>
              </li>
            ))}
          </ol>
          {operations.length === 0 ? (
            <p className="cleaning-muted">空 EDL 是合法的服务端基线。</p>
          ) : null}
          <div className={styles.operationHash}>
            Operation hash <code>{baseline.operationHash}</code>
          </div>
        </aside>
        <section
          className={`cleaning-viewer-panel ${styles.viewerPanel}`}
          aria-label="Episode 播放与对照"
        >
          <div className={styles.viewerHeader}>
            <div>
              <GitCompareArrows aria-hidden="true" size={17} />
              <strong>相机与状态对照</strong>
            </div>
            <nav className="cleaning-projection-tabs" aria-label="Preview 对照模式">
              {(['source', 'cleaned', 'ab'] as const).map((compare) => (
                <Link
                  key={compare}
                  aria-current={search.compare === compare ? 'page' : undefined}
                  to={cleaningWorkbenchQueryCodec.build(model.draft.id, { ...search, compare })}
                >
                  {compare === 'ab' ? 'A/B' : compare === 'source' ? 'Source' : 'Cleaned'}
                </Link>
              ))}
            </nav>
          </div>
          <section className={styles.compareDeck} aria-label="Source 与 Cleaned 安全缺省对照">
            <section>
              <header>
                <strong>Source</strong>
                <span>{model.streams[0]?.channelPath ?? '无 Stream'}</span>
              </header>
              <div>
                <EyeOff aria-hidden="true" size={23} />
                <strong>源媒体未授权</strong>
                <span>Bootstrap 仅返回 Stream 身份，未返回媒体 descriptor。</span>
              </div>
              <footer>{model.base.revisionId}</footer>
            </section>
            <section>
              <header>
                <strong>Cleaned</strong>
                <span>{model.preview?.status ?? 'NO_PREVIEW'}</span>
              </header>
              <div>
                <EyeOff aria-hidden="true" size={23} />
                <strong>清洗预览媒体不可用</strong>
                <span>
                  {model.preview
                    ? 'Preview 事实已就绪，但当前响应未携带授权资源。'
                    : '尚无权威 Preview 事实。'}
                </span>
              </div>
              <footer>{model.preview?.previewId ?? '未生成 Preview'}</footer>
            </section>
          </section>
          <EpisodeWorkbenchCore
            datasetId={model.base.datasetId}
            versionId={model.base.versionId}
            episodeId={model.base.episodeId}
            clock={clock}
            mode="cleaning"
            streams={streams}
            onTimeRangeSelect={(startNs, endNs) => setSelectedRange([startNs, endNs])}
          />
          <section className={styles.operationTracks} aria-label="清洗操作时间带">
            <div className={styles.trackScale}>
              <span>{formatEffectiveDuration('0')}</span>
              <span>{formatEffectiveDuration(timelineDuration)}</span>
            </div>
            <div className={styles.trackRow}>
              <strong>保留范围</strong>
              <div>
                <span className={styles.keepBand}>权威 Source 窗口</span>
              </div>
            </div>
            <div className={styles.trackRow}>
              <strong>EDL 操作</strong>
              <div>
                {operations.map((operation) => {
                  const range = operationRange(operation, timelineDuration);
                  return range ? (
                    <span
                      key={operation.id}
                      className={styles.operationBand}
                      style={range}
                      data-compact={Number.parseFloat(range.width) < 8 || undefined}
                      aria-label={`${operation.sequenceNo + 1}. ${operation.type}`}
                      title={`${operation.sequenceNo + 1}. ${operation.type}`}
                    >
                      {Number.parseFloat(range.width) < 8
                        ? operation.sequenceNo + 1
                        : `${operation.sequenceNo + 1}. ${operation.type}`}
                    </span>
                  ) : null;
                })}
              </div>
            </div>
            <div className={styles.trackRow}>
              <strong>Channel</strong>
              <div>
                {operations
                  .filter(
                    (operation) =>
                      operation.type === 'TIME_OFFSET' || operation.type === 'DISABLE_CHANNEL',
                  )
                  .map((operation) => (
                    <span key={operation.id} className={styles.channelBand}>
                      {operationSummary(operation)}
                    </span>
                  ))}
              </div>
            </div>
          </section>
          {selectedRange ? (
            <p>
              当前半开范围：{formatTimeRange(selectedRange[0], selectedRange[1])}
            </p>
          ) : null}
        </section>
        <aside className={styles.inspectorPanel} aria-label="清洗事实检视">
          <div className={styles.panelTitle}>
            <ShieldCheck aria-hidden="true" size={17} />
            <div>
              <h2>当前草稿</h2>
              <span>权威校验与影响摘要</span>
            </div>
          </div>
          <section className={styles.validationSummary}>
            <h3>服务端校验</h3>
            <p>校验：{model.validation.status}</p>
            {model.validation.issues.map((issue) => (
              <p
                className={issue.severity === 'BLOCKER' ? 'cleaning-error' : 'cleaning-warning'}
                key={`${issue.code}:${issue.operationId ?? '-'}`}
              >
                {issue.code}：{issue.message}
              </p>
            ))}
            <dl>
              <dt>Source 时长</dt>
              <dd>{formatEffectiveDuration(model.summary.sourceDurationNs)}</dd>
              <dt>Output 时长</dt>
              <dd>{formatEffectiveDuration(model.summary.outputDurationNs)}</dd>
              <dt>输出段</dt>
              <dd>{model.summary.outputSegmentCount}</dd>
              <dt>复用率</dt>
              <dd>{model.summary.reuseRate}</dd>
            </dl>
          </section>
          {model.preview ? (
            <section className={styles.factSection}>
              <h3>Preview</h3>
              <p>
                {model.preview.status} · revision {model.preview.edlRevision}
              </p>
              <p>
                <code>{model.preview.previewId}</code>
              </p>
            </section>
          ) : null}
          {model.commit ? (
            <section className={styles.factSection}>
              <h3>Commit</h3>
              <p>
                {model.commit.status} · materialization {model.commit.materializationStatus}
              </p>
              {model.commit.outputRevisions.map((revision) => (
                <p key={revision.revisionId}>
                  #{revision.ordinal} <code>{revision.revisionId}</code>
                </p>
              ))}
            </section>
          ) : null}
          {feedback ? (
            <section
              className={`cleaning-feedback-panel ${styles.feedbackPanel}`}
              aria-labelledby="readonly-findings-title"
            >
              <div className={styles.feedbackTitle}>
                <ListChecks aria-hidden="true" size={16} />
                <h2 id="readonly-findings-title">复核退回定位（只读）</h2>
              </div>
              <p>
                ReviewDecision <code>{feedback.decisionId}</code>；P11 不提供 Finding mutation。
              </p>
              <ul>
                {feedback.findings.map((finding) => (
                  <li key={finding.id} data-focused={focusedFinding === finding.id || undefined}>
                    <button type="button" onClick={() => focusFinding(finding)}>
                      <code>{finding.id}</code> · {finding.severity} · {finding.findingType} ·{' '}
                      {formatTimeRange(finding.startNs, finding.endNs)}
                    </button>
                    <p>{finding.note}</p>
                  </li>
                ))}
              </ul>
              {model.draft.id === feedback.supersedesDraftId ? (
                <Link
                  className="primary-link"
                  to={cleaningRoutes.cleaningWorkbench.build({
                    draftId: feedback.successorDraftId,
                    findingId: focusedFinding ?? undefined,
                  })}
                >
                  打开后继草稿 <code>{feedback.successorDraftId}</code>
                </Link>
              ) : null}
            </section>
          ) : null}
        </aside>
      </div>
      <ConfirmDialog
        open={commitOpen}
        title="确认提交清洗 Draft"
        resourceId={model.draft.id}
        description="Commit 会记录待物化的不可变输出；物化状态以服务端返回为准，且不会乐观推进 Draft 或 Version 状态。"
        impact={[
          `输出 ${model.summary.outputSegmentCount} 个 Revision 段，Output 时长 ${formatEffectiveDuration(model.summary.outputDurationNs)}`,
          `新增派生数据 ${formatStorageSize(model.summary.newDerivedBytes)}，复用源数据 ${formatStorageSize(model.summary.reusedSourceBytes)}`,
        ]}
        blockedReasons={[]}
        confirmLabel="确认危险提交"
        confirmDisabled={!acknowledged || allCommitBlockedReasons.length > 0}
        submitting={commit.isPending}
        onCancel={() => setCommitOpen(false)}
        onConfirm={performCommit}
      >
        <section className="cleaning-commit-dialog" aria-label="blocked_reasons">
          <h3>blocked_reasons</h3>
          {allCommitBlockedReasons.length ? (
            <ul>
              {allCommitBlockedReasons.map((reason) => (
                <li key={reason.code}>
                  <code>{reason.code}</code>：{reason.message}
                </li>
              ))}
            </ul>
          ) : (
            <p>无阻断项。</p>
          )}
        </section>
        <label>
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(event) => setAcknowledged(event.target.checked)}
          />{' '}
          我已核对影响摘要并完成 Preview 对照
        </label>
      </ConfirmDialog>
    </>
  );
}

export function ManualCleaningWorkbenchPage() {
  const params = useParams<{ draftId: string }>();
  const draftId = validDraftId(params.draftId);
  const [query] = useSearchParams();
  const search = cleaningWorkbenchQueryCodec.parse(query);
  const capabilities = useCapabilities();
  const canRead =
    !capabilities.loading && !capabilities.failed && capabilities.has('cleaning.read');
  const bootstrap = useCleaningDraftBootstrap(draftId, canRead);
  const decisionId = bootstrap.data?.reviewFeedback?.decisionId;
  const findings = useReadonlyCleaningReviewFindings(
    draftId,
    decisionId,
    canRead && Boolean(decisionId),
  );

  if (capabilities.loading)
    return (
      <main className="cleaning-page">
        <PageHeader title="手动清洗工作台" />
        <CleaningStatePanel state="first-loading" label="权限" />
      </main>
    );
  if (!canRead)
    return (
      <main className="cleaning-page">
        <PageHeader title="手动清洗工作台" />
        <CleaningStatePanel state="forbidden" label="清洗读取权限" />
      </main>
    );
  if (!draftId)
    return (
      <main className="cleaning-page">
        <PageHeader title="手动清洗工作台" />
        <CleaningStatePanel state="contract-mismatch" label="非法 Draft ID" />
      </main>
    );
  if (bootstrap.isPending)
    return (
      <main className="cleaning-page">
        <PageHeader title="手动清洗工作台" description={`Draft ${draftId}`} />
        <CleaningStatePanel state="first-loading" label="工作台 Bootstrap" />
      </main>
    );
  if (bootstrap.error)
    return (
      <main className="cleaning-page">
        <PageHeader title="手动清洗工作台" />
        <CleaningStatePanel
          state={cleaningStateFromError(bootstrap.error, true)}
          label="工作台 Bootstrap"
          error={bootstrap.error}
          onRetry={() => void bootstrap.refetch()}
        />
      </main>
    );
  if (!bootstrap.data) return null;
  return (
    <main className={`cleaning-page cleaning-workbench-page ${styles.page}`}>
      <PageHeader
        title="手动清洗工作台"
        description={`Draft ${bootstrap.data.draft.id} · 固定 base Revision ${bootstrap.data.base.revisionId}`}
        breadcrumbs={[
          { key: 'issues', label: '手动清洗', to: cleaningRoutes.manualIssues.build({}) },
          { key: 'drafts', label: '清洗草稿', to: cleaningRoutes.cleaningDrafts.build({}) },
          { key: 'workbench', label: '工作台' },
        ]}
      />
      {findings.error ? (
        <CleaningStatePanel
          state={cleaningStateFromError(findings.error)}
          label="只读 ReviewFinding"
          error={findings.error}
          onRetry={() => void findings.refetch()}
        />
      ) : null}
      <WorkbenchContent
        model={bootstrap.data}
        search={search}
        feedback={findings.data ?? bootstrap.data.reviewFeedback}
      />
    </main>
  );
}

export const Component = ManualCleaningWorkbenchPage;
export default ManualCleaningWorkbenchPage;
