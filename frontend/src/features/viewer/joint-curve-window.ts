import { JOINT_WINDOW_NS } from "./buffered-joint-window-source";

const VISIBLE_SPAN_NS = 8_000_000_000n;
export const JOINT_CURVE_PREFETCH_NS = 2_000_000_000n;

/** Keep the playhead centered until the fixed-width view meets an episode edge. */
export function jointCurveViewport(
  current: bigint,
  start: bigint,
  end: bigint,
) {
  const span = end - start < VISIBLE_SPAN_NS ? end - start : VISIBLE_SPAN_NS;
  const centered = current - span / 2n;
  const from =
    centered < start ? start : centered > end - span ? end - span : centered;
  return { start: from, end: from + span };
}

/** Read beyond both visible edges, aligned with the shared curve/robot cache. */
export function jointCurveBuffer(current: bigint, start: bigint, end: bigint) {
  const view = jointCurveViewport(current, start, end);
  const from =
    view.start - start > JOINT_WINDOW_NS
      ? view.start - start - JOINT_WINDOW_NS
      : 0n;
  const to = view.end - start + JOINT_WINDOW_NS;
  const bufferEnd =
    start + ((to + JOINT_WINDOW_NS - 1n) / JOINT_WINDOW_NS) * JOINT_WINDOW_NS;
  return {
    start: start + (from / JOINT_WINDOW_NS) * JOINT_WINDOW_NS,
    end: bufferEnd < end ? bufferEnd : end,
  };
}

export function nearestJointSample(
  times: readonly bigint[],
  current: bigint,
): number {
  if (!times.length) return -1;
  let low = 0;
  let high = times.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (times[middle]! < current) low = middle + 1;
    else high = middle;
  }
  if (low === 0) return 0;
  if (low === times.length) return low - 1;
  return current - times[low - 1]! <= times[low]! - current ? low - 1 : low;
}
