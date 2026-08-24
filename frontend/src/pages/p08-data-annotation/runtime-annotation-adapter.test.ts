import { afterEach, describe, expect, it, vi } from "vitest";
import { createDomainError } from "../../shared/api/domain-error";
import { request } from "../../shared/api/http-client";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { createPlaybackClock } from "../../features/viewer";
import {
  applyRuntimeAutoAnnotationJob,
  buildRuntimeWorkbenchAdapter,
  createRuntimeAutoAnnotationJob,
  listRuntimeAnnotationRevisionThreads,
  loadRuntimeAutoAnnotationCapability,
  loadRuntimeAnnotationBundle,
  restoreRuntimeAnnotationRevision,
  runtimeAnnotationScopeFromShell,
  saveRuntimeAnnotationDraft,
} from "./runtime-annotation-adapter";
import {
  createVisualAnnotationBundle,
  visualAnnotationScope,
} from "./testing/annotation-fixture";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));

const requestMock = vi.mocked(request);

afterEach(() => {
  vi.clearAllMocks();
  resetRuntimeConfigForTests();
});

function installRuntime(apiBaseUrl = "/api/v1"): void {
  configureRuntime({
    apiBaseUrl,
    sseBaseUrl: "/api/v1/events",
    buildVersion: "test",
    releaseEnv: "test",
  });
}

function installBundleResponses(
  playlistUrl = "https://media.invalid/preview.m3u8",
) {
  const fixture = createVisualAnnotationBundle({
    mode: "annotation",
    cameraCount: 4,
  });
  requestMock.mockImplementation(async (options) => {
    if (options.path === `/annotation-tasks/${fixture.task.task_id}`)
      return fixture.task as never;
    if (
      options.path ===
      `/projects/${visualAnnotationScope.projectId}/annotation-tasks`
    )
      return fixture.tasks as never;
    if (options.path.endsWith("/draft")) return fixture.draft as never;
    if (options.path.endsWith("/history")) return fixture.history as never;
    if (options.path.includes("/tag-schemas/")) return fixture.schema as never;
    if (options.path.endsWith("/manifest-discovery"))
      return fixture.manifest as never;
    if (options.path === "/previews/sessions")
      return {
        project_id: fixture.task.project_id,
        rollout_id: fixture.task.rollout_id,
        camera_id: fixture.manifest?.cameras[0]?.camera_id,
        annotation_revision: fixture.draft?.revision,
        playlist_url: playlistUrl,
        signed_url_expires_at: "2026-08-18T12:00:00Z",
      } as never;
    throw new Error(`Unexpected request ${options.method} ${options.path}`);
  });
  return fixture;
}

describe("P08 formal runtime annotation adapter", () => {
  it("accepts the real SessionBootstrap scope when organization is absent", () => {
    expect(
      runtimeAnnotationScopeFromShell({
        organizationId: "",
        projectId: "project-real",
        regionCode: "cn-real",
      }),
    ).toEqual({
      organizationId: "",
      projectId: "project-real",
      regionCode: "cn-real",
    });
  });

  it("loads one strict, scope-bound revision page through the generated operation", async () => {
    requestMock.mockResolvedValue({
      items: [
        {
          task_id: "task-revision-1",
          project_id: visualAnnotationScope.projectId,
          region_code: visualAnnotationScope.regionCode,
          dataset_id: "dataset-revision-1",
          dataset_version: 4,
          rollout_id: "rollout-revision-1",
          status: "SUBMITTED",
          latest_revision: {
            revision: 3,
            origin: "ANNOTATION",
            author_id: "annotator-1",
            content_hash: "a".repeat(64),
            created_at: "2026-08-20T08:00:00Z",
          },
          submitted_revision: 3,
          current_submission_id: "submission-revision-1",
          approved_revision: null,
          approved_review_id: null,
          legacy_draft_id: null,
          updated_at: "2026-08-20T08:01:00Z",
        },
      ],
      page_info: {
        has_next_page: true,
        has_previous_page: false,
        start_cursor: null,
        end_cursor: "signed-next-cursor",
      },
      snapshot_at: "2026-08-20T08:02:00Z",
    } as never);

    const result = await listRuntimeAnnotationRevisionThreads(
      visualAnnotationScope,
      {
        status: "SUBMITTED",
        origin: "ANNOTATION",
        legacyDraftId: "draft_legacy-01",
        limit: 20,
      },
    );

    expect(result.items[0]?.task_id).toBe("task-revision-1");
    expect(requestMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "GET",
        path: "/annotations/revisions",
        scope: visualAnnotationScope,
        query: {
          status: "SUBMITTED",
          origin: "ANNOTATION",
          legacy_draft_id: "draft_legacy-01",
          after: undefined,
          limit: 20,
        },
      }),
    );
  });

  it("fails closed when a revision page crosses the selected scope", async () => {
    requestMock.mockResolvedValue({
      items: [
        {
          task_id: "task-revision-other",
          project_id: "other-project",
          region_code: visualAnnotationScope.regionCode,
          dataset_id: "dataset-revision-1",
          dataset_version: 4,
          rollout_id: "rollout-revision-1",
          status: "DRAFT",
          latest_revision: {
            revision: 0,
            origin: "ANNOTATION",
            author_id: "annotator-1",
            content_hash: "a".repeat(64),
            created_at: "2026-08-20T08:00:00Z",
          },
          updated_at: "2026-08-20T08:01:00Z",
        },
      ],
      page_info: {
        has_next_page: false,
        has_previous_page: false,
      },
      snapshot_at: "2026-08-20T08:02:00Z",
    } as never);

    await expect(
      listRuntimeAnnotationRevisionThreads(visualAnnotationScope),
    ).rejects.toMatchObject({ code: "CONTRACT_MISMATCH" });
  });

  it("loads task, draft, history, fixed Schema and Manifest without Browser Mock fallback", async () => {
    const fixture = installBundleResponses();
    const bundle = await loadRuntimeAnnotationBundle(
      visualAnnotationScope,
      fixture.task.task_id,
      "annotation",
    );
    expect(bundle.task.task_id).toBe(fixture.task.task_id);
    expect(bundle.schema.version).toBe(fixture.task.tag_schema_version);
    expect(bundle.manifest?.cameras).toHaveLength(4);
    expect(bundle.manifestIssue).toBeNull();
    expect(requestMock.mock.calls.map(([options]) => options.path)).toContain(
      `/projects/${visualAnnotationScope.projectId}/regions/${visualAnnotationScope.regionCode}/annotation-tasks/${fixture.task.task_id}/manifest-discovery`,
    );
  });

  it("keeps a real Manifest network failure local and exposes its request ID", async () => {
    const fixture = installBundleResponses();
    requestMock.mockImplementation(async (options) => {
      if (options.path.endsWith("/manifest-discovery")) {
        throw createDomainError({
          code: "NETWORK_ERROR",
          message: "Manifest 网络失败",
          fieldErrors: [],
          operationErrors: [],
          blockedReasons: [],
          requestId: "manifest-request-1",
          retryable: true,
          httpStatus: null,
        });
      }
      if (options.path === `/annotation-tasks/${fixture.task.task_id}`)
        return fixture.task as never;
      if (
        options.path ===
        `/projects/${visualAnnotationScope.projectId}/annotation-tasks`
      )
        return fixture.tasks as never;
      if (options.path.endsWith("/draft")) return fixture.draft as never;
      if (options.path.endsWith("/history")) return fixture.history as never;
      if (options.path.includes("/tag-schemas/"))
        return fixture.schema as never;
      throw new Error(`Unexpected request ${options.path}`);
    });
    const bundle = await loadRuntimeAnnotationBundle(
      visualAnnotationScope,
      fixture.task.task_id,
      "annotation",
    );
    expect(bundle.manifest).toBeNull();
    expect(bundle.manifestIssue).toMatchObject({
      message: "Manifest 网络失败",
      requestId: "manifest-request-1",
    });
  });

  it("sends generated SaveDraftRequest fields with If-Match and no legacy wire envelope", async () => {
    const fixture = createVisualAnnotationBundle({ mode: "annotation" });
    requestMock.mockResolvedValue(fixture.history.revisions.at(-1) as never);
    await saveRuntimeAnnotationDraft(
      visualAnnotationScope,
      fixture.task,
      fixture.draft!,
      fixture.draft!.tags,
    );
    expect(requestMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "POST",
        path: `/annotation-tasks/${fixture.task.task_id}/revisions`,
        ifMatch: fixture.task.etag,
        body: expect.objectContaining({
          expected_revision: fixture.task.current_revision,
          operations: fixture.draft!.operations,
          tags: fixture.draft!.tags,
        }),
      }),
    );
  });

  it("restores a historical revision through the formal append-only endpoint", async () => {
    const fixture = createVisualAnnotationBundle({ mode: "annotation" });
    requestMock.mockResolvedValue({
      ...(fixture.history.revisions.at(-1) as object),
      origin: "ANNOTATION_RESTORE",
      parent_revision: fixture.task.current_revision,
      revision: fixture.task.current_revision + 1,
    } as never);

    await restoreRuntimeAnnotationRevision(
      visualAnnotationScope,
      fixture.task,
      0,
    );

    expect(requestMock).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "POST",
        path: `/annotation-tasks/${fixture.task.task_id}/revisions:restore`,
        ifMatch: fixture.task.etag,
        body: expect.objectContaining({
          expected_revision: fixture.task.current_revision,
          target_revision: 0,
        }),
      }),
    );
  });

  it("loads a strict provider capability and creates a source-revision-bound durable job", async () => {
    const fixture = createVisualAnnotationBundle({ mode: "annotation" });
    requestMock
      .mockResolvedValueOnce({
        enabled: true,
        code: null,
        providers: [{ provider: "vlm", models: ["vision-v1"] }],
        max_concurrent_jobs_per_project: 4,
        max_jobs_per_hour: 60,
        daily_cost_limit_micros: 5_000_000,
      } as never)
      .mockResolvedValueOnce({
        job_id: "71b98020-d722-4b3b-960c-65bb1fd89ed1",
        project_id: visualAnnotationScope.projectId,
        region_code: visualAnnotationScope.regionCode,
        task_id: fixture.task.task_id,
        source_revision: fixture.task.current_revision,
        provider: "vlm",
        model: "vision-v1",
        input_selection: {
          start_step: 10,
          end_step: 20,
          modalities: [],
        },
        status: "QUEUED",
        progress_percent: 0,
        estimated_cost_micros: 1_000,
        created_by: "annotator",
        created_at: "2026-08-24T03:00:00Z",
        updated_at: "2026-08-24T03:00:00Z",
      } as never);

    const capability = await loadRuntimeAutoAnnotationCapability(
      visualAnnotationScope,
    );
    const job = await createRuntimeAutoAnnotationJob(
      visualAnnotationScope,
      fixture.task,
      {
        provider: capability.providers[0]!.provider,
        model: capability.providers[0]!.models[0]!,
        startStep: 10,
        endStep: 20,
      },
    );

    expect(job.status).toBe("QUEUED");
    expect(requestMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        method: "POST",
        path: `/annotation-tasks/${fixture.task.task_id}/auto-annotation`,
        idempotencyKey: expect.stringMatching(/^auto-annotation-/u),
        body: {
          revision: fixture.task.current_revision,
          provider: "vlm",
          model: "vision-v1",
          input_selection: {
            start_step: 10,
            end_step: 20,
            modalities: [],
          },
        },
      }),
    );
  });

  it("fails closed on contradictory provider capability and applies results with task CAS", async () => {
    requestMock.mockResolvedValueOnce({
      enabled: true,
      code: null,
      providers: [],
      max_concurrent_jobs_per_project: 4,
      max_jobs_per_hour: 60,
      daily_cost_limit_micros: 5_000_000,
    } as never);
    await expect(
      loadRuntimeAutoAnnotationCapability(visualAnnotationScope),
    ).rejects.toMatchObject({ code: "CONTRACT_MISMATCH" });

    const fixture = createVisualAnnotationBundle({ mode: "annotation" });
    const job = {
      job_id: "71b98020-d722-4b3b-960c-65bb1fd89ed1",
      project_id: visualAnnotationScope.projectId,
      region_code: visualAnnotationScope.regionCode,
      task_id: fixture.task.task_id,
      source_revision: fixture.task.current_revision,
      provider: "vlm",
      model: "vision-v1",
      input_selection: { start_step: 0, end_step: 100, modalities: [] },
      status: "SUCCEEDED" as const,
      progress_percent: 100,
      tags: fixture.draft!.tags,
      operations: fixture.draft!.operations,
      usage: { input_units: 100, output_units: 1, cost_micros: 1_000 },
      estimated_cost_micros: 1_000,
      created_by: "annotator",
      created_at: "2026-08-24T03:00:00Z",
      updated_at: "2026-08-24T03:00:01Z",
    };
    requestMock.mockResolvedValueOnce({
      ...fixture.history.revisions.at(-1),
      revision: fixture.task.current_revision + 1,
      parent_revision: fixture.task.current_revision,
    } as never);

    await applyRuntimeAutoAnnotationJob(
      visualAnnotationScope,
      fixture.task,
      job,
    );

    expect(requestMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        method: "POST",
        path: `/annotation-tasks/${fixture.task.task_id}/auto-annotation-jobs/${job.job_id}:apply`,
        ifMatch: fixture.task.etag,
        body: { expected_revision: fixture.task.current_revision },
      }),
    );
  });

  it("authorizes preview media only through the formal preview session operation", async () => {
    installRuntime();
    const fixture = installBundleResponses();
    const bundle = await loadRuntimeAnnotationBundle(
      visualAnnotationScope,
      fixture.task.task_id,
      "annotation",
    );
    const clock = createPlaybackClock({ startNs: "0", endNs: "1000000000" });
    const adapter = buildRuntimeWorkbenchAdapter({
      bundle,
      scope: visualAnnotationScope,
      mode: "annotation",
      clock,
      tags: bundle.draft?.tags ?? [],
      readOnly: false,
    });
    const controller = new AbortController();
    const media = await adapter.cameraStreams[0]?.mediaSource?.authorize(
      controller.signal,
    );
    expect(media?.url).toBe("https://media.invalid/preview.m3u8");
    expect(requestMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        method: "POST",
        path: "/previews/sessions",
        body: expect.objectContaining({
          camera_id: fixture.manifest?.cameras[0]?.camera_id,
          annotation_revision: fixture.draft?.revision,
          view_mode: "edited",
        }),
      }),
    );
    clock.dispose();
  });

  it("resolves a relative signed media capability on the configured API origin", async () => {
    installRuntime("https://api.example.test/api/v1");
    const fixture = installBundleResponses(
      "/api/v1/previews/sessions/s1/media/index.m3u8?expires=1&sig=a",
    );
    const bundle = await loadRuntimeAnnotationBundle(
      visualAnnotationScope,
      fixture.task.task_id,
      "annotation",
    );
    const clock = createPlaybackClock({ startNs: "0", endNs: "1000000000" });
    const adapter = buildRuntimeWorkbenchAdapter({
      bundle,
      scope: visualAnnotationScope,
      mode: "annotation",
      clock,
      tags: bundle.draft?.tags ?? [],
      readOnly: false,
    });
    const media = await adapter.cameraStreams[0]?.mediaSource?.authorize(
      new AbortController().signal,
    );

    expect(media?.url).toBe(
      "https://api.example.test/api/v1/previews/sessions/s1/media/index.m3u8?expires=1&sig=a",
    );
    clock.dispose();
  });
});
