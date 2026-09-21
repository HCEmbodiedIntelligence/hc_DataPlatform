import type { ViewerWindowPayload, ViewerWindowSource } from "./types";

export const JOINT_WINDOW_NS = 4_000_000_000n;

/** Share immutable joint samples between the curve and the robot, independent of LOD. */
export function bufferJointWindows(
  source: ViewerWindowSource,
  startNs: string,
  endNs: string,
): ViewerWindowSource {
  const start = BigInt(startNs);
  const end = BigInt(endNs);
  const cache = new Map<string, ViewerWindowPayload>();
  const pending = new Map<string, Promise<ViewerWindowPayload>>();

  const load = (chunkStart: bigint): Promise<ViewerWindowPayload> => {
    const key = chunkStart.toString();
    const cached = cache.get(key);
    if (cached) {
      cache.delete(key);
      cache.set(key, cached);
      return Promise.resolve(cached);
    }
    const existing = pending.get(key);
    if (existing) return existing;
    const chunkEnd =
      chunkStart + JOINT_WINDOW_NS < end ? chunkStart + JOINT_WINDOW_NS : end;
    // Cancelling one view must not cancel a chunk the other view is using.
    const request = source
      .loadWindow(
        { startNs: key, endNs: chunkEnd.toString(), lod: 0 },
        new AbortController().signal,
      )
      .then((payload) => {
        cache.set(key, payload);
        while (cache.size > 6) {
          const oldestKey = cache.keys().next().value!;
          cache.get(oldestKey)?.dispose?.();
          cache.delete(oldestKey);
        }
        return payload;
      })
      .finally(() => pending.delete(key));
    pending.set(key, request);
    return request;
  };

  return {
    async loadWindow(window, signal) {
      signal.throwIfAborted();
      const from =
        BigInt(window.startNs) < start ? start : BigInt(window.startNs);
      const to = BigInt(window.endNs) > end ? end : BigInt(window.endNs);
      const requests: Promise<ViewerWindowPayload>[] = [];
      for (
        let at = start + ((from - start) / JOINT_WINDOW_NS) * JOINT_WINDOW_NS;
        at < to;
        at += JOINT_WINDOW_NS
      )
        requests.push(load(at));
      const payloads = await Promise.all(requests);
      signal.throwIfAborted();
      const timestampsNs: string[] = [];
      const values: (readonly number[])[] = [];
      for (const payload of payloads) {
        payload.timestampsNs.forEach((timestamp, index) => {
          const ns = BigInt(timestamp);
          if (ns < from || ns >= to) return;
          timestampsNs.push(timestamp);
          values.push(payload.values?.[index] ?? []);
        });
      }
      return {
        generation: 0,
        timestampsNs,
        values,
        series: payloads[0]?.series,
      };
    },
  };
}
