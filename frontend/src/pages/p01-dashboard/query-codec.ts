import { defineQueryCodec } from "../../shared/routing/route-registry";

export type DashboardRange = "24h" | "7d" | "30d" | "custom";
export type DashboardSearch = Readonly<{
  range: DashboardRange;
  taskId?: string;
  from?: string;
  to?: string;
}>;

const ranges = new Set<DashboardRange>(["24h", "7d", "30d", "custom"]);
const defaults: DashboardSearch = { range: "24h" };

function validInstant(value: string | null): value is string {
  return (
    value !== null && value.length > 0 && Number.isFinite(Date.parse(value))
  );
}

const taskIdPattern = /^[A-Za-z0-9._-]{1,128}$/u;

export const dashboardQueryCodec = defineQueryCodec<
  DashboardSearch,
  URLSearchParams
>({
  defaults,
  parse(searchParams) {
    const candidate = searchParams.get("range");
    const range =
      candidate && ranges.has(candidate as DashboardRange)
        ? (candidate as DashboardRange)
        : "24h";
    const taskCandidate = searchParams.get("task_id");
    const taskId =
      taskCandidate && taskIdPattern.test(taskCandidate)
        ? taskCandidate
        : undefined;
    if (range !== "custom") return { range, ...(taskId ? { taskId } : {}) };
    const from = searchParams.get("from");
    const to = searchParams.get("to");
    if (
      !validInstant(from) ||
      !validInstant(to) ||
      Date.parse(from) >= Date.parse(to)
    )
      return { ...defaults, ...(taskId ? { taskId } : {}) };
    return {
      range,
      ...(taskId ? { taskId } : {}),
      from: new Date(from).toISOString(),
      to: new Date(to).toISOString(),
    };
  },
  build(value) {
    const params = new URLSearchParams();
    const range = value.range ?? defaults.range;
    if (value.taskId && taskIdPattern.test(value.taskId))
      params.set("task_id", value.taskId);
    if (range !== "24h") params.set("range", range);
    if (
      range === "custom" &&
      validInstant(value.from ?? null) &&
      validInstant(value.to ?? null) &&
      Date.parse(value.from!) < Date.parse(value.to!)
    ) {
      params.set("from", new Date(value.from!).toISOString());
      params.set("to", new Date(value.to!).toISOString());
    }
    return params;
  },
});

export default dashboardQueryCodec;
