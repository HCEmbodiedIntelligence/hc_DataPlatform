// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ProviderHarness } from "../../app/providers";
import {
  configureRuntime,
  resetRuntimeConfigForTests,
} from "../../shared/config/runtime";
import { useShellStore } from "../../shared/scope/shell-store";
import { AccountPage } from "./page";

const principalId = "10277040-188f-4f9f-a59b-16dd83511c98";

function response(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });
}

describe("personal account page", () => {
  beforeEach(() => {
    configureRuntime({
      apiBaseUrl: "/api/v1",
      sseBaseUrl: "/api/v1",
      buildVersion: "personal-account-test",
      releaseEnv: "test",
    });
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
    vi.stubGlobal(
      "ResizeObserver",
      class ResizeObserver {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    useShellStore.getState().setSession(
      { actorId: principalId, displayName: "个人用户", roleIds: [] },
      "personal-session-token",
    );
    useShellStore.getState().setSessionScopes([], 1, [], []);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    resetRuntimeConfigForTests();
    useShellStore.getState().setSession(null, null);
  });

  it("presents zero organization and project membership as a ready account state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.endsWith("/account/profile")) {
          return Promise.resolve(
            response({
              profile: {
                principal_id: principalId,
                username: "personal-user",
                display_name: "个人用户",
                status: "ACTIVE",
                created_at: "2026-08-25T01:00:00Z",
                updated_at: "2026-08-25T01:00:00Z",
                password_changed_at: "2026-08-25T01:00:00Z",
                revision: 1,
                etag: '"v1"',
              },
              password_policy: {
                min_length: 12,
                max_length: 128,
                disallow_username: true,
                blocked_password_count: 8192,
              },
            }),
          );
        }
        if (url.endsWith("/account/access-overview")) {
          return Promise.resolve(
            response({
              organizations: [],
              projects: [],
              requests: [],
              pending_request_count: 0,
            }),
          );
        }
        if (url.endsWith("/account/notifications/unread-count")) {
          return Promise.resolve(response({ unread_count: 0 }));
        }
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      }),
    );

    render(
      <ProviderHarness>
        <MemoryRouter>
          <AccountPage />
        </MemoryRouter>
      </ProviderHarness>,
    );

    expect(await screen.findByRole("heading", { name: "个人主页" })).toBeVisible();
    expect(await screen.findByText("个人账户已就绪")).toBeVisible();
    expect(screen.getByText(/你尚未加入任何组织或项目/u)).toBeVisible();
    expect(screen.getByText("@personal-user")).toBeVisible();
    expect(screen.getByLabelText("状态：账户正常")).toBeVisible();
    expect(screen.getAllByRole("link", { name: "前往账户设置" })[0]).toHaveAttribute(
      "href",
      "/account/settings",
    );
    expect(screen.queryByLabelText("组织 ID 或加入码")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /提交加入/u })).not.toBeInTheDocument();
  });
});
