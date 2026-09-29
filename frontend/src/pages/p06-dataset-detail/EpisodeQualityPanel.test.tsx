// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { EpisodeQualityPanel } from "./EpisodeQualityPanel";
import { loadEpisodeQuality } from "./episode-quality";
import { qualityFixture } from "./testing/quality-fixture";

vi.mock("./episode-quality", async (original) => ({
  ...(await original<typeof import("./episode-quality")>()),
  loadEpisodeQuality: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
function setup(legacy = false, canRead = true) {
  const f = qualityFixture();
  if (legacy) {
    f.quality.evidence = null;
    f.quality.evidence_status = "LEGACY_MISSING";
  }
  vi.mocked(loadEpisodeQuality).mockResolvedValue(f.quality);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <EpisodeQualityPanel scope={f.scope} data={f.data} canRead={canRead} />
    </QueryClientProvider>,
  );
}
it("shows total PASS while keeping unmeasured rules visibly separate", async () => {
  setup();
  expect(await screen.findByText("自动 QC 总判定：PASS")).toBeVisible();
  expect(screen.getByText("通过 0")).toBeVisible();
  expect(screen.getByText("未验明 / 跳过 1")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: /RGB 黑帧/ }));
  expect(await screen.findByText("缺少可评估观测")).toBeVisible();
});
it("displays the missing historical evidence reason", async () => {
  setup(true);
  expect(await screen.findByText("历史报告缺少逐规则执行证据")).toBeVisible();
  expect(screen.getByText("自动 QC 总判定：PASS")).toBeVisible();
});
it("does not load QC without the read capability", () => {
  setup(false, false);
  expect(loadEpisodeQuality).not.toHaveBeenCalled();
  expect(screen.getByText("缺少数据版本读取权限")).toBeVisible();
});
