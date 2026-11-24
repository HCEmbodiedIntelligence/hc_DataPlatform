import type { GrantTerms } from "../../shared/auth/project-grants";
import type { components } from "../../shared/api/generated/platform";

export type AccessRequestStatus = components["schemas"]["AccessRequestStatus"];
export type AccessDecisionCommand =
  components["schemas"]["AccessDecisionCommand"];
export type MembershipRequest = components["schemas"]["MembershipRequest"];
export type MembershipRequestList =
  components["schemas"]["MembershipRequestList"];
interface GrantChanges {
  allowed_decisions?: readonly AccessDecision[];
  project_grants_added?: readonly string[];
  project_grants_removed?: readonly string[];
}
export type CapabilityRequest = components["schemas"]["CapabilityRequest"];
export type CapabilityRequestList =
  components["schemas"]["CapabilityRequestList"];
export type ManagedAccount = components["schemas"]["ManagedAccount"];
export type ManagedAccountCreate =
  components["schemas"]["ManagedAccountCreate"];
export type ManagedAccountPage = components["schemas"]["ManagedAccountPage"];
export type ManagedAccountPasswordReset =
  components["schemas"]["ManagedAccountPasswordReset"];
export type ManagedAccountPasswordResetResult =
  components["schemas"]["ManagedAccountPasswordResetResult"];
export type ManagedAccountState = components["schemas"]["ManagedAccountState"];
export type PlatformAccountRole = components["schemas"]["PlatformAccountRole"];

export type AccessRequestKind = "membership" | "capability";
export type AccessDecision = "approve" | "reject" | "revoke";

export interface AccessRequestRow extends GrantTerms, GrantChanges {
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
    expires_at: request.expires_at ?? null,
    resource_id: request.resource_id ?? null,
    replaces_request_id: request.replaces_request_id ?? null,
    allowed_decisions: request.allowed_decisions,
    project_grants_added: request.project_grants_added,
    project_grants_removed: request.project_grants_removed,
    createdAt: request.created_at,
    updatedAt: request.updated_at,
    revision: request.revision,
  };
}
