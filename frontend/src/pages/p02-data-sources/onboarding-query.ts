import {
  robotFilterDefaults,
  type RobotFilters,
} from "../../features/robot-ingest/onboarding";

export function parseRobotFilters(params: URLSearchParams): RobotFilters {
  const value = (key: keyof RobotFilters) => params.get(`robot.${key}`) ?? "";
  const choice = (key: keyof RobotFilters, options: string[], fallback = "") =>
    options.includes(value(key)) ? value(key) : fallback;
  return {
    q: value("q").trim().slice(0, 200),
    connection: choice("connection", [
      "ONLINE",
      "OFFLINE",
      "UNKNOWN",
      "UNVERIFIED",
    ]),
    permission: choice("permission", ["NOT_ENABLED", "ENABLED", "DISABLED"]),
    credential: choice("credential", [
      "NOT_ISSUED",
      "AVAILABLE",
      "UNAVAILABLE",
    ]),
    segment: choice("segment", ["online", "pending", "attention"]),
    sort: choice("sort", ["updated", "name", "communication"], "updated"),
    page: /^\d+$/.test(value("page")) ? Math.max(1, Number(value("page"))) : 1,
    page_size: [10, 20, 50].includes(Number(value("page_size")))
      ? Number(value("page_size"))
      : 20,
  };
}
export function updateRobotFilters(
  params: URLSearchParams,
  patch: Partial<RobotFilters>,
): URLSearchParams {
  const next = { ...parseRobotFilters(params), ...patch };
  if (Object.keys(patch).some((key) => key !== "page")) next.page = 1;
  const result = new URLSearchParams(params);
  for (const [key, value] of Object.entries(next)) {
    result.delete(`robot.${key}`);
    if (value !== robotFilterDefaults[key as keyof RobotFilters])
      result.set(`robot.${key}`, String(value));
  }
  return result;
}
