import { compareInt64, int64String, type Int64String } from './bigint-string';

export interface TimeRange {
  readonly startNs: Int64String;
  readonly endNs: Int64String;
}

export function makeTimeRange(startNs: string, endNs: string): TimeRange {
  const start = int64String(startNs);
  const end = int64String(endNs);
  if (compareInt64(start, end) >= 0) throw new RangeError('Time range must satisfy startNs < endNs');
  return Object.freeze({ startNs: start, endNs: end });
}

export function containsNs(range: TimeRange, value: Int64String): boolean {
  return compareInt64(range.startNs, value) <= 0 && compareInt64(value, range.endNs) < 0;
}

export function overlaps(left: TimeRange, right: TimeRange): boolean {
  return compareInt64(left.startNs, right.endNs) < 0 && compareInt64(right.startNs, left.endNs) < 0;
}

export function intersectTimeRanges(left: TimeRange, right: TimeRange): TimeRange | null {
  if (!overlaps(left, right)) return null;
  const startNs = compareInt64(left.startNs, right.startNs) >= 0 ? left.startNs : right.startNs;
  const endNs = compareInt64(left.endNs, right.endNs) <= 0 ? left.endNs : right.endNs;
  return Object.freeze({ startNs, endNs });
}
