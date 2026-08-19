import type { components } from "../../shared/api/generated/platform";

export type AccessRequestStatus = components["schemas"]["AccessRequestStatus"];
export type AccessDecisionCommand =
  components["schemas"]["AccessDecisionCommand"];
export type MembershipRequest = components["schemas"]["MembershipRequest"];
export type MembershipRequestList =
  components["schemas"]["MembershipRequestList"];
export type CapabilityRequest = components["schemas"]["CapabilityRequest"];
export type CapabilityRequestList =
  components["schemas"]["CapabilityRequestList"];

export type AccessRequestKind = "membership" | "capability";
export type AccessDecision = "approve" | "reject" | "revoke" | "withdraw";

export interface AccessRequestRow {
  readonly kind: AccessRequestKind;
  readonly requestId: string;
  readonly projectId: string;
  readonly requesterId: string;
  readonly status: AccessRequestStatus;
  readonly reason: string | null;
  readonly decisionReason: string | null;
  readonly decidedBy: string | null;
  readonly capabilityKeys: readonly string[];
  readonly createdAt: string;
  readonly updatedAt: string;
  readonly revision: number;
}

export function membershipRequestRow(
  request: MembershipRequest,
): AccessRequestRow {
  return {
    kind: "membership",
    requestId: request.request_id,
    projectId: request.project_id,
    requesterId: request.requester_id,
    status: request.status,
    reason: request.reason ?? null,
    decisionReason: request.decision_reason ?? null,
    decidedBy: request.decided_by ?? null,
    capabilityKeys: [],
    createdAt: request.created_at,
    updatedAt: request.updated_at,
    revision: request.revision,
  };
}

export function capabilityRequestRow(
  request: CapabilityRequest,
): AccessRequestRow {
  return {
    kind: "capability",
    requestId: request.request_id,
    projectId: request.project_id,
    requesterId: request.requester_id,
    status: request.status,
    reason: request.reason ?? null,
    decisionReason: request.decision_reason ?? null,
    decidedBy: request.decided_by ?? null,
    capabilityKeys: request.capability_keys,
    createdAt: request.created_at,
    updatedAt: request.updated_at,
    revision: request.revision,
  };
}
