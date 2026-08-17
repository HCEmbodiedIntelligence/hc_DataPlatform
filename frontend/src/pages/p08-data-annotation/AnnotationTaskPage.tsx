import { useEffect, useMemo, useRef, useState } from 'react';
import type { JSX } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  Bookmark,
  BoxSelect,
  CircleDot,
  Flag,
  Layers3,
  ListChecks,
  Save,
  Send,
  Tags,
  TriangleAlert,
} from 'lucide-react';
import type { AnnotationEntryWire } from '../../entities/annotation-draft';
import { annotationEntryWireSchema } from '../../entities/annotation-draft';
import { annotationTaskDisplayStateLabel } from '../../entities/annotation-task';
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
  useAnnotationTasks,
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
import {
  createLazyThreeRobotSceneLoader,
  createPlaybackClock,
  EpisodeWorkbenchCore,
} from '../../features/viewer';
import type { ViewerTimelineSelection, ViewerTimelineTrack } from '../../features/viewer';
import { annotationRoutes } from './routes';
import { annotationTaskQueryCodec } from './query-codec';
import './p08.css';
import styles from './workbench.module.css';

const annotationRobotJointNames = Array.from({ length: 7 }, (_, index) => `joint_${index + 1}`);
const annotationRobotModelRef = { modelId: 'annotation-demo-arm', modelVersion: '1.0.0' } as const;
const annotationRobotJointMapping: Readonly<Record<string, string>> = Object.fromEntries(
  annotationRobotJointNames.map((jointName) => [jointName, jointName]),
);
const annotationRobotSceneLoader = createLazyThreeRobotSceneLoader(() =>
  Promise.resolve({
    manifest: {
      ...annotationRobotModelRef,
      requiredJoints: annotationRobotJointNames,
    },
    urdfUrl: `${import.meta.env.BASE_URL}robots/annotation-demo/robot.urdf`,
    background: '#eaf1ef',
  }),
);

type DialogKind = 'submit' | 'review-approve' | 'review-return' | 'rebase' | null;

const supportedFieldTypes = new Set([
  'string',
  'number',
  'boolean',
  'enum',
  'multi-enum',
  'time-point',
  'time-range',
]);

function relativeSecondsToNs(value: string): bigint {
  const [seconds = '0', fraction = ''] = value.split('.', 2);
  return BigInt(seconds) * 1_000_000_000n + BigInt(fraction.padEnd(9, '0'));
}

function entryValues(detail: AnnotationTaskDetail): Record<string, unknown> {
  const first = detail.draft.entries.find(
    (entry): entry is AnnotationEntryWire => 'semantic_type' in entry,
  );
  if (!first)
    return {
      semanticType: 'PHASE',
      labelCode: '',
      startNs: detail.task.source.startNs,
      endNs: detail.task.source.endNs,
    };
  const result: Record<string, unknown> = {
    semanticType: first.semantic_type,
    labelCode: first.label_code,
    ...first.attributes,
  };
  if (first.anchor.anchor_type === 'TIME_RANGE') {
    result.startNs = first.anchor.start_ns;
    result.endNs = first.anchor.end_ns;
  } else if (first.anchor.anchor_type === 'TIME_POINT') result.atNs = first.anchor.at_ns;
  else {
    result.startNs = first.anchor.start_ns;
    result.endNs = first.anchor.end_ns;
  }
  return result;
}

function buildEntry(
  detail: AnnotationTaskDetail,
  values: Readonly<Record<string, unknown>>,
): AnnotationEntryWire {
  const semanticTypeValue = typeof values.semanticType === 'string' ? values.semanticType : '';
  const semanticType = ['ACTION', 'PHASE', 'OBJECT', 'EVENT', 'KEYFRAME'].includes(
    semanticTypeValue,
  )
    ? semanticTypeValue
    : 'PHASE';
  const labelCode =
    typeof values.labelCode === 'string' && values.labelCode ? values.labelCode : 'unlabeled';
  const startNs = typeof values.startNs === 'string' ? values.startNs : detail.task.source.startNs;
  const endNs = typeof values.endNs === 'string' ? values.endNs : detail.task.source.endNs;
  const atNs = typeof values.atNs === 'string' ? values.atNs : startNs;
  const attributes = Object.fromEntries(
    Object.entries(values).filter(
      ([key, value]) =>
        !['semanticType', 'labelCode', 'startNs', 'endNs', 'atNs'].includes(key) &&
        (value === null ||
          ['string', 'number', 'boolean'].includes(typeof value) ||
          Array.isArray(value)),
    ),
  );
  const existing = detail.draft.entries.find(
    (entry): entry is AnnotationEntryWire => 'semantic_type' in entry,
  );
  const annotationId = existing?.annotation_id ?? `${detail.task.id}:entry-1`;
  const base = { annotation_id: annotationId, label_code: labelCode, attributes };
  if (semanticType === 'ACTION' || semanticType === 'PHASE')
    return annotationEntryWireSchema.parse({
      ...base,
      semantic_type: semanticType,
      anchor: {
        anchor_type: 'TIME_RANGE',
        coordinate_system: 'REVISION_TIME_NS',
        start_ns: startNs,
        end_ns: endNs,
      },
    });
  if (semanticType === 'EVENT' || semanticType === 'KEYFRAME')
    return annotationEntryWireSchema.parse({
      ...base,
      semantic_type: semanticType,
      anchor: { anchor_type: 'TIME_POINT', coordinate_system: 'REVISION_TIME_NS', at_ns: atNs },
    });
  const streamId = detail.task.source.streamIds[0];
  return annotationEntryWireSchema.parse({
    ...base,
    semantic_type: 'OBJECT',
    anchor: {
      anchor_type: 'OBJECT_TRACK',
      coordinate_system: 'REVISION_TIME_NS',
      stream_id: streamId,
      start_ns: startNs,
      end_ns: endNs,
      observations: [
        {
          at_ns: startNs,
          geometry: { geometry_type: 'BBOX_2D_NORMALIZED', x: 0, y: 0, width: 0.1, height: 0.1 },
          occluded: false,
        },
      ],
    },
  });
}

function buildDraftEntries(
  detail: AnnotationTaskDetail,
  values: Readonly<Record<string, unknown>>,
): readonly AnnotationEntryWire[] {
  if (detail.draft.hasUnsupportedEntries)
    throw new Error('草稿包含当前客户端未知的标注类型，不能安全保存。');
  const replacement = buildEntry(detail, values);
  let replaced = false;
  const entries = detail.draft.entries.map((entry) => {
    if (!('semantic_type' in entry))
      throw new Error('草稿包含当前客户端未知的标注类型，不能安全保存。');
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

function timelineRange(
  startValue: string,
  endValue: string,
  timelineStart: string,
  timelineEnd: string,
): Readonly<{ left: string; width: string }> {
  const start = BigInt(timelineStart);
  const end = BigInt(timelineEnd);
  const duration = end - start;
  if (duration <= 0n) return { left: '0%', width: '100%' };
  const low = BigInt(startValue) < start ? start : BigInt(startValue);
  const high = BigInt(endValue) > end ? end : BigInt(endValue);
  const left = Number(((low - start) * 10_000n) / duration) / 100;
  const width = Number(((high - low) * 10_000n) / duration) / 100;
  return { left: `${left}%`, width: `${Math.max(width, 1)}%` };
}

export function AnnotationTaskPage(): JSX.Element {
  const { taskId } = useParams<{ taskId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const search = useMemo(() => annotationTaskQueryCodec.parse(location.search), [location.search]);
  const shellScope = useShellStore((state) => state.scope);
  const capabilities = useCapabilities();
  const scope = useMemo(
    () =>
      shellScope?.projectId && shellScope.regionCode
        ? { projectId: shellScope.projectId, regionCode: shellScope.regionCode }
        : null,
    [shellScope?.projectId, shellScope?.regionCode],
  );
  const activeScope = scope ?? { projectId: 'unavailable', regionCode: 'unavailable' };
  const safeTaskId = taskId ?? 'unavailable';
  const canRead = capabilities.has('annotation_task.read') && capabilities.has('episode.read');
  const detailQuery = useAnnotationTask(scope, taskId, canRead);
  const queueQuery = useAnnotationTasks(
    scope,
    { queue: 'assigned_to_me', states: [], sort: 'priority_desc', limit: 20 },
    canRead && !capabilities.loading && !capabilities.failed,
  );
  const save = useSaveAnnotationDraft(activeScope, safeTaskId);
  const preflight = usePreflightAnnotationSubmission(activeScope, safeTaskId);
  const submit = useSubmitAnnotationTask(activeScope, safeTaskId);
  const review = useReviewAnnotationTask(activeScope, safeTaskId);
  const rebase = useRebaseAnnotationTask(activeScope, safeTaskId);
  const [values, setValues] = useState<Readonly<Record<string, unknown>>>({});
  const [dirty, setDirty] = useState(false);
  const [dialog, setDialog] = useState<DialogKind>(null);
  const [preflightResult, setPreflightResult] = useState<Awaited<
    ReturnType<typeof preflight.mutateAsync>
  > | null>(null);
  const [acceptedJob, setAcceptedJob] = useState<{
    readonly id: string;
    readonly status: string;
  } | null>(null);
  const [summaryErrors, setSummaryErrors] = useState<readonly string[]>([]);
  const [serverErrors, setServerErrors] = useState<
    readonly { path: string; code: string; message: string }[]
  >([]);
  const [issueNote, setIssueNote] = useState('');
  const [issueLink, setIssueLink] = useState<string | null>(null);
  const [issuePending, setIssuePending] = useState(false);
  const [recoveredLocalDraft, setRecoveredLocalDraft] = useState(false);
  const initializedBaseline = useRef<string | null>(null);

  const detail = detailQuery.data;
  const clockTaskId = detail?.task.id;
  const clockStartNs = detail?.task.source.startNs;
  const clockEndNs = detail?.task.source.endNs;
  const clock = useMemo(
    () =>
      clockTaskId && clockStartNs && clockEndNs
        ? createPlaybackClock({ startNs: clockStartNs, endNs: clockEndNs })
        : null,
    [clockEndNs, clockStartNs, clockTaskId],
  );
  useEffect(() => () => clock?.dispose(), [clock]);
  useEffect(() => {
    if (!clock || !search.t) return;
    clock.seek((BigInt(clock.startNs) + relativeSecondsToNs(search.t)).toString());
  }, [clock, search.t]);
  const localIdentity = useMemo(
    () =>
      detail && scope
        ? {
            projectId: scope.projectId,
            regionCode: scope.regionCode,
            taskId: detail.task.id,
            taskEtag: detail.task.etag,
            baselineRevision: detail.draft.revision,
          }
        : null,
    [detail, scope],
  );
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

  const granted = useMemo(
    () => new Set(annotationCapabilities.filter((capability) => capabilities.has(capability))),
    [capabilities],
  );
  const editDecision = decideAnnotationCapability(granted, 'annotation.edit');
  const draftDecision = decideAnnotationCapability(granted, 'annotation_draft.edit');
  const saveDecision = decideAnnotationCapability(granted, 'annotation.save');
  const submitDecision = decideAnnotationCapability(granted, 'annotation.submit');
  const reviewDecision = decideAnnotationCapability(granted, 'annotation.review');
  const rebaseDecision = decideAnnotationCapability(granted, 'annotation_task.rebase');

  useLocalAnnotationDraftAutosave(localIdentity, detail?.formDefinition ?? null, values, dirty);
  useUnsavedChangesWarning(dirty);

  if (capabilities.loading || detailQuery.isLoading)
    return <AnnotationPageState kind="first-loading" />;
  if (capabilities.failed || !canRead) return <AnnotationPageState kind="forbidden" />;
  if (!scope || !taskId)
    return <AnnotationPageState kind="feature-unavailable" detail="缺少项目、Region 或任务 ID。" />;
  if (detailQuery.error)
    return (
      <AnnotationPageState
        kind={errorState(detailQuery.error)}
        requestId={
          isDomainError(detailQuery.error) ? (detailQuery.error.requestId ?? undefined) : undefined
        }
        onRetry={() => void detailQuery.refetch()}
      />
    );
  if (!detail || !clock) return <AnnotationPageState kind="fatal-error" />;

  const task = detail.task;
  const editorReady = initializedBaseline.current === baselineIdentity;
  const stale = task.sourceStatus === 'STALE';
  const unsupportedSchema =
    detail.formDefinition?.fields.some((field) => !supportedFieldTypes.has(field.type)) ?? true;
  const unknown =
    task.workflowStatus === 'UNKNOWN' ||
    task.sourceStatus === 'UNKNOWN' ||
    detail.draft.state === 'UNKNOWN' ||
    detail.draft.hasUnsupportedEntries ||
    unsupportedSchema;
  const mutationPending =
    save.isPending ||
    submit.isPending ||
    review.isPending ||
    rebase.isPending ||
    acceptedJob !== null;
  const canEdit =
    editorReady &&
    !stale &&
    !unknown &&
    !mutationPending &&
    editDecision.state === 'allowed' &&
    task.allowedActions.has('EDIT_DRAFT');
  const canSave =
    canEdit &&
    dirty &&
    draftDecision.state === 'allowed' &&
    saveDecision.state === 'allowed' &&
    task.allowedActions.has('SAVE_DRAFT');
  const canSubmit =
    !dirty &&
    !stale &&
    !unknown &&
    editorReady &&
    !mutationPending &&
    detail.draft.state === 'ACTIVE' &&
    submitDecision.state === 'allowed' &&
    task.allowedActions.has('PREFLIGHT_SUBMIT') &&
    task.allowedActions.has('SUBMIT') &&
    (detail.submissionGate.status === 'PASS' || detail.submissionGate.status === 'ADVISORY');
  const canReview =
    !mutationPending &&
    reviewDecision.state === 'allowed' &&
    task.allowedActions.has('REVIEW') &&
    task.workflowStatus === 'SUBMITTED';
  const canRebase =
    !mutationPending &&
    stale &&
    rebaseDecision.state === 'allowed' &&
    task.allowedActions.has('REBASE');
  const canReportIssue =
    capabilities.has('manual_issue.create') &&
    task.allowedActions.has('REPORT_ISSUE') &&
    !stale &&
    !unknown;

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
      await save.mutateAsync({
        ...scope,
        taskId: task.id,
        etag: task.etag,
        idempotencyKey: createIdempotencyKey(),
        expectedRevision: detail.draft.revision,
        clientMutationId: createIdempotencyKey(),
        entries,
      });
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
      const result = await preflight.mutateAsync({
        ...scope,
        taskId: task.id,
        etag: task.etag,
        idempotencyKey: createIdempotencyKey(),
        expectedRevision: detail.draft.revision,
        expectedContentHash: detail.draft.contentHash,
      });
      if (!result.valid || result.validation_errors.length) {
        setServerErrors(result.validation_errors);
        setSummaryErrors(result.submission_gate.blocked_reasons);
        return;
      }
      setPreflightResult(result);
      setDialog('submit');
    } catch (error) {
      captureMutationError(error);
    }
  };

  const confirmSubmit = async () => {
    if (!preflightResult) return;
    try {
      const result = await submit.mutateAsync({
        ...scope,
        taskId: task.id,
        etag: task.etag,
        idempotencyKey: createIdempotencyKey(),
        expectedRevision: detail.draft.revision,
        expectedContentHash: detail.draft.contentHash,
        preflightId: preflightResult.preflight_id,
      });
      if (result.kind === 'accepted-job')
        setAcceptedJob({ id: result.jobId, status: result.status });
      setDialog(null);
    } catch (error) {
      setDialog(null);
      captureMutationError(error);
    }
  };

  const confirmReview = async () => {
    const returned = dialog === 'review-return';
    try {
      await review.mutateAsync({
        ...scope,
        taskId: task.id,
        etag: task.etag,
        idempotencyKey: createIdempotencyKey(),
        decision: returned ? 'RETURNED' : 'APPROVED',
        reasonCodes: returned ? ['ANNOTATION_CORRECTION_REQUIRED'] : [],
        comment: returned ? '请按结构化反馈修订后重新提交。' : null,
      });
      setDialog(null);
    } catch (error) {
      setDialog(null);
      captureMutationError(error);
    }
  };

  const confirmRebase = async () => {
    const targetRevisionId = detail.staleInfo?.replacementRevisionId ?? '';
    if (!targetRevisionId) {
      setSummaryErrors(['服务端未返回权威替代 Revision，不能 Rebase。']);
      return;
    }
    try {
      const result = await rebase.mutateAsync({
        ...scope,
        taskId: task.id,
        etag: task.etag,
        idempotencyKey: createIdempotencyKey(),
        targetRevisionId,
        reason: '用户确认在权威替代 Revision 上创建空白任务',
        successorAssigneeId: task.assigneeId,
      });
      void navigate(annotationRoutes.task.build({ taskId: result.successorTask.id }));
    } catch (error) {
      setDialog(null);
      captureMutationError(error);
    }
  };

  const reportIssue = async () => {
    const streamId = task.source.streamIds[0];
    if (!streamId || !shellScope?.organizationId) return;
    setIssuePending(true);
    try {
      const current = BigInt(clock.currentNs());
      const issueEnd =
        current + 1_000_000_000n < BigInt(task.source.endNs)
          ? current + 1_000_000_000n
          : BigInt(task.source.endNs);
      const issue = await reportManualIssueFromAnnotation({
        organizationId: shellScope.organizationId,
        projectId: scope.projectId,
        regionCode: scope.regionCode,
        datasetId: task.source.datasetId,
        versionId: task.source.datasetVersionId,
        episodeId: task.source.episodeId,
        revisionId: task.source.baseRevisionId,
        streamId,
        startNs: current.toString(),
        endNs: issueEnd.toString(),
        issueType: 'OTHER',
        severity: 'MEDIUM',
        note: issueNote,
        idempotencyKey: createIdempotencyKey(),
      });
      setIssueLink(
        buildManualIssuesLink({
          datasetId: task.source.datasetId,
          versionId: task.source.datasetVersionId,
          episodeId: task.source.episodeId,
          issueId: issue.id,
          returnTo: `${location.pathname}${location.search}`,
        }),
      );
      setIssueNote('');
      void detailQuery.refetch();
    } catch (error) {
      setSummaryErrors([error instanceof Error ? error.message : '人工问题创建失败']);
    } finally {
      setIssuePending(false);
    }
  };

  const semanticEntry = detail.draft.entries.find(
    (entry): entry is AnnotationEntryWire => 'semantic_type' in entry,
  );
  const semanticTypes: readonly AnnotationEntryWire['semantic_type'][] = [
    'PHASE',
    'ACTION',
    'OBJECT',
    'EVENT',
    'KEYFRAME',
  ];
  const rawSemanticType = typeof values.semanticType === 'string' ? values.semanticType : '';
  const currentSemanticType: AnnotationEntryWire['semantic_type'] = semanticTypes.includes(
    rawSemanticType as AnnotationEntryWire['semantic_type'],
  )
    ? (rawSemanticType as AnnotationEntryWire['semantic_type'])
    : (semanticEntry?.semantic_type ?? 'PHASE');
  const currentLabel =
    typeof values.labelCode === 'string' && values.labelCode
      ? values.labelCode
      : (semanticEntry?.label_code ?? '未命名标注');
  const isRangeSemantic = ['PHASE', 'ACTION', 'OBJECT'].includes(currentSemanticType);
  const selectedStart =
    typeof values.startNs === 'string'
      ? values.startNs
      : semanticEntry?.anchor.anchor_type !== 'TIME_POINT'
        ? semanticEntry?.anchor.start_ns
        : undefined;
  const selectedEnd =
    typeof values.endNs === 'string'
      ? values.endNs
      : semanticEntry?.anchor.anchor_type !== 'TIME_POINT'
        ? semanticEntry?.anchor.end_ns
        : undefined;
  const canonicalNs = /^(0|[1-9][0-9]*)$/;
  const validSelection =
    isRangeSemantic &&
    selectedStart !== undefined &&
    selectedEnd !== undefined &&
    canonicalNs.test(selectedStart) &&
    canonicalNs.test(selectedEnd) &&
    BigInt(selectedStart) < BigInt(selectedEnd) &&
    BigInt(selectedStart) >= BigInt(task.source.startNs) &&
    BigInt(selectedEnd) <= BigInt(task.source.endNs);
  const timelineSelection: ViewerTimelineSelection | undefined = validSelection
    ? { startNs: selectedStart!, endNs: selectedEnd!, label: currentLabel }
    : undefined;
  const toneBySemantic = {
    PHASE: 'phase',
    ACTION: 'action',
    OBJECT: 'object',
    EVENT: 'event',
    KEYFRAME: 'event',
  } as const;
  const segmentBuckets = new Map<
    AnnotationEntryWire['semantic_type'],
    ViewerTimelineTrack['segments'][number][]
  >(semanticTypes.map((semanticType) => [semanticType, []]));
  const currentAtNs =
    typeof values.atNs === 'string'
      ? values.atNs
      : semanticEntry?.anchor.anchor_type === 'TIME_POINT'
        ? semanticEntry.anchor.at_ns
        : undefined;
  if (isRangeSemantic && timelineSelection) {
    segmentBuckets.get(currentSemanticType)?.push({
      id: semanticEntry?.annotation_id ?? `${task.id}:current`,
      label: currentLabel,
      startNs: timelineSelection.startNs,
      endNs: timelineSelection.endNs,
      tone: toneBySemantic[currentSemanticType],
    });
  } else if (currentAtNs && canonicalNs.test(currentAtNs)) {
    segmentBuckets.get(currentSemanticType)?.push({
      id: semanticEntry?.annotation_id ?? `${task.id}:current`,
      label: currentLabel,
      startNs: currentAtNs,
      tone: toneBySemantic[currentSemanticType],
    });
  }
  detail.draft.entries.forEach((entry) => {
    if (!('semantic_type' in entry) || entry.annotation_id === semanticEntry?.annotation_id) return;
    const segment = entry.anchor.anchor_type === 'TIME_POINT'
      ? { id: entry.annotation_id, label: entry.label_code, startNs: entry.anchor.at_ns, tone: toneBySemantic[entry.semantic_type] }
      : { id: entry.annotation_id, label: entry.label_code, startNs: entry.anchor.start_ns, endNs: entry.anchor.end_ns, tone: toneBySemantic[entry.semantic_type] };
    segmentBuckets.get(entry.semantic_type)?.push(segment);
  });
  const trackDefinitions = [
    { id: 'phase', label: '阶段', level: 0, semanticType: 'PHASE' },
    { id: 'action', label: '动作', level: 1, semanticType: 'ACTION' },
    { id: 'object', label: '对象', level: 2, semanticType: 'OBJECT' },
    { id: 'event', label: '事件', level: 1, semanticType: 'EVENT' },
    { id: 'keyframe', label: '关键帧', level: 2, semanticType: 'KEYFRAME' },
  ] as const;
  const timelineTracks: readonly ViewerTimelineTrack[] = [
    ...trackDefinitions.map((track) => ({ ...track, segments: segmentBuckets.get(track.semanticType) ?? [] })),
    {
      id: 'issues',
      label: '数据问题',
      level: 0,
      segments: detail.manualIssues.map((issue) => ({
        id: issue.id,
        label: issue.summary || issue.impact,
        startNs: issue.startNs,
        endNs: issue.endNs,
        tone: 'issue' as const,
      })),
    },
  ];
  const pendingCapabilityReason = [draftDecision, rebaseDecision].find(
    (decision) => decision.state === 'feature-unavailable',
  )?.reason;
  const semanticRange =
    semanticEntry && semanticEntry.anchor.anchor_type !== 'TIME_POINT'
      ? timelineRange(
          semanticEntry.anchor.start_ns,
          semanticEntry.anchor.end_ns,
          task.source.startNs,
          task.source.endNs,
        )
      : null;
  const authorizedMediaAvailable = detail.streams.some((stream) => Boolean(stream.mediaSource));
  const tools = [
    { label: '动作', icon: <Bookmark aria-hidden="true" size={17} /> },
    { label: '阶段', icon: <Layers3 aria-hidden="true" size={17} /> },
    { label: '对象', icon: <BoxSelect aria-hidden="true" size={17} /> },
    { label: '事件', icon: <Flag aria-hidden="true" size={17} /> },
    { label: '关键帧', icon: <CircleDot aria-hidden="true" size={17} /> },
  ];
  return (
    <main className={`p08-page p08-task-page ${styles.page}`}>
      <header className={`p08-task-header ${styles.taskHeader}`}>
        <button type="button" className={styles.backButton} onClick={leave}>
          <ArrowLeft aria-hidden="true" size={16} /> 返回任务
        </button>
        <div className={styles.identity}>
          <p className="p08-eyebrow">数据标注 / 我的任务 / {task.id}</p>
          <div className={styles.titleLine}>
            <h1>任务 {task.id}</h1>
            <span className={`p08-badge p08-badge--${task.displayState.toLowerCase()}`}>
              {annotationTaskDisplayStateLabel(task.displayState)}
            </span>
          </div>
          <p>
            {task.source.datasetId} · {task.source.datasetVersionId} · {task.source.episodeId} ·{' '}
            {task.source.baseRevisionId}
          </p>
        </div>
        <div className={`p08-primary-actions ${styles.primaryActions}`}>
          <button type="button" className={styles.issueAction} disabled={!canReportIssue}>
            <TriangleAlert aria-hidden="true" size={15} /> 报告人工问题
          </button>
          <button type="button" disabled={!canSave || save.isPending} onClick={() => void doSave()}>
            <Save aria-hidden="true" size={15} /> {save.isPending ? '保存中…' : '保存标注'}
          </button>
          <button
            type="button"
            disabled={!canSubmit || preflight.isPending}
            onClick={() => void openSubmit()}
          >
            <Send aria-hidden="true" size={15} /> 提交复核
          </button>
          {canReview ? (
            <>
              <button type="button" onClick={() => setDialog('review-approve')}>
                通过标注
              </button>
              <button type="button" onClick={() => setDialog('review-return')}>
                退回修改
              </button>
            </>
          ) : null}
        </div>
      </header>
      {detailQuery.isFetching ? <AnnotationPageState kind="refreshing" /> : null}
      {acceptedJob ? (
        <section role="status" className="p08-stale-banner">
          <h2>提交任务已接受</h2>
          <p>
            异步 Job <code>{acceptedJob.id}</code> 当前为 {acceptedJob.status}
            ；完成前不会乐观显示为已提交。
          </p>
        </section>
      ) : null}
      {recoveredLocalDraft ? (
        <section role="status" className="p08-recovery-banner">
          <p>已恢复本任务在当前服务端版本上的短期本地工作副本；保存前请复核。</p>
        </section>
      ) : null}
      {stale ? (
        <section className="p08-stale-banner" role="alert">
          <h2>基础数据已更新，任务已失效</h2>
          <p>旧草稿保持只读且不会静默迁移。Rebase 将在权威替代 Revision 上创建空白任务。</p>
          <button type="button" disabled={!canRebase} onClick={() => setDialog('rebase')}>
            在新修订上重建任务
          </button>
        </section>
      ) : null}
      {unknown ? <AnnotationPageState kind="unknown-enum" /> : null}
      {pendingCapabilityReason ? (
        <AnnotationPageState kind="feature-unavailable" detail={pendingCapabilityReason} />
      ) : null}
      {summaryErrors.length ? (
        <section role="alert" className="p08-error-summary">
          <h2>需要处理的问题</h2>
          <ul>
            {summaryErrors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        </section>
      ) : null}
      <div className={`p08-workbench-grid ${styles.workbenchGrid}`}>
        <aside className={`p08-tool-rail ${styles.toolRail}`} aria-label="任务队列与标注工具">
          <div className={styles.railHeading}>
            <ListChecks aria-hidden="true" size={17} />
            <h2>我的任务</h2>
          </div>
          <div className={styles.taskQueue}>
            {queueQuery.data?.items.slice(0, 6).map((item) => (
              <button
                type="button"
                key={item.id}
                aria-current={item.id === task.id ? 'page' : undefined}
                onClick={() => void navigate(annotationRoutes.task.build({ taskId: item.id }))}
              >
                <span>{item.id}</span>
                <small>
                  {annotationTaskDisplayStateLabel(item.displayState)} · P{item.priority}
                </small>
              </button>
            )) ?? <span className={styles.queueState}>任务列表加载中</span>}
          </div>
          <div className={styles.railHeading}>
            <Tags aria-hidden="true" size={17} />
            <h2>标注工具</h2>
          </div>
          <div className={styles.toolButtons}>
            {tools.map((tool) => (
              <button
                type="button"
                key={tool.label}
                disabled={!canEdit}
                title={`${tool.label}标注`}
              >
                {tool.icon}
                <span>{tool.label}</span>
              </button>
            ))}
          </div>
          <div className={styles.railMeta}>
            <span>Schema</span>
            <strong>{task.ontology.version}</strong>
          </div>
        </aside>
        <section
          className={`p08-viewer-area ${styles.viewerArea}`}
          aria-label="多模态标注画布"
        >
          <div className={styles.panelHeader}>
            <div>
              <strong>多模态视图</strong>
              <span>{detail.streams.length} 个权威流</span>
            </div>
            <span className={authorizedMediaAvailable ? styles.healthy : styles.unavailable}>
              {authorizedMediaAvailable ? '● 资源就绪' : '○ 媒体未授权'}
            </span>
          </div>
          <EpisodeWorkbenchCore
            episodeId={task.source.episodeId}
            datasetId={task.source.datasetId}
            versionId={task.source.datasetVersionId}
            clock={clock}
            mode="annotate"
            streams={detail.streams}
            timelineSelection={timelineSelection}
            timelineTracks={timelineTracks}
            timelineDisabled={!canEdit || !isRangeSemantic}
            onTimeRangeSelect={(startNs, endNs) => {
              setValues((currentValues) => ({ ...currentValues, startNs, endNs }));
              setDirty(true);
              setRecoveredLocalDraft(false);
              setPreflightResult(null);
              setServerErrors([]);
              if (dialog === 'submit') setDialog(null);
            }}
            robotScene={{
              modelRef: annotationRobotModelRef,
              jointMapping: annotationRobotJointMapping,
              clock,
              runtimeLoader: annotationRobotSceneLoader,
              title: '机器人 URDF',
              canonicalPath: 'robot/model/annotation-demo',
            }}
            onResourceError={(error) => setSummaryErrors([`局部资源失败：${error.message}`])}
          />
          <section className={styles.annotationTracks} aria-label="标注状态与事件时间带">
            <div className={styles.trackScale}>
              <span>0 ns</span>
              <span>{task.source.endNs} ns</span>
            </div>
            <div className={styles.trackRow}>
              <strong>动作/阶段</strong>
              <div>
                {semanticEntry && semanticRange ? (
                  <span className={styles.semanticSegment} style={semanticRange}>
                    {semanticEntry.label_code}
                  </span>
                ) : (
                  <em>当前草稿无区间标注</em>
                )}
              </div>
            </div>
            <div className={styles.trackRow}>
              <strong>人工问题</strong>
              <div>
                {detail.manualIssues.length ? (
                  detail.manualIssues.map((issue) => (
                    <span key={issue.id} className={styles.issueMarker}>
                      {issue.impact}
                    </span>
                  ))
                ) : (
                  <em>无问题投影</em>
                )}
              </div>
            </div>
            <div className={styles.trackRow}>
              <strong>Event</strong>
              <div>
                <em>Bootstrap 未返回事件轨道</em>
              </div>
            </div>
          </section>
        </section>
        <aside className={`p08-inspector ${styles.inspector}`} aria-label="标注属性与任务检视">
          <nav className={styles.inspectorTabs} aria-label="Inspector 分区">
            <button type="button" aria-current="page">
              标签
            </button>
            <button type="button" disabled>
              任务信息
            </button>
          </nav>
          {detail.manualIssues.length ? (
            <section className={styles.issueSummary}>
              <strong>{detail.manualIssues.length} 个问题影响当前任务</strong>
              <span>问题投影保持只读，编辑不会改写问题事实。</span>
            </section>
          ) : null}
          {detail.formDefinition && editorReady ? (
            <SchemaDrivenAnnotationForm
              key={`${task.id}:${detail.draft.revision}:${timelineSelection?.startNs ?? 'point'}:${timelineSelection?.endNs ?? 'point'}`}
              definition={detail.formDefinition}
              defaultValues={values}
              disabled={!canEdit}
              serverErrors={serverErrors}
              onChange={(next, changed) => {
                setValues(next);
                setDirty(changed);
                if (changed) {
                  setPreflightResult(null);
                  if (dialog === 'submit') setDialog(null);
                }
              }}
              onSubmit={(next) => {
                setValues(next);
                setDirty(true);
                setPreflightResult(null);
              }}
              onFormErrorSummary={setSummaryErrors}
            />
          ) : detail.formDefinition ? (
            <AnnotationPageState kind="first-loading" />
          ) : (
            <AnnotationPageState
              kind="feature-unavailable"
              detail="Bootstrap 未返回可验证的 Schema 表单定义。"
            />
          )}
          <section className={styles.issues}>
            <h2>数据问题（只读）</h2>
            {detail.manualIssues.length ? (
              <ul>
                {detail.manualIssues.map((issue) => (
                  <li key={issue.id}>
                    <strong>{issue.impact}</strong> {issue.summary}
                  </li>
                ))}
              </ul>
            ) : (
              <p>当前投影无问题。</p>
            )}
            {canReportIssue ? (
              <>
                <label>
                  报告问题说明
                  <textarea
                    value={issueNote}
                    onChange={(event) => setIssueNote(event.target.value)}
                  />
                </label>
                <button
                  type="button"
                  disabled={!issueNote.trim() || issuePending}
                  onClick={() => void reportIssue()}
                >
                  {issuePending ? '提交中…' : '报告数据问题'}
                </button>
              </>
            ) : null}
            {issueLink && capabilities.has('manual_issue.read') ? (
              <a href={issueLink}>在人工问题中查看</a>
            ) : null}
          </section>
        </aside>
      </div>
      <DangerousActionDialog
        open={dialog === 'submit'}
        title="确认提交标注复核"
        stableResourceId={task.id}
        impact={[
          `冻结 Draft revision ${detail.draft.revision}`,
          '创建不可变 AnnotationSubmission 与 AnnotationSet',
          '任务进入已提交状态，编辑器切换为只读',
        ]}
        blockedReasons={preflightResult?.submission_gate.blocked_reasons}
        pending={submit.isPending}
        confirmLabel="确认提交"
        onCancel={() => setDialog(null)}
        onConfirm={() => void confirmSubmit()}
      />
      <DangerousActionDialog
        open={dialog === 'review-approve' || dialog === 'review-return'}
        title={dialog === 'review-return' ? '确认退回标注' : '确认通过标注'}
        stableResourceId={task.id}
        impact={
          dialog === 'review-return'
            ? ['创建不可变的退回复核记录', '任务返回标注员继续修订，旧提交快照保持不变']
            : ['创建不可变 APPROVED 复核记录', '任务进入完成状态，提交快照不可修改']
        }
        pending={review.isPending}
        confirmLabel={dialog === 'review-return' ? '确认退回' : '确认通过'}
        onCancel={() => setDialog(null)}
        onConfirm={() => void confirmReview()}
      />
      <DangerousActionDialog
        open={dialog === 'rebase'}
        title="确认在新修订上重建任务"
        stableResourceId={task.id}
        impact={[
          '旧任务与旧草稿永久保持失效只读',
          '创建新的 taskId 与 revision-0 空白草稿',
          '不会复制任何标签、锚点或 annotation ID',
        ]}
        pending={rebase.isPending}
        confirmLabel="创建空白后继任务"
        onCancel={() => setDialog(null)}
        onConfirm={() => void confirmRebase()}
      />
    </main>
  );
}
