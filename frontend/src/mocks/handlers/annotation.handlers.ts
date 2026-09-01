import { http, HttpResponse, passthrough } from "msw";
import { annotationFixtureScope } from "../fixtures/annotation";
import { getAnnotationScenario } from "../scenarios/annotation";
import { formalUploadSessionFixture } from "../fixtures/ingest";
import { createVisualAnnotationBundle } from "../../pages/p08-data-annotation/testing/annotation-fixture";

let mediaAttempt = 0;

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
  http.post("*/api/v1/aligned-media/authorize", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json(
      {
        schema_version: "aligned-media-authorization/v1",
        artifact_id: "aligned-media-fx-01",
        artifact_key: "a".repeat(64),
        project_id: String(body.project_id),
        dataset_id: String(body.dataset_id),
        rollout_id: String(body.rollout_id),
        dataset_version: Number(body.dataset_version),
        camera_id: String(body.camera_id),
        media_url: "https://media.fixture.invalid/aligned-media.mp4",
        content_type: "video/mp4",
        expires_at: "2026-08-18T12:00:00Z",
        fps: 30,
        duration_seconds: 600,
        frame_count: 18_000,
        width: 1280,
        height: 720,
        timeline: {
          fps: 30,
          frame_count: 18_000,
          first_step: 0,
          pts_time_base_numerator: 1,
          pts_time_base_denominator: 30,
          start_timestamp_ns: "0",
        },
        alignment_version: "causal-30hz-v1",
        profile_id: "canonical-h264-crf20-v1",
        profile_version: "1",
      },
      { status: 200 },
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
  mediaAttempt = 0;
}

export default annotationHandlers;
