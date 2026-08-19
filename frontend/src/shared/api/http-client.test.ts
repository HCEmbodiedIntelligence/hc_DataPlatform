import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Scope } from "../../entities/scope";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../config/runtime";
import { useShellStore } from "../scope/shell-store";
import { request } from "./http-client";
import {
  createDomainError,
  isDomainError,
  type DomainError,
} from "./domain-error";

const activeScope: Scope = {
  organizationId: "org-a",
  projectId: "project-a",
  regionCode: "region-a",
};

const otherScope: Scope = {
  organizationId: "org-b",
  projectId: "project-b",
  regionCode: "region-b",
};

function problemResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/problem+json" },
  });
}

function stubResponse(response: Response): void {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
}

function scopedRequest(
  method: "GET" | "POST" = "GET",
  signal?: AbortSignal,
): Promise<unknown> {
  return request({
    method,
    path: "/projects/project-a/resource",
    scope: activeScope,
    signal,
  });
}

async function rejectedDomainError(
  promise: Promise<unknown>,
): Promise<DomainError & Error> {
  let caught: unknown;
  try {
    await promise;
  } catch (error) {
    caught = error;
  }
  expect(isDomainError(caught)).toBe(true);
  if (!isDomainError(caught))
    throw new Error("Expected a DomainError rejection");
  return caught;
}

function configureTestRuntime(): void {
  configureRuntime({
    apiBaseUrl: "/api/v1",
    sseBaseUrl: "/api/v1",
    buildVersion: "test-build",
    releaseEnv: "test",
  });
  const shell = useShellStore.getState();
  shell.finishScopeChange();
  shell.setSession(
    { actorId: "actor-a", displayName: "Actor A", roleIds: [] },
    "test-token",
  );
  shell.setScope(activeScope);
}

function resetTestRuntime(): void {
  vi.unstubAllGlobals();
  resetRuntimeConfigForTests();
}

describe("scoped HTTP requests", () => {
  beforeEach(configureTestRuntime);

  afterEach(resetTestRuntime);

  it("uses the bound scope for all scope headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await request({
      method: "GET",
      path: "/projects/project-a/regions/region-a/upload-sessions",
      scope: activeScope,
    });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    const headers = new Headers(init?.headers);
    expect(headers.get("X-Organization-Id")).toBe(activeScope.organizationId);
    expect(headers.get("X-Project-Id")).toBe(activeScope.projectId);
    expect(headers.get("X-Region-Code")).toBe(activeScope.regionCode);
  });

  it("rejects a stale bound scope before issuing a request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      request({
        method: "GET",
        path: "/projects/project-b/regions/region-b/upload-sessions",
        scope: otherScope,
      }),
    ).rejects.toMatchObject({
      code: "PRECONDITION_FAILED",
      blockedReasons: [{ code: "SCOPE_CHANGED" }],
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects a response when the active scope changes in flight", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => {
      useShellStore.getState().setScope(otherScope);
      return problemResponse({ ok: true }, 200);
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(scopedRequest()).rejects.toMatchObject({
      code: "PRECONDITION_FAILED",
      problemCode: null,
      blockedReasons: [{ code: "SCOPE_CHANGED" }],
    });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("blocks writes while a scope transition is in progress", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    useShellStore.getState().beginScopeChange();

    await expect(scopedRequest("POST")).rejects.toMatchObject({
      code: "PRECONDITION_FAILED",
      problemCode: null,
      blockedReasons: [{ code: "SCOPE_SWITCH_IN_PROGRESS" }],
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("HTTP response handling", () => {
  beforeEach(configureTestRuntime);
  afterEach(resetTestRuntime);

  it("parses RFC 9457 403 responses without replacing the frontend classification", async () => {
    stubResponse(
      problemResponse(
        {
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "当前账户缺少所需能力",
          code: "CAPABILITY_REQUIRED",
          request_id: "req-403",
          retryable: false,
          details: {},
        },
        403,
      ),
    );

    await expect(scopedRequest()).rejects.toMatchObject({
      code: "FORBIDDEN",
      problemCode: "CAPABILITY_REQUIRED",
      message: "当前账户缺少所需能力",
      requestId: "req-403",
      retryable: false,
      httpStatus: 403,
    });
  });

  it("parses RFC 9457 401 responses and preserves the stable problem code", async () => {
    stubResponse(
      problemResponse(
        {
          type: "about:blank",
          title: "Unauthorized",
          status: 401,
          code: "AUTHENTICATION_REQUIRED",
          retryable: false,
        },
        401,
      ),
    );

    await expect(scopedRequest()).rejects.toMatchObject({
      code: "UNAUTHENTICATED",
      problemCode: "AUTHENTICATION_REQUIRED",
      message: "Unauthorized",
      httpStatus: 401,
    });
  });

  it("projects allowlisted field errors from RFC 9457 422 details", async () => {
    stubResponse(
      problemResponse(
        {
          type: "about:blank",
          title: "Validation Error",
          status: 422,
          detail: "请求字段无效",
          code: "REQUEST_VALIDATION_FAILED",
          retryable: false,
          details: {
            field_errors: [
              { path: "/timezone", code: "INVALID", message: "时区无效" },
            ],
            operation_errors: [
              {
                code: "QUERY_INVALID",
                message: "查询组合无效",
                operation_id: "dashboard-query",
              },
            ],
            blocked_reasons: [{ code: "POLICY_BLOCKED", message: "策略阻断" }],
          },
        },
        422,
      ),
    );

    await expect(scopedRequest()).rejects.toMatchObject({
      code: "VALIDATION_ERROR",
      problemCode: "REQUEST_VALIDATION_FAILED",
      fieldErrors: [
        { path: "/timezone", code: "INVALID", message: "时区无效" },
      ],
      operationErrors: [
        {
          code: "QUERY_INVALID",
          message: "查询组合无效",
          operationId: "dashboard-query",
        },
      ],
      blockedReasons: [{ code: "POLICY_BLOCKED", message: "策略阻断" }],
      httpStatus: 422,
    });
  });

  it("marks RFC 9457 429 responses retryable only when the value is exactly true", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        problemResponse(
          {
            title: "Too Many Requests",
            status: 429,
            detail: "请稍后重试",
            code: "DASHBOARD_QUERY_RATE_LIMITED",
            retryable: true,
          },
          429,
        ),
      )
      .mockResolvedValueOnce(
        problemResponse(
          {
            title: "Too Many Requests",
            status: 429,
            detail: "请稍后重试",
            code: "DASHBOARD_QUERY_RATE_LIMITED",
            retryable: "true",
          },
          429,
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const retryableError = await rejectedDomainError(scopedRequest());
    expect(retryableError).toMatchObject({
      code: "RATE_LIMITED",
      problemCode: "DASHBOARD_QUERY_RATE_LIMITED",
      retryable: true,
      httpStatus: 429,
    });
    const malformedRetryableError = await rejectedDomainError(scopedRequest());
    expect(malformedRetryableError.retryable).toBe(false);
  });

  it("uses only the sanitized 500 detail and ignores unapproved nested fields", async () => {
    stubResponse(
      problemResponse(
        {
          type: "about:blank",
          title: "Internal Server Error",
          status: 500,
          detail: "服务暂时不可用",
          code: "INTERNAL_SERVER_ERROR",
          request_id: "req-500",
          retryable: false,
          details: {
            exception: "DatabasePasswordError",
            stack: "secret internal stack",
            signed_url: "https://storage.invalid/private?signature=secret",
            object_path: "private/project/object",
            nested: { password: "secret" },
          },
        },
        500,
      ),
    );

    const error = await rejectedDomainError(scopedRequest());
    expect(error).toMatchObject({
      code: "SERVER_ERROR",
      problemCode: "INTERNAL_SERVER_ERROR",
      message: "服务暂时不可用",
      requestId: "req-500",
      httpStatus: 500,
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
    });
    expect(JSON.stringify(error)).not.toContain("secret");
    expect(JSON.stringify(error)).not.toContain("private/project/object");
  });

  it("uses the real HTTP status when the Problem Details status disagrees", async () => {
    stubResponse(
      problemResponse(
        {
          title: "Forged server error",
          status: 500,
          detail: "权限不足",
          code: "CAPABILITY_REQUIRED",
          request_id: "req-mismatch",
          retryable: false,
        },
        403,
      ),
    );

    await expect(scopedRequest()).rejects.toMatchObject({
      code: "FORBIDDEN",
      problemCode: "CAPABILITY_REQUIRED",
      httpStatus: 403,
    });
  });

  it("preserves the legacy error envelope mapping", async () => {
    stubResponse(
      problemResponse(
        {
          error: {
            code: "LEGACY_COMMAND_BLOCKED",
            message: "旧式错误消息",
            field_errors: [
              { path: "/name", code: "REQUIRED", message: "名称必填" },
            ],
            operation_errors: [
              {
                code: "COMMAND_FAILED",
                message: "命令失败",
                operation_id: "operation-1",
              },
            ],
            blocked_reasons: [{ code: "POLICY_BLOCKED", message: "策略阻断" }],
            request_id: "req-legacy",
            retryable: true,
          },
        },
        409,
      ),
    );

    await expect(scopedRequest()).rejects.toMatchObject({
      code: "VERSION_CONFLICT",
      problemCode: "LEGACY_COMMAND_BLOCKED",
      message: "旧式错误消息",
      fieldErrors: [{ path: "/name", code: "REQUIRED", message: "名称必填" }],
      operationErrors: [
        {
          code: "COMMAND_FAILED",
          message: "命令失败",
          operationId: "operation-1",
        },
      ],
      blockedReasons: [{ code: "POLICY_BLOCKED", message: "策略阻断" }],
      requestId: "req-legacy",
      retryable: true,
      httpStatus: 409,
    });
  });

  it.each([
    ["an empty body", () => new Response(null, { status: 502 })],
    [
      "an HTML body",
      () =>
        new Response("<html><body>private upstream failure</body></html>", {
          status: 502,
          headers: { "Content-Type": "text/html" },
        }),
    ],
    ["JSON null", () => problemResponse(null, 502)],
    [
      "a JSON array",
      () => problemResponse([{ detail: "private array detail" }], 502),
    ],
    [
      "malformed JSON fields",
      () =>
        problemResponse(
          {
            title: ["private title"],
            status: 403,
            detail: { internal: "private detail" },
            code: { value: "PRIVATE_CODE" },
            request_id: ["private-request"],
            retryable: "true",
            details: ["private nested details"],
          },
          502,
        ),
    ],
  ])(
    "fails closed for %s without reporting a network error",
    async (_name, responseFactory) => {
      stubResponse(responseFactory());

      const error = await rejectedDomainError(scopedRequest());
      expect(error).toMatchObject({
        code: "SERVER_ERROR",
        problemCode: null,
        message: "请求未成功完成",
        fieldErrors: [],
        operationErrors: [],
        blockedReasons: [],
        requestId: null,
        retryable: false,
        httpStatus: 502,
      });
      expect(error.message).not.toContain("[object Object]");
      expect(error.message).not.toContain("private");
    },
  );

  it("keeps fetch failures as retryable GET network errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );

    await expect(scopedRequest()).rejects.toMatchObject({
      code: "NETWORK_ERROR",
      problemCode: null,
      message: "网络连接失败",
      retryable: true,
      httpStatus: null,
    });
  });

  it("rethrows an existing DomainError without wrapping it", async () => {
    const original = createDomainError({
      code: "CONTRACT_MISMATCH",
      problemCode: "UPSTREAM_CONTRACT_MISMATCH",
      message: "合同不匹配",
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: "req-existing",
      retryable: false,
      httpStatus: null,
    });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(original));

    const caught = await rejectedDomainError(scopedRequest());
    expect(caught).toBe(original);
  });

  it("does not mark non-GET network failures as retryable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );

    await expect(scopedRequest("POST")).rejects.toMatchObject({
      code: "NETWORK_ERROR",
      problemCode: null,
      retryable: false,
      httpStatus: null,
    });
  });

  it("keeps aborted requests as non-retryable network errors", async () => {
    const controller = new AbortController();
    controller.abort("test-abort");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new DOMException("Aborted", "AbortError")),
    );

    await expect(scopedRequest("GET", controller.signal)).rejects.toMatchObject(
      {
        code: "NETWORK_ERROR",
        problemCode: null,
        message: "请求已取消",
        retryable: false,
        httpStatus: null,
      },
    );
  });

  it("returns successful 200 JSON data unchanged", async () => {
    const data = { data: { id: "resource-1" }, request_id: "req-success" };
    stubResponse(
      new Response(JSON.stringify(data), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(scopedRequest()).resolves.toEqual(data);
  });

  it("normalizes omitted problem codes and recognizes created DomainErrors", () => {
    const error = createDomainError({
      code: "CONTRACT_MISMATCH",
      message: "合同不匹配",
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: null,
      retryable: false,
      httpStatus: null,
    });

    expect(error.problemCode).toBeNull();
    expect(isDomainError(error)).toBe(true);
  });
});
