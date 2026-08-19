import { z } from "zod";
import type { ActorSummary } from "../../entities/actor";
import type { components } from "../../shared/api/generated/platform";
import { createDomainError } from "../../shared/api/domain-error";
import { request } from "../../shared/api/http-client";
import { parseWire } from "../../shared/api/validate";

export type AccountPrincipal = components["schemas"]["AccountPrincipal"];
export type SessionCreated = components["schemas"]["SessionCreated"];
export type SessionBootstrap = components["schemas"]["SessionBootstrap"];
export type AvailableScope = components["schemas"]["AvailableScope"];
type RegistrationResult = components["schemas"]["RegistrationResult"];
type RegistrationCommand = components["schemas"]["RegistrationCommand"];
type LoginCommand = components["schemas"]["LoginCommand"];
type MembershipRequest = components["schemas"]["MembershipRequest"];
type MembershipRequestCreate =
  components["schemas"]["MembershipRequestCreate"];
type CapabilityRequest = components["schemas"]["CapabilityRequest"];
type CapabilityRequestCreate =
  components["schemas"]["CapabilityRequestCreate"];

const accountPrincipalWireSchema: z.ZodType<AccountPrincipal> = z
  .object({
    principal_id: z.string().min(1),
    username: z.string().min(1),
    status: z.enum(["ACTIVE", "DISABLED"]),
    created_at: z.string().min(1),
  })
  .strict();

const registrationResultWireSchema: z.ZodType<RegistrationResult> = z
  .object({ principal: accountPrincipalWireSchema })
  .strict();

const sessionCreatedWireSchema: z.ZodType<SessionCreated> = z
  .object({
    access_token: z.string().min(1),
    token_type: z.literal("Bearer"),
    principal: accountPrincipalWireSchema,
    capability_revision: z.number().int().nonnegative(),
  })
  .strict();

const availableScopeWireSchema: z.ZodType<AvailableScope> = z
  .object({
    project_id: z.string().min(1),
    region_codes: z.array(z.string().min(1)),
    project_wide: z.boolean(),
    capabilities: z.array(z.string().min(1)),
  })
  .strict();

const sessionBootstrapWireSchema: z.ZodType<SessionBootstrap> = z
  .object({
    principal: accountPrincipalWireSchema,
    available_scopes: z.array(availableScopeWireSchema),
    capability_revision: z.number().int().nonnegative(),
  })
  .strict();

export function toActorSummary(principal: AccountPrincipal): ActorSummary {
  return {
    actorId: principal.principal_id,
    displayName: principal.username,
    roleIds: [],
  };
}

export async function registerAccount(username: string, password: string) {
  const body = { username, password } satisfies RegistrationCommand;
  const raw = await request<unknown>({
    method: "POST",
    path: "/auth/registrations",
    body,
    cache: "no-store",
  });
  return parseWire(registrationResultWireSchema, raw, {
    endpoint: "registerAccount",
  });
}

export async function createSession(username: string, password: string) {
  const body = { username, password } satisfies LoginCommand;
  const raw = await request<unknown>({
    method: "POST",
    path: "/auth/sessions",
    body,
    cache: "no-store",
  });
  return parseWire(sessionCreatedWireSchema, raw, {
    endpoint: "createSession",
  });
}

export async function getSessionBootstrap(signal?: AbortSignal) {
  const raw = await request<unknown>({
    method: "GET",
    path: "/auth/session/bootstrap",
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  return parseWire(sessionBootstrapWireSchema, raw, {
    endpoint: "getSessionBootstrap",
  });
}

export async function logoutSession(): Promise<void> {
  await request<void>({
    method: "POST",
    path: "/auth/session:logout",
    cache: "no-store",
  });
}

function accessRequestMismatch(message: string): Error {
  return createDomainError({
    code: "CONTRACT_MISMATCH",
    message,
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [],
    requestId: null,
    retryable: false,
    httpStatus: null,
  });
}

export async function requestProjectMembership(
  projectId: string,
  reason: string | null,
  idempotencyKey: string,
): Promise<MembershipRequest> {
  const body = { reason } satisfies MembershipRequestCreate;
  const result = await request<MembershipRequest>({
    method: "POST",
    path: `/projects/${encodeURIComponent(projectId)}/membership-requests`,
    body,
    idempotencyKey,
    cache: "no-store",
  });
  if (result.project_id !== projectId) {
    throw accessRequestMismatch("项目加入申请响应与填写的项目不一致。");
  }
  return result;
}

export async function requestProjectCapabilities(
  projectId: string,
  capabilityKeys: readonly string[],
  reason: string | null,
  idempotencyKey: string,
): Promise<CapabilityRequest> {
  const body = {
    capability_keys: [...capabilityKeys],
    reason,
  } satisfies CapabilityRequestCreate;
  const result = await request<CapabilityRequest>({
    method: "POST",
    path: `/projects/${encodeURIComponent(projectId)}/capability-requests`,
    body,
    idempotencyKey,
    cache: "no-store",
  });
  if (result.project_id !== projectId) {
    throw accessRequestMismatch("权限申请响应与填写的项目不一致。");
  }
  return result;
}
