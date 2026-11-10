import { z } from "zod";
import { isDomainError } from "../api/domain-error";

// The sole relative download form is the authenticated platform cache grant.
export const deliveryUrl = z.union([
  z.url(),
  z.string().regex(/^\/api\/v1\/local-cache\/episode-deliveries\/[a-f0-9]{64}\?expires=\d+&signature=[a-f0-9]{64}(?:&name=[^&#]+)?$/u),
]);

export async function waitForDeliveryCache<T>(load: () => Promise<T>, signal?: AbortSignal): Promise<T> {
  const deadline = Date.now() + 15 * 60 * 1000;
  for (;;) {
    signal?.throwIfAborted();
    try { return await load(); }
    catch (error) {
      if (!isDomainError(error) || error.problemCode !== "EPISODE_DELIVERY_CACHE_REBUILDING" || Date.now() >= deadline) throw error;
    }
    await new Promise<void>((resolve, reject) => {
      const cancel = () => { clearTimeout(timer); reject(signal?.reason ?? new DOMException("Aborted", "AbortError")); };
      const timer = setTimeout(() => { signal?.removeEventListener("abort", cancel); resolve(); }, 2000);
      signal?.addEventListener("abort", cancel, { once: true });
      if (signal?.aborted) cancel();
    });
  }
}

export const previewCacheUrl = z.union([
  z.url(),
  z.string().regex(/^\/api\/v1\/local-cache\/(?:episode-previews|recording-previews)\/[a-f0-9]{64}\?expires=\d+&signature=[a-f0-9]{64}$/u),
]);
