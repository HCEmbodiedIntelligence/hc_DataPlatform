import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Scope } from "../../../entities/scope";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../../shared/config/runtime";
import { useShellStore } from "../../../shared/scope/shell-store";
import {
  changeOwnAccountPassword,
  getOwnAccountProfile,
  updateOwnAccountProfile,
  type AccountSettings,
} from ".";

const principalId = "10277040-188f-4f9f-a59b-16dd83511c98";
const scope: Scope = {
  organizationId: "org-account-a",
  projectId: "project-account-a",
  regionCode: "region-account-a",
};

const settings: AccountSettings = {
  profile: {
    principal_id: principalId,
    username: "account-user",
    display_name: "账户用户",
    status: "ACTIVE",
    created_at: "2026-08-20T01:00:00Z",
    updated_at: "2026-08-20T01:00:00Z",
    password_changed_at: "2026-08-20T01:00:00Z",
    revision: 1,
    etag: '"v1"',
  },
  password_policy: {
    min_length: 12,
    max_length: 128,
    disallow_username: true,
    blocked_password_count: 8192,
  },
};

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  configureRuntime({
    apiBaseUrl: "/api/v1",
    sseBaseUrl: "/api/v1",
    buildVersion: "account-api-test",
    releaseEnv: "test",
  });
  const store = useShellStore.getState();
  store.setSession(
    { actorId: principalId, displayName: "账户用户", roleIds: [] },
    "account-session-token",
  );
  store.setScope(scope);
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetRuntimeConfigForTests();
  useShellStore.getState().setSession(null, null);
});

describe("account settings real API client", () => {
  it("loads the current account without leaking the active project scope", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(settings));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getOwnAccountProfile(principalId)).resolves.toEqual(settings);

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/account/profile");
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(init.method).toBe("GET");
    expect(headers.get("Authorization")).toBe("Bearer account-session-token");
    expect(headers.get("X-Organization-Id")).toBeNull();
    expect(headers.get("X-Project-Id")).toBeNull();
    expect(headers.get("X-Region-Code")).toBeNull();
  });

  it("updates only display_name with concurrency and idempotency headers", async () => {
    const updated = {
      ...settings,
      profile: {
        ...settings.profile,
        display_name: "新显示名称",
        revision: 2,
        etag: '"v2"',
      },
    } satisfies AccountSettings;
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(updated));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      updateOwnAccountProfile(
        principalId,
        "新显示名称",
        settings.profile.etag,
        "profile-idempotency-a",
      ),
    ).resolves.toEqual(updated);

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({
      display_name: "新显示名称",
    });
    expect(headers.get("If-Match")).toBe(settings.profile.etag);
    expect(headers.get("Idempotency-Key")).toBe("profile-idempotency-a");
  });

  it("never sends the confirmation password or stores secrets in the result", async () => {
    const result = {
      account: {
        ...settings,
        profile: {
          ...settings.profile,
          revision: 2,
          etag: '"v2"',
          password_changed_at: "2026-08-20T02:00:00Z",
        },
      },
      other_sessions_revoked: 2,
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(result));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      changeOwnAccountPassword(
        principalId,
        "Current-password-9!",
        "Next-password-10!",
        settings.profile.etag,
        "password-idempotency-a",
      ),
    ).resolves.toEqual(result);

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(init.method).toBe("POST");
    expect(body).toEqual({
      current_password: "Current-password-9!",
      new_password: "Next-password-10!",
    });
    expect(body).not.toHaveProperty("confirm_password");
    expect(JSON.stringify(result)).not.toContain("Current-password-9!");
    expect(JSON.stringify(result)).not.toContain("Next-password-10!");
  });

  it("fails closed when the server returns another principal", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({
            ...settings,
            profile: {
              ...settings.profile,
              principal_id: "5975341a-ae66-4a94-bd49-99861d710963",
            },
          }),
        ),
    );

    await expect(getOwnAccountProfile(principalId)).rejects.toMatchObject({
      code: "CONTRACT_MISMATCH",
      problemCode: "ACCOUNT_PRINCIPAL_MISMATCH",
    });
  });
});
