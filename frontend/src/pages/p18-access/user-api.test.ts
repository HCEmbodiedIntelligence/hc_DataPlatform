import { beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import type { ManagedAccount } from "./contracts";
import {
  createManagedAccount,
  deleteManagedAccount,
  listManagedAccounts,
  resetManagedAccountPassword,
  setManagedAccountRole,
  setManagedAccountStatus,
  unlockManagedAccount,
} from "./user-api";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));

const requestMock = vi.mocked(request);
const account: ManagedAccount = {
  principal_id: "96d2afd5-33d1-4e63-b22a-9d296cd24c18",
  username: "robot-operator",
  display_name: "机器人操作员",
  state: "ACTIVE",
  platform_role: "USER",
  recovery_email_configured: true,
  recovery_email_hint: "op******@example.com",
  active_session_count: 2,
  created_at: "2026-08-20T08:00:00Z",
  updated_at: "2026-08-21T08:00:00Z",
  password_changed_at: "2026-08-20T08:00:00Z",
  deleted_at: null,
  revision: 1,
  etag: '"v1"',
};

beforeEach(() => {
  requestMock.mockReset();
});

describe("P18 platform user administration gateway", () => {
  it("lists global accounts with session scope and server-side filters", async () => {
    requestMock.mockResolvedValue({
      items: [account],
      page: 2,
      page_size: 20,
      total: 25,
    });

    await listManagedAccounts({
      query: "robot",
      state: "ACTIVE",
      role: "USER",
      page: 2,
      pageSize: 20,
    });

    expect(requestMock).toHaveBeenCalledWith({
      method: "GET",
      path: "/platform/accounts",
      scopeMode: "session",
      query: {
        query: "robot",
        state: "ACTIVE",
        role: "USER",
        page: 2,
        page_size: 20,
      },
      cache: "no-store",
    });
  });

  it("creates an account without leaking project scope into the platform operation", async () => {
    requestMock.mockResolvedValue(account);
    const command = {
      username: "robot-operator",
      display_name: "机器人操作员",
      password: "12345678",
      platform_role: "USER",
    } as const;

    await createManagedAccount(command);

    expect(requestMock).toHaveBeenCalledWith({
      method: "POST",
      path: "/platform/accounts",
      scopeMode: "session",
      body: command,
      cache: "no-store",
    });
  });

  it("resets a password without an old-password field and uses optimistic concurrency", async () => {
    requestMock.mockResolvedValue({ account, sessions_revoked: 2 });

    await resetManagedAccountPassword({
      account,
      new_password: "12345678",
      confirm_password: "12345678",
    });

    expect(requestMock).toHaveBeenCalledWith({
      method: "POST",
      path: `/platform/accounts/${account.principal_id}:reset-password`,
      scopeMode: "session",
      ifMatch: account.etag,
      body: {
        new_password: "12345678",
        confirm_password: "12345678",
      },
      cache: "no-store",
    });
    expect(requestMock.mock.calls[0]?.[0]).not.toHaveProperty(
      "body.current_password",
    );
  });

  it("carries the account revision through status, role, and soft-delete writes", async () => {
    requestMock.mockResolvedValue(account);

    await setManagedAccountStatus({ account, enabled: false });
    await setManagedAccountRole({ account, role: "PLATFORM_ADMIN" });
    await deleteManagedAccount({ account });

    expect(requestMock).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({
        path: `/platform/accounts/${account.principal_id}:disable`,
        scopeMode: "session",
        ifMatch: account.etag,
      }),
    );
    expect(requestMock).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({
        path: `/platform/accounts/${account.principal_id}:role`,
        ifMatch: account.etag,
        body: { platform_role: "PLATFORM_ADMIN" },
      }),
    );
    expect(requestMock).toHaveBeenNthCalledWith(
      3,
      expect.objectContaining({
        method: "DELETE",
        path: `/platform/accounts/${account.principal_id}`,
        ifMatch: account.etag,
      }),
    );
  });

  it("unlocks temporary abuse protection with the account-scoped platform operation", async () => {
    requestMock.mockResolvedValue(undefined);

    await unlockManagedAccount({ account });

    expect(requestMock).toHaveBeenCalledWith({
      method: "POST",
      path: `/platform/accounts/${account.principal_id}:unlock`,
      scopeMode: "session",
      cache: "no-store",
    });
  });
});
