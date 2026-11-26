export type TimeRange = Readonly<{ from: string; to: string }>;

export function readTimeRange(
  from?: string | null,
  to?: string | null,
): TimeRange | undefined {
  if (!from || !to || !(Date.parse(from) < Date.parse(to))) return undefined;
  return { from: new Date(from).toISOString(), to: new Date(to).toISOString() };
}

export function withinTimeRange(
  value: string | undefined,
  range?: TimeRange,
): boolean {
  if (!range) return true;
  if (!value) return false;
  const instant = Date.parse(value);
  return instant >= Date.parse(range.from) && instant < Date.parse(range.to);
}
