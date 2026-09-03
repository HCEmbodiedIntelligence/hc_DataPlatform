// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import { ProviderHarness } from "../../app/providers";
import NotificationInbox from "./NotificationInbox";

const notificationId = "3d35f5fa-3df5-40a4-ab7f-b1d6f5bcd531";

function unreadPage() {
  return {
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
  };
}

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
    buildVersion: "notification-inbox-test",
    releaseEnv: "test",
  });
  useShellStore.getState().setSession(
    {
      actorId: "10277040-188f-4f9f-a59b-16dd83511c98",
      displayName: "通知用户",
      roleIds: [],
    },
    "notification-session-token",
  );
});

it("loads a bounded next page only when the server supplies a cursor", async () => {
  const user = userEvent.setup();
  const nextCursor = "notification-page-two";
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(
      jsonResponse({ ...unreadPage(), next_cursor: nextCursor }),
    )
    .mockResolvedValueOnce(
      jsonResponse({
        items: [
          {
            ...unreadPage().items[0],
            notification_id: "8cbbd06f-37da-44e6-a9cb-5e52dd7d1fd7",
            kind: "CAPABILITY_APPROVED",
          },
        ],
        next_cursor: null,
      }),
    );
  vi.stubGlobal("fetch", fetchMock);

  render(
    <ProviderHarness>
      <NotificationInbox />
    </ProviderHarness>,
  );

  expect(await screen.findByText("项目加入申请已通过")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "加载更多通知" }));
  expect(await screen.findByText("权限申请已通过")).toBeVisible();
  expect(
    fetchMock.mock.calls.map(([url]) => url),
  ).toContain(`/api/v1/account/notifications?limit=20&cursor=${nextCursor}`);
  expect(
    screen.queryByRole("button", { name: "加载更多通知" }),
  ).not.toBeInTheDocument();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  resetRuntimeConfigForTests();
  useShellStore.getState().setSession(null, null);
});

it("renders a recipient-safe fact and marks it read through the real client boundary", async () => {
  const user = userEvent.setup();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse(unreadPage()))
    .mockResolvedValueOnce(new Response(null, { status: 204 }))
    .mockResolvedValueOnce(
      jsonResponse({
        ...unreadPage(),
        items: [
          {
            ...unreadPage().items[0],
            state: "READ",
            read_at: "2026-08-21T08:01:00Z",
          },
        ],
      }),
    );
  vi.stubGlobal("fetch", fetchMock);

  render(
    <ProviderHarness>
      <NotificationInbox />
    </ProviderHarness>,
  );

  expect(await screen.findByText("项目加入申请已通过")).toBeVisible();
  expect(screen.queryByText("private decision reason")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /标记为已读/u }));

  await waitFor(() =>
    expect(fetchMock.mock.calls.map(([url]) => url)).toContain(
      `/api/v1/account/notifications/${notificationId}:read`,
    ),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: /标记为已读/u }),
    ).not.toBeInTheDocument(),
  );
});
