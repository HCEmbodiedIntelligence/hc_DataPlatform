import { z } from "zod";
import type { ActorSummary } from "../../entities/actor";
import type { components } from "../../shared/api/generated/platform";
import { isSafeBearerToken, request } from "../../shared/api/http-client";
import { parseWire } from "../../shared/api/validate";

export type AccountPrincipal = components["schemas"]["AccountPrincipal"];
export type SessionCreated = components["schemas"]["SessionCreated"];
export type AvailableScope = components["schemas"]["AvailableScope"];
export interface AvailableOrganization {
  readonly organization_id: string;
  readonly organization_name: string;
  readonly member_status: "ACTIVE";
}
export interface SessionBootstrap {
  readonly principal: AccountPrincipal;
  readonly available_organizations?: readonly AvailableOrganization[];
  readonly available_scopes: readonly AvailableScope[];
  readonly platform_capabilities: readonly string[];
  readonly capability_revision: number;
}
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
    organization_name: z.string().min(1).max(256).nullable().optional(),
    project_id: z.string().min(1),
    project_name: z.string().min(1).max(256).nullable().optional(),
    region_codes: z.array(z.string().min(1)),
    project_wide: z.boolean(),
    capabilities: z.array(z.string().min(1)),
  })
  .strict();

const availableOrganizationWireSchema: z.ZodType<AvailableOrganization> = z
  .object({
    organization_id: z.string().min(1),
    organization_name: z.string().min(1),
    member_status: z.literal("ACTIVE"),
  })
  .strict();

const sessionBootstrapWireSchema: z.ZodType<SessionBootstrap> = z
  .object({
    principal: accountPrincipalWireSchema,
    available_organizations: z
      .array(availableOrganizationWireSchema)
      .default([]),
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
