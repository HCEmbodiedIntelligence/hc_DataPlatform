import type { Scope } from "../../entities/scope";
import { z } from "zod";
import { authorizeAlignedMedia } from "../../features/aligned-media/authorize-aligned-media";
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
export type RuntimeDatasetVersion = components["schemas"]["DatasetVersionRef"];
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
const revisionOriginWireSchema = z.enum(["ANNOTATION", "ANNOTATION_RESTORE"]);
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
    current_episode_version: z.number().int().positive().nullable().optional(),
    approved_revision: z.number().int().nonnegative().nullable().optional(),
    approved_review_id: z.string().min(1).nullable().optional(),
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
  readonly datasetVersion: RuntimeDatasetVersion;
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

const DEFAULT_STEP_RATE_HZ = 30;
const NS_PER_SECOND = 1_000_000_000n;
const RATE_PRECISION = 1_000_000n;

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

export function normalizeStepRateHz(value: number | null | undefined): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : DEFAULT_STEP_RATE_HZ;
}

function scaledStepRate(value: number | null | undefined): bigint {
  return BigInt(
    Math.max(
      1,
      Math.round(normalizeStepRateHz(value) * Number(RATE_PRECISION)),
    ),
  );
}

export function stepToTimelineNs(
  step: number,
  frequencyHz: number = DEFAULT_STEP_RATE_HZ,
): string {
  const normalized = Number.isFinite(step) ? Math.max(0, Math.trunc(step)) : 0;
  return (
    (BigInt(normalized) * NS_PER_SECOND * RATE_PRECISION) /
    scaledStepRate(frequencyHz)
  ).toString();
}

export function timelineNsToStep(
  value: string,
  frequencyHz: number = DEFAULT_STEP_RATE_HZ,
): number {
  return Number(
    (BigInt(value) * scaledStepRate(frequencyHz)) /
      (NS_PER_SECOND * RATE_PRECISION),
  );
}

export function annotationTaskStatusLabel(
  status: RuntimeAnnotationTask["status"],
): string {
  const labels: Readonly<Record<RuntimeAnnotationTask["status"], string>> = {
    DRAFT: "待标注",
    SUBMITTED: "待审核",
    APPROVED: "标注完成",
    NEEDS_REVISION: "待修改",
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
  if (task.status !== "DRAFT" || task.assignee_id !== null) {
    contractMismatch("当前任务状态或分配关系不允许创建标注草稿。");
  }
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

async function getRuntimeAnnotationDraftOrNull(
  scope: RuntimeAnnotationScope,
  taskId: string,
  signal?: AbortSignal,
): Promise<RuntimeAnnotationDraft | null> {
  try {
    return await getRuntimeAnnotationDraft(scope, taskId, signal);
  } catch (error) {
    if (
      isDomainError(error) &&
      (error.code === "FORBIDDEN" ||
        error.code === "NOT_FOUND" ||
        error.code === "GONE")
    )
      return null;
    throw error;
  }
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

async function getRuntimeDatasetVersion(
  scope: RuntimeAnnotationScope,
  task: RuntimeAnnotationTask,
  signal?: AbortSignal,
): Promise<RuntimeDatasetVersion> {
  return request<RuntimeDatasetVersion>({
    method: "GET",
    path: `/projects/${encoded(scope.projectId)}/datasets/${encoded(task.dataset_id)}/lance-versions/${encoded(task.base_lance_version)}`,
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
      manifestIssue: asDomainError(error, "数据清单加载失败。"),
    }));
  const [draft, history, schema, manifestResult, datasetVersion] =
    await Promise.all([
      mode === "annotation"
        ? getRuntimeAnnotationDraftOrNull(scope, taskId, signal)
        : Promise.resolve(null),
      getRuntimeAnnotationHistory(scope, taskId, signal),
      getRuntimeTagSchema(scope, task, signal),
      manifestPromise,
      getRuntimeDatasetVersion(scope, task, signal),
    ]);
  if (
    history.task.task_id !== task.task_id ||
    schema.project_id !== task.project_id ||
    schema.schema_id !== task.tag_schema_id ||
    schema.version !== task.tag_schema_version ||
    datasetVersion.project_id !== task.project_id ||
    datasetVersion.dataset_id !== task.dataset_id ||
    datasetVersion.version !== task.base_lance_version ||
    (draft !== null &&
      (draft.task_id !== task.task_id ||
        draft.tag_schema_id !== task.tag_schema_id ||
        draft.tag_schema_version !== task.tag_schema_version))
  ) {
    contractMismatch("标注任务、草稿、历史或标签结构的固定身份不一致。");
  }
  return {
    task,
    datasetVersion,
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
  if (
    (task.status !== "DRAFT" && task.status !== "NEEDS_REVISION") ||
    draft.task_id !== task.task_id ||
    draft.revision !== task.current_revision ||
    draft.etag !== task.etag
  ) {
    contractMismatch("当前任务状态、草稿修订或并发版本不允许保存修改。");
  }
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
    contractMismatch("保存后的修订与当前任务或标签结构不一致。");
  }
  return revision;
}

export async function restoreRuntimeAnnotationRevision(
  scope: RuntimeAnnotationScope,
  task: RuntimeAnnotationTask,
  targetRevision: number,
): Promise<RuntimeAnnotationRevision> {
  if (task.status !== "DRAFT" && task.status !== "NEEDS_REVISION") {
    contractMismatch("当前任务状态不允许写入数据修订。");
  }
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
    contractMismatch("回退后的修订与任务、父修订或标签结构不一致。");
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
  if (task.status !== "DRAFT" && task.status !== "NEEDS_REVISION") {
    contractMismatch("当前任务状态不允许启动自动标注写入。");
  }
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
  if (
    (task.status !== "DRAFT" && task.status !== "NEEDS_REVISION") ||
    job.status !== "SUCCEEDED" ||
    job.source_revision !== task.current_revision
  ) {
    contractMismatch("当前任务或自动标注结果状态不允许应用新修订。");
  }
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
  if (
    (task.status !== "DRAFT" && task.status !== "NEEDS_REVISION") ||
    revision !== task.current_revision
  ) {
    contractMismatch("当前任务状态或草稿修订不允许提交审核。");
  }
  const body = {
    expected_revision: revision,
  } satisfies components["schemas"]["SubmitRequest"];
  const submission = await request<RuntimeAnnotationSubmission>({
    method: "POST",
    path: `/annotation-tasks/${encoded(task.task_id)}/submit`,
    scope: scopeForRequest(scope),
    ifMatch: task.etag,
    idempotencyKey: createClientMutationId("submit"),
    body,
  });
  if (
    submission.task_id !== task.task_id ||
    !Number.isInteger(submission.episode_version) ||
    submission.episode_version < 1 ||
    submission.revision !== revision ||
    submission.tag_schema_id !== task.tag_schema_id ||
    submission.tag_schema_version !== task.tag_schema_version
  ) {
    contractMismatch("提交版本与当前任务、修订或标签结构不一致。");
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
  if (
    task.status !== "SUBMITTED" ||
    task.current_submission_id !== submission.submission_id ||
    task.submitted_revision !== submission.revision ||
    (decision !== "APPROVE" && comment.trim().length === 0)
  ) {
    contractMismatch("当前任务、审核意见或提交版本不允许创建审核决定。");
  }
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

function createAlignedMediaSource(
  scope: RuntimeAnnotationScope,
  task: RuntimeAnnotationTask,
  cameraId: string,
): ViewerMediaSource {
  const authorize = async (signal: AbortSignal) => {
    const selector = {
      camera_id: cameraId,
      dataset_id: task.dataset_id,
      dataset_version: task.dataset_version,
      project_id: task.project_id,
      rollout_id: task.rollout_id,
    };
    const descriptor = await authorizeAlignedMedia(
      scopeForRequest(scope),
      selector,
      signal,
    );
    if (
      descriptor.project_id !== task.project_id ||
      descriptor.dataset_id !== task.dataset_id ||
      descriptor.dataset_version !== task.dataset_version ||
      descriptor.rollout_id !== task.rollout_id ||
      descriptor.camera_id !== cameraId
    ) {
      contractMismatch("媒体授权与当前任务、Dataset 版本或相机不一致。");
    }
    return {
      url: resolveAlignedMediaUrl(descriptor.media_url),
      expiresAt: descriptor.expires_at,
      mediaStartSeconds: descriptor.timeline.original_source?.start_seconds,
      mediaEndSeconds: descriptor.timeline.original_source?.end_seconds,
      kind: "rgb-video" as const,
    };
  };
  return { authorize, refresh: authorize };
}

function resolveAlignedMediaUrl(url: string): string {
  if (/^https?:\/\//u.test(url)) return url;
  const origin = globalThis.location?.origin ?? "http://localhost";
  const apiOrigin = new URL(getRuntimeConfig().apiBaseUrl, origin).origin;
  return new URL(url, apiOrigin).toString();
}

export function resolveReviewSubmission(
  bundle: RuntimeAnnotationBundle,
): RuntimeAnnotationSubmission | null {
  const currentId = bundle.task.current_submission_id;
  const submittedRevision = bundle.task.submitted_revision;
  if (
    bundle.task.status !== "SUBMITTED" ||
    !currentId ||
    submittedRevision === null ||
    submittedRevision === undefined
  )
    return null;
  return (
    bundle.history.submissions.find(
      (item) =>
        item.submission_id === currentId &&
        item.task_id === bundle.task.task_id &&
        item.revision === submittedRevision,
    ) ?? null
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
  frequencyHz: number,
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
    rateHz: frequencyHz,
    startNs: "0",
    endNs: stepToTimelineNs(endStep, frequencyHz),
    ...(camera.frame_id
      ? { frame: { id: camera.frame_id, name: camera.frame_id } }
      : {}),
    availability: "ready",
    accessibleSummary: `${camera.camera_id}，来自数据清单的只读相机流，与其他模态共享 ${frequencyHz} Hz 时间光标。`,
    mediaSource: createAlignedMediaSource(scope, task, camera.topic),
  };
}

function placeholderCameraStream(
  task: RuntimeAnnotationTask,
  slotIndex: number,
  frequencyHz: number,
): StreamDescriptor {
  const endStep = Math.max(1, task.base_step_count ?? 1);
  const slotNumber = slotIndex + 1;
  return {
    id: `camera-slot-${slotNumber}`,
    canonicalPath: `/camera-slots/${slotNumber}`,
    displayName: `摄像头 ${slotNumber}`,
    modality: "rgb",
    semanticRole: "camera-slot-placeholder",
    schema: {
      id: "camera-slot-placeholder",
      version: String(task.base_lance_version),
    },
    rateHz: frequencyHz,
    startNs: "0",
    endNs: stepToTimelineNs(endStep, frequencyHz),
    availability: "missing",
    accessibleSummary: `第 ${slotNumber} 个视频槽位尚未接入摄像头。真实视频会按数据清单顺序从第一格开始显示。`,
  };
}

function tagTracks(
  mode: AnnotationWorkbenchMode,
  bundle: RuntimeAnnotationBundle,
  tags: readonly RuntimeAnnotationTag[],
): readonly ViewerTimelineTrack[] {
  const frequencyHz = normalizeStepRateHz(bundle.datasetVersion.frequency_hz);
  const schemaLabels = new Map(
    bundle.schema.document.nodes.map((node) => [
      node.tag_id,
      node.display_name,
    ]),
  );
  const intervalTracks = (
    idPrefix: string,
    labelPrefix: string,
    values: readonly RuntimeAnnotationTag[],
    tone: "phase" | "action" | "tag-level",
  ): readonly ViewerTimelineTrack[] => {
    const byId = new Map(values.map((tag) => [tag.annotation_id, tag]));
    const depthCache = new Map<string, number>();
    const pathCache = new Map<string, readonly string[]>();
    const depthFor = (
      tag: RuntimeAnnotationTag,
      seen = new Set<string>(),
    ): number => {
      const cached = depthCache.get(tag.annotation_id);
      if (cached !== undefined) return cached;
      if (!tag.parent_annotation_id || seen.has(tag.annotation_id)) return 0;
      const parent = byId.get(tag.parent_annotation_id);
      if (!parent) return 0;
      const depth = depthFor(parent, new Set(seen).add(tag.annotation_id)) + 1;
      depthCache.set(tag.annotation_id, depth);
      return depth;
    };
    const pathFor = (
      tag: RuntimeAnnotationTag,
      seen = new Set<string>(),
    ): readonly string[] => {
      const cached = pathCache.get(tag.annotation_id);
      if (cached) return cached;
      const label =
        tag.label?.trim() ||
        schemaLabels.get(tag.tag_id) ||
        tag.path.at(-1) ||
        tag.tag_id;
      if (!tag.parent_annotation_id || seen.has(tag.annotation_id)) {
        const path = tag.label
          ? [label]
          : tag.path.map((tagId) => schemaLabels.get(tagId) ?? tagId);
        pathCache.set(tag.annotation_id, path);
        return path;
      }
      const parent = byId.get(tag.parent_annotation_id);
      const path = parent
        ? [...pathFor(parent, new Set(seen).add(tag.annotation_id)), label]
        : [label];
      pathCache.set(tag.annotation_id, path);
      return path;
    };
    const byDepth = new Map<number, RuntimeAnnotationTag[]>();
    for (const tag of values) {
      const depth = depthFor(tag);
      const level = byDepth.get(depth) ?? [];
      level.push(tag);
      byDepth.set(depth, level);
    }
    const tagLevelTones = [
      "tag-level-1",
      "tag-level-2",
      "tag-level-3",
      "tag-level-4",
    ] as const;
    return [...byDepth.entries()]
      .toSorted(([left], [right]) => left - right)
      .flatMap(([depth, level]) => {
        const lanes: RuntimeAnnotationTag[][] = [];
        for (const tag of level.toSorted(
          (left, right) =>
            left.start_step - right.start_step ||
            left.end_step - right.end_step,
        )) {
          const lane = lanes.find(
            (candidate) =>
              (candidate.at(-1)?.end_step ?? Number.NEGATIVE_INFINITY) <=
              tag.start_step,
          );
          if (lane) lane.push(tag);
          else lanes.push([tag]);
        }
        return lanes.map(
          (lane, laneIndex) =>
            ({
              id: `${idPrefix}-level-${depth + 1}-lane-${laneIndex + 1}`,
              label: `${labelPrefix} L${depth + 1}${laneIndex ? ` · ${laneIndex + 1}` : ""}`,
              level: depth,
              segments: lane.map((tag) => ({
                id: `${idPrefix}-${tag.annotation_id}`,
                label: pathFor(tag).join(" / "),
                startNs: stepToTimelineNs(tag.start_step, frequencyHz),
                endNs: stepToTimelineNs(tag.end_step, frequencyHz),
                activatePlayback: true,
                tone:
                  tone === "tag-level"
                    ? tagLevelTones[depth % tagLevelTones.length]
                    : tone,
              })),
            }) satisfies ViewerTimelineTrack,
        );
      });
  };
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
      startNs: stepToTimelineNs(operation.start_step, frequencyHz),
      endNs: stepToTimelineNs(operation.end_step, frequencyHz),
      tone:
        operation.kind === "EXCLUDE"
          ? ("issue" as const)
          : ("quality-pass" as const),
    })),
  } satisfies ViewerTimelineTrack;
  if (mode === "annotation")
    return intervalTracks("tag-intervals", "Tag", tags, "tag-level");
  const original = resolveOriginalRevision(bundle)?.tags ?? [];
  return [
    ...intervalTracks("original-tags", "原始 Tag", original, "phase"),
    ...intervalTracks("revised-tags", "修订 Tag", tags, "action"),
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
              label: "数据结构",
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
  readonly cameraLimit?: number;
  readonly cameraSlotCount?: number;
  readonly readOnly: boolean;
  readonly onSelectTask?: (taskId: string) => void;
  readonly timelineSelection?: ViewerTimelineSelection;
  readonly onTimeRangeSelect?: (startNs: string, endNs: string) => void;
  readonly onResourceError?: DataVisualizationWorkbenchAdapter["onResourceError"];
}): DataVisualizationWorkbenchAdapter {
  const { bundle, mode } = input;
  const paddedCameraStreams = buildRuntimeCameraStreams(input);
  return {
    mode,
    id: `p08-${mode}-${bundle.task.task_id}`,
    title: mode === "tag-review" ? "Tag 审核" : "数据标注",
    description: `固定 Lance v${bundle.task.base_lance_version} · ${bundle.task.rollout_id}`,
    readOnly: input.readOnly,
    clock: input.clock,
    cameraStreams: paddedCameraStreams,
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
            title: "数据清单相机暂不可用",
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

export function buildRuntimeCameraStreams(input: {
  readonly bundle: RuntimeAnnotationBundle;
  readonly scope: RuntimeAnnotationScope;
  readonly mode: AnnotationWorkbenchMode;
  readonly selectedCameraId?: string;
  readonly cameraLimit?: number;
  readonly cameraSlotCount?: number;
}): readonly StreamDescriptor[] {
  const { bundle, mode } = input;
  const frequencyHz = normalizeStepRateHz(bundle.datasetVersion.frequency_hz);
  const allCameras = bundle.manifest?.cameras ?? [];
  const cameras =
    mode === "tag-review" || input.selectedCameraId
      ? allCameras
          .filter(
            (camera, index) =>
              camera.camera_id === input.selectedCameraId ||
              (!input.selectedCameraId && index === 0),
          )
          .slice(0, 1)
      : input.cameraLimit
        ? allCameras.slice(0, input.cameraLimit)
        : allCameras;
  const cameraStreams = cameras.map((camera) =>
    streamForCamera(input.scope, bundle.task, camera, frequencyHz),
  );
  const slotCount = Math.max(cameraStreams.length, input.cameraSlotCount ?? 0);
  const paddedCameraStreams = [
    ...cameraStreams,
    ...Array.from({ length: slotCount - cameraStreams.length }, (_, index) =>
      placeholderCameraStream(
        bundle.task,
        cameraStreams.length + index,
        frequencyHz,
      ),
    ),
  ];
  return paddedCameraStreams;
}
