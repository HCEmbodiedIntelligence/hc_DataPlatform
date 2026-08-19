// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { IngestScope } from "../../entities/data-source";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import { loadFormalUploadDetail } from "./formal-detail-client";

const scope: IngestScope = {
  organizationId: "org-a",
  projectId: "project-a",
  regionCode: "cn-test",
};

const session = {
  session_id: "session-a",
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
};

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
  useShellStore.getState().setScope(scope);
  useShellStore
    .getState()
    .setSession(
      { actorId: "actor-a", displayName: "测试用户", roleIds: [] },
      "session-token",
    );
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
    const fetchMock = vi.fn(
      (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/upload-sessions/session-a/manifest")) {
        return Promise.resolve(json(manifest));
      }
      if (url.endsWith("/upload-sessions/session-a")) {
        return sessionResponse;
      }
      if (url.endsWith("/rollouts/rollout-a/quality")) {
        return Promise.resolve(json(quality));
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    const pending = loadFormalUploadDetail(scope, session.session_id);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      "/api/v1/projects/project-a/regions/cn-test/upload-sessions/session-a/manifest",
      "/api/v1/projects/project-a/regions/cn-test/upload-sessions/session-a",
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
});
