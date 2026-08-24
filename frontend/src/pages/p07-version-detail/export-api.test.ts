// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  authorizePublishedExportDownload,
  cancelPublishedExport,
  createPublishedExport,
  fetchPublishedExport,
  retryPublishedExport,
} from "./export-api";
import { versionDetailQueryCodec } from "./query-codec";

const target = {
  projectId: "project-a",
  datasetId: "dataset-a",
  datasetVersion: "version-a",
} as const;

const job = {
  job_id:
    "export:v1:project-a:dataset-a%2Fversion-a%2Flance_snapshot%2Fattempt-a",
  project_id: target.projectId,
  dataset_id: target.datasetId,
  dataset_version: target.datasetVersion,
  format: "lance_snapshot",
  attempt_id: "attempt-a",
  status: "SUCCEEDED",
  stage: "completed",
  progress: {
    phase: "completed",
    completed_phases: 3,
    total_phases: 3,
  },
  cancellation_requested: false,
  result: {
    format: "lance_snapshot",
    project_id: target.projectId,
    dataset_id: target.datasetId,
    dataset_version: target.datasetVersion,
    manifest_content_hash: "a".repeat(64),
    attempt_id: "attempt-a",
    artifact_content_hash: "b".repeat(64),
    row_count: 4,
    media_type: "application/vnd.hc.lance-snapshot+json",
  },
  error_code: null,
  error_message: null,
  created_at: "2026-08-20T00:00:00Z",
  updated_at: "2026-08-20T00:01:00Z",
} as const;

beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "/api/v1",
    sseBaseUrl: "/api/v1",
    buildVersion: "p07-export-test",
    releaseEnv: "test",
  });
  useShellStore
    .getState()
    .setSession(
      { actorId: "actor-a", displayName: "测试用户", roleIds: [] },
      "session-token",
    );
  useShellStore.getState().setScope({
    organizationId: "org-a",
    projectId: target.projectId,
    regionCode: "cn-test",
  });
});

describe("P07 export job URL state", () => {
  it("retains the complete four-part Temporal workflow ID", () => {
    const parsed = versionDetailQueryCodec.parse(
      `tab=exports&exportJobId=${encodeURIComponent(job.job_id)}`,
    );

    expect(parsed.exportJobId).toBe(job.job_id);
    expect(
      versionDetailQueryCodec.withChanges(
        versionDetailQueryCodec.parse("tab=exports"),
        { exportJobId: job.job_id },
      ).exportJobId,
    ).toBe(job.job_id);
  });

  it("drops a malformed export workflow identity", () => {
    expect(
      versionDetailQueryCodec.parse(
        "tab=exports&exportJobId=export%3Av1%3Amissing-resource",
      ).exportJobId,
    ).toBeUndefined();
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  resetRuntimeConfigForTests();
});

describe("P07 published export API", () => {
  it("creates a durable export with scope, bearer and idempotency headers", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify(job), {
          status: 202,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createPublishedExport({
      ...target,
      format: "lance_snapshot",
      idempotencyKey: "p07-export-create",
    });

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/api/v1/datasets/dataset-a/versions/version-a/exports");
    expect(init?.method).toBe("POST");
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer session-token");
    expect(headers.get("X-Organization-Id")).toBe("org-a");
    expect(headers.get("X-Project-Id")).toBe(target.projectId);
    expect(headers.get("X-Region-Code")).toBe("cn-test");
    expect(headers.get("Idempotency-Key")).toBe("p07-export-create");
    expect(JSON.parse(String(init?.body))).toEqual({
      project_id: target.projectId,
      format: "lance_snapshot",
    });
  });

  it("reads a task and mints a fresh download only through its authorization endpoint", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/download")) {
        return new Response(
          JSON.stringify({
            job_id: job.job_id,
            format: "lance_snapshot",
            download_url:
              "https://object.example.test/export?X-Amz-Signature=opaque",
            expires_at: "2026-08-20T00:16:00Z",
            artifact_content_hash: "b".repeat(64),
            media_type: "application/vnd.hc.lance-snapshot+json",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify(job), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const status = await fetchPublishedExport({ ...target, jobId: job.job_id });
    const authorization = await authorizePublishedExportDownload({
      ...target,
      jobId: job.job_id,
    });

    expect(status.result?.artifact_content_hash).toBe("b".repeat(64));
    expect(authorization.download_url).toContain("X-Amz-Signature=opaque");
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      `/api/v1/datasets/dataset-a/versions/version-a/exports/${encodeURIComponent(job.job_id)}?project_id=project-a`,
      `/api/v1/datasets/dataset-a/versions/version-a/exports/${encodeURIComponent(job.job_id)}/download?project_id=project-a`,
    ]);
  });

  it("fails closed when a task response leaks a physical locator", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({ ...job, artifact_uri: "s3://private/forbidden" }),
            {
              status: 200,
              headers: { "Content-Type": "application/json" },
            },
          ),
      ),
    );

    await expect(
      fetchPublishedExport({ ...target, jobId: job.job_id }),
    ).rejects.toMatchObject({
      code: "CONTRACT_MISMATCH",
    });
  });

  it("uses task-local cancellation and retry endpoints without retaining a download grant", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ ...job, status: "CANCELLED" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await cancelPublishedExport({ ...target, jobId: job.job_id });
    await retryPublishedExport({
      ...target,
      jobId: job.job_id,
      idempotencyKey: "p07-export-retry",
    });

    const [cancelUrl, cancelInit] = fetchMock.mock.calls[0] ?? [];
    const [retryUrl, retryInit] = fetchMock.mock.calls[1] ?? [];
    expect(cancelUrl).toBe(
      `/api/v1/datasets/dataset-a/versions/version-a/exports/${encodeURIComponent(job.job_id)}:cancel?project_id=project-a`,
    );
    expect(cancelInit?.method).toBe("POST");
    expect(retryUrl).toBe(
      `/api/v1/datasets/dataset-a/versions/version-a/exports/${encodeURIComponent(job.job_id)}:retry?project_id=project-a`,
    );
    expect(retryInit?.method).toBe("POST");
    expect(new Headers(retryInit?.headers).get("Idempotency-Key")).toBe(
      "p07-export-retry",
    );
  });
});
