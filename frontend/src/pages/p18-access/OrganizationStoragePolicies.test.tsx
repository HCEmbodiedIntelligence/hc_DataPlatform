// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import { OrganizationStoragePolicies } from "./OrganizationStoragePolicies";

const switchScope = vi.hoisted(() => vi.fn());
vi.mock("../../app/providers/ScopeProvider", () => ({ useScope: () => ({ switchScope }) }));
vi.mock("../../features/account/session/runtime-scope", () => ({ loadRuntimeAuthorization: vi.fn() }));
vi.mock("../../shared/api/http-client", async (original) => ({
  ...(await original<typeof import("../../shared/api/http-client")>()), request: vi.fn(),
}));
beforeEach(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  vi.mocked(request).mockResolvedValue({ items: [], page_info: { has_next_page: false } });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

it("uses the organization being managed for both reads and navigation when project IDs repeat", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const content = (org: string) => <MemoryRouter><QueryClientProvider client={client}>
    <OrganizationStoragePolicies key={org} organization={org} projects={[{ project_id: "shared", project_name: "测试项目" }]} />
  </QueryClientProvider></MemoryRouter>;
  const view = render(content("A"));
  fireEvent.click(screen.getByText("测试项目（shared）"));
  await waitFor(() => expect(request).toHaveBeenCalledWith(expect.objectContaining({ scope: { organizationId: "A", projectId: "shared" } })));
  view.rerender(content("B"));
  fireEvent.click(screen.getByText("测试项目（shared）"));
  await waitFor(() => expect(request).toHaveBeenCalledWith(expect.objectContaining({ scope: { organizationId: "B", projectId: "shared" } })));
  fireEvent.click(await screen.findByRole("button", { name: "管理生命周期" }));
  await waitFor(() => expect(switchScope).toHaveBeenCalledWith({ organizationId: "B", projectId: "shared" }, expect.any(Function)));
});

