import { z } from "zod";
import type { ActorSummary } from "../../entities/actor";
import type { components } from "../../shared/api/generated/platform";
import { createDomainError } from "../../shared/api/domain-error";
import { isSafeBearerToken, request } from "../../shared/api/http-client";
import { parseWire } from "../../shared/api/validate";

export type AccountPrincipal = components["schemas"]["AccountPrincipal"];
export type SessionCreated = components["schemas"]["SessionCreated"];
export type SessionBootstrap = components["schemas"]["SessionBootstrap"];
export type AvailableScope = components["schemas"]["AvailableScope"];
export type PasswordPolicyView = components["schemas"]["PasswordPolicyView"];
export type PublicAuthConfiguration =
  components["schemas"]["PublicAuthConfiguration"];
export type PublicAuthChallengeConfiguration =
  components["schemas"]["PublicAuthChallengeConfiguration"];
export type PasswordRecoveryAccepted =
  components["schemas"]["PasswordRecoveryAccepted"];
export type PasswordRecoveryCompleted =
  components["schemas"]["PasswordRecoveryCompleted"];
type RegistrationResult = components["schemas"]["RegistrationResult"];
type RegistrationCommand = components["schemas"]["RegistrationCommand"];
type LoginCommand = components["schemas"]["LoginCommand"];
type PasswordRecoveryRequest = components["schemas"]["PasswordRecoveryRequest"];
type PasswordRecoveryConfirmation =
  components["schemas"]["PasswordRecoveryConfirmation"];
type MembershipRequest = components["schemas"]["MembershipRequest"];
type MembershipRequestCreate = components["schemas"]["MembershipRequestCreate"];
type CapabilityRequest = components["schemas"]["CapabilityRequest"];
type CapabilityRequestCreate = components["schemas"]["CapabilityRequestCreate"];

const accountPrincipalWireSchema: z.ZodType<AccountPrincipal> = z
  .object({
    principal_id: z.string().min(1),
    username: z.string().min(1),
    display_name: z.string().min(1),
    status: z.enum(["ACTIVE", "DISABLED"]),
    created_at: z.string().min(1),
  })
  .strict();

const registrationResultWireSchema: z.ZodType<RegistrationResult> = z
  .object({ principal: accountPrincipalWireSchema })
  .strict();

const sessionCreatedWireSchema: z.ZodType<SessionCreated> = z
  .object({
    access_token: z.string().refine(isSafeBearerToken),
    token_type: z.literal("Bearer"),
    principal: accountPrincipalWireSchema,
    capability_revision: z.number().int().nonnegative(),
  })
  .strict();

const availableScopeWireSchema: z.ZodType<AvailableScope> = z
  .object({
    organization_id: z.string().min(1),
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
    platform_capabilities: z.array(z.string().min(1)).default([]),
    capability_revision: z.number().int().nonnegative(),
  })
  .strict();

const passwordPolicyWireSchema: z.ZodType<PasswordPolicyView> = z
  .object({
    min_length: z.number().int().min(1).max(128),
    max_length: z.number().int().min(1).max(128),
    disallow_username: z.boolean(),
    blocked_password_count: z.number().int().nonnegative(),
  })
  .strict()
  .refine((policy) => policy.max_length >= policy.min_length, {
    message: "max_length must be greater than or equal to min_length",
    path: ["max_length"],
  });

const publicAuthConfigurationWireSchema: z.ZodType<PublicAuthConfiguration> = z
  .object({
    password_policy: passwordPolicyWireSchema,
    challenge: z
      .object({
        provider: z.literal("TURNSTILE"),
        site_key: z.string().min(1).max(256),
      })
      .strict()
      .nullable(),
  })
  .strict();

const passwordRecoveryAcceptedWireSchema: z.ZodType<PasswordRecoveryAccepted> =
  z.object({ accepted: z.literal(true) }).strict();

const passwordRecoveryCompletedWireSchema: z.ZodType<PasswordRecoveryCompleted> =
  z.object({ sessions_revoked: z.number().int().nonnegative() }).strict();

export function toActorSummary(principal: AccountPrincipal): ActorSummary {
  return {
    actorId: principal.principal_id,
    displayName: principal.display_name,
    roleIds: [],
  };
}

interface PublicAuthSubmissionOptions {
  readonly challengeResponse?: string;
}

export async function registerAccount(
  username: string,
  password: string,
  options: PublicAuthSubmissionOptions = {},
) {
  const body = { username, password } satisfies RegistrationCommand;
  const raw = await request<unknown>({
    method: "POST",
    path: "/auth/registrations",
    scopeMode: "public",
    body,
    cache: "no-store",
    ...(options.challengeResponse === undefined
      ? {}
      : { authChallengeResponse: options.challengeResponse }),
  });
  return parseWire(registrationResultWireSchema, raw, {
    endpoint: "registerAccount",
  });
}

export async function getPublicAuthConfiguration(signal?: AbortSignal) {
  const raw = await request<unknown>({
    method: "GET",
    path: "/auth/config",
    scopeMode: "public",
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  return parseWire(publicAuthConfigurationWireSchema, raw, {
    endpoint: "getPublicAuthConfiguration",
  });
}

export async function requestPasswordRecovery(
  identifier: string,
  options: PublicAuthSubmissionOptions = {},
) {
  const endpoint = "/auth/password-recovery-requests";
  const body = { identifier } satisfies PasswordRecoveryRequest;
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    scopeMode: "public",
    body,
    cache: "no-store",
    ...(options.challengeResponse === undefined
      ? {}
      : { authChallengeResponse: options.challengeResponse }),
  });
  return parseWire(passwordRecoveryAcceptedWireSchema, raw, { endpoint });
}

export async function confirmPasswordRecovery(
  token: string,
  newPassword: string,
) {
  const endpoint = "/auth/password-recovery-confirmations";
  const body = {
    token,
    new_password: newPassword,
  } satisfies PasswordRecoveryConfirmation;
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    scopeMode: "public",
    body,
    cache: "no-store",
  });
  return parseWire(passwordRecoveryCompletedWireSchema, raw, { endpoint });
}

interface SessionCreationOptions extends PublicAuthSubmissionOptions {
  readonly onIssuedToken?: (token: string) => void;
}

function extractSafeIssuedToken(raw: unknown): string | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw))
    return null;
  const candidate = (raw as { readonly access_token?: unknown }).access_token;
  return typeof candidate === "string" && isSafeBearerToken(candidate)
    ? candidate
    : null;
}

export async function createSession(
  username: string,
  password: string,
  options: SessionCreationOptions = {},
) {
  const body = { username, password } satisfies LoginCommand;
  const raw = await request<unknown>({
    method: "POST",
    path: "/auth/sessions",
    scopeMode: "public",
    body,
    cache: "no-store",
    ...(options.challengeResponse === undefined
      ? {}
      : { authChallengeResponse: options.challengeResponse }),
  });
  const issuedToken = extractSafeIssuedToken(raw);
  if (issuedToken !== null) options.onIssuedToken?.(issuedToken);
  return parseWire(sessionCreatedWireSchema, raw, {
    endpoint: "createSession",
  });
}

interface ExplicitSessionRequestOptions {
  readonly bearerToken?: string;
  readonly signal?: AbortSignal;
}

export async function getSessionBootstrap(
  options: ExplicitSessionRequestOptions = {},
) {
  const raw = await request<unknown>({
    method: "GET",
    path: "/auth/session/bootstrap",
    scopeMode: "session",
    cache: "no-store",
    ...(options.bearerToken === undefined
      ? {}
      : { bearerToken: options.bearerToken }),
    ...(options.signal ? { signal: options.signal } : {}),
  });
  return parseWire(sessionBootstrapWireSchema, raw, {
    endpoint: "getSessionBootstrap",
  });
}

export async function logoutSession(
  options: Pick<ExplicitSessionRequestOptions, "bearerToken"> = {},
): Promise<void> {
  await request<void>({
    method: "POST",
    path: "/auth/session:logout",
    scopeMode: "session",
    cache: "no-store",
    ...(options.bearerToken === undefined
      ? {}
      : { bearerToken: options.bearerToken }),
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
  organizationId: string,
  projectId: string,
  reason: string | null,
  idempotencyKey: string,
): Promise<MembershipRequest> {
  const body = { reason } satisfies MembershipRequestCreate;
  const result = await request<MembershipRequest>({
    method: "POST",
    path: `/organizations/${encodeURIComponent(organizationId)}/projects/${encodeURIComponent(projectId)}/membership-requests`,
    body,
    idempotencyKey,
    cache: "no-store",
    scopeMode: "session",
  });
  if (
    result.organization_id !== organizationId ||
    result.project_id !== projectId
  ) {
    throw accessRequestMismatch("组织或项目加入申请响应与填写的范围不一致。");
  }
  return result;
}

export async function requestProjectCapabilities(
  organizationId: string,
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
    path: `/organizations/${encodeURIComponent(organizationId)}/projects/${encodeURIComponent(projectId)}/capability-requests`,
    body,
    idempotencyKey,
    cache: "no-store",
    scopeMode: "session",
  });
  if (
    result.organization_id !== organizationId ||
    result.project_id !== projectId
  ) {
    throw accessRequestMismatch("组织或项目权限申请响应与填写的范围不一致。");
  }
  return result;
}
