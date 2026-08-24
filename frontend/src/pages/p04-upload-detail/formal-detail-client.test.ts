// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { IngestScope } from "../../entities/data-source";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  getFormalUploadProcessingStatus,
  getFormalUploadRawMedia,
  loadFormalUploadDetail,
  resolveFormalUploadPreviewTarget,
  resolveFormalUploadViewerTarget,
  type FormalUploadSession,
} from "./formal-detail-client";

const scope: IngestScope = {
  organizationId: "org-a",
  projectId: "project-a",
  regionCode: "cn-test",
};

const session = {
  session_id: "52faee8f-f489-4c19-8b54-38a51cf46899",
  project_id: scope.projectId,
  region_code: scope.regionCode,
  rollout_id: "rollout-a",
  data_package_id: "package-a",
  manifest_fingerprint: "manifest-fingerprint-a",
  expected_crc64: "1",
  expected_sha256: "sha256-a",
  expected_size: 1024,
  object_key: "objects/package-a.mcap",
  source_type: "BROWSER_MULTIPART",
  status: "RAW_COMMITTED",
  workflow: {
    event_id: "a1111111-1111-4111-8111-111111111111",
    workflow_id: "ingest-rollout/project-a/rollout-a",
    status: "DISPATCHED",
    attempts: 1,
    updated_at: "2026-08-21T08:00:00Z",
  },
} satisfies FormalUploadSession;

const manifest = {
  identifiers: {
    data_package_id: session.data_package_id,
    robot_id: "robot-a",
    collection_session_id: "collection-a",
    recording_request_id: "recording-a",
  },
  manifest_fingerprint: session.manifest_fingerprint,
  manifest: {
    project_id: scope.projectId,
    rollout_id: session.rollout_id,
  },
  discovery: {
    source: "MANIFEST",
    read_only: true,
    cameras: [],
    topics: [],
    missing_expected_topics: [],
  },
};

const quality = {
  rollout_id: session.rollout_id,
  status: "PASS",
  findings: [],
  topic_metrics: [],
  start_ns: 0,
  end_ns: 1,
  duration_ns: 1,
};

function json(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "/api/v1",
    sseBaseUrl: "/api/v1",
    buildVersion: "p04-test",
    releaseEnv: "test",
  });
  useShellStore
    .getState()
    .setSession(
      { actorId: "actor-a", displayName: "测试用户", roleIds: [] },
      "session-token",
    );
  useShellStore.getState().setScope(scope);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  useShellStore.getState().setSession(null, null);
  resetRuntimeConfigForTests();
});

describe("P04 formal upload detail client", () => {
  it("starts session and Manifest reads together, then loads QC from the returned rollout", async () => {
    let resolveSession!: (response: Response) => void;
    const sessionResponse = new Promise<Response>((resolve) => {
      resolveSession = resolve;
    });
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith(`/upload-sessions/${session.session_id}/manifest`)) {
        return Promise.resolve(json(manifest));
      }
      if (url.endsWith(`/upload-sessions/${session.session_id}`)) {
        return sessionResponse;
      }
      if (url.endsWith("/rollouts/rollout-a/quality")) {
        return Promise.resolve(json(quality));
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    const pending = loadFormalUploadDetail(scope, session.session_id);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      `/api/v1/projects/project-a/regions/cn-test/upload-sessions/${session.session_id}/manifest`,
      `/api/v1/projects/project-a/regions/cn-test/upload-sessions/${session.session_id}`,
    ]);

    resolveSession(json(session));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const result = await pending;

    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      "/api/v1/projects/project-a/regions/cn-test/rollouts/rollout-a/quality",
    );
    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer session-token");
    expect(headers.get("X-Project-Id")).toBe(scope.projectId);
    expect(headers.get("X-Region-Code")).toBe(scope.regionCode);
    expect(result).toMatchObject({ session, manifest, quality });
  });

  it("fails closed when the session response crosses the active scope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/manifest")) return Promise.resolve(json(manifest));
        return Promise.resolve(
          json({ ...session, project_id: "project-other" }),
        );
      }),
    );

    await expect(
      loadFormalUploadDetail(scope, session.session_id),
    ).rejects.toMatchObject({
      code: "CONTRACT_MISMATCH",
      retryable: false,
    });
  });

  it("fails closed when Manifest or QC identity differs from the session", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        json({
          ...manifest,
          identifiers: {
            ...manifest.identifiers,
            data_package_id: "package-other",
          },
        }),
      )
      .mockResolvedValueOnce(json(session))
      .mockResolvedValueOnce(json(quality));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      loadFormalUploadDetail(scope, session.session_id),
    ).rejects.toMatchObject({ code: "CONTRACT_MISMATCH" });
  });

  it("gets an authorized Raw source with the active scoped credentials", async () => {
    const source = {
      schema_version: "raw-media-source/v1",
      format: "MCAP",
      media_type: "application/x-mcap",
      download_url: "https://objects.example.test/signed/raw.mcap",
      expires_at: "2026-08-20T10:00:00Z",
      byte_length: 1024,
      sha256: "a".repeat(64),
    };
    const fetchMock = vi.fn().mockResolvedValue(json(source));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      getFormalUploadRawMedia(scope, session.session_id),
    ).resolves.toEqual(source);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `/api/v1/projects/project-a/regions/cn-test/upload-sessions/${session.session_id}/raw-media`,
    );
    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer session-token");
    expect(headers.get("X-Organization-Id")).toBe(scope.organizationId);
    expect(headers.get("X-Project-Id")).toBe(scope.projectId);
    expect(headers.get("X-Region-Code")).toBe(scope.regionCode);
  });

  it("loads the exact ingest workflow and derives a fixed Lance preview target", async () => {
    const processing = {
      schema_version: "upload-processing-status/v1",
      session_id: session.session_id,
      rollout_id: session.rollout_id,
      workflow_id: session.workflow.workflow_id,
      status: "SUCCEEDED",
      stage: "succeeded",
      attempt: 1,
      preview: {
        schema_version: "upload-preview-target/v1",
        project_id: scope.projectId,
        dataset_id: "dataset_ingest_a",
        rollout_id: session.rollout_id,
        dataset_version: 4,
        lance_version: 7,
        annotation_task_id: "annotation-a",
        frequency_hz: 30,
        start_step: 0,
        end_step: 300,
      },
      viewer: {
        schema_version: "dataset-ingest-viewer-target/v1",
        dataset_id: "dataset_ingest_a",
        version_id: "version_lance_4",
        episode_id: "episode_ingest_a",
        revision_id: "revision_ingest_a",
      },
      error_code: null,
      updated_at: "2026-08-21T08:01:00Z",
    };
    const fetchMock = vi.fn().mockResolvedValue(json(processing));
    vi.stubGlobal("fetch", fetchMock);

    const loaded = await getFormalUploadProcessingStatus(scope, session);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/projects/project-a/regions/cn-test/upload-sessions/52faee8f-f489-4c19-8b54-38a51cf46899/processing",
    );
    expect(resolveFormalUploadPreviewTarget(session, loaded)).toEqual({
      datasetId: "dataset_ingest_a",
      datasetVersion: 4,
      lanceVersion: 7,
      annotationTaskId: "annotation-a",
      frequencyHz: 30,
      startStep: 0,
      endStep: 300,
    });
    expect(resolveFormalUploadViewerTarget(session, loaded)).toEqual({
      datasetId: "dataset_ingest_a",
      versionId: "version_lance_4",
      episodeId: "episode_ingest_a",
      revisionId: "revision_ingest_a",
    });
  });

  it("fails closed when a successful workflow result crosses upload lineage", async () => {
    const processing = {
      schema_version: "upload-processing-status/v1",
      session_id: session.session_id,
      rollout_id: session.rollout_id,
      workflow_id: session.workflow.workflow_id,
      status: "SUCCEEDED",
      stage: "succeeded",
      attempt: 1,
      preview: {
        schema_version: "upload-preview-target/v1",
        project_id: "project-other",
        dataset_id: "dataset_ingest_a",
        rollout_id: session.rollout_id,
        dataset_version: 4,
        lance_version: 7,
        annotation_task_id: "annotation-a",
        frequency_hz: 30,
        start_step: 0,
        end_step: 300,
      },
      viewer: {
        schema_version: "dataset-ingest-viewer-target/v1",
        dataset_id: "dataset_ingest_a",
        version_id: "version_lance_4",
        episode_id: "episode_ingest_a",
        revision_id: "revision_ingest_a",
      },
      error_code: null,
      updated_at: "2026-08-21T08:01:00Z",
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(processing)));

    await expect(
      getFormalUploadProcessingStatus(scope, session),
    ).rejects.toMatchObject({ code: "CONTRACT_MISMATCH" });
  });
});
