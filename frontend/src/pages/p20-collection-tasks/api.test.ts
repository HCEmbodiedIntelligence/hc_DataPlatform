// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import { collectionTaskGateway, type CollectionTaskScope } from "./api";

const scope: CollectionTaskScope = {
  organizationId: "org-a",
  projectId: "project-a",
  regionCode: "cn-test",
};

const taskResponse = {
  schema_version: "1",
  collection_task_id: "task-a",
  project_id: scope.projectId,
  task_code: "00000001",
  name: "采集任务",
  type: "抓取采集",
  scenario: "测试工位",
  description: "",
  target: null,
  quality_threshold: null,
  status: "ACTIVE",
} as const;

beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "/api/v1",
    sseBaseUrl: "/api/v1",
    buildVersion: "p20-test",
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
  resetRuntimeConfigForTests();
});

describe("P20 generated-contract gateway", () => {
  it("sends only confirmed create fields with scope and idempotency headers", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify(taskResponse), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await collectionTaskGateway.create(
      scope,
      {
        name: "采集任务",
        type: "抓取采集",
        scenario: "测试工位",
        description: "",
        target: null,
        quality_threshold: null,
      },
      "create-idempotency-key",
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/api/v1/projects/project-a/collection-tasks");
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer session-token");
    expect(headers.get("Idempotency-Key")).toBe("create-idempotency-key");
    expect(headers.get("X-Project-Id")).toBe(scope.projectId);
    expect(headers.get("X-Region-Code")).toBe(scope.regionCode);
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual([
      "description",
      "name",
      "quality_threshold",
      "scenario",
      "target",
      "type",
    ]);
    for (const forbidden of [
      "assignment",
      "assignee",
      "person",
      "pico",
      "robot",
      "device",
      "start_at",
      "end_at",
      "pause",
      "resume",
      "required_modalities",
      "required_topics",
      "topics",
    ]) {
      expect(body).not.toHaveProperty(forbidden);
    }
  });

  it.each([
    [409, "VERSION_CONFLICT"],
    [422, "VALIDATION_ERROR"],
    [429, "RATE_LIMITED"],
  ] as const)(
    "preserves RFC 9457 details for HTTP %s",
    async (status, code) => {
      vi.stubGlobal(
        "fetch",
        vi.fn(
          async () =>
            new Response(
              JSON.stringify({
                type: "about:blank",
                title: "Request rejected",
                status,
                detail: `P20 ${status} detail`,
                code: `P20_${status}`,
                request_id: `request-${status}`,
                retryable: status === 429,
              }),
              {
                status,
                headers: { "Content-Type": "application/problem+json" },
              },
            ),
        ),
      );

      await expect(
        collectionTaskGateway.create(
          scope,
          {
            name: "采集任务",
            type: "抓取采集",
            scenario: "测试工位",
            description: "",
            target: null,
            quality_threshold: null,
          },
          `create-${status}`,
        ),
      ).rejects.toMatchObject({
        code,
        problemCode: `P20_${status}`,
        message: `P20 ${status} detail`,
        requestId: `request-${status}`,
        httpStatus: status,
      });
    },
  );

  it("requires the detail ETag before edit or close can proceed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify(taskResponse), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );

    await expect(
      collectionTaskGateway.detail(scope, taskResponse.collection_task_id),
    ).rejects.toMatchObject({
      code: "CONTRACT_MISMATCH",
      blockedReasons: [
        { code: "ETAG_MISSING", message: "服务端未返回 ETag。" },
      ],
    });
  });
});
