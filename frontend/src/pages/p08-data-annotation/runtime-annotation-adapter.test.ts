import { afterEach, describe, expect, it, vi } from "vitest";
import { createDomainError } from "../../shared/api/domain-error";
import { request } from "../../shared/api/http-client";
import { createPlaybackClock } from "../../features/viewer";
import {
  buildRuntimeWorkbenchAdapter,
  loadRuntimeAnnotationBundle,
  runtimeAnnotationScopeFromShell,
  saveRuntimeAnnotationDraft,
} from "./runtime-annotation-adapter";
import {
  createVisualAnnotationBundle,
  visualAnnotationScope,
} from "./testing/annotation-fixture";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));

const requestMock = vi.mocked(request);

afterEach(() => vi.clearAllMocks());

function installBundleResponses() {
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
    if (options.path.endsWith("/upload-sessions"))
      return {
        total: 1,
        items: [
          {
            rollout_id: fixture.task.rollout_id,
            session_id: "upload-session-1",
            project_id: visualAnnotationScope.projectId,
            region_code: visualAnnotationScope.regionCode,
          },
        ],
      } as never;
    if (options.path.endsWith("/upload-sessions/upload-session-1/manifest"))
      return {
        manifest: {
          rollout_id: fixture.task.rollout_id,
          project_id: fixture.task.project_id,
        },
        discovery: fixture.manifest,
      } as never;
    if (options.path === "/previews/sessions")
      return {
        project_id: fixture.task.project_id,
        rollout_id: fixture.task.rollout_id,
        camera_id: fixture.manifest?.cameras[0]?.camera_id,
        annotation_revision: fixture.draft?.revision,
        playlist_url: "https://media.invalid/preview.m3u8",
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
      `/projects/${visualAnnotationScope.projectId}/regions/${visualAnnotationScope.regionCode}/upload-sessions/upload-session-1/manifest`,
    );
  });

  it("keeps a real Manifest network failure local and exposes its request ID", async () => {
    const fixture = installBundleResponses();
    requestMock.mockImplementation(async (options) => {
      if (options.path.endsWith("/upload-sessions")) {
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

  it("authorizes preview media only through the formal preview session operation", async () => {
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
});
