import { delay, http, HttpResponse, passthrough } from "msw";
import {
  acceptedAnnotationJobFixture,
  annotationFixtureIds,
  annotationFixtureScope,
  annotationUnknownFormDefinitionWire,
  annotationTaskFixtures,
  annotationViewerStreams,
  makeAnnotationDetailEnvelope,
  makeAnnotationDraft,
  makeAnnotationListEnvelope,
  makeAnnotationTask,
} from "../fixtures/annotation";
import { getAnnotationScenario } from "../scenarios/annotation";
import { formalUploadSessionFixture } from "../fixtures/ingest";
import { createVisualAnnotationBundle } from "../../pages/p08-data-annotation/testing/annotation-fixture";

const api = "*/api/v1/projects/:projectId/regions/:regionCode/annotation-tasks";
const manifestDiscoveryApi = `${api}/:taskId/manifest-discovery`;
const entryResolutionApi =
  "*/api/v1/projects/:projectId/regions/:regionCode/episode-revisions/:revisionId/annotation-task-entry-resolution";

const materializeEntryPath =
  /\/api\/v1\/projects\/(?<projectId>[^/]+)\/regions\/(?<regionCode>[^/]+)\/annotation-task-entry-resolutions\/(?<resolutionId>[^/:]+):materialize$/u;

function commandPath(
  command: "claim" | "submit-preflight" | "submit" | "review" | "rebase",
): RegExp {
  return new RegExp(
    `/api/v1/projects/(?<projectId>[^/]+)/regions/(?<regionCode>[^/]+)/annotation-tasks/(?<taskId>[^/:]+):${command}$`,
    "u",
  );
}

let currentTask: ReturnType<typeof makeAnnotationTask> =
  annotationTaskFixtures.progress;
let currentDraft: ReturnType<typeof makeAnnotationDraft> =
  makeAnnotationDraft();
let rateAttempt = 0;
let offlineAttempt = 0;
let mediaAttempt = 0;
const idempotency = new Map<string, { body: string; response: unknown }>();

function runtimeRegion(request: Request): string {
  return (
    request.headers.get("X-Region-Code") || annotationFixtureScope.region_code
  );
}

function runtimeProject(request: Request): string {
  return (
    request.headers.get("X-Project-Id") || annotationFixtureScope.project_id
  );
}

function runtimeBundle(projectId: string, regionCode: string, taskId?: string) {
  const source = createVisualAnnotationBundle({
    mode: "annotation",
    cameraCount: 1,
  });
  const remapTask = (task: typeof source.task, nextTaskId = task.task_id) => ({
    ...task,
    task_id: nextTaskId,
    project_id: projectId,
    region_code: regionCode,
    rollout_id: formalUploadSessionFixture.rollout_id,
    dataset_id: "dataset_fx_01",
  });
  const tasks = source.tasks.map((task) => remapTask(task));
  const selectedSource =
    tasks.find((task) => task.task_id === taskId) ?? tasks[0]!;
  const task =
    taskId && selectedSource.task_id !== taskId
      ? remapTask(source.task, taskId)
      : selectedSource;
  const draft = source.draft
    ? { ...source.draft, task_id: task.task_id, etag: task.etag }
    : null;
  const history = {
    ...source.history,
    task,
    revisions: source.history.revisions.map((revision) => ({
      ...revision,
      task_id: task.task_id,
    })),
    submissions: source.history.submissions.map((submission) => ({
      ...submission,
      task_id: task.task_id,
    })),
    reviews: source.history.reviews.map((review) => ({
      ...review,
      task_id: task.task_id,
    })),
  };
  return {
    task,
    tasks,
    draft,
    history,
    schema: { ...source.schema, project_id: projectId },
    manifest: source.manifest,
  };
}

function runtimeFailure(): Response | null {
  const scenario = getAnnotationScenario();
  if (scenario === "forbidden" || scenario === "permission-revoked") {
    return problem(403, "FORBIDDEN", "annotation_task.read 已撤销");
  }
  if (scenario === "fatal-error") {
    return problem(500, "INTERNAL_ERROR", "Fixture 首屏错误");
  }
  return null;
}

function problem(
  status: number,
  code: string,
  message: string,
  fieldErrors: readonly unknown[] = [],
) {
  return HttpResponse.json(
    {
      error: {
        code,
        message,
        field_errors: fieldErrors,
        operation_errors: [],
        blocked_reasons: [],
        request_id: `req_annotation_${code.toLowerCase()}`,
        retryable: status >= 500 || status === 429,
      },
    },
    { status },
  );
}

function validateScope(
  params: Readonly<Record<string, string | readonly string[] | undefined>>,
): Response | null {
  return params.projectId === annotationFixtureScope.project_id &&
    params.regionCode === annotationFixtureScope.region_code
    ? null
    : problem(404, "NOT_FOUND", "Fixture scope 不存在");
}

function validateRead(
  request: Request,
  params: Readonly<Record<string, string | readonly string[] | undefined>>,
): Response | null {
  return (
    validateScope(params) ??
    (!request.headers.get("X-Client-Version")
      ? problem(400, "MISSING_HEADER", "缺少 X-Client-Version")
      : null)
  );
}

function validateWrite(
  request: Request,
  params: Readonly<Record<string, string | readonly string[] | undefined>>,
): Response | null {
  return (
    validateRead(request, params) ??
    (!request.headers.get("Idempotency-Key")
      ? problem(400, "IDEMPOTENCY_KEY_REQUIRED", "缺少 Idempotency-Key")
      : null) ??
    (!request.headers.get("If-Match")
      ? problem(428, "PRECONDITION_REQUIRED", "缺少 If-Match")
      : null)
  );
}

function validateIdempotentCreate(
  request: Request,
  params: Readonly<Record<string, string | readonly string[] | undefined>>,
): Response | null {
  return (
    validateRead(request, params) ??
    (!request.headers.get("Idempotency-Key")
      ? problem(400, "IDEMPOTENCY_KEY_REQUIRED", "缺少 Idempotency-Key")
      : null)
  );
}

async function scenarioDelay(): Promise<void> {
  const scenario = getAnnotationScenario();
  if (scenario === "first-loading") await delay(2_000);
  if (scenario === "submitting") await delay(1_200);
}

function streamsForScenario(): readonly unknown[] {
  const scenario = getAnnotationScenario();
  if (scenario === "single-arm-6-axis-camera-pointcloud")
    return annotationViewerStreams.sixAxisPointcloud;
  if (scenario === "dual-arm-14-axis-multicam")
    return annotationViewerStreams.fourteenAxis;
  if (scenario === "variable-axis-mismatch")
    return annotationViewerStreams.axisMismatch;
  if (scenario === "pointcloud-preview-pending")
    return annotationViewerStreams.pointcloudPending;
  if (scenario === "unknown-modality") return annotationViewerStreams.unknown;
  if (scenario === "partial-media-error" || scenario === "partial-error")
    return annotationViewerStreams.sevenAxis.map((stream, index) =>
      index ? stream : { ...stream, availability: "MISSING" },
    );
  return annotationViewerStreams.sevenAxis;
}

function taskForId(taskId: string) {
  if (taskId === annotationFixtureIds.queuedTask)
    return annotationTaskFixtures.queued;
  if (taskId === annotationFixtureIds.submittedTask)
    return annotationTaskFixtures.submitted;
  if (
    taskId === annotationFixtureIds.staleTask ||
    getAnnotationScenario() === "stale"
  )
    return annotationTaskFixtures.stale;
  return currentTask;
}

function replay(
  request: Request,
  body: unknown,
  response: unknown,
): Response | null {
  const key = request.headers.get("Idempotency-Key")!;
  const canonical = JSON.stringify(body);
  const prior = idempotency.get(key);
  if (prior && prior.body !== canonical)
    return problem(409, "IDEMPOTENCY_KEY_REUSED", "幂等键被不同请求体复用");
  if (prior) return HttpResponse.json(prior.response as never);
  idempotency.set(key, { body: canonical, response });
  return null;
}

export const annotationHandlers = [
  http.get(
    "*/api/v1/projects/:projectId/datasets/:datasetId/rollouts/:rolloutId/steps",
    ({ request, params }) => {
      if (params.rolloutId !== formalUploadSessionFixture.rollout_id)
        return passthrough();
      const url = new URL(request.url);
      const startStep = Number(url.searchParams.get("startStep"));
      const endStep = Number(url.searchParams.get("endStep"));
      const version = Number(url.searchParams.get("version"));
      if (
        !Number.isSafeInteger(startStep) ||
        !Number.isSafeInteger(endStep) ||
        !Number.isSafeInteger(version) ||
        startStep < 0 ||
        endStep <= startStep ||
        endStep - startStep > 480
      )
        return problem(422, "STEP_WINDOW_INVALID", "关节角步骤窗口无效");
      return HttpResponse.json({
        schema_version: "1",
        project_id: String(params.projectId),
        dataset_id: String(params.datasetId),
        dataset_version: version,
        rollout_id: String(params.rolloutId),
        start_step: startStep,
        end_step: endStep,
        steps: Array.from({ length: endStep - startStep }, (_, offset) => {
          const step = startStep + offset;
          const values = Array.from({ length: 7 }, (_, joint) => {
            const phase = step / 22 + joint * 0.72;
            return Math.sin(phase) * (0.72 + joint * 0.06) + joint * 0.08;
          });
          return {
            schema_version: "1",
            rollout_id: String(params.rolloutId),
            step_index: step,
            timestamp_ns: `${(BigInt(step) * 1_000_000_000n) / 30n}`,
            modalities: { "joint.position": values },
            source_timestamps_ns: { "joint.position": [] },
            time_error_ns: { "joint.position": 0 },
            valid: { "joint.position": true },
            repeated: { "joint.position": false },
            sample_valid: true,
          };
        }),
      });
    },
  ),
  // This module is first in the eager handler registry. Bypass Vite source modules before
  // legacy command-style matchers in unrelated page handlers attempt to parse the URL.
  http.all(/^(?!.*\/api\/v1\/).*$/u, () => passthrough()),
  http.get(entryResolutionApi, ({ request, params }) => {
    const invalid = validateRead(request, params);
    if (invalid) return invalid;
    const url = new URL(request.url);
    const streamIds = url.searchParams.getAll("stream_id");
    const startNs = url.searchParams.get("start_ns");
    const endNs = url.searchParams.get("end_ns");
    if (
      params.revisionId !== annotationFixtureIds.revision ||
      startNs !== "0" ||
      endNs !== "120000000000" ||
      !streamIds.length
    )
      return problem(
        400,
        "INVALID_REQUEST",
        "Resolver 必须携带固定 Revision、范围和 Stream 集合",
      );
    const scenario = getAnnotationScenario();
    const state =
      scenario === "asset-handoff-claimable"
        ? "CLAIMABLE"
        : scenario === "asset-handoff-create"
          ? "CAN_CREATE"
          : scenario === "asset-handoff-assigned-other"
            ? "ASSIGNED_TO_OTHER"
            : scenario === "asset-handoff-forbidden"
              ? "FORBIDDEN"
              : "OPEN_EXISTING";
    const taskId =
      state === "OPEN_EXISTING"
        ? annotationFixtureIds.task
        : state === "CLAIMABLE"
          ? annotationFixtureIds.queuedTask
          : null;
    return HttpResponse.json({
      data: {
        resolution_id: "annotation_resolution_fx_01",
        state,
        resolved_context: {
          source: {
            ...annotationTaskFixtures.progress.source,
            stream_ids: streamIds,
            start_ns: startNs,
            end_ns: endNs,
          },
          ontology: annotationTaskFixtures.progress.ontology,
          coverage_key_hash: `sha256:${"2".repeat(64)}`,
        },
        task_id: taskId,
        task_etag: taskId
          ? state === "CLAIMABLE"
            ? annotationTaskFixtures.queued.etag
            : annotationTaskFixtures.progress.etag
          : null,
        entry_resolution_token:
          state === "CAN_CREATE"
            ? "fixture-entry-resolution-token-00001"
            : null,
        next_action:
          state === "OPEN_EXISTING"
            ? "OPEN_TASK"
            : state === "CLAIMABLE"
              ? "CLAIM_TASK"
              : state === "CAN_CREATE"
                ? "CREATE_TASK"
                : "NONE",
        blocked_reason:
          state === "ASSIGNED_TO_OTHER"
            ? "MATCH_ASSIGNED_TO_OTHER"
            : state === "FORBIDDEN"
              ? "ENTRY_FORBIDDEN"
              : null,
        expires_at: "2026-08-05T08:15:00Z",
      },
      scope: annotationFixtureScope,
      request_id: "req_annotation_resolution_fx_01",
      contract_version: "data-annotation.v1",
    });
  }),
  http.post(materializeEntryPath, async ({ request, params }) => {
    const invalid = validateIdempotentCreate(request, params);
    if (invalid) return invalid;
    if (params.resolutionId !== "annotation_resolution_fx_01")
      return problem(404, "NOT_FOUND", "Resolver 不存在");
    const body = (await request.json()) as Readonly<Record<string, unknown>>;
    if (
      body.entry_resolution_token !== "fixture-entry-resolution-token-00001" ||
      typeof body.client_session_id !== "string"
    )
      return problem(
        400,
        "INVALID_REQUEST",
        "物化只能提交 Resolver token 与 client_session_id",
      );
    const task = makeAnnotationTask({
      task_id: "ann-task-created-01",
      task_source: "COVERAGE_GAP",
      workflow_status: "ASSIGNED",
      current_draft_revision: 0,
      current_draft_hash: `sha256:${"1".repeat(64)}`,
      allowed_actions: ["EDIT_DRAFT", "SAVE_DRAFT"],
      etag: '"task-rv-1"',
    });
    const response = {
      data: {
        disposition: "CREATED",
        task,
        draft: makeAnnotationDraft(task.task_id, 0),
      },
      scope: annotationFixtureScope,
      request_id: "req_annotation_materialize_fx_01",
      contract_version: "data-annotation.v1",
    };
    return (
      replay(request, body, response) ??
      HttpResponse.json(response, { status: 201 })
    );
  }),
  http.get(api, async ({ request, params }) => {
    const invalid = validateRead(request, params);
    if (invalid) return invalid;
    await scenarioDelay();
    const scenario = getAnnotationScenario();
    const url = new URL(request.url);
    if (!url.searchParams.get("view"))
      return problem(400, "INVALID_REQUEST", "view 为必填服务端筛选");
    if (url.searchParams.has("after") && url.searchParams.has("before"))
      return problem(400, "INVALID_CURSOR", "after 与 before 互斥");
    if (scenario === "forbidden" || scenario === "permission-revoked")
      return problem(403, "FORBIDDEN", "annotation_task.read 已撤销");
    if (scenario === "fatal-error")
      return problem(500, "INTERNAL_ERROR", "Fixture 首屏错误");
    if (scenario === "rate-limited" && rateAttempt++ === 0)
      return problem(429, "RATE_LIMITED", "请稍后重试");
    if (scenario === "offline-recovery" && offlineAttempt++ === 0)
      return HttpResponse.error();
    if (scenario === "contract-mismatch")
      return HttpResponse.json({
        ...makeAnnotationListEnvelope(),
        scope: { ...annotationFixtureScope, project_id: "wrong_scope" },
      });
    if (scenario === "unknown-enum")
      return HttpResponse.json(
        makeAnnotationListEnvelope([
          makeAnnotationTask({
            workflow_status: "FUTURE_TASK_STATE",
            allowed_actions: [],
          }),
        ]),
      );
    if (scenario === "empty" || scenario === "filtered-empty")
      return HttpResponse.json(makeAnnotationListEnvelope([]));
    const view = url.searchParams.get("view");
    return HttpResponse.json(
      makeAnnotationListEnvelope(
        view === "available"
          ? [annotationTaskFixtures.queued]
          : [
              currentTask,
              annotationTaskFixtures.submitted,
              annotationTaskFixtures.stale,
            ],
      ),
    );
  }),
  http.get(manifestDiscoveryApi, ({ request, params }) => {
    const invalid = validateRead(request, params);
    if (invalid) return invalid;
    const scenario = getAnnotationScenario();
    const cameraCount =
      scenario === "dual-arm-14-axis-multicam"
        ? 3
        : scenario === "single-arm-6-axis-camera-pointcloud" ||
            scenario === "single-arm-7-axis-camera"
          ? 1
          : 3;
    const fixture = createVisualAnnotationBundle({ cameraCount });
    const manifest = fixture.manifest;
    if (!manifest)
      return problem(
        500,
        "FIXTURE_INVALID",
        "标注数据清单模拟数据缺失",
      );
    return HttpResponse.json({
      ...manifest,
      topics: [
        ...manifest.topics,
        {
          name: "/robot/joint_states",
          required: true,
          schema_name: "sensor_msgs/msg/JointState",
          message_encoding: "cdr",
        },
      ],
    });
  }),
  http.get(`${api}/:taskId`, async ({ request, params }) => {
    const invalid = validateRead(request, params);
    if (invalid) return invalid;
    await scenarioDelay();
    const scenario = getAnnotationScenario();
    if (scenario === "forbidden" || scenario === "permission-revoked")
      return problem(403, "FORBIDDEN", "任务权限已撤销");
    if (scenario === "not-found")
      return problem(404, "NOT_FOUND", "任务不存在");
    if (scenario === "gone") return problem(410, "GONE", "任务已过期");
    const task = taskForId(String(params.taskId));
    const detail = makeAnnotationDetailEnvelope(task, streamsForScenario());
    if (scenario === "contract-mismatch")
      return HttpResponse.json({
        ...detail,
        data: {
          ...detail.data,
          draft: { ...detail.data.draft, task_id: "different_task" },
        },
      });
    if (scenario === "unknown-enum")
      return HttpResponse.json({
        ...detail,
        data: {
          ...detail.data,
          task: {
            ...detail.data.task,
            workflow_status: "FUTURE_STATE",
            allowed_actions: [],
          },
          draft: {
            ...detail.data.draft,
            entries: [
              {
                semantic_type: "FUTURE_KIND",
                annotation_id: "future_entry",
                label_code: "future",
                attributes: {},
              },
            ],
          },
          viewer: {
            ...detail.data.viewer,
            form_definition: annotationUnknownFormDefinitionWire,
          },
        },
      });
    return HttpResponse.json(detail);
  }),
  http.post(commandPath("claim"), async ({ request, params }) => {
    const invalid = validateWrite(request, params);
    if (invalid) return invalid;
    if (
      getAnnotationScenario() === "etag-conflict" ||
      getAnnotationScenario() === "conflict"
    )
      return problem(412, "PRECONDITION_FAILED", "任务 ETag 已变化");
    const body = await request.json();
    currentTask = makeAnnotationTask({
      task_id: String(params.taskId),
      workflow_status: "ASSIGNED",
      assignment: {
        mode: "CLAIMED",
        assignee_id: "usr_fx_developer",
        assigned_by: "usr_fx_developer",
        assigned_at: "2026-08-05T08:11:00Z",
      },
      etag: '"task-rv-2"',
    });
    const response = {
      data: currentTask,
      scope: annotationFixtureScope,
      request_id: "req_annotation_claim_fx",
      contract_version: "data-annotation.v1",
    };
    return replay(request, body, response) ?? HttpResponse.json(response);
  }),
  http.put(`${api}/:taskId/draft`, async ({ request, params }) => {
    const invalid = validateWrite(request, params);
    if (invalid) return invalid;
    await scenarioDelay();
    const scenario = getAnnotationScenario();
    if (scenario === "validation-error")
      return problem(422, "ANNOTATION_DRAFT_INVALID", "数据结构校验失败", [
        {
          path: "/entries/0/label_code",
          code: "REQUIRED",
          message: "标签不能为空",
        },
        { path: "/future_field", code: "UNKNOWN", message: "未知字段错误摘要" },
      ]);
    if (scenario === "etag-conflict" || scenario === "conflict")
      return problem(412, "PRECONDITION_FAILED", "草稿 ETag 已变化");
    if (scenario === "permission-revoked")
      return problem(403, "FORBIDDEN", "保存权限已撤销");
    const body = (await request.json()) as Record<string, unknown>;
    const expected = Number(body.expected_draft_revision);
    currentDraft = {
      ...makeAnnotationDraft(String(params.taskId), expected + 1),
      entries: body.entries as ReturnType<
        typeof makeAnnotationDraft
      >["entries"],
    };
    currentTask = makeAnnotationTask({
      task_id: String(params.taskId),
      current_draft_revision: expected + 1,
      current_draft_hash: currentDraft.content_hash,
      etag: `"task-rv-${expected + 8}"`,
    });
    const response = {
      data: { task: currentTask, draft: currentDraft },
      scope: annotationFixtureScope,
      request_id: "req_annotation_save_fx",
      contract_version: "data-annotation.v1",
    };
    return replay(request, body, response) ?? HttpResponse.json(response);
  }),
  http.post(commandPath("submit-preflight"), async ({ request, params }) => {
    const invalid = validateWrite(request, params);
    if (invalid) return invalid;
    const body = await request.json();
    const blocked = getAnnotationScenario() === "partial-error";
    const response = {
      data: {
        preflight_id: "preflight_annotation_fx_01",
        task_id: String(params.taskId),
        draft_revision: currentTask.current_draft_revision,
        content_hash: currentTask.current_draft_hash,
        valid: !blocked,
        submission_gate: {
          status: blocked ? "BLOCKED" : "PASS",
          policy_version: "issue-gate-v1",
          issue_watermark: "watermark_fx_02",
          evaluated_at: "2026-08-05T08:12:00Z",
          blocking_issue_ids: blocked ? ["manual_issue_fx_blocking"] : [],
          advisory_issue_ids: [],
          issues: [],
          blocked_reasons: blocked ? ["存在阻断级人工问题"] : [],
        },
        validation_errors: [],
        expires_at: "2026-08-05T08:17:00Z",
      },
      scope: annotationFixtureScope,
      request_id: "req_annotation_preflight_fx",
      contract_version: "data-annotation.v1",
    };
    return replay(request, body, response) ?? HttpResponse.json(response);
  }),
  http.post(commandPath("submit"), async ({ request, params }) => {
    const invalid = validateWrite(request, params);
    if (invalid) return invalid;
    await scenarioDelay();
    if (getAnnotationScenario() === "accepted-job")
      return HttpResponse.json(acceptedAnnotationJobFixture, { status: 202 });
    if (
      getAnnotationScenario() === "etag-conflict" ||
      getAnnotationScenario() === "conflict"
    )
      return problem(412, "PRECONDITION_FAILED", "提交前 Task 已变化");
    const body = await request.json();
    currentTask = makeAnnotationTask({
      task_id: String(params.taskId),
      workflow_status: "SUBMITTED",
      current_submission_id: "submission_fx_02",
      submitted_annotation_set_id: "annotation_set_fx_02",
      allowed_actions: ["REVIEW", "VIEW_ANNOTATION_SET"],
      etag: '"task-rv-11"',
    });
    const response = {
      data: { task: currentTask, submission: {}, annotation_set: {} },
      scope: annotationFixtureScope,
      request_id: "req_annotation_submit_fx",
      contract_version: "data-annotation.v1",
    };
    return (
      replay(request, body, response) ??
      HttpResponse.json(response, { status: 201 })
    );
  }),
  http.post(commandPath("review"), async ({ request, params }) => {
    const invalid = validateWrite(request, params);
    if (invalid) return invalid;
    const body = (await request.json()) as Record<string, unknown>;
    currentTask = makeAnnotationTask({
      task_id: String(params.taskId),
      workflow_status: body.decision === "RETURNED" ? "RETURNED" : "APPROVED",
      current_submission_id: "submission_fx_02",
      submitted_annotation_set_id: "annotation_set_fx_02",
      latest_review_id: "annotation_review_fx_01",
      allowed_actions: [],
      etag: '"task-rv-12"',
    });
    const response = {
      data: {
        task: currentTask,
        submission: {},
        review: {},
        active_draft:
          body.decision === "RETURNED"
            ? makeAnnotationDraft(String(params.taskId), 4)
            : null,
      },
      scope: annotationFixtureScope,
      request_id: "req_annotation_review_fx",
      contract_version: "data-annotation.v1",
    };
    return (
      replay(request, body, response) ??
      HttpResponse.json(response, { status: 201 })
    );
  }),
  http.post(commandPath("rebase"), async ({ request, params }) => {
    const invalid = validateWrite(request, params);
    if (invalid) return invalid;
    const body = await request.json();
    const successor = makeAnnotationTask({
      task_id: annotationFixtureIds.successorTask,
      task_source: "REVISION_REBASE",
      predecessor_task_id: String(params.taskId),
      source: {
        ...annotationTaskFixtures.progress.source,
        base_revision_id: annotationFixtureIds.replacementRevision,
      },
      current_draft_revision: 0,
      allowed_actions: ["EDIT_DRAFT", "SAVE_DRAFT"],
      etag: '"task-rv-1"',
    });
    const response = {
      data: {
        stale_task_id: String(params.taskId),
        successor_task: successor,
        successor_draft: makeAnnotationDraft(successor.task_id, 0),
        migration_mode: "EMPTY",
        migrated_annotation_count: 0,
      },
      scope: annotationFixtureScope,
      request_id: "req_annotation_rebase_fx",
      contract_version: "data-annotation.v1",
    };
    return (
      replay(request, body, response) ??
      HttpResponse.json(response, { status: 201 })
    );
  }),
  // Runtime-generated annotation contracts used by the current P08 queue and
  // workbench. The handlers above intentionally retain the older region-scoped
  // contract for its deterministic legacy scenarios.
  http.get(
    "*/api/v1/projects/:projectId/annotation-tasks",
    ({ request, params }) => {
      const failed = runtimeFailure();
      if (failed) return failed;
      const projectId = String(params.projectId);
      return HttpResponse.json(
        runtimeBundle(projectId, runtimeRegion(request)).tasks,
      );
    },
  ),
  http.get("*/api/v1/annotation-tasks/:taskId/draft", ({ request, params }) => {
    const failed = runtimeFailure();
    if (failed) return failed;
    const bundle = runtimeBundle(
      runtimeProject(request),
      runtimeRegion(request),
      String(params.taskId),
    );
    return bundle.draft
      ? HttpResponse.json(bundle.draft)
      : problem(404, "NOT_FOUND", "标注草稿不存在");
  }),
  http.get(
    "*/api/v1/annotation-tasks/:taskId/history",
    ({ request, params }) => {
      const failed = runtimeFailure();
      if (failed) return failed;
      return HttpResponse.json(
        runtimeBundle(
          runtimeProject(request),
          runtimeRegion(request),
          String(params.taskId),
        ).history,
      );
    },
  ),
  http.get("*/api/v1/annotation-tasks/:taskId", ({ request, params }) => {
    const failed = runtimeFailure();
    if (failed) return failed;
    return HttpResponse.json(
      runtimeBundle(
        runtimeProject(request),
        runtimeRegion(request),
        String(params.taskId),
      ).task,
    );
  }),
  http.get(
    "*/api/v1/projects/:projectId/tag-schemas/:schemaId/versions/:version",
    ({ request, params }) => {
      const failed = runtimeFailure();
      if (failed) return failed;
      const schema = runtimeBundle(
        String(params.projectId),
        runtimeRegion(request),
      ).schema;
      return HttpResponse.json({
        ...schema,
        schema_id: String(params.schemaId),
        version: Number(params.version),
      });
    },
  ),
  http.post(
    "*/api/v1/annotation-tasks/:taskId/claim",
    ({ request, params }) => {
      const failed = runtimeFailure();
      if (failed) return failed;
      const task = runtimeBundle(
        runtimeProject(request),
        runtimeRegion(request),
        String(params.taskId),
      ).task;
      return HttpResponse.json({
        ...task,
        assignee_id: "usr_fx_admin",
        state_version: task.state_version + 1,
        etag: `"${task.task_id}-claimed"`,
      });
    },
  ),
  http.post(
    "*/api/v1/annotation-tasks/:taskId/revisions",
    async ({ request, params }) => {
      const failed = runtimeFailure();
      if (failed) return failed;
      const bundle = runtimeBundle(
        runtimeProject(request),
        runtimeRegion(request),
        String(params.taskId),
      );
      const body = (await request.json()) as Record<string, unknown>;
      const previous = bundle.history.revisions.at(-1)!;
      return HttpResponse.json({
        ...previous,
        revision: Number(body.expected_revision ?? previous.revision) + 1,
        parent_revision: Number(body.expected_revision ?? previous.revision),
        client_mutation_id: String(body.client_mutation_id ?? "save-fixture"),
        operations: Array.isArray(body.operations) ? body.operations : [],
        tags: Array.isArray(body.tags) ? body.tags : previous.tags,
        created_at: "2026-08-18T09:00:00Z",
      });
    },
  ),
  http.post(
    "*/api/v1/annotation-tasks/:taskId/submit",
    async ({ request, params }) => {
      const failed = runtimeFailure();
      if (failed) return failed;
      const bundle = runtimeBundle(
        runtimeProject(request),
        runtimeRegion(request),
        String(params.taskId),
      );
      const body = (await request.json()) as Record<string, unknown>;
      const source = bundle.history.submissions.at(-1)!;
      return HttpResponse.json(
        {
          ...source,
          submission_id: `submission-${String(params.taskId)}-fixture`,
          task_id: String(params.taskId),
          revision: Number(body.expected_revision ?? source.revision),
          created_at: "2026-08-18T09:05:00Z",
        },
        { status: 201 },
      );
    },
  ),
  http.post(
    "*/api/v1/annotation-tasks/:taskId/reviews",
    async ({ request, params }) => {
      const failed = runtimeFailure();
      if (failed) return failed;
      const bundle = runtimeBundle(
        runtimeProject(request),
        runtimeRegion(request),
        String(params.taskId),
      );
      const body = (await request.json()) as Record<string, unknown>;
      const approved = body.decision === "APPROVE";
      return HttpResponse.json({
        ...bundle.task,
        status: approved ? "APPROVED" : "NEEDS_REVISION",
        approved_revision: approved ? Number(body.revision) : null,
        approved_review_id: approved
          ? `review-${String(params.taskId)}-fixture`
          : null,
        state_version: bundle.task.state_version + 1,
        etag: `"${String(params.taskId)}-reviewed"`,
      });
    },
  ),
  http.post("*/api/v1/previews/sessions", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json(
      {
        schema_version: 1,
        session_id: "preview-session-fx-01",
        project_id: String(body.project_id),
        dataset_id: String(body.dataset_id),
        rollout_id: String(body.rollout_id),
        lance_version: String(body.lance_version),
        annotation_revision: Number(body.annotation_revision),
        camera_id: String(body.camera_id),
        view_mode: body.view_mode,
        encoding_profile: "H264_BASELINE",
        media_type: "application/vnd.apple.mpegurl",
        playlist_url: "https://media.fixture.invalid/preview.m3u8",
        signed_url_expires_at: "2026-08-18T12:00:00Z",
        cache_key: "preview-cache-fx-01",
        cache_expires_at: "2026-08-18T12:00:00Z",
        duration_seconds: 600,
        frame_count: 18_000,
        placeholder_count: 0,
        placeholders: [],
        timeline: {
          start_step: 0,
          end_step: 18_000,
          start_ns: 0,
          end_ns: 600_000_000_000,
        },
      },
      { status: 201 },
    );
  }),
  http.post(
    "*/api/v1/projects/:projectId/regions/:regionCode/episode-revisions/:revisionId/streams/:streamId/media-descriptors",
    ({ params }) => {
      if (
        getAnnotationScenario() === "signed-url-expired" &&
        mediaAttempt++ === 0
      )
        return problem(410, "SIGNED_URL_EXPIRED", "媒体授权已过期");
      return HttpResponse.json(
        {
          kind: "rgb-video",
          url: "https://fixture.invalid/media/range.mp4",
          expires_at: "2026-08-05T08:20:00Z",
          revision_id: params.revisionId,
          stream_id: params.streamId,
        },
        {
          headers: {
            "Cache-Control": "private, no-store",
            "Accept-Ranges": "bytes",
          },
        },
      );
    },
  ),
  http.get(
    "*/api/v1/projects/:projectId/regions/:regionCode/annotation-events",
    () =>
      getAnnotationScenario() === "sse-disconnected"
        ? problem(503, "SSE_DISCONNECTED", "SSE 已断开，客户端应降级轮询")
        : HttpResponse.json({ events: [] }),
  ),
];

export function resetAnnotationHandlerState(): void {
  currentTask = annotationTaskFixtures.progress;
  currentDraft = makeAnnotationDraft();
  rateAttempt = 0;
  offlineAttempt = 0;
  mediaAttempt = 0;
  idempotency.clear();
}

export default annotationHandlers;
