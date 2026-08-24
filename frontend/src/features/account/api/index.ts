import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import type { components } from "../../../shared/api/generated/platform";
import {
  createDomainError,
  isDomainError,
} from "../../../shared/api/domain-error";
import { request } from "../../../shared/api/http-client";
import { parseWire } from "../../../shared/api/validate";
import { useShellStore } from "../../../shared/scope/shell-store";

export type AccountStatus = components["schemas"]["AccountStatus"];
export type AccountProfile = components["schemas"]["AccountProfile"];
export type PasswordPolicyView = components["schemas"]["PasswordPolicyView"];
export type AccountSettings = components["schemas"]["AccountSettings"];
export type PasswordChangeResult =
  components["schemas"]["PasswordChangeResult"];
export type RecoveryEmailConfigured =
  components["schemas"]["RecoveryEmailConfigured"];
type AccountProfileUpdate = components["schemas"]["AccountProfileUpdate"];
type PasswordChangeCommand = components["schemas"]["PasswordChangeCommand"];
type PasswordRecoveryAccepted =
  components["schemas"]["PasswordRecoveryAccepted"];
type RecoveryEmailVerificationRequest =
  components["schemas"]["RecoveryEmailVerificationRequest"];
type RecoveryEmailConfirmation =
  components["schemas"]["RecoveryEmailConfirmation"];

const instant = z.string().datetime({ offset: true });
const accountEtag = z.string().regex(/^"v[1-9][0-9]*"$/u);

const accountProfileWireSchema: z.ZodType<AccountProfile> = z
  .object({
    principal_id: z.string().uuid(),
    username: z.string().min(1).max(128),
    display_name: z.string().min(1).max(128),
    status: z.enum(["ACTIVE", "DISABLED"]),
    created_at: instant,
    updated_at: instant,
    password_changed_at: instant,
    revision: z.number().int().min(1),
    etag: accountEtag,
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
    message: "密码策略的最大长度不能小于最小长度。",
  });

const accountSettingsWireSchema: z.ZodType<AccountSettings> = z
  .object({
    profile: accountProfileWireSchema,
    password_policy: passwordPolicyWireSchema,
  })
  .strict();

const passwordChangeResultWireSchema: z.ZodType<PasswordChangeResult> = z
  .object({
    account: accountSettingsWireSchema,
    other_sessions_revoked: z.number().int().nonnegative(),
  })
  .strict();

const passwordRecoveryAcceptedWireSchema: z.ZodType<PasswordRecoveryAccepted> =
  z.object({ accepted: z.literal(true) }).strict();

const recoveryEmailConfiguredWireSchema: z.ZodType<RecoveryEmailConfigured> = z
  .object({ recovery_email_hint: z.string().min(3).max(254) })
  .strict();

function assertOwnProfile(
  settings: AccountSettings,
  expectedPrincipalId: string,
): AccountSettings {
  if (settings.profile.principal_id === expectedPrincipalId) return settings;
  throw createDomainError({
    code: "CONTRACT_MISMATCH",
    problemCode: "ACCOUNT_PRINCIPAL_MISMATCH",
    message: "账户响应与当前登录身份不一致，页面已安全关闭。",
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [],
    requestId: null,
    retryable: false,
    httpStatus: null,
  });
}

export const accountSettingsKeys = {
  profile: (principalId: string) =>
    ["account-settings", principalId, "profile"] as const,
};

export async function getOwnAccountProfile(
  expectedPrincipalId: string,
  signal?: AbortSignal,
): Promise<AccountSettings> {
  const endpoint = "/account/profile";
  const raw = await request<unknown>({
    method: "GET",
    path: endpoint,
    scopeMode: "session",
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  const settings = parseWire(accountSettingsWireSchema, raw, { endpoint });
  return assertOwnProfile(settings, expectedPrincipalId);
}

export async function updateOwnAccountProfile(
  expectedPrincipalId: string,
  displayName: string,
  etag: string,
  idempotencyKey: string,
): Promise<AccountSettings> {
  const endpoint = "/account/profile";
  const body = { display_name: displayName } satisfies AccountProfileUpdate;
  const raw = await request<unknown>({
    method: "PATCH",
    path: endpoint,
    scopeMode: "session",
    body,
    ifMatch: etag,
    idempotencyKey,
    cache: "no-store",
  });
  const settings = parseWire(accountSettingsWireSchema, raw, { endpoint });
  return assertOwnProfile(settings, expectedPrincipalId);
}

export async function changeOwnAccountPassword(
  expectedPrincipalId: string,
  currentPassword: string,
  newPassword: string,
  etag: string,
  idempotencyKey: string,
): Promise<PasswordChangeResult> {
  const endpoint = "/account/password:change";
  const body = {
    current_password: currentPassword,
    new_password: newPassword,
  } satisfies PasswordChangeCommand;
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    scopeMode: "session",
    body,
    ifMatch: etag,
    idempotencyKey,
    cache: "no-store",
  });
  const result = parseWire(passwordChangeResultWireSchema, raw, { endpoint });
  return {
    ...result,
    account: assertOwnProfile(result.account, expectedPrincipalId),
  };
}

export async function requestRecoveryEmailVerification(
  recoveryEmail: string,
): Promise<PasswordRecoveryAccepted> {
  const endpoint = "/account/recovery-email-verifications";
  const body = {
    recovery_email: recoveryEmail,
  } satisfies RecoveryEmailVerificationRequest;
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    scopeMode: "session",
    body,
    cache: "no-store",
  });
  return parseWire(passwordRecoveryAcceptedWireSchema, raw, { endpoint });
}

export async function confirmRecoveryEmail(
  token: string,
): Promise<RecoveryEmailConfigured> {
  const endpoint = "/account/recovery-email:confirm";
  const body = { token } satisfies RecoveryEmailConfirmation;
  const raw = await request<unknown>({
    method: "POST",
    path: endpoint,
    scopeMode: "session",
    body,
    cache: "no-store",
  });
  return parseWire(recoveryEmailConfiguredWireSchema, raw, { endpoint });
}

export function useOwnAccountProfile() {
  const principalId = useShellStore(
    (state) => state.principal?.actorId ?? null,
  );
  return useQuery({
    queryKey: accountSettingsKeys.profile(principalId ?? "signed-out"),
    enabled: principalId !== null,
    staleTime: 30_000,
    retry: (failureCount, reason) =>
      !(isDomainError(reason) && reason.httpStatus === 401) && failureCount < 1,
    queryFn: ({ signal }) => getOwnAccountProfile(principalId!, signal),
  });
}
