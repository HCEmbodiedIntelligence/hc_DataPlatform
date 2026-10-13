// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
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
it("shows the verdict and data identity without passed or skipped rule lists", async () => {
  setup();
  expect(await screen.findByText("自动 QC 总判定：PASS")).toBeVisible();
  expect(screen.getByText("未发现失败项。")).toBeVisible();
  expect(screen.queryByLabelText("逐规则结果统计")).not.toBeInTheDocument();
  expect(screen.queryByText("RGB 黑帧")).not.toBeInTheDocument();
  expect(screen.getByText(/数据集.*版本/)).toBeVisible();
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
