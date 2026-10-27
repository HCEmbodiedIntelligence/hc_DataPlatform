import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createDomainError } from "../../shared/api/domain-error";
import { request } from "../../shared/api/http-client";
import { cancelActiveTransports } from "../../shared/api/transport-lifecycle";
import { captureApi } from "./api";
import type { Sample, SchemeDetail } from "./contracts";
import { fixtureDetail } from "./fixtures.test-support";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));

const sample: Sample = {
  project_id: "project",
  region_code: "global",
  raw_source_id: "raw-sample",
  source_path: "meta/info.json",
  episode_index: 0,
  source_format: "LEROBOT_V3",
};

function transportError(status: number | null) {
  return createDomainError({
    code: status === null ? "NETWORK_ERROR" : "SERVER_ERROR",
    message: "upstream response lost",
    fieldErrors: [], operationErrors: [], blockedReasons: [],
    requestId: null, retryable: false, httpStatus: status,
  });
}

function completed(status: "PASSED" | "FAILED" = "PASSED"): SchemeDetail {
  return {
    ...fixtureDetail(),
    validations: [{
      revision: 1, status, source_format: sample.source_format,
      sample, differences: [], presence: {},
      ...(status === "FAILED" ? { error_code: "CAPTURE_STRUCTURE_MISMATCH" } : {}),
    }],
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(request).mockReset();
});
afterEach(async () => {
  await cancelActiveTransports();
  vi.useRealTimers();
});

it("returns a successful validation without another request", async () => {
  vi.mocked(request).mockResolvedValue(completed());
  await expect(captureApi.validate("org", "shared", 1, sample)).resolves.toMatchObject(completed());
  expect(request).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
});

it.each([502, 504, 524, null])("recovers a lost %s response using only scoped reads", async (status) => {
  vi.mocked(request)
    .mockRejectedValueOnce(transportError(status))
    .mockResolvedValueOnce(fixtureDetail())
    .mockResolvedValueOnce(completed());
  const result = captureApi.validate("org", "shared", 1, sample);
  await vi.advanceTimersByTimeAsync(5000);
  await expect(result).resolves.toMatchObject(completed());
  expect(vi.mocked(request).mock.calls.map(([options]) => options.method)).toEqual(["POST", "GET", "GET"]);
  for (const [options] of vi.mocked(request).mock.calls) {
    expect(options.scope).toEqual({ organizationId: "org" });
    expect(options.scopeMode).toBe("organization");
    expect(options.signal).toBeInstanceOf(AbortSignal);
  }
  expect(vi.getTimerCount()).toBe(0);
});

it("displays failed validation evidence without treating it as a pass", async () => {
  vi.mocked(request).mockRejectedValueOnce(transportError(504)).mockResolvedValueOnce(completed("FAILED"));
  await expect(captureApi.validate("org", "shared", 1, sample)).resolves.toMatchObject({
    validations: [{ status: "FAILED", error_code: "CAPTURE_STRUCTURE_MISMATCH" }],
  });
});

it.each([
  { revision: 0 },
  { sample: { ...sample, project_id: "other" } },
  { sample: { ...sample, region_code: "other" } },
  { sample: { ...sample, raw_source_id: "other" } },
  { sample: { ...sample, source_path: "other" } },
  { sample: { ...sample, episode_index: 1 } },
  { sample: { ...sample, source_format: "MCAP" } },
  { sample: null },
])("does not recover from unrelated evidence: %j", async (override) => {
  const unrelated = completed();
  const wire = { ...unrelated, validations: [{ ...unrelated.validations[0], ...override }] };
  vi.mocked(request).mockRejectedValueOnce(transportError(524))
    .mockResolvedValueOnce(wire).mockResolvedValueOnce(completed());
  const result = captureApi.validate("org", "shared", 1, sample);
  await vi.advanceTimersByTimeAsync(0);
  expect(request).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(5000);
  await expect(result).resolves.toMatchObject(completed());
  expect(request).toHaveBeenCalledTimes(3);
});

it("stops when the draft changes while validation is running", async () => {
  vi.mocked(request).mockRejectedValueOnce(transportError(504))
    .mockResolvedValueOnce({ ...completed(), revision: 2 });
  await expect(captureApi.validate("org", "shared", 1, sample)).rejects.toThrow("方案已更新或归档");
  expect(vi.getTimerCount()).toBe(0);
});

it.each([400, 401, 403, 409, 422, 500])("does not hide a %s validation rejection", async (status) => {
  const error = transportError(status);
  vi.mocked(request).mockRejectedValueOnce(error);
  await expect(captureApi.validate("org", "shared", 1, sample)).rejects.toBe(error);
  expect(request).toHaveBeenCalledTimes(1);
});

it("stops immediately if permission is denied during result lookup", async () => {
  const denied = transportError(403);
  vi.mocked(request).mockRejectedValueOnce(transportError(504)).mockRejectedValueOnce(denied);
  await expect(captureApi.validate("org", "shared", 1, sample)).rejects.toBe(denied);
  expect(vi.getTimerCount()).toBe(0);
});

it("cancels recovery between requests when the session or scope changes", async () => {
  vi.mocked(request).mockRejectedValueOnce(transportError(504)).mockResolvedValue(fixtureDetail());
  const result = captureApi.validate("org", "shared", 1, sample);
  const rejection = expect(result).rejects.toBe("scope-change");
  await vi.advanceTimersByTimeAsync(0);
  await cancelActiveTransports();
  await rejection;
  await vi.advanceTimersByTimeAsync(20_000);
  expect(request).toHaveBeenCalledTimes(2);
  expect(vi.getTimerCount()).toBe(0);
});

it("bounds recovery and explains that the saved scheme is retained", async () => {
  vi.mocked(request).mockRejectedValueOnce(transportError(504)).mockResolvedValue(fixtureDetail());
  const result = captureApi.validate("org", "shared", 1, sample);
  const rejection = expect(result).rejects.toThrow("方案已保存");
  await vi.advanceTimersByTimeAsync(120_000);
  await rejection;
  expect(vi.mocked(request).mock.calls.filter(([options]) => options.method === "POST")).toHaveLength(1);
  expect(vi.getTimerCount()).toBe(0);
});
