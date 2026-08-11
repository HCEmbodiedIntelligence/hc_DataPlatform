import { annotationEntryWireSchema } from '../../../entities/annotation-draft';
import type { AnnotationDraft, AnnotationEntryWire, UnsupportedAnnotationEntry } from '../../../entities/annotation-draft';
import type { AnnotationFieldDefinition, AnnotationFormDefinition } from '../../../entities/annotation-schema';
import type { AnnotationTask, AnnotationTaskAction, AnnotationTaskSourceStatus, AnnotationTaskWorkflowStatus } from '../../../entities/annotation-task';
import { toAnnotationTaskDisplayState } from '../../../entities/annotation-task';
import { createDomainError } from '../../../shared/api/domain-error';
import type { StreamDescriptor, ViewerAxisDescriptor, ViewerStreamModality } from '../../viewer';
import type { AnnotationDraftWire, AnnotationTaskDetailEnvelopeWire, AnnotationTaskWire } from './wire-schemas';

const workflowStatuses = new Set<AnnotationTaskWorkflowStatus>(['QUEUED', 'ASSIGNED', 'IN_PROGRESS', 'BLOCKED', 'SUBMITTED', 'RETURNED', 'APPROVED', 'CANCELLED']);
const sourceStatuses = new Set<AnnotationTaskSourceStatus>(['CURRENT', 'STALE']);
const actions = new Set<AnnotationTaskAction>(['CLAIM', 'ASSIGN', 'EDIT_DRAFT', 'SAVE_DRAFT', 'PREFLIGHT_SUBMIT', 'SUBMIT', 'REVIEW', 'REBASE', 'REPORT_ISSUE', 'VIEW_ANNOTATION_SET', 'VIEW_MANUAL_ISSUE']);
const modalities = new Set<ViewerStreamModality>(['rgb', 'depth', 'pointcloud', 'joint_state', 'action', 'force', 'pose', 'imu', 'tactile', 'event', 'other']);
const unsignedNs = /^(0|[1-9][0-9]*)$/;

function safeWireLabel(value: unknown, fallback: string): string {
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
    ? String(value)
    : fallback;
}

function contractMismatch(requestId: string, message: string): Error {
  return createDomainError({
    code: 'CONTRACT_MISMATCH',
    message,
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [],
    requestId,
    retryable: false,
    httpStatus: null,
  });
}

export function adaptAnnotationTask(wire: AnnotationTaskWire): AnnotationTask {
  const workflowStatus = workflowStatuses.has(wire.workflow_status as AnnotationTaskWorkflowStatus) ? wire.workflow_status as AnnotationTaskWorkflowStatus : 'UNKNOWN';
  const sourceStatus = sourceStatuses.has(wire.source_status as AnnotationTaskSourceStatus) ? wire.source_status as AnnotationTaskSourceStatus : 'UNKNOWN';
  return {
    id: wire.task_id,
    scope: { organizationId: wire.scope.organization_id, projectId: wire.scope.project_id, regionCode: wire.scope.region_code },
    workflowStatus,
    sourceStatus,
    displayState: toAnnotationTaskDisplayState(workflowStatus, sourceStatus),
    source: {
      datasetId: wire.source.dataset_id,
      datasetVersionId: wire.source.dataset_version_id,
      episodeId: wire.source.episode_id,
      baseRevisionId: wire.source.base_revision_id,
      streamIds: wire.source.stream_ids,
      startNs: wire.source.start_ns,
      endNs: wire.source.end_ns,
    },
    ontology: { id: wire.ontology.ontology_id, version: wire.ontology.ontology_version, hash: wire.ontology.ontology_hash },
    priority: wire.priority,
    assigneeId: wire.assignment.assignee_id,
    currentDraftRevision: wire.current_draft_revision,
    currentDraftHash: wire.current_draft_hash,
    etag: wire.etag,
    allowedActions: new Set(wire.allowed_actions.filter((action): action is AnnotationTaskAction => actions.has(action as AnnotationTaskAction))),
    blockedReasons: [...wire.action_reasons.map((reason) => reason.message), ...(wire.block_reason ? [wire.block_reason] : [])],
    predecessorTaskId: wire.predecessor_task_id,
    successorTaskId: wire.successor_task_id,
    updatedAt: wire.updated_at,
  };
}

export function adaptAnnotationDraft(wire: AnnotationDraftWire): AnnotationDraft {
  let unsupported = false;
  const entries = wire.entries.map((raw): AnnotationEntryWire | UnsupportedAnnotationEntry => {
    const parsed = annotationEntryWireSchema.safeParse(raw);
    if (parsed.success) return parsed.data;
    unsupported = true;
    return { semanticType: 'UNSUPPORTED', rawSemanticType: safeWireLabel(raw.semantic_type, 'UNKNOWN'), raw };
  });
  const knownStates = new Set<AnnotationDraft['state']>(['ACTIVE', 'FROZEN_SUBMITTED', 'STALE_READ_ONLY']);
  return {
    taskId: wire.task_id,
    revision: wire.draft_revision,
    state: knownStates.has(wire.state as AnnotationDraft['state']) ? wire.state as AnnotationDraft['state'] : 'UNKNOWN',
    entries,
    contentHash: wire.content_hash,
    savedAt: wire.saved_at,
    etag: wire.etag,
    hasUnsupportedEntries: unsupported,
  };
}

function adaptStream(raw: Readonly<Record<string, unknown>>): StreamDescriptor | null {
  const schema = raw.schema;
  if (
    typeof raw.stream_id !== 'string'
    || typeof raw.channel_definition_id !== 'string'
    || typeof raw.canonical_path !== 'string'
    || typeof raw.display_name !== 'string'
    || typeof raw.modality !== 'string'
    || typeof raw.start_ns !== 'string'
    || typeof raw.end_ns !== 'string'
    || !unsignedNs.test(raw.start_ns)
    || !unsignedNs.test(raw.end_ns)
    || BigInt(raw.start_ns) >= BigInt(raw.end_ns)
    || !schema
    || typeof schema !== 'object'
  ) return null;
  const schemaRecord = schema as Readonly<Record<string, unknown>>;
  if (typeof schemaRecord.schema_id !== 'string' || typeof schemaRecord.schema_version !== 'string') return null;
  const rawAxes = Array.isArray(schemaRecord.axes) ? schemaRecord.axes : undefined;
  const axes = rawAxes?.map((value): ViewerAxisDescriptor | null => {
    if (!value || typeof value !== 'object') return null;
    const axis = value as Readonly<Record<string, unknown>>;
    if (
      typeof axis.axis_id !== 'string'
      || typeof axis.source_name !== 'string'
      || typeof axis.display_name !== 'string'
      || typeof axis.sample_index !== 'number'
      || !Number.isInteger(axis.sample_index)
      || typeof axis.unit !== 'string'
      || !['MAPPED', 'UNMAPPED', 'NOT_APPLICABLE'].includes(String(axis.mapping_status))
    ) return null;
    const limit = axis.limit;
    let axisLimit: ViewerAxisDescriptor['limit'];
    if (limit !== undefined) {
      if (typeof limit !== 'object' || limit === null) return null;
      const limitRecord = limit as Readonly<Record<string, unknown>>;
      const min = limitRecord.min;
      const max = limitRecord.max;
      if (typeof min !== 'number' || typeof max !== 'number') return null;
      axisLimit = { min, max };
    }
    return {
      axisId: axis.axis_id,
      sourceName: axis.source_name,
      displayName: axis.display_name,
      sampleIndex: axis.sample_index,
      unit: axis.unit,
      ...(typeof axis.mapped_joint_name === 'string' ? { mappedJointName: axis.mapped_joint_name } : {}),
      mappingStatus: axis.mapping_status === 'MAPPED' ? 'mapped' : axis.mapping_status === 'UNMAPPED' ? 'unmapped' : 'not-applicable',
      ...(axisLimit ? { limit: axisLimit } : {}),
    };
  });
  if (axes?.some((axis) => axis === null)) return null;
  if (schemaRecord.shape !== undefined && (!Array.isArray(schemaRecord.shape) || !schemaRecord.shape.every((value) => Number.isInteger(value) && value >= 0))) return null;
  const modalityAlias: Readonly<Record<string, ViewerStreamModality>> = { rgb_video: 'rgb', depth_preview: 'depth' };
  const normalizedModality = raw.modality.toLowerCase();
  const rawModality = modalityAlias[normalizedModality] ?? normalizedModality;
  const availabilityValue = safeWireLabel(raw.availability, '').toLowerCase().replaceAll('_', '-');
  const availability = availabilityValue === 'preview-generating' || availabilityValue === 'generating'
    ? 'preview-generating'
    : availabilityValue === 'partial' || availabilityValue === 'unsupported' || availabilityValue === 'missing' || availabilityValue === 'ready'
      ? availabilityValue
      : null;
  if (availability === null) return null;
  const frame = raw.frame;
  const clockDomain = raw.clock_domain;
  if (frame !== undefined && (typeof frame !== 'object' || frame === null || typeof (frame as Record<string, unknown>).id !== 'string' || typeof (frame as Record<string, unknown>).name !== 'string')) return null;
  if (clockDomain !== undefined && (typeof clockDomain !== 'object' || clockDomain === null || typeof (clockDomain as Record<string, unknown>).id !== 'string' || typeof (clockDomain as Record<string, unknown>).name !== 'string')) return null;
  return {
    id: raw.stream_id,
    channelDefinitionId: raw.channel_definition_id,
    canonicalPath: raw.canonical_path,
    displayName: raw.display_name,
    modality: modalities.has(rawModality as ViewerStreamModality) ? rawModality as ViewerStreamModality : rawModality,
    ...(typeof raw.semantic_role === 'string' ? { semanticRole: raw.semantic_role } : {}),
    schema: {
      id: schemaRecord.schema_id,
      version: schemaRecord.schema_version,
      ...(typeof schemaRecord.dtype === 'string' ? { dtype: schemaRecord.dtype } : {}),
      ...(Array.isArray(schemaRecord.shape) && schemaRecord.shape.every((value) => typeof value === 'number') ? { shape: schemaRecord.shape } : {}),
      ...(axes ? { axes: axes as ViewerAxisDescriptor[] } : {}),
      ...(typeof schemaRecord.unit === 'string' ? { unit: schemaRecord.unit } : {}),
      ...(typeof schemaRecord.encoding === 'string' ? { encoding: schemaRecord.encoding } : {}),
    },
    ...(typeof raw.rate_hz === 'number' && Number.isFinite(raw.rate_hz) && raw.rate_hz > 0 ? { rateHz: raw.rate_hz } : {}),
    startNs: raw.start_ns,
    endNs: raw.end_ns,
    ...(frame ? { frame: frame as { id: string; name: string } } : {}),
    ...(clockDomain ? { clockDomain: clockDomain as { id: string; name: string } } : {}),
    ...(typeof raw.calibration_set_id === 'string' ? { calibrationSetId: raw.calibration_set_id } : {}),
    availability,
  };
}

export interface AnnotationTaskDetail {
  readonly task: AnnotationTask;
  readonly draft: AnnotationDraft;
  readonly manualIssues: readonly {
    readonly id: string;
    readonly impact: 'ADVISORY' | 'BLOCKING' | 'UNKNOWN';
    readonly summary: string;
    readonly startNs: string;
    readonly endNs: string;
  }[];
  readonly submissionGate: {
    readonly status: 'PASS' | 'ADVISORY' | 'BLOCKED' | 'UNAVAILABLE' | 'UNKNOWN';
    readonly blockedReasons: readonly string[];
    readonly advisoryIssueIds: readonly string[];
  };
  readonly streams: readonly StreamDescriptor[];
  readonly formDefinition: AnnotationFormDefinition | null;
  readonly staleInfo: {
    readonly reason: string;
    readonly supersededRevisionId: string;
    readonly replacementRevisionId: string;
    readonly staleAt: string;
  } | null;
  readonly requestId: string;
}

function adaptFormDefinition(raw: Readonly<Record<string, unknown>> | undefined): AnnotationFormDefinition | null {
  if (!raw || typeof raw.schema_version_id !== 'string' || typeof raw.title !== 'string' || !Array.isArray(raw.fields)) return null;
  const fields = raw.fields.flatMap((value): AnnotationFieldDefinition[] => {
    if (!value || typeof value !== 'object') return [];
    const field = value as Readonly<Record<string, unknown>>;
    if (typeof field.key !== 'string' || typeof field.label !== 'string' || typeof field.type !== 'string') return [];
    const options = Array.isArray(field.options) ? field.options.flatMap((option): Array<{ value: string; label: string }> => {
      if (!option || typeof option !== 'object') return [];
      const record = option as Readonly<Record<string, unknown>>;
      return typeof record.value === 'string' && typeof record.label === 'string' ? [{ value: record.value, label: record.label }] : [];
    }) : undefined;
    return [{
      key: field.key,
      label: field.label,
      type: field.type,
      ...(typeof field.description === 'string' ? { description: field.description } : {}),
      ...(typeof field.required === 'boolean' ? { required: field.required } : {}),
      ...(typeof field.read_only === 'boolean' ? { readOnly: field.read_only } : {}),
      ...(typeof field.local_cache === 'boolean' ? { localCache: field.local_cache } : {}),
      ...(options ? { options } : {}),
      ...(typeof field.min === 'number' ? { min: field.min } : {}),
      ...(typeof field.max === 'number' ? { max: field.max } : {}),
      ...(typeof field.max_length === 'number' ? { maxLength: field.max_length } : {}),
    }];
  });
  return { schemaVersionId: raw.schema_version_id, title: raw.title, fields };
}

export function adaptAnnotationTaskDetail(envelope: AnnotationTaskDetailEnvelopeWire): AnnotationTaskDetail {
  const task = adaptAnnotationTask(envelope.data.task);
  const draft = adaptAnnotationDraft(envelope.data.draft);
  if (
    task.id !== draft.taskId
    || task.scope.organizationId !== envelope.scope.organization_id
    || task.scope.projectId !== envelope.scope.project_id
    || task.scope.regionCode !== envelope.scope.region_code
    || task.currentDraftRevision !== draft.revision
    || task.currentDraftHash !== draft.contentHash
  ) {
    throw contractMismatch(envelope.request_id, 'Annotation bootstrap identity mismatch');
  }
  const rawStreams = envelope.data.viewer?.streams ?? [];
  const streams = rawStreams.flatMap((stream) => adaptStream(stream) ?? []);
  if (streams.length !== rawStreams.length || new Set(streams.map((stream) => stream.id)).size !== streams.length) {
    throw contractMismatch(envelope.request_id, 'Viewer stream contract mismatch');
  }
  if (task.source.streamIds.length && (task.source.streamIds.length !== streams.length || task.source.streamIds.some((streamId) => !streams.some((stream) => stream.id === streamId)))) {
    throw contractMismatch(envelope.request_id, 'Viewer streams are outside the fixed task context');
  }
  const formDefinition = adaptFormDefinition(envelope.data.viewer?.form_definition);
  if (formDefinition && formDefinition.schemaVersionId !== `${task.ontology.id}:${task.ontology.version}`) {
    throw contractMismatch(envelope.request_id, 'Annotation form schema does not match the fixed task ontology');
  }
  if (envelope.data.manual_issues.some((issue) => issue.range.base_revision_id !== task.source.baseRevisionId)) {
    throw contractMismatch(envelope.request_id, 'ManualIssue projection is outside the fixed task Revision');
  }
  const gateStatus = envelope.data.submission_gate.status;
  return {
    task,
    draft,
    manualIssues: envelope.data.manual_issues.map((issue) => ({
      id: issue.manual_issue_id,
      impact: issue.submission_impact === 'ADVISORY' || issue.submission_impact === 'BLOCKING' ? issue.submission_impact : 'UNKNOWN',
      summary: issue.summary,
      startNs: issue.range.start_ns,
      endNs: issue.range.end_ns,
    })),
    submissionGate: {
      status: gateStatus === 'PASS' || gateStatus === 'ADVISORY' || gateStatus === 'BLOCKED' || gateStatus === 'UNAVAILABLE' ? gateStatus : 'UNKNOWN',
      blockedReasons: envelope.data.submission_gate.blocked_reasons,
      advisoryIssueIds: envelope.data.submission_gate.advisory_issue_ids,
    },
    streams,
    formDefinition,
    staleInfo: envelope.data.stale_info ? {
      reason: envelope.data.stale_info.stale_reason,
      supersededRevisionId: envelope.data.stale_info.superseded_revision_id,
      replacementRevisionId: envelope.data.stale_info.replacement_revision_id,
      staleAt: envelope.data.stale_info.stale_at,
    } : null,
    requestId: envelope.request_id,
  };
}
