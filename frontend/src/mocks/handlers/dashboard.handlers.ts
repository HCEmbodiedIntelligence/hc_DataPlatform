import { delay, http, HttpResponse } from "msw";
import {
  dashboardActivityFixture,
  dashboardEmptyFixtures,
  dashboardPendingFixture,
  dashboardSnapshotFixture,
  dashboardTaskStatusEmptyFixture,
  dashboardTaskStatusFixture,
  dashboardUnknownFixtures,
} from "../fixtures/dashboard";
import {
  dashboardOfflineShouldFail,
  getDashboardScenario,
} from "../scenarios/dashboard";

const error = (status: number, code: string, requestId: string) =>
  HttpResponse.json(
    {
      error: {
        code,
        message: code,
        field_errors: [],
        operation_errors: [],
        blocked_reasons: [],
        request_id: requestId,
        retryable: status >= 500,
      },
    },
    { status },
  );

function invalidRequest(
  request: Request,
  params: Readonly<Record<string, string | readonly string[] | undefined>>,
): Response | null {
  const projectId =
    typeof params.projectId === "string" ? params.projectId : "";
  const regionCode = new URL(request.url).searchParams.get("region_code") ?? "";
  const headers = request.headers;
  if (
    !headers.get("authorization")?.startsWith("Bearer ") ||
    headers.get("x-organization-id") !== "org_fx_01" ||
    headers.get("x-project-id") !== projectId ||
    headers.get("x-region-code") !== regionCode ||
    !headers.get("x-client-version")
  ) {
    return error(400, "INVALID_HEADERS", "req_fx_dashboard_headers");
  }
  if (!regionCode)
    return error(400, "INVALID_QUERY", "req_fx_dashboard_region_query");
  return null;
}

async function scenarioGate(endpoint: string): Promise<Response | null> {
  const scenario = getDashboardScenario();
  if (scenario === "first-loading") await delay(2_000);
  if (scenario === "forbidden")
    return error(403, "FORBIDDEN", `req_fx_dashboard_${endpoint}_403`);
  if (scenario === "not-found")
    return error(404, "PROJECT_NOT_FOUND", `req_fx_dashboard_${endpoint}_404`);
  if (scenario === "gone")
    return error(410, "SCOPE_GONE", `req_fx_dashboard_${endpoint}_410`);
  if (scenario === "conflict" && endpoint === "pending")
    return error(
      409,
      "CURSOR_SNAPSHOT_EXPIRED",
      "req_fx_dashboard_pending_409",
    );
  if (scenario === "fatal-error")
    return error(
      503,
      "DASHBOARD_UNAVAILABLE",
      `req_fx_dashboard_${endpoint}_503`,
    );
  if (scenario === "rate-limited" && endpoint === "activity")
    return HttpResponse.json(
      {
        error: {
          code: "RATE_LIMITED",
          message: "rate limited",
          field_errors: [],
          operation_errors: [],
          blocked_reasons: [],
          request_id: "req_fx_dashboard_429",
          retryable: true,
        },
      },
      { status: 429, headers: { "Retry-After": "3" } },
    );
  if (dashboardOfflineShouldFail()) return HttpResponse.error();
  return null;
}

export const dashboardHandlers = [
  http.get(
    "*/api/v1/projects/:projectId/dashboard/task-status",
    async ({ request, params }) => {
      const invalid = invalidRequest(request, params);
      if (invalid) return invalid;
      const gated = await scenarioGate("task-status");
      if (gated) return gated;
      if (getDashboardScenario() === "empty")
        return HttpResponse.json(dashboardTaskStatusEmptyFixture);
      if (getDashboardScenario() === "contract-mismatch")
        return HttpResponse.json({
          ...dashboardTaskStatusFixture,
          project_id: "wrong-project",
        });
      return HttpResponse.json(dashboardTaskStatusFixture);
    },
  ),
  http.get(
    "*/api/v1/projects/:projectId/dashboard/activity",
    async ({ request, params }) => {
      const invalid = invalidRequest(request, params);
      if (invalid) return invalid;
      const query = new URL(request.url).searchParams;
      if (
        !query.get("from") ||
        !query.get("to") ||
        !query.get("timezone") ||
        query.get("from")! >= query.get("to")!
      )
        return error(
          422,
          "INVALID_TIME_RANGE",
          "req_fx_dashboard_activity_query",
        );
      const gated = await scenarioGate("activity");
      if (gated) return gated;
      if (
        getDashboardScenario() === "empty" ||
        getDashboardScenario() === "filtered-empty"
      )
        return HttpResponse.json(dashboardEmptyFixtures.activity);
      return HttpResponse.json(dashboardActivityFixture);
    },
  ),
  http.get(
    "*/api/v1/projects/:projectId/dashboard/snapshot",
    async ({ request, params }) => {
      const invalid = invalidRequest(request, params);
      if (invalid) return invalid;
      const query = new URL(request.url).searchParams;
      if (!query.get("from") || !query.get("to") || !query.get("timezone"))
        return error(422, "INVALID_QUERY", "req_fx_dashboard_snapshot_query");
      const gated = await scenarioGate("snapshot");
      if (gated) return gated;
      if (getDashboardScenario() === "empty")
        return HttpResponse.json(dashboardEmptyFixtures.snapshot);
      if (getDashboardScenario() === "unknown-enum")
        return HttpResponse.json(dashboardUnknownFixtures.snapshot);
      if (getDashboardScenario() === "contract-mismatch")
        return HttpResponse.json({
          ...dashboardSnapshotFixture,
          project_id: "wrong-project",
        });
      return HttpResponse.json(dashboardSnapshotFixture);
    },
  ),
  http.get(
    "*/api/v1/projects/:projectId/dashboard/pending-items",
    async ({ request, params }) => {
      const invalid = invalidRequest(request, params);
      if (invalid) return invalid;
      const query = new URL(request.url).searchParams;
      if (
        !query.get("from") ||
        !query.get("to") ||
        !query.get("timezone") ||
        !["5", "50"].includes(query.get("limit") ?? "")
      )
        return error(422, "INVALID_QUERY", "req_fx_dashboard_pending_query");
      const gated = await scenarioGate("pending");
      if (gated) return gated;
      if (getDashboardScenario() === "empty")
        return HttpResponse.json(dashboardEmptyFixtures.pending);
      if (getDashboardScenario() === "unknown-enum")
        return HttpResponse.json(dashboardUnknownFixtures.pending);
      return HttpResponse.json(dashboardPendingFixture);
    },
  ),
];

export default dashboardHandlers;
