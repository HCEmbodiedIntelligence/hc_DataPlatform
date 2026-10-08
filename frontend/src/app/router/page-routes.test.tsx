// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildPageCatalog,
  definePage,
  filterPageNavigation,
} from "../../shared/routing/page-descriptor";
import { makeScopeKey } from "../../shared/scope/model";
import { useShellStore } from "../../shared/scope/shell-store";
import { guardedPageRoutes } from "./page-routes";

afterEach(() => {
  cleanup();
  useShellStore.getState().clearSensitiveState();
  useShellStore.setState({
    bootstrapLoaded: false,
    authorizationFailed: false,
  });
});

describe("registered page integration", () => {
  it.each([true, false])(
    "opens a newly registered route only with its declared grant: %s",
    async (granted) => {
      const visited = vi.fn();
      const page = definePage({
        id: "EXAMPLE",
        enabled: true,
        routes: [
          {
            path: "/extension-example",
            requiredCapabilities: ["dataset.read"],
            navigation: {
              label: "扩展页面",
              groupId: "production",
              order: 1,
              visibility: "authorized",
            },
            lazy: async () => ({
              Component: () => {
                visited();
                return <h1>扩展页面</h1>;
              },
            }),
          },
        ],
      });
      const scope = {
        organizationId: "org-example",
        projectId: "project-example",
        regionCode: "cn-test",
      };
      const capabilities = granted ? ["dataset.read"] : [];
      useShellStore.setState({
        scope,
        scopeKey: makeScopeKey(scope),
        bootstrapLoaded: true,
        authorizationLoading: false,
        authorizationFailed: false,
        platformCapabilities: [],
        authorization: {
          scopeKey: makeScopeKey(scope),
          roleVersion: "v1",
          capabilities,
          fetchedAt: "2026-09-30T00:00:00Z",
        },
      });
      const catalog = buildPageCatalog([page]);
      const router = createMemoryRouter(guardedPageRoutes(catalog), {
        initialEntries: ["/extension-example"],
      });
      render(
        <QueryClientProvider client={new QueryClient()}>
          <RouterProvider router={router} />
        </QueryClientProvider>,
      );
      const navigation = filterPageNavigation(
        catalog.navigation,
        new Set(capabilities),
        catalog.availability,
      );
      if (granted) {
        expect(
          await screen.findByRole("heading", { name: "扩展页面" }),
        ).toBeVisible();
        expect(navigation[0]?.items[0]?.path).toBe("/extension-example");
        expect(visited).toHaveBeenCalled();
      } else {
        expect(await screen.findByText("无权访问")).toBeVisible();
        expect(visited).not.toHaveBeenCalled();
        expect(navigation).toEqual([]);
      }
      router.dispose();
    },
  );
});
