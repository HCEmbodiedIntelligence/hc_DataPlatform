// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { request } from "../../../shared/api/http-client";
import { LeRobotProcessingRecords } from "./LeRobotProcessingRecords";

vi.mock("../../../shared/api/http-client", () => ({ request: vi.fn() }));
vi.mock("./ProcessingLabels", () => ({
  useProcessingConfiguration: () => ({}),
  ProcessingLabels: () => null,
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("explains the 22 unfinished episodes and refreshes both pages after retry", async () => {
  const progress = {
    import_id: "import-a",
    dataset_id: "dataset-a",
    status: "PARTIALLY_FAILED",
    episode_count: 154,
    ready: 132,
    failed: 22,
    resume_required: 21,
    reprocessing_conflicts: 1,
    last_error_code: "RUNTIMEERROR",
    source_format: "LEROBOT_V3",
    file_count: 50,
    total_bytes: 100,
    updated_at: "2026-09-21T03:10:12Z",
  };
  vi.mocked(request).mockImplementation(async (input) =>
    input.method === "POST" ? { ...progress, status: "PENDING" } : [progress],
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <LeRobotProcessingRecords
          scope={{
            organizationId: "org",
            projectId: "project",
            regionCode: "region",
          }}
          canManage
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(
    await screen.findByText(/共 154 个 Episode · 已就绪 132 · 处理未完成 22/),
  ).toBeVisible();
  expect(screen.getByText(/待继续处理 21 · 处理冲突 1/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "重试未完成处理" }));
  await waitFor(() =>
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["dashboard"] }),
  );
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: ["native-processing", "org", "project", "region"],
  });
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      method: "POST",
      path: "/projects/project/regions/region/lerobot-imports/import-a:retry",
    }),
  );
  client.clear();
});
