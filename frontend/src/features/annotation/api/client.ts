import type { AnnotationTask, AnnotationTaskDisplayState } from '../../../entities/annotation-task';
import type { AnnotationEntryWire } from '../../../entities/annotation-draft';
import { createDomainError } from '../../../shared/api/domain-error';
import type { AnnotationEntryResolution, AnnotationSchemaOption } from '../handoff/AnnotationEntryAction';
import { adaptAnnotationDraft, adaptAnnotationTask, adaptAnnotationTaskDetail } from './adapter';
import type { AnnotationTaskDetail } from './adapter';
import {
  annotationTaskDetailEnvelopeWireSchema,
  annotationEntryResolutionEnvelopeWireSchema,
  acceptedAnnotationJobEnvelopeWireSchema,
  annotationTaskEnvelopeWireSchema,
  annotationTaskListEnvelopeWireSchema,
  materializeAnnotationTaskEnvelopeWireSchema,
  rebaseEnvelopeWireSchema,
  reviewEnvelopeWireSchema,
  saveDraftEnvelopeWireSchema,
  submitPreflightEnvelopeWireSchema,
  submitTaskEnvelopeWireSchema,
} from './wire-schemas';
import { getAnnotationTransport } from './transport';

export interface AnnotationScope { readonly projectId: string; readonly regionCode: string }
export interface AnnotationEntryContext extends AnnotationScope {
  readonly revisionId: string;
  readonly startNs: string;
  readonly endNs: string;
  readonly streamIds: readonly string[];
  readonly ontologyId?: string;
  readonly ontologyVersion?: string;
}
export interface AnnotationListFilters {
  readonly queue: 'assigned_to_me' | 'claimable';
  readonly states?: readonly AnnotationTaskDisplayState[];
  readonly datasetId?: string;
  readonly schemaVersionId?: string;
  readonly assigneeId?: string;
  readonly q?: string;
  readonly sort?: string;
  readonly after?: string;
  readonly before?: string;
  readonly limit: number;
}

function root(scope: AnnotationScope): string {
  return `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/annotation-tasks`;
}

function contractMismatch(requestId: string | null, message: string): never {
  throw createDomainError({
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

function assertTaskContext(task: AnnotationTask, scope: AnnotationScope, taskId: string, requestId: string | null): void {
  if (task.id !== taskId || task.scope.projectId !== scope.projectId || task.scope.regionCode !== scope.regionCode) {
    contractMismatch(requestId, 'Annotation task response identity does not match the request');
  }
}

export async function resolveAnnotationTaskEntry(context: AnnotationEntryContext, signal?: AbortSignal): Promise<AnnotationEntryResolution> {
  const raw = await getAnnotationTransport().request({
    method: 'GET',
    path: `/projects/${encodeURIComponent(context.projectId)}/regions/${encodeURIComponent(context.regionCode)}/episode-revisions/${encodeURIComponent(context.revisionId)}/annotation-task-entry-resolution`,
    query: {
      start_ns: context.startNs,
      end_ns: context.endNs,
      stream_id: context.streamIds,
      ontology_id: context.ontologyId,
      ontology_version: context.ontologyVersion,
    },
    signal,
  });
  const envelope = annotationEntryResolutionEnvelopeWireSchema.parse(raw);
  const resolvedSource = envelope.data.resolved_context.source;
  const sameStreams = [...resolvedSource.stream_ids].sort().join('\u0000') === [...context.streamIds].sort().join('\u0000');
  if (
    envelope.scope.project_id !== context.projectId
    || envelope.scope.region_code !== context.regionCode
    || resolvedSource.base_revision_id !== context.revisionId
    || resolvedSource.start_ns !== context.startNs
    || resolvedSource.end_ns !== context.endNs
    || !sameStreams
    || (context.ontologyId !== undefined && envelope.data.resolved_context.ontology.ontology_id !== context.ontologyId)
    || (context.ontologyVersion !== undefined && envelope.data.resolved_context.ontology.ontology_version !== context.ontologyVersion)
  ) {
    contractMismatch(envelope.request_id, 'Annotation entry resolution identity mismatch');
  }
  const resolution = envelope.data;
  if (resolution.state === 'OPEN_EXISTING') return { kind: 'OPEN_EXISTING', taskId: resolution.task_id! };
  if (resolution.state === 'CLAIMABLE') return { kind: 'CLAIMABLE', taskId: resolution.task_id!, etag: resolution.task_etag! };
  if (resolution.state === 'ASSIGNED_TO_OTHER') return { kind: 'ASSIGNED_TO_OTHER', message: resolution.blocked_reason ?? '匹配任务已分配给其他标注员。' };
  if (resolution.state === 'FORBIDDEN') return { kind: 'FORBIDDEN', reasonCode: resolution.blocked_reason ?? 'ENTRY_FORBIDDEN' };
  const schemaOption: AnnotationSchemaOption = {
    schemaVersionId: `${resolution.resolved_context.ontology.ontology_id}:${resolution.resolved_context.ontology.ontology_version}`,
    label: `${resolution.resolved_context.ontology.ontology_id} @ ${resolution.resolved_context.ontology.ontology_version}`,
    resolutionId: resolution.resolution_id,
    resolutionToken: resolution.entry_resolution_token!,
  };
  return { kind: 'CAN_CREATE', schemaOptions: [schemaOption] };
}

export async function materializeAnnotationTaskEntry(
  scope: AnnotationScope,
  option: AnnotationSchemaOption,
  clientSessionId: string,
  idempotencyKey: string,
): Promise<{ taskId: string; disposition: 'CREATED' | 'OPEN_EXISTING' | 'CLAIMED_EXISTING' }> {
  const raw = await getAnnotationTransport().request({
    method: 'POST',
    path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/annotation-task-entry-resolutions/${encodeURIComponent(option.resolutionId)}:materialize`,
    headers: { 'Idempotency-Key': idempotencyKey },
    body: { entry_resolution_token: option.resolutionToken, client_session_id: clientSessionId },
  });
  const envelope = materializeAnnotationTaskEnvelopeWireSchema.parse(raw);
  if (envelope.scope.project_id !== scope.projectId || envelope.scope.region_code !== scope.regionCode || envelope.data.task.task_id !== envelope.data.draft.task_id) {
    contractMismatch(envelope.request_id, 'Materialized annotation task identity mismatch');
  }
  return { taskId: envelope.data.task.task_id, disposition: envelope.data.disposition };
}

function wireStatuses(states?: readonly AnnotationTaskDisplayState[]): readonly string[] | undefined {
  return states?.map((state) => state === 'UNASSIGNED' ? 'QUEUED' : state === 'COMPLETED' ? 'APPROVED' : state).filter((state) => state !== 'STALE' && state !== 'UNKNOWN');
}

export async function listAnnotationTasks(scope: AnnotationScope, filters: AnnotationListFilters, signal?: AbortSignal): Promise<{
  items: readonly AnnotationTask[];
  pageInfo: { hasNextPage: boolean; hasPreviousPage: boolean; startCursor: string | null; endCursor: string | null };
  snapshotAt: string;
  requestId: string;
}> {
  const raw = await getAnnotationTransport().request({
    method: 'GET', path: root(scope), signal,
    query: {
      view: filters.queue === 'claimable' ? 'available' : 'assigned_to_me',
      status: wireStatuses(filters.states),
      dataset_id: filters.datasetId,
      schema_version_id: filters.schemaVersionId,
      assignee_id: filters.assigneeId,
      q: filters.q,
      sort: filters.sort,
      after: filters.after,
      before: filters.before,
      limit: filters.limit,
    },
  });
  const envelope = annotationTaskListEnvelopeWireSchema.parse(raw);
  if (envelope.scope.project_id !== scope.projectId || envelope.scope.region_code !== scope.regionCode) {
    contractMismatch(envelope.request_id, 'Annotation task list scope mismatch');
  }
  const items = envelope.items.map(adaptAnnotationTask);
  if (items.some((task) => task.scope.projectId !== scope.projectId || task.scope.regionCode !== scope.regionCode)) {
    contractMismatch(envelope.request_id, 'Annotation task list contains an item from another scope');
  }
  return {
    items,
    pageInfo: {
      hasNextPage: envelope.page_info.has_next_page,
      hasPreviousPage: envelope.page_info.has_previous_page,
      startCursor: envelope.page_info.start_cursor,
      endCursor: envelope.page_info.end_cursor,
    },
    snapshotAt: envelope.snapshot_at,
    requestId: envelope.request_id,
  };
}

export async function getAnnotationTaskDetail(scope: AnnotationScope, taskId: string, signal?: AbortSignal): Promise<AnnotationTaskDetail> {
  const raw = await getAnnotationTransport().request({ method: 'GET', path: `${root(scope)}/${encodeURIComponent(taskId)}`, signal });
  const detail = adaptAnnotationTaskDetail(annotationTaskDetailEnvelopeWireSchema.parse(raw));
  assertTaskContext(detail.task, scope, taskId, detail.requestId);
  return detail;
}

interface CommandContext extends AnnotationScope { readonly taskId: string; readonly etag: string; readonly idempotencyKey: string }
function commandHeaders(context: CommandContext): Readonly<Record<string, string>> {
  return { 'Idempotency-Key': context.idempotencyKey, 'If-Match': context.etag };
}

export async function claimAnnotationTask(context: CommandContext, clientSessionId: string): Promise<AnnotationTask> {
  const raw = await getAnnotationTransport().request({ method: 'POST', path: `${root(context)}/${encodeURIComponent(context.taskId)}:claim`, headers: commandHeaders(context), body: { client_session_id: clientSessionId } });
  const envelope = annotationTaskEnvelopeWireSchema.parse(raw);
  const task = adaptAnnotationTask(envelope.data);
  assertTaskContext(task, context, context.taskId, envelope.request_id);
  return task;
}

export async function saveAnnotationDraft(context: CommandContext, input: { expectedRevision: number; clientMutationId: string; entries: readonly AnnotationEntryWire[] }) {
  const raw = await getAnnotationTransport().request({ method: 'PUT', path: `${root(context)}/${encodeURIComponent(context.taskId)}/draft`, headers: commandHeaders(context), body: { expected_draft_revision: input.expectedRevision, client_mutation_id: input.clientMutationId, entries: input.entries } });
  const envelope = saveDraftEnvelopeWireSchema.parse(raw);
  const task = adaptAnnotationTask(envelope.data.task);
  const draft = adaptAnnotationDraft(envelope.data.draft);
  assertTaskContext(task, context, context.taskId, envelope.request_id);
  if (draft.taskId !== context.taskId || draft.revision !== task.currentDraftRevision || draft.contentHash !== task.currentDraftHash) {
    contractMismatch(envelope.request_id, 'Saved Draft identity does not match the returned task pointer');
  }
  return { task, draft };
}

export async function preflightAnnotationSubmission(context: CommandContext, input: { expectedRevision: number; expectedContentHash: string }) {
  const raw = await getAnnotationTransport().request({ method: 'POST', path: `${root(context)}/${encodeURIComponent(context.taskId)}:submit-preflight`, headers: commandHeaders(context), body: { expected_draft_revision: input.expectedRevision, expected_content_hash: input.expectedContentHash } });
  const envelope = submitPreflightEnvelopeWireSchema.parse(raw);
  if (envelope.scope.project_id !== context.projectId || envelope.scope.region_code !== context.regionCode || envelope.data.task_id !== context.taskId || envelope.data.draft_revision !== input.expectedRevision || envelope.data.content_hash !== input.expectedContentHash) {
    contractMismatch(envelope.request_id, 'Submission preflight identity mismatch');
  }
  return envelope.data;
}

export async function submitAnnotationTask(context: CommandContext, input: { expectedRevision: number; expectedContentHash: string; preflightId: string | null }) {
  const raw = await getAnnotationTransport().request({ method: 'POST', path: `${root(context)}/${encodeURIComponent(context.taskId)}:submit`, headers: commandHeaders(context), body: { expected_draft_revision: input.expectedRevision, expected_content_hash: input.expectedContentHash, preflight_id: input.preflightId } });
  const completed = submitTaskEnvelopeWireSchema.safeParse(raw);
  if (completed.success) {
    const task = adaptAnnotationTask(completed.data.data.task);
    assertTaskContext(task, context, context.taskId, completed.data.request_id);
    return { kind: 'completed' as const, task };
  }
  const accepted = acceptedAnnotationJobEnvelopeWireSchema.parse(raw);
  return { kind: 'accepted-job' as const, jobId: accepted.job.job_id, status: accepted.job.status, requestId: accepted.request_id };
}

export async function reviewAnnotationTask(context: CommandContext, input: { decision: 'APPROVED' | 'RETURNED'; reasonCodes: readonly string[]; comment: string | null }) {
  const raw = await getAnnotationTransport().request({ method: 'POST', path: `${root(context)}/${encodeURIComponent(context.taskId)}:review`, headers: commandHeaders(context), body: { decision: input.decision, reason_codes: input.reasonCodes, comment: input.comment } });
  const envelope = reviewEnvelopeWireSchema.parse(raw);
  const task = adaptAnnotationTask(envelope.data.task);
  assertTaskContext(task, context, context.taskId, envelope.request_id);
  return task;
}

export async function rebaseAnnotationTask(context: CommandContext, input: { targetRevisionId: string; reason: string; successorAssigneeId: string | null }) {
  const raw = await getAnnotationTransport().request({ method: 'POST', path: `${root(context)}/${encodeURIComponent(context.taskId)}:rebase`, headers: commandHeaders(context), body: { target_revision_id: input.targetRevisionId, reason: input.reason, successor_assignee_id: input.successorAssigneeId } });
  const envelope = rebaseEnvelopeWireSchema.parse(raw);
  const data = envelope.data;
  const successorTask = adaptAnnotationTask(data.successor_task);
  const successorDraft = adaptAnnotationDraft(data.successor_draft);
  if (
    data.stale_task_id !== context.taskId
    || successorTask.scope.projectId !== context.projectId
    || successorTask.scope.regionCode !== context.regionCode
    || successorTask.source.baseRevisionId !== input.targetRevisionId
    || successorDraft.taskId !== successorTask.id
    || successorDraft.revision !== 0
    || successorDraft.entries.length !== 0
  ) {
    contractMismatch(envelope.request_id, 'Rebase response does not prove an empty successor task on the target Revision');
  }
  return { staleTaskId: data.stale_task_id, successorTask, successorDraft };
}
