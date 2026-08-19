// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { makeScopeKey } from "../../entities/scope";
import { createDomainError } from "../../shared/api/domain-error";
import { request } from "../../shared/api/http-client";
import { useShellStore } from "../../shared/scope/shell-store";
import type { MembershipRequest } from "./contracts";
import AccessPage from "./page";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));

const requestMock = vi.mocked(request);
const scope = {
  organizationId: "org-p18",
  projectId: "project-a",
  regionCode: "cn-east-01",
} as const;
const pendingMembership: MembershipRequest = {
  request_id: "membership-pending",
  project_id: scope.projectId,
  requester_id: "contractor-17",
  status: "PENDING",
  reason: "参与当前项目的数据整理",
  created_at: "2026-08-18T01:00:00Z",
  updated_at: "2026-08-18T01:00:00Z",
  revision: 1,
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/access"]}>
        <AccessPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  requestMock.mockReset();
  vi.stubGlobal(
    "ResizeObserver",
    class ResizeObserverStub {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
  useShellStore.setState({
    principal: {
      actorId: "admin-1",
      displayName: "项目管理员",
      roleIds: ["PROJECT_ADMIN"],
    },
    sessionToken: "p18-page-test-token",
    scope,
    scopeKey: makeScopeKey(scope),
    scopeChanging: false,
    authorization: {
      scopeKey: makeScopeKey(scope),
      roleVersion: "p18-role-v1",
      capabilities: ["project.access.manage"],
      fetchedAt: "2026-08-18T01:00:00Z",
    },
    authorizationLoading: false,
    authorizationFailed: false,
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("P18 page-owned decision feedback", () => {
  it("keeps a polite dismissible success after the successful mutation refetch removes the selected row and drawer", async () => {
    const user = userEvent.setup();
    let membershipRows: MembershipRequest[] = [pendingMembership];
    requestMock.mockImplementation(async (options) => {
      if (
        options.method === "GET" &&
        options.path.endsWith("membership-requests")
      )
        return { items: membershipRows };
      if (
        options.method === "GET" &&
        options.path.endsWith("capability-requests")
      )
        return { items: [] };
      if (options.method === "POST") {
        membershipRows = [];
        return {
          ...pendingMembership,
          status: "APPROVED",
          decided_by: "admin-1",
          revision: 2,
        };
      }
      throw new Error(`Unexpected request: ${options.method} ${options.path}`);
    });

    renderPage();
    await screen.findByRole("button", { name: /contractor-17/u });
    await user.click(screen.getByRole("button", { name: "提交批准申请" }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("批准申请已由服务端确认。");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(
      screen.queryByRole("dialog", { name: "审批申请" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /contractor-17/u }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "关闭成功提示" })).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "关闭成功提示" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("clears an old success when the tab/search context or project scope changes", async () => {
    const user = userEvent.setup();
    let membershipRows: MembershipRequest[] = [pendingMembership];
    requestMock.mockImplementation(async (options) => {
      if (
        options.method === "GET" &&
        options.path.endsWith("membership-requests")
      )
        return { items: membershipRows };
      if (
        options.method === "GET" &&
        options.path.endsWith("capability-requests")
      )
        return { items: [] };
      membershipRows = [];
      return {
        ...pendingMembership,
        status: "APPROVED",
        decided_by: "admin-1",
        revision: 2,
      };
    });

    renderPage();
    await screen.findByRole("button", { name: /contractor-17/u });
    await user.click(screen.getByRole("button", { name: "提交批准申请" }));
    await screen.findByRole("status");

    await user.click(screen.getByRole("tab", { name: /权限申请/u }));
    expect(
      screen.queryByText("批准申请已由服务端确认。"),
    ).not.toBeInTheDocument();

    membershipRows = [pendingMembership];
    await user.click(screen.getByRole("tab", { name: /项目加入申请/u }));
    await user.click(screen.getByRole("button", { name: "刷新" }));
    await screen.findByRole("button", { name: /contractor-17/u });
    await user.click(screen.getByRole("button", { name: "提交批准申请" }));
    await screen.findByRole("status");

    const nextScope = { ...scope, projectId: "project-b" };
    await act(async () => {
      useShellStore.setState({
        scope: nextScope,
        scopeKey: makeScopeKey(nextScope),
      });
    });
    await waitFor(() =>
      expect(
        screen.queryByText("批准申请已由服务端确认。"),
      ).not.toBeInTheDocument(),
    );
  });

  it("shows only Problem Details after a failed decision, never success", async () => {
    const user = userEvent.setup();
    const failure = createDomainError({
      code: "VERSION_CONFLICT",
      problemCode: "ACCESS_409",
      message: "申请状态已由其他管理员更新。",
      fieldErrors: [],
      operationErrors: [],
      blockedReasons: [],
      requestId: "request-p18-409",
      retryable: false,
      httpStatus: 409,
    });
    requestMock.mockImplementation(async (options) => {
      if (
        options.method === "GET" &&
        options.path.endsWith("membership-requests")
      )
        return { items: [pendingMembership] };
      if (
        options.method === "GET" &&
        options.path.endsWith("capability-requests")
      )
        return { items: [] };
      throw failure;
    });

    renderPage();
    await screen.findByRole("button", { name: /contractor-17/u });
    await user.click(screen.getByRole("button", { name: "提交批准申请" }));

    expect(await screen.findByText("申请状态已变化")).toBeVisible();
    expect(screen.getByText("ACCESS_409")).toBeVisible();
    expect(screen.getByText("request-p18-409")).toBeVisible();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
