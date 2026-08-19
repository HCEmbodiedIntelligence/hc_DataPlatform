// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { ProviderHarness } from "../providers";
import { makeScopeKey } from "../../entities/scope";
import { useShellStore } from "../../shared/scope/shell-store";
import { PlatformShell } from "./PlatformShell";

const scope = {
  organizationId: "org-shell-test",
  projectId: "project-shell-test",
  regionCode: "cn-shanghai",
} as const;

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}</output>;
}

beforeEach(() => {
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    value: class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query.includes("1360"),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  useShellStore.getState().setSession(
    {
      actorId: "actor-shell-test",
      displayName: "测试用户",
      roleIds: ["PROJECT_DEVELOPER"],
    },
    "session-shell-test",
  );
  useShellStore.getState().setScope(scope);
  useShellStore.getState().setAuthorization({
    scopeKey: makeScopeKey(scope),
    roleVersion: "role-shell-test",
    capabilities: ["upload.read", "annotation_task.read", "manual_issue.read"],
    fetchedAt: "2026-08-17T00:00:00Z",
  });
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  useShellStore.setState({
    principal: null,
    sessionToken: null,
    scope: null,
    scopeKey: makeScopeKey({ organizationId: "unscoped" }),
    scopeChanging: false,
    authorization: null,
    authorizationLoading: false,
    authorizationFailed: false,
  });
});

function renderShell() {
  render(
    <ProviderHarness>
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Routes>
          <Route
            path="/"
            element={
              <PlatformShell
                authorizationLoader={async () => ({
                  scopeKey: makeScopeKey(scope),
                  roleVersion: "role-shell-test",
                  capabilities: [
                    "upload.read",
                    "annotation_task.read",
                    "manual_issue.read",
                  ],
                  fetchedAt: "2026-08-17T00:00:00Z",
                })}
                pageAvailability={{
                  P01: true,
                  P03: true,
                  P08: true,
                  P09: true,
                }}
                scopeOptions={[
                  {
                    ...scope,
                    organizationName: "杭叉集团",
                    projectName: "双臂采集一期",
                    regionName: "华东-01",
                  },
                ]}
              />
            }
          >
            <Route path="*" element={<LocationProbe />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ProviderHarness>,
  );
}

describe("PlatformShell", () => {
  it("uses the official fixed-size brand asset and consolidated function navigation", async () => {
    renderShell();

    const brandLink = screen.getByRole("link", {
      name: "杭叉集团 HC 数据平台工作台",
    });
    const logo = screen.getByRole("img", { name: "杭叉集团" });
    expect(brandLink).toHaveAttribute("href", "/dashboard");
    expect(logo).toHaveAttribute(
      "src",
      expect.stringContaining("hangcha-logo.png"),
    );
    expect(logo).toHaveAttribute("width", "106");
    expect(logo).toHaveAttribute("height", "62");
    expect(screen.getByRole("link", { name: "数据上传" })).toHaveAttribute(
      "href",
      "/ingest/uploads/new",
    );
    expect(screen.getByRole("link", { name: "数据标注" })).toHaveAttribute(
      "href",
      "/annotations/annotate",
    );
    expect(screen.getByText("采集与接收")).toBeVisible();
    expect(screen.getByText("数据生产")).toBeVisible();
    expect(screen.queryByText("数据清洗")).not.toBeInTheDocument();
    expect(screen.queryByText("清洗草稿")).not.toBeInTheDocument();
  });

  it("shows project and region context while unavailable global controls are truly disabled", () => {
    renderShell();

    expect(screen.getByRole("combobox", { name: "当前项目" })).toBeEnabled();
    expect(screen.getByRole("combobox", { name: "当前区域" })).toBeEnabled();
    expect(screen.getByText("双臂采集一期")).toBeVisible();
    expect(screen.getByText("华东-01")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "全局搜索尚未开放" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "通知尚未开放" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "账户菜单" })).toBeEnabled();
  });

  it("supports skip navigation and keyboard activation of navigation links", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.tab();
    const skipLink = screen.getByRole("link", { name: "跳到主要内容" });
    expect(skipLink).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("main")).toHaveFocus();

    const uploadLink = screen.getByRole("link", { name: "数据上传" });
    uploadLink.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/ingest/uploads/new",
    );
    await waitFor(() => expect(screen.getByRole("main")).toHaveFocus());
  });
});
