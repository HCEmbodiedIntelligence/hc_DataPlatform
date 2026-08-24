import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Scope } from "../../entities/scope";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  getUnreadNotificationCount,
  listAccountNotifications,
  markAccountNotificationRead,
} from "./api";

const principalId = "10277040-188f-4f9f-a59b-16dd83511c98";
const notificationId = "3d35f5fa-3df5-40a4-ab7f-b1d6f5bcd531";
const scope: Scope = {
  organizationId: "org-notification-a",
  projectId: "project-notification-a",
  regionCode: "region-notification-a",
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
    buildVersion: "notification-api-test",
    releaseEnv: "test",
  });
  const store = useShellStore.getState();
  store.setSession(
    { actorId: principalId, displayName: "通知用户", roleIds: [] },
    "notification-session-token",
  );
  store.setScope(scope);
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetRuntimeConfigForTests();
  useShellStore.getState().setSession(null, null);
});

describe("account notification real API client", () => {
  it("uses the session bearer without stale project headers", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ unread_count: 2 }))
      .mockResolvedValueOnce(
        jsonResponse({
          items: [
            {
              notification_id: notificationId,
              kind: "MEMBERSHIP_APPROVED",
              organization_id: "org-notification-a",
              project_id: "project-notification-a",
              resource_type: "ACCESS_REQUEST",
              resource_id: "12a27193-235f-4b3d-bba7-53361fb091eb",
              state: "UNREAD",
              created_at: "2026-08-21T08:00:00Z",
              read_at: null,
            },
          ],
          next_cursor: null,
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getUnreadNotificationCount()).resolves.toBe(2);
    await expect(listAccountNotifications("UNREAD")).resolves.toMatchObject({
      items: [{ notification_id: notificationId }],
    });
    await expect(
      markAccountNotificationRead(notificationId),
    ).resolves.toBeUndefined();

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/account/notifications/unread-count",
      "/api/v1/account/notifications?limit=20&state=UNREAD",
      `/api/v1/account/notifications/${notificationId}:read`,
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      const headers = new Headers((init as RequestInit).headers);
      expect(headers.get("Authorization")).toBe(
        "Bearer notification-session-token",
      );
      expect(headers.get("X-Organization-Id")).toBeNull();
      expect(headers.get("X-Project-Id")).toBeNull();
      expect(headers.get("X-Region-Code")).toBeNull();
    }
    expect((fetchMock.mock.calls[2]?.[1] as RequestInit).method).toBe("POST");
  });

  it("fails closed when the inbox wire payload has an unknown field", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          items: [],
          next_cursor: null,
          private_reason: "must-not-render",
        }),
      ),
    );

    await expect(listAccountNotifications(null)).rejects.toMatchObject({
      code: "CONTRACT_MISMATCH",
    });
  });
});
