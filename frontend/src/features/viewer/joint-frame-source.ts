import { createDomainError } from "../../shared/api/domain-error";
import type { RobotJointFrameSource } from "./RobotSceneCore";
import { JOINT_WINDOW_NS } from "./buffered-joint-window-source";
import type { StreamDescriptor, ViewerWindowPayload } from "./types";

const CHUNK_NS = JOINT_WINDOW_NS;

function emptyJointFrameError() {
  return createDomainError({
    code: "PRECONDITION_FAILED",
    problemCode: "VIEWER_JOINT_FRAME_EMPTY",
    message: "当前时间点没有可用的关节角样本。",
    fieldErrors: [],
    operationErrors: [
      {
        code: "VIEWER_JOINT_FRAME_EMPTY",
        message: "当前时间点没有可用的关节角样本。",
      },
    ],
    blockedReasons: [],
    requestId: null,
    retryable: true,
    httpStatus: null,
  });
}

export function buildJointFrameSource(
  stream: StreamDescriptor | null,
): RobotJointFrameSource | undefined {
  if (!stream?.windowSource) return;
  const windowSource = stream.windowSource;
  const streamStartNs = BigInt(stream.startNs);
  const streamEndNs = BigInt(stream.endNs);
  const payloadCache = new Map<string, ViewerWindowPayload>();
  const sampleTimes = new WeakMap<
    ViewerWindowPayload,
    readonly { time: bigint; index: number }[]
  >();
  const inFlight = new Map<string, Promise<ViewerWindowPayload>>();
  const failures = new Map<
    string,
    { readonly cause: unknown; readonly retryAfter: number }
  >();

  const loadChunk = (
    key: string,
    startNs: bigint,
    endNs: bigint,
  ): Promise<ViewerWindowPayload> => {
    const cached = payloadCache.get(key);
    if (cached) {
      payloadCache.delete(key);
      payloadCache.set(key, cached);
      return Promise.resolve(cached);
    }
    const failure = failures.get(key);
    if (failure && Date.now() < failure.retryAfter)
      return Promise.reject(failure.cause);
    failures.delete(key);
    const pending = inFlight.get(key);
    if (pending) return pending;

    // A render tick aborts its previous consumer. Keep the shared chunk request
    // alive so normal playback can finish one object-store window read.
    const requestController = new AbortController();
    const request = windowSource
      .loadWindow(
        { startNs: startNs.toString(), endNs: endNs.toString(), lod: 0 },
        requestController.signal,
      )
      .then((payload) => {
        failures.delete(key);
        sampleTimes.set(
          payload,
          payload.timestampsNs
            .map((timestamp, index) => ({ time: BigInt(timestamp), index }))
            .sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0)),
        );
        payloadCache.set(key, payload);
        while (payloadCache.size > 3) {
          const oldestKey = payloadCache.keys().next().value as
            | string
            | undefined;
          if (!oldestKey) break;
          const oldest = payloadCache.get(oldestKey);
          payloadCache.delete(oldestKey);
          oldest?.dispose?.();
        }
        return payload;
      })
      .catch((cause: unknown) => {
        failures.set(key, { cause, retryAfter: Date.now() + 1_000 });
        throw cause;
      })
      .finally(() => inFlight.delete(key));
    inFlight.set(key, request);
    return request;
  };

  const waitForConsumer = <T>(
    promise: Promise<T>,
    signal: AbortSignal,
  ): Promise<T> => {
    if (signal.aborted)
      return Promise.reject(
        new DOMException("The operation was aborted.", "AbortError"),
      );
    return new Promise<T>((resolve, reject) => {
      const abort = () =>
        reject(new DOMException("The operation was aborted.", "AbortError"));
      signal.addEventListener("abort", abort, { once: true });
      promise.then(
        (value) => {
          signal.removeEventListener("abort", abort);
          resolve(value);
        },
        (cause: unknown) => {
          signal.removeEventListener("abort", abort);
          reject(cause);
        },
      );
    });
  };

  return {
    async sampleAt(ns, signal) {
      const requestedNs = BigInt(ns);
      const targetNs =
        requestedNs < streamStartNs
          ? streamStartNs
          : requestedNs >= streamEndNs
            ? streamEndNs - 1n
            : requestedNs;
      const relativeNs = targetNs - streamStartNs;
      const startNs = streamStartNs + (relativeNs / CHUNK_NS) * CHUNK_NS;
      const endNs =
        startNs + CHUNK_NS < streamEndNs ? startNs + CHUNK_NS : streamEndNs;
      const payload = await waitForConsumer(
        loadChunk(`${startNs}:${endNs}`, startNs, endNs),
        signal,
      );
      if (!payload.values?.length || !payload.timestampsNs.length)
        throw emptyJointFrameError();
      // Load ahead while the current chunk is still playing, including at 2x.
      if (endNs < streamEndNs && endNs - targetNs <= 2_000_000_000n) {
        const nextEnd =
          endNs + CHUNK_NS < streamEndNs ? endNs + CHUNK_NS : streamEndNs;
        const nextKey = `${endNs}:${nextEnd}`;
        if (!payloadCache.has(nextKey) && !inFlight.has(nextKey))
          void loadChunk(nextKey, endNs, nextEnd).catch(() => undefined);
      }
      const times = sampleTimes.get(payload)!;
      let low = 0;
      let high = times.length;
      while (low < high) {
        const middle = (low + high) >>> 1;
        if (times[middle]!.time < targetNs) low = middle + 1;
        else high = middle;
      }
      const nearest =
        low === 0
          ? 0
          : low === times.length
            ? low - 1
            : targetNs - times[low - 1]!.time <= times[low]!.time - targetNs
              ? low - 1
              : low;
      const nearestIndex = times[nearest]!.index;
      const values = payload.values[nearestIndex] ?? [];
      return Object.fromEntries(
        values.map((value, index) => [
          payload.series?.[index]?.displayName ?? `J${index + 1}`,
          value,
        ]),
      );
    },
  };
}
