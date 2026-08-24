import type { Scope } from "../../entities/scope";
import { z } from "zod";
import {
  createDomainError,
  isDomainError,
} from "../../shared/api/domain-error";
import type { DomainError } from "../../shared/api/domain-error";
import type { components } from "../../shared/api/generated/platform";
import { request } from "../../shared/api/http-client";
import { getRuntimeConfig } from "../../shared/config/runtime";
import { parseWire } from "../../shared/api/validate";
import type {
  DataVisualizationWorkbenchAdapter,
  StreamDescriptor,
  ViewerMediaSource,
  ViewerTimelineSelection,
  ViewerTimelineTrack,
  WorkbenchCollectionItem,
} from "../../features/viewer";
import type { PlaybackClock } from "../../features/viewer";

export type RuntimeAnnotationTask = components["schemas"]["AnnotationTask"];
export type RuntimeAnnotationDraft = components["schemas"]["AnnotationDraft"];
export type RuntimeAnnotationHistory =
  components["schemas"]["AnnotationHistory"];
export type RuntimeAnnotationRevision =
  components["schemas"]["AnnotationRevision"];
export type RuntimeAnnotationRevisionThread =
  components["schemas"]["AnnotationRevisionThread"];
export type RuntimeAnnotationRevisionThreadPage =
  components["schemas"]["AnnotationRevisionThreadPage"];
export type RuntimeAnnotationSubmission =
  components["schemas"]["AnnotationSubmission"];
export type RuntimeAnnotationTag = components["schemas"]["AnnotationTag"];
export type RuntimeAutoAnnotationCapability =
  components["schemas"]["AutoAnnotationCapability"];
export type RuntimeAutoAnnotationJob =
  components["schemas"]["AutoAnnotationJob"];
export type RuntimeTagSchemaVersion = components["schemas"]["TagSchemaVersion"];
export type RuntimeManifestDiscovery =
  components["schemas"]["ManifestDiscoveryV1"];
export type RuntimePreviewDescriptor =
  components["schemas"]["PreviewDescriptorV1"];
export type RuntimeReviewDecision = components["schemas"]["ReviewDecision"];
export type RuntimeReviewCheckKind = components["schemas"]["ReviewCheckKind"];

export type AnnotationWorkbenchMode = "annotation" | "tag-review";

const annotationStatusWireSchema = z.enum([
  "DRAFT",
  "SUBMITTED",
  "APPROVED",
  "NEEDS_REVISION",
  "REJECTED",
]);
const revisionOriginWireSchema = z.enum([
  "ANNOTATION",
  "ANNOTATION_RESTORE",
  "LEGACY_CLEANING",
]);
const annotationRevisionThreadWireSchema = z
  .object({
    task_id: z.string().min(1),
    project_id: z.string().min(1),
    region_code: z.string().min(1),
    dataset_id: z.string().min(1),
    dataset_version: z.number().int().positive(),
    rollout_id: z.string().min(1),
    status: annotationStatusWireSchema,
    latest_revision: z
      .object({
        revision: z.number().int().nonnegative(),
        origin: revisionOriginWireSchema,
        author_id: z.string().min(1),
        content_hash: z.string().regex(/^[0-9a-f]{64}$/u),
        created_at: z.iso.datetime({ offset: true }),
      })
      .strict(),
    submitted_revision: z.number().int().nonnegative().nullable().optional(),
    current_submission_id: z.string().min(1).nullable().optional(),
    approved_revision: z.number().int().nonnegative().nullable().optional(),
    approved_review_id: z.string().min(1).nullable().optional(),
    legacy_draft_id: z.string().min(1).nullable().optional(),
    updated_at: z.iso.datetime({ offset: true }),
  })
  .strict();
const annotationRevisionThreadPageWireSchema = z
  .object({
    items: z.array(annotationRevisionThreadWireSchema),
    page_info: z
      .object({
        has_next_page: z.boolean(),
        has_previous_page: z.boolean(),
        start_cursor: z.string().min(1).nullable().optional(),
        end_cursor: z.string().min(1).nullable().optional(),
      })
      .strict(),
    snapshot_at: z.iso.datetime({ offset: true }),
  })
  .strict();

export interface RuntimeAnnotationScope {
  readonly projectId: string;
  readonly regionCode: string;
  readonly organizationId: string;
}

export function runtimeAnnotationScopeFromShell(
  scope: Scope | null,
): RuntimeAnnotationScope | null {
  if (!scope?.projectId || !scope.regionCode) return null;
  return {
    organizationId: scope.organizationId,
    projectId: scope.projectId,
    regionCode: scope.regionCode,
  };
}

export interface RuntimeAnnotationBundle {
  readonly task: RuntimeAnnotationTask;
  readonly draft: RuntimeAnnotationDraft | null;
  readonly history: RuntimeAnnotationHistory;
  readonly schema: RuntimeTagSchemaVersion;
  readonly manifest: RuntimeManifestDiscovery | null;
  readonly manifestIssue: DomainError | null;
  readonly tasks: readonly RuntimeAnnotationTask[];
}

export interface RuntimeAnnotationCommands {
  readonly saveDraft: (
    task: RuntimeAnnotationTask,
    draft: RuntimeAnnotationDraft,
    tags: readonly RuntimeAnnotationTag[],
  ) => Promise<RuntimeAnnotationRevision>;
  readonly submit: (
    task: RuntimeAnnotationTask,
    revision: number,
  ) => Promise<RuntimeAnnotationSubmission>;
  readonly review: (
    task: RuntimeAnnotationTask,
    submission: RuntimeAnnotationSubmission,
    decision: RuntimeReviewDecision,
    comment: string,
  ) => Promise<RuntimeAnnotationTask>;
  readonly claim: (
    task: RuntimeAnnotationTask,
  ) => Promise<RuntimeAnnotationTask>;
  readonly restoreRevision: (
    task: RuntimeAnnotationTask,
    targetRevision: number,
  ) => Promise<RuntimeAnnotationRevision>;
}

const STEP_RATE_HZ = 30n;
const NS_PER_SECOND = 1_000_000_000n;

function encoded(value: string | number): string {
  return encodeURIComponent(String(value));
}

function scopeForRequest(scope: RuntimeAnnotationScope): Scope {
  return {
    organizationId: scope.organizationId,
    projectId: scope.projectId,
    regionCode: scope.regionCode,
  };
}

function asDomainError(error: unknown, message: string): DomainError & Error {
  if (isDomainError(error)) return error;
  return createDomainError({
    code: "SERVER_ERROR",
    message,
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [],
    requestId: null,
    retryable: true,
    httpStatus: null,
  });
}

function contractMismatch(message: string): never {
  throw createDomainError({
    code: "CONTRACT_MISMATCH",
    message,
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [],
    requestId: null,
    retryable: false,
    httpStatus: null,
  });
}

function assertTaskIdentity(
  task: RuntimeAnnotationTask,
  scope: RuntimeAnnotationScope,
  expectedTaskId?: string,
): void {
  if (
    task.project_id !== scope.projectId ||
    (expectedTaskId !== undefined && task.task_id !== expectedTaskId) ||
    (task.region_code !== null &&
      task.region_code !== undefined &&
      task.region_code !== scope.regionCode)
  ) {
    contractMismatch("标注任务身份与当前项目、Region 或任务请求不一致。");
  }
}

function assertAutoAnnotationCapability(
  capability: RuntimeAutoAnnotationCapability,
): void {
  const providerNames = capability.providers.map((item) => item.provider);
  if (
    capability.enabled !== capability.providers.length > 0 ||
    new Set(providerNames).size !== providerNames.length ||
    capability.providers.some(
      (provider) =>
        provider.models.length === 0 ||
        new Set(provider.models).size !== provider.models.length,
    )
  ) {
    contractMismatch("自动标注能力返回了不一致的 Provider 配置。");
  }
}

function assertAutoAnnotationJobIdentity(
  job: RuntimeAutoAnnotationJob,
  scope: RuntimeAnnotationScope,
  taskId: string,
): void {
  const successful = job.status === "SUCCEEDED" || job.status === "APPLIED";
  if (
    job.project_id !== scope.projectId ||
    job.region_code !== scope.regionCode ||
    job.task_id !== taskId ||
    successful !==
      (job.tags !== null &&
        job.tags !== undefined &&
        job.operations !== null &&
        job.operations !== undefined &&
        job.usage !== null &&
        job.usage !== undefined) ||
    (job.status === "APPLIED") !==
      (job.applied_revision !== null && job.applied_revision !== undefined)
  ) {
    contractMismatch("自动标注任务与当前作用域、任务或状态不一致。");
  }
}

export function createClientMutationId(prefix: string): string {
  const suffix =
    globalThis.crypto?.randomUUID?.() ??
    `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${suffix}`;
}

export function stepToTimelineNs(step: number): string {
  const normalized = Number.isFinite(step) ? Math.max(0, Math.trunc(step)) : 0;
  return ((BigInt(normalized) * NS_PER_SECOND) / STEP_RATE_HZ).toString();
}

export function timelineNsToStep(value: string): number {
  return Number((BigInt(value) * STEP_RATE_HZ) / NS_PER_SECOND);
}

export function annotationTaskStatusLabel(
  status: RuntimeAnnotationTask["status"],
): string {
  const labels: Readonly<Record<RuntimeAnnotationTask["status"], string>> = {
    DRAFT: "草稿",
    SUBMITTED: "待审核",
    APPROVED: "已通过",
    NEEDS_REVISION: "需修改",
    REJECTED: "已拒绝",
  };
  return labels[status];
}

export async function listRuntimeAnnotationTasks(
  scope: RuntimeAnnotationScope,
  signal?: AbortSignal,
): Promise<readonly RuntimeAnnotationTask[]> {
  const tasks = await request<components["schemas"]["AnnotationTask"][]>({
    method: "GET",
    path: `/projects/${encoded(scope.projectId)}/annotation-tasks`,
    scope: scopeForRequest(scope),
    ...(signal ? { signal } : {}),
  });
  tasks.forEach((task) => assertTaskIdentity(task, scope));
  return tasks;
}

export async function listRuntimeAnnotationRevisionThreads(
  scope: RuntimeAnnotationScope,
  input: {
    readonly status?: RuntimeAnnotationRevisionThread["status"];
    readonly origin?: RuntimeAnnotationRevisionThread["latest_revision"]["origin"];
    readonly legacyDraftId?: string;
    readonly after?: string;
    readonly limit?: number;
  } = {},
  signal?: AbortSignal,
): Promise<RuntimeAnnotationRevisionThreadPage> {
  const raw = await request<unknown>({
    method: "GET",
    path: "/annotations/revisions",
    scope: scopeForRequest(scope),
    query: {
      status: input.status,
      origin: input.origin,
      legacy_draft_id: input.legacyDraftId,
      after: input.after,
      limit: input.limit ?? 25,
    },
    ...(signal ? { signal } : {}),
  });
  const page = parseWire(annotationRevisionThreadPageWireSchema, raw, {
    endpoint: "listAnnotationRevisionThreads",
  });
  for (const thread of page.items) {
    if (
      thread.project_id !== scope.projectId ||
      thread.region_code !== scope.regionCode
    ) {
      contractMismatch("修订索引包含当前项目或 Region 以外的任务。");
    }
  }
  return page;
}

export async function claimRuntimeAnnotationTask(
  scope: RuntimeAnnotationScope,
  task: RuntimeAnnotationTask,
): Promise<RuntimeAnnotationTask> {
  const result = await request<components["schemas"]["AnnotationTask"]>({
    method: "POST",
    path: `/annotation-tasks/${encoded(task.task_id)}/claim`,
    scope: scopeForRequest(scope),
  });
  assertTaskIdentity(result, scope, task.task_id);
  return result;
}

async function getRuntimeAnnotationTask(
  scope: RuntimeAnnotationScope,
  taskId: string,
  signal?: AbortSignal,
): Promise<RuntimeAnnotationTask> {
  const task = await request<components["schemas"]["AnnotationTask"]>({
    method: "GET",
    path: `/annotation-tasks/${encoded(taskId)}`,
    scope: scopeForRequest(scope),
    ...(signal ? { signal } : {}),
  });
  assertTaskIdentity(task, scope, taskId);
  return task;
}

async function getRuntimeAnnotationDraft(
  scope: RuntimeAnnotationScope,
  taskId: string,
  signal?: AbortSignal,
): Promise<RuntimeAnnotationDraft> {
  return request<components["schemas"]["AnnotationDraft"]>({
    method: "GET",
    path: `/annotation-tasks/${encoded(taskId)}/draft`,
    scope: scopeForRequest(scope),
    ...(signal ? { signal } : {}),
  });
}

async function getRuntimeAnnotationHistory(
  scope: RuntimeAnnotationScope,
  taskId: string,
  signal?: AbortSignal,
): Promise<RuntimeAnnotationHistory> {
  return request<components["schemas"]["AnnotationHistory"]>({
    method: "GET",
    path: `/annotation-tasks/${encoded(taskId)}/history`,
    scope: scopeForRequest(scope),
    ...(signal ? { signal } : {}),
  });
}

async function getRuntimeTagSchema(
  scope: RuntimeAnnotationScope,
  task: RuntimeAnnotationTask,
  signal?: AbortSignal,
): Promise<RuntimeTagSchemaVersion> {
  return request<components["schemas"]["TagSchemaVersion"]>({
    method: "GET",
    path: `/projects/${encoded(scope.projectId)}/tag-schemas/${encoded(task.tag_schema_id)}/versions/${encoded(task.tag_schema_version)}`,
    scope: scopeForRequest(scope),
    ...(signal ? { signal } : {}),
  });
}

async function loadManifestForTask(
  scope: RuntimeAnnotationScope,
  task: RuntimeAnnotationTask,
  signal?: AbortSignal,
): Promise<RuntimeManifestDiscovery> {
  return request<components["schemas"]["ManifestDiscoveryV1"]>({
    method: "GET",
    path: `/projects/${encoded(scope.projectId)}/regions/${encoded(scope.regionCode)}/annotation-tasks/${encoded(task.task_id)}/manifest-discovery`,
    scope: scopeForRequest(scope),
    ...(signal ? { signal } : {}),
  });
}

export async function loadRuntimeAnnotationBundle(
  scope: RuntimeAnnotationScope,
  taskId: string,
  mode: AnnotationWorkbenchMode,
  signal?: AbortSignal,
): Promise<RuntimeAnnotationBundle> {
  const [task, tasks] = await Promise.all([
    getRuntimeAnnotationTask(scope, taskId, signal),
    listRuntimeAnnotationTasks(scope, signal),
  ]);
  const manifestPromise = loadManifestForTask(scope, task, signal)
    .then((manifest) => ({
      manifest,
      manifestIssue: null as DomainError | null,
    }))
    .catch((error: unknown) => ({
      manifest: null,
      manifestIssue: asDomainError(error, "Manifest 加载失败。"),
    }));
  const [draft, history, schema, manifestResult] = await Promise.all([
    mode === "annotation"
      ? getRuntimeAnnotationDraft(scope, taskId, signal)
      : Promise.resolve(null),
    getRuntimeAnnotationHistory(scope, taskId, signal),
    getRuntimeTagSchema(scope, task, signal),
    manifestPromise,
  ]);
  if (
    history.task.task_id !== task.task_id ||
    schema.project_id !== task.project_id ||
    schema.schema_id !== task.tag_schema_id ||
    schema.version !== task.tag_schema_version ||
    (draft !== null &&
      (draft.task_id !== task.task_id ||
        draft.tag_schema_id !== task.tag_schema_id ||
        draft.tag_schema_version !== task.tag_schema_version))
  ) {
    contractMismatch("标注任务、草稿、历史或 Tag Schema 的固定身份不一致。");
  }
  return {
    task,
    draft,
    history,
    schema,
    manifest: manifestResult.manifest,
    manifestIssue: manifestResult.manifestIssue,
    tasks,
  };
}

export async function saveRuntimeAnnotationDraft(
  scope: RuntimeAnnotationScope,
  task: RuntimeAnnotationTask,
  draft: RuntimeAnnotationDraft,
  tags: readonly RuntimeAnnotationTag[],
): Promise<RuntimeAnnotationRevision> {
  const body = {
    client_mutation_id: createClientMutationId("save"),
    expected_revision: task.current_revision,
    operations: draft.operations,
    tags: [...tags],
  } satisfies components["schemas"]["SaveDraftRequest"];
  const revision = await request<components["schemas"]["AnnotationRevision"]>({
    method: "POST",
    path: `/annotation-tasks/${encoded(task.task_id)}/revisions`,
    scope: scopeForRequest(scope),
    ifMatch: task.etag,
    body,
  });
  if (
    revision.task_id !== task.task_id ||
    revision.tag_schema_id !== task.tag_schema_id ||
    revision.tag_schema_version !== task.tag_schema_version
  ) {
    contractMismatch("保存后的修订与当前任务或 Tag Schema 不一致。");
  }
  return revision;
}

export async function restoreRuntimeAnnotationRevision(
  scope: RuntimeAnnotationScope,
  task: RuntimeAnnotationTask,
  targetRevision: number,
): Promise<RuntimeAnnotationRevision> {
  if (!Number.isSafeInteger(targetRevision) || targetRevision < 0) {
    contractMismatch("回退目标修订必须是非负整数。");
  }
  if (targetRevision >= task.current_revision) {
    contractMismatch("回退目标必须早于当前标注修订。");
  }
  const body = {
    client_mutation_id: createClientMutationId("restore"),
    expected_revision: task.current_revision,
    target_revision: targetRevision,
  } satisfies components["schemas"]["RestoreRevisionRequest"];
  const revision = await request<components["schemas"]["AnnotationRevision"]>({
    method: "POST",
    path: `/annotation-tasks/${encoded(task.task_id)}/revisions:restore`,
    scope: scopeForRequest(scope),
    ifMatch: task.etag,
    body,
  });
  if (
    revision.task_id !== task.task_id ||
    revision.tag_schema_id !== task.tag_schema_id ||
    revision.tag_schema_version !== task.tag_schema_version ||
    revision.parent_revision !== task.current_revision ||
    revision.origin !== "ANNOTATION_RESTORE"
  ) {
    contractMismatch("回退后的修订与任务、父修订或 Tag Schema 不一致。");
  }
  return revision;
}

export async function loadRuntimeAutoAnnotationCapability(
  scope: RuntimeAnnotationScope,
  signal?: AbortSignal,
): Promise<RuntimeAutoAnnotationCapability> {
  const capability = await request<RuntimeAutoAnnotationCapability>({
    method: "GET",
    path: "/capabilities/auto-annotation",
    scope: scopeForRequest(scope),
    ...(signal ? { signal } : {}),
  });
  assertAutoAnnotationCapability(capability);
  return capability;
}

export async function createRuntimeAutoAnnotationJob(
  scope: RuntimeAnnotationScope,
  task: RuntimeAnnotationTask,
  input: {
    readonly provider: string;
    readonly model: string;
    readonly startStep: number;
    readonly endStep: number;
  },
): Promise<RuntimeAutoAnnotationJob> {
  const body = {
    revision: task.current_revision,
    provider: input.provider,
    model: input.model,
    input_selection: {
      start_step: input.startStep,
      end_step: input.endStep,
      modalities: [],
    },
  } satisfies components["schemas"]["AutoAnnotationRequest"];
  const job = await request<RuntimeAutoAnnotationJob>({
    method: "POST",
    path: `/annotation-tasks/${encoded(task.task_id)}/auto-annotation`,
    scope: scopeForRequest(scope),
    idempotencyKey: createClientMutationId("auto-annotation"),
    body,
  });
  assertAutoAnnotationJobIdentity(job, scope, task.task_id);
  if (job.source_revision !== task.current_revision) {
    contractMismatch("自动标注任务没有固定当前不可变修订。");
  }
  return job;
}

export async function getRuntimeAutoAnnotationJob(
  scope: RuntimeAnnotationScope,
  taskId: string,
  jobId: string,
  signal?: AbortSignal,
): Promise<RuntimeAutoAnnotationJob> {
  const job = await request<RuntimeAutoAnnotationJob>({
    method: "GET",
    path: `/annotation-tasks/${encoded(taskId)}/auto-annotation-jobs/${encoded(jobId)}`,
    scope: scopeForRequest(scope),
    ...(signal ? { signal } : {}),
  });
  assertAutoAnnotationJobIdentity(job, scope, taskId);
  return job;
}

async function mutateRuntimeAutoAnnotationJob(
  scope: RuntimeAnnotationScope,
  taskId: string,
  jobId: string,
  action: "cancel" | "retry",
): Promise<RuntimeAutoAnnotationJob> {
  const job = await request<RuntimeAutoAnnotationJob>({
    method: "POST",
    path: `/annotation-tasks/${encoded(taskId)}/auto-annotation-jobs/${encoded(jobId)}:${action}`,
    scope: scopeForRequest(scope),
  });
  assertAutoAnnotationJobIdentity(job, scope, taskId);
  return job;
}

export function cancelRuntimeAutoAnnotationJob(
  scope: RuntimeAnnotationScope,
  taskId: string,
  jobId: string,
): Promise<RuntimeAutoAnnotationJob> {
  return mutateRuntimeAutoAnnotationJob(scope, taskId, jobId, "cancel");
}

export function retryRuntimeAutoAnnotationJob(
  scope: RuntimeAnnotationScope,
  taskId: string,
  jobId: string,
): Promise<RuntimeAutoAnnotationJob> {
  return mutateRuntimeAutoAnnotationJob(scope, taskId, jobId, "retry");
}

export async function applyRuntimeAutoAnnotationJob(
  scope: RuntimeAnnotationScope,
  task: RuntimeAnnotationTask,
  job: RuntimeAutoAnnotationJob,
): Promise<RuntimeAnnotationRevision> {
  const body = {
    expected_revision: job.source_revision,
  } satisfies components["schemas"]["ApplyAutoAnnotationRequest"];
  const revision = await request<RuntimeAnnotationRevision>({
    method: "POST",
    path: `/annotation-tasks/${encoded(task.task_id)}/auto-annotation-jobs/${encoded(job.job_id)}:apply`,
    scope: scopeForRequest(scope),
    ifMatch: task.etag,
    body,
  });
  if (
    revision.task_id !== task.task_id ||
    revision.parent_revision !== task.current_revision ||
    revision.revision !== task.current_revision + 1
  ) {
    contractMismatch("应用自动标注结果后没有追加预期的不可变修订。");
  }
  return revision;
}

export async function submitRuntimeAnnotationRevision(
  scope: RuntimeAnnotationScope,
  task: RuntimeAnnotationTask,
  revision: number,
): Promise<RuntimeAnnotationSubmission> {
  const body = {
    expected_revision: revision,
  } satisfies components["schemas"]["SubmitRequest"];
  const submission = await request<
    components["schemas"]["AnnotationSubmission"]
  >({
    method: "POST",
    path: `/annotation-tasks/${encoded(task.task_id)}/submit`,
    scope: scopeForRequest(scope),
    ifMatch: task.etag,
    idempotencyKey: createClientMutationId("submit"),
    body,
  });
  if (
    submission.task_id !== task.task_id ||
    submission.revision !== revision ||
    submission.tag_schema_id !== task.tag_schema_id ||
    submission.tag_schema_version !== task.tag_schema_version
  ) {
    contractMismatch("提交快照与当前任务、修订或 Tag Schema 不一致。");
  }
  return submission;
}

export async function reviewRuntimeAnnotationRevision(
  scope: RuntimeAnnotationScope,
  task: RuntimeAnnotationTask,
  submission: RuntimeAnnotationSubmission,
  decision: RuntimeReviewDecision,
  comment: string,
): Promise<RuntimeAnnotationTask> {
  const body = {
    revision: submission.revision,
    submission_id: submission.submission_id,
    decision,
    comment,
  } satisfies components["schemas"]["ReviewRequest"];
  const reviewedTask = await request<components["schemas"]["AnnotationTask"]>({
    method: "POST",
    path: `/annotation-tasks/${encoded(task.task_id)}/reviews`,
    scope: scopeForRequest(scope),
    ifMatch: task.etag,
    body,
  });
  assertTaskIdentity(reviewedTask, scope, task.task_id);
  return reviewedTask;
}

export function createRuntimeAnnotationCommands(
  scope: RuntimeAnnotationScope,
): RuntimeAnnotationCommands {
  return {
    saveDraft: (task, draft, tags) =>
      saveRuntimeAnnotationDraft(scope, task, draft, tags),
    submit: (task, revision) =>
      submitRuntimeAnnotationRevision(scope, task, revision),
    review: (task, submission, decision, comment) =>
      reviewRuntimeAnnotationRevision(
        scope,
        task,
        submission,
        decision,
        comment,
      ),
    claim: (task) => claimRuntimeAnnotationTask(scope, task),
    restoreRevision: (task, targetRevision) =>
      restoreRuntimeAnnotationRevision(scope, task, targetRevision),
  };
}

function createPreviewMediaSource(
  scope: RuntimeAnnotationScope,
  task: RuntimeAnnotationTask,
  cameraId: string,
  annotationRevision: number,
  viewMode: components["schemas"]["ViewMode"],
): ViewerMediaSource {
  const authorize = async (signal: AbortSignal) => {
    const body = {
      annotation_revision: annotationRevision,
      camera_id: cameraId,
      dataset_id: task.dataset_id,
      frequency_hz: Number(STEP_RATE_HZ),
      lance_version: String(task.base_lance_version),
      project_id: task.project_id,
      rollout_id: task.rollout_id,
      view_mode: viewMode,
      ...(task.base_step_count === null || task.base_step_count === undefined
        ? {}
        : { start_step: 0, end_step: task.base_step_count }),
    } satisfies components["schemas"]["PreviewRequestV1"];
    const descriptor = await request<RuntimePreviewDescriptor>({
      method: "POST",
      path: "/previews/sessions",
      scope: scopeForRequest(scope),
      body,
      signal,
    });
    if (
      descriptor.project_id !== task.project_id ||
      descriptor.rollout_id !== task.rollout_id ||
      descriptor.camera_id !== cameraId ||
      descriptor.annotation_revision !== annotationRevision
    ) {
      contractMismatch("预览授权与当前任务、相机或修订不一致。");
    }
    return {
      url: resolvePreviewMediaUrl(descriptor.playlist_url),
      expiresAt: descriptor.signed_url_expires_at,
      kind: "rgb-video" as const,
    };
  };
  return { authorize, refresh: authorize };
}

function resolvePreviewMediaUrl(url: string): string {
  if (/^https?:\/\//u.test(url)) return url;
  const origin = globalThis.location?.origin ?? "http://localhost";
  const apiOrigin = new URL(getRuntimeConfig().apiBaseUrl, origin).origin;
  return new URL(url, apiOrigin).toString();
}

export function resolveReviewSubmission(
  bundle: RuntimeAnnotationBundle,
): RuntimeAnnotationSubmission | null {
  const currentId = bundle.task.current_submission_id;
  if (currentId) {
    const current = bundle.history.submissions.find(
      (item) => item.submission_id === currentId,
    );
    if (current) return current;
  }
  return (
    [...bundle.history.submissions].sort(
      (left, right) => right.revision - left.revision,
    )[0] ?? null
  );
}

export function resolveReviewRevision(
  bundle: RuntimeAnnotationBundle,
): RuntimeAnnotationRevision | null {
  const submission = resolveReviewSubmission(bundle);
  if (!submission) return null;
  return (
    bundle.history.revisions.find(
      (item) => item.revision === submission.revision,
    ) ?? null
  );
}

export function resolveOriginalRevision(
  bundle: RuntimeAnnotationBundle,
): RuntimeAnnotationRevision | null {
  const revised = resolveReviewRevision(bundle);
  if (!revised) return null;
  if (revised.parent_revision !== null) {
    const parent = bundle.history.revisions.find(
      (item) => item.revision === revised.parent_revision,
    );
    if (parent) return parent;
  }
  return (
    [...bundle.history.revisions]
      .filter((item) => item.revision < revised.revision)
      .sort((left, right) => right.revision - left.revision)[0] ?? null
  );
}

function streamForCamera(
  scope: RuntimeAnnotationScope,
  task: RuntimeAnnotationTask,
  camera: RuntimeManifestDiscovery["cameras"][number],
  revision: number,
  mode: AnnotationWorkbenchMode,
): StreamDescriptor {
  const endStep = Math.max(1, task.base_step_count ?? 1);
  const modality = (camera.encoding ?? "").toLowerCase().includes("depth")
    ? "depth"
    : "rgb";
  return {
    id: camera.camera_id,
    canonicalPath: camera.topic,
    displayName: camera.camera_id,
    modality,
    schema: {
      id: "manifest-camera",
      version: String(task.base_lance_version),
      ...(camera.encoding ? { encoding: camera.encoding } : {}),
    },
    rateHz: Number(STEP_RATE_HZ),
    startNs: "0",
    endNs: stepToTimelineNs(endStep),
    ...(camera.frame_id
      ? { frame: { id: camera.frame_id, name: camera.frame_id } }
      : {}),
    availability: "ready",
    accessibleSummary: `${camera.camera_id}，来自 Manifest 的只读相机流，与其他模态共享 30 Hz 时间光标。`,
    mediaSource: createPreviewMediaSource(
      scope,
      task,
      camera.camera_id,
      revision,
      mode === "tag-review" ? "compare" : "edited",
    ),
  };
}

function tagTracks(
  mode: AnnotationWorkbenchMode,
  bundle: RuntimeAnnotationBundle,
  tags: readonly RuntimeAnnotationTag[],
): readonly ViewerTimelineTrack[] {
  const intervalTrack = (
    id: string,
    label: string,
    values: readonly RuntimeAnnotationTag[],
    tone: "phase" | "action",
  ) =>
    ({
      id,
      label,
      segments: values.map((tag) => ({
        id: `${id}-${tag.annotation_id}`,
        label: tag.path.join(" / "),
        startNs: stepToTimelineNs(tag.start_step),
        endNs: stepToTimelineNs(tag.end_step),
        tone,
      })),
    }) satisfies ViewerTimelineTrack;
  const coverageEnd = stepToTimelineNs(
    Math.max(1, bundle.task.base_step_count ?? 1),
  );
  const coverage = {
    id: "aligned-30hz",
    label: "30 Hz 对齐",
    segments: [
      {
        id: "aligned-coverage",
        label:
          bundle.task.base_step_count === null ||
          bundle.task.base_step_count === undefined
            ? "边界事实未返回"
            : `${bundle.task.base_step_count.toLocaleString("zh-CN")} 步`,
        startNs: "0",
        endNs: coverageEnd,
        tone: "signal" as const,
      },
    ],
  } satisfies ViewerTimelineTrack;
  const operations = {
    id: "revision-operations",
    label: "数据修订",
    segments: (
      bundle.draft?.operations ??
      resolveReviewRevision(bundle)?.operations ??
      []
    ).map((operation) => ({
      id: operation.operation_id,
      label: operation.kind === "EXCLUDE" ? "排除" : "恢复",
      startNs: stepToTimelineNs(operation.start_step),
      endNs: stepToTimelineNs(operation.end_step),
      tone:
        operation.kind === "EXCLUDE"
          ? ("issue" as const)
          : ("quality-pass" as const),
    })),
  } satisfies ViewerTimelineTrack;
  if (mode === "annotation")
    return [
      intervalTrack("tag-intervals", "Tag 区间", tags, "action"),
      coverage,
      operations,
    ];
  const original = resolveOriginalRevision(bundle)?.tags ?? [];
  return [
    intervalTrack("original-tags", "原始标注", original, "phase"),
    intervalTrack("revised-tags", "修订标注", tags, "action"),
    operations,
  ];
}

function collectionItems(
  bundle: RuntimeAnnotationBundle,
): readonly WorkbenchCollectionItem[] {
  return bundle.tasks.map((task) => ({
    id: task.task_id,
    label: task.rollout_id,
    description: `${task.dataset_id} · Lance v${task.base_lance_version}`,
    status: annotationTaskStatusLabel(task.status),
    statusTone:
      task.status === "APPROVED"
        ? "success"
        : task.status === "REJECTED"
          ? "error"
          : task.status === "SUBMITTED" || task.status === "NEEDS_REVISION"
            ? "warning"
            : "info",
    facts:
      task.task_id === bundle.task.task_id
        ? [
            { label: "任务 ID", value: task.task_id, technical: true },
            {
              label: "Schema",
              value: `${task.tag_schema_id} v${task.tag_schema_version}`,
            },
            { label: "Lance", value: `v${task.base_lance_version}` },
            {
              label: "区间",
              value:
                task.base_step_count === null ||
                task.base_step_count === undefined
                  ? "未返回"
                  : `${task.base_step_count.toLocaleString("zh-CN")} 步`,
            },
          ]
        : undefined,
  }));
}

export function buildRuntimeWorkbenchAdapter(input: {
  readonly bundle: RuntimeAnnotationBundle;
  readonly scope: RuntimeAnnotationScope;
  readonly mode: AnnotationWorkbenchMode;
  readonly clock: PlaybackClock;
  readonly tags: readonly RuntimeAnnotationTag[];
  readonly selectedCameraId?: string;
  readonly readOnly: boolean;
  readonly onSelectTask?: (taskId: string) => void;
  readonly timelineSelection?: ViewerTimelineSelection;
  readonly onTimeRangeSelect?: (startNs: string, endNs: string) => void;
  readonly onResourceError?: DataVisualizationWorkbenchAdapter["onResourceError"];
}): DataVisualizationWorkbenchAdapter {
  const { bundle, mode } = input;
  const revision =
    mode === "tag-review"
      ? (resolveReviewSubmission(bundle)?.revision ??
        bundle.task.current_revision)
      : (bundle.draft?.revision ?? bundle.task.current_revision);
  const allCameras = bundle.manifest?.cameras ?? [];
  const cameras =
    mode === "tag-review"
      ? allCameras
          .filter(
            (camera, index) =>
              camera.camera_id === input.selectedCameraId ||
              (!input.selectedCameraId && index === 0),
          )
          .slice(0, 1)
      : allCameras;
  return {
    mode,
    id: `p08-${mode}-${bundle.task.task_id}`,
    title: mode === "tag-review" ? "Tag 审核" : "数据标注",
    description: `固定 Lance v${bundle.task.base_lance_version} · ${bundle.task.rollout_id}`,
    readOnly: input.readOnly,
    clock: input.clock,
    cameraStreams: cameras.map((camera) =>
      streamForCamera(input.scope, bundle.task, camera, revision, mode),
    ),
    collectionItems: collectionItems(bundle),
    selectedCollectionItemId: bundle.task.task_id,
    onSelectCollectionItem: input.onSelectTask,
    findings: [],
    timelineTracks: tagTracks(mode, bundle, input.tags),
    ...(input.timelineSelection
      ? { timelineSelection: input.timelineSelection }
      : {}),
    actions: [],
    ...(bundle.manifestIssue
      ? {
          banner: {
            label: "局部加载失败",
            title: "Manifest 相机暂不可用",
            description: `${bundle.manifestIssue.message}${bundle.manifestIssue.problemCode ? `（问题代码：${bundle.manifestIssue.problemCode}）` : ""}${bundle.manifestIssue.requestId ? `（请求 ${bundle.manifestIssue.requestId}）` : ""}${bundle.manifestIssue.retryable ? "；可重新加载任务重试。" : ""}`,
            tone: "warning" as const,
          },
        }
      : {}),
    ...(input.onTimeRangeSelect
      ? { onTimeRangeSelect: input.onTimeRangeSelect }
      : {}),
    ...(input.onResourceError
      ? { onResourceError: input.onResourceError }
      : {}),
  };
}
