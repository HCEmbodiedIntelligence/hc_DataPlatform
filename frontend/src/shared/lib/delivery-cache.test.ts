import { afterEach, expect, it, vi } from "vitest";
import { createDomainError } from "../api/domain-error";
import { deliveryUrl, previewCacheUrl, waitForDeliveryCache } from "./delivery-cache";

afterEach(() => vi.useRealTimers());
const rebuilding = () => createDomainError({
  code: "VERSION_CONFLICT", problemCode: "EPISODE_DELIVERY_CACHE_REBUILDING",
  message: "正在重建", fieldErrors: [], operationErrors: [], blockedReasons: [],
  requestId: null, retryable: true, httpStatus: 409,
});

it("waits for an expired archive and returns the original verified grant", async () => {
  vi.useFakeTimers();
  const load = vi.fn().mockRejectedValueOnce(rebuilding()).mockResolvedValue({ sha: "frozen", bytes: 20 });
  const result = waitForDeliveryCache(load);
  await vi.advanceTimersByTimeAsync(2000);
  await expect(result).resolves.toEqual({ sha: "frozen", bytes: 20 });
  expect(load).toHaveBeenCalledTimes(2);
});

it("stops polling when navigation aborts and never retries integrity errors", async () => {
  vi.useFakeTimers();
  const controller = new AbortController();
  const load = vi.fn().mockRejectedValue(rebuilding());
  const result = waitForDeliveryCache(load, controller.signal);
  const check = expect(result).rejects.toMatchObject({ name: "AbortError" });
  await vi.advanceTimersByTimeAsync(0);
  controller.abort();
  await check;
  expect(load).toHaveBeenCalledTimes(1);
  const error = new Error("integrity mismatch");
  await expect(waitForDeliveryCache(() => Promise.reject(error))).rejects.toBe(error);
});

it("accepts only the signed local route for relative download and preview URLs", () => {
  const suffix = `${"a".repeat(64)}?expires=1800000000&signature=${"b".repeat(64)}`;
  expect(deliveryUrl.safeParse(`/api/v1/local-cache/episode-deliveries/${suffix}`).success).toBe(true);
  expect(previewCacheUrl.safeParse(`/api/v1/local-cache/episode-previews/${suffix}`).success).toBe(true);
  for (const raw of ["//evil.example/zip", "/api/admin", "/api/v1/local-cache/episode-deliveries/../zip"]) {
    expect(deliveryUrl.safeParse(raw).success).toBe(false);
  }
});
