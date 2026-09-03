import { http, HttpResponse } from "msw";
import type { components } from "../../shared/api/generated/platform";
import {
  accessBootstrapFixture,
  membersPageFixture,
} from "../fixtures/management";
import { getManagementScenario } from "../scenarios/management";

type MembershipRequest = components["schemas"]["MembershipRequest"];
type CapabilityRequest = components["schemas"]["CapabilityRequest"];

const response = (
  fixture: typeof accessBootstrapFixture | typeof membersPageFixture,
) =>
  getManagementScenario() === "forbidden"
    ? HttpResponse.json(
        {
          error: {
            code: "CAPABILITY_MISSING",
            message: "Capability is required.",
            field_errors: [],
            operation_errors: [],
            blocked_reasons: [],
            request_id: "req_fx_access_403",
            retryable: false,
          },
        },
        { status: 403 },
      )
    : HttpResponse.json(fixture);

const requestedAt = "2026-08-18T01:00:00Z";
const initialMembershipRequest: MembershipRequest = {
  request_id: "membership-request-fx-01",
  organization_id: "org_fx_01",
  project_id: "prj_fx_01",
  requester_id: "contractor_fx_17",
  status: "PENDING",
  reason: "参与当前项目的数据整理与质量复核",
  created_at: requestedAt,
  updated_at: requestedAt,
  revision: 1,
};
const initialCapabilityRequest: CapabilityRequest = {
  request_id: "capability-request-fx-01",
  organization_id: "org_fx_01",
  project_id: "prj_fx_01",
  requester_id: "developer_fx_09",
  capability_keys: ["dataset_version.publish", "access.manage"],
  status: "PENDING",
  reason: "负责数据版本发布与项目权限审批",
  created_at: requestedAt,
  updated_at: requestedAt,
  revision: 1,
};

let membershipRequests: MembershipRequest[] = [{ ...initialMembershipRequest }];
let capabilityRequests: CapabilityRequest[] = [{ ...initialCapabilityRequest }];

function accessRequestResponse<T>(value: T): Response {
  if (getManagementScenario() !== "forbidden")
    return HttpResponse.json(value as never);
  return HttpResponse.json(
    {
      type: "about:blank",
      title: "Forbidden",
      status: 403,
      detail: "Project access management is not granted in this fixture.",
      code: "ACCESS_FORBIDDEN",
      request_id: "req_fx_access_request_403",
      retryable: false,
    },
    { status: 403 },
  );
}

const decisionPath =
  /\/api\/v1\/organizations\/(?<organizationId>[^/]+)\/projects\/(?<projectId>[^/]+)\/(?<kind>membership-requests|capability-requests)\/(?<requestId>[^/:]+):(?<action>approve|reject|revoke|withdraw)$/u;

export default [
  http.get("*/api/v1/projects/:projectId/access/bootstrap", () =>
    response(accessBootstrapFixture),
  ),
  http.get("*/api/v1/projects/:projectId/members", () =>
    response(membersPageFixture),
  ),
  http.get("*/api/v1/organizations/:organizationId/projects/:projectId/membership-requests", ({ params }) => {
    const organizationId = String(params.organizationId);
    const projectId = String(params.projectId);
    return accessRequestResponse({
      items: membershipRequests.map((item) => ({
        ...item,
        organization_id: organizationId,
        project_id: projectId,
      })),
    });
  }),
  http.get("*/api/v1/organizations/:organizationId/projects/:projectId/capability-requests", ({ params }) => {
    const organizationId = String(params.organizationId);
    const projectId = String(params.projectId);
    return accessRequestResponse({
      items: capabilityRequests.map((item) => ({
        ...item,
        organization_id: organizationId,
        project_id: projectId,
      })),
    });
  }),
  http.post(decisionPath, async ({ request, params }) => {
    if (getManagementScenario() === "forbidden") {
      return accessRequestResponse({});
  }
    const organizationId = String(params.organizationId);
    const projectId = String(params.projectId);
    const requestId = String(params.requestId);
    const action = String(params.action);
    const body = (await request.json()) as { readonly reason?: string | null };
    const nextStatus =
      action === "approve"
        ? "APPROVED"
        : action === "reject"
          ? "REJECTED"
          : action === "revoke"
            ? "REVOKED"
            : "WITHDRAWN";
    const collection =
      params.kind === "membership-requests"
        ? membershipRequests
        : capabilityRequests;
    const index = collection.findIndex((item) => item.request_id === requestId);
    if (index < 0) {
      return HttpResponse.json(
        { detail: "Access request not found." },
        { status: 404 },
      );
    }
    const updated = {
      ...collection[index]!,
      organization_id: organizationId,
      project_id: projectId,
      status: nextStatus,
      decision_reason: body.reason ?? null,
      decided_by: "usr_fx_admin",
      updated_at: "2026-08-18T02:00:00Z",
      revision: collection[index]!.revision + 1,
    } as MembershipRequest | CapabilityRequest;
    if (params.kind === "membership-requests")
      membershipRequests[index] = updated as MembershipRequest;
    else capabilityRequests[index] = updated as CapabilityRequest;
    return HttpResponse.json(updated);
  }),
];

export function resetAccessRequestHandlerState(): void {
  membershipRequests = [{ ...initialMembershipRequest }];
  capabilityRequests = [{ ...initialCapabilityRequest }];
}
