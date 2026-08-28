// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { makeScopeKey } from "../../entities/scope";
import { useShellStore } from "../../shared/scope/shell-store";
import { RouteCapabilityGuard } from "./RouteCapabilityGuard";

function renderGuard(requiredCapabilities = ["dataset.read"]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/target"]}>
        <Routes>
          <Route
            element={
              <RouteCapabilityGuard
                requiredCapabilities={requiredCapabilities}
              />
            }
          >
            <Route path="/target" element={<h1>数据集页面</h1>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  useShellStore.getState().clearSensitiveState();
  useShellStore.setState({
    bootstrapLoaded: false,
    authorizationFailed: false,
  });
});

describe("RouteCapabilityGuard", () => {
  it("opens ordinary pages for a bootstrapped personal account without tenant scope", () => {
    useShellStore.setState({
      scope: null,
      scopeKey: makeScopeKey({ organizationId: "unscoped" }),
      authorization: null,
      authorizationLoading: false,
      authorizationFailed: false,
      bootstrapLoaded: true,
    });

    renderGuard();

    expect(screen.getByRole("heading", { name: "数据集页面" })).toBeVisible();
    expect(screen.queryByText("无权访问")).not.toBeInTheDocument();
  });

  it("still blocks a scoped account that lacks the required capability", () => {
    const scope = {
      organizationId: "org-guard",
      projectId: "project-guard",
      regionCode: "cn-test",
    } as const;
    useShellStore.setState({
      scope,
      scopeKey: makeScopeKey(scope),
      authorization: {
        scopeKey: makeScopeKey(scope),
        roleVersion: "guard-role-v1",
        capabilities: [],
        fetchedAt: "2026-08-25T00:00:00Z",
      },
      authorizationLoading: false,
      authorizationFailed: false,
      bootstrapLoaded: true,
    });

    renderGuard();

    expect(screen.getByText("无权访问")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "数据集页面" })).toBeNull();
  });

  it("does not expose administrator-only routes to an unscoped account", () => {
    useShellStore.setState({
      scope: null,
      scopeKey: makeScopeKey({ organizationId: "unscoped" }),
      authorization: null,
      authorizationLoading: false,
      authorizationFailed: false,
      bootstrapLoaded: true,
    });

    renderGuard(["audit.read"]);

    expect(screen.getByText("无权访问")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "数据集页面" })).toBeNull();
  });
});
