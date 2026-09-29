// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { DepthPixelPanel } from "./DepthPixelPanel";
import { loadDepthPixel, loadDepthPreview } from "./depth-pixels";
import { dataReviewFixture } from "./testing/data-review-fixture";

vi.mock("./depth-pixels", async (original) => ({
  ...(await original<typeof import("./depth-pixels")>()),
  loadDepthPreview: vi.fn(),
  loadDepthPixel: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  vi.unstubAllGlobals();
});
function setup(canRead = true) {
  const f = dataReviewFixture();
  const revoke = vi.fn();
  vi.stubGlobal("URL", {
    createObjectURL: () => "blob:official-depth",
    revokeObjectURL: revoke,
  });
  vi.mocked(loadDepthPreview).mockResolvedValue(
    new Blob(["official png fixture"]),
  );
  vi.mocked(loadDepthPixel).mockResolvedValue({
    data: f.data,
    feature: "depth",
    frame_index: 0,
    x: 0,
    y: 0,
    shape: [16, 16],
    value: null,
    unit: "m",
    valid: false,
    saturated_low: false,
    saturated_high: false,
    flags: 0,
    quantized: true,
    coordinate_space: "depth_native",
    profile: {
      bits: 12,
      depth_min: 0.01,
      depth_max: 10,
      use_log: true,
      profile_sha256: "a".repeat(64),
    },
    source: { missing: true },
  });
  const result = render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <DepthPixelPanel
        scope={f.scope}
        data={f.data}
        canRead={canRead}
        cameras={[{ feature: "depth", label: "Head", start: 0, end: 4 }]}
      />
    </QueryClientProvider>,
  );
  return { ...result, revoke };
}
it("loads only the explicitly selected frame and releases its official preview", async () => {
  const { unmount, revoke } = setup();
  expect(loadDepthPreview).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "查看取值帧" }));
  expect(await screen.findByAltText("RealSense 深度取值帧 0")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "读取物理深度" }));
  expect(await screen.findByText(/无效，无物理测量值/)).toBeVisible();
  expect(screen.getByText("源帧缺失")).toBeVisible();
  const input = screen.getByRole("spinbutton", { name: "取值帧" });
  await userEvent.clear(input);
  await userEvent.type(input, "2");
  expect(loadDepthPreview).toHaveBeenCalledTimes(1);
  await userEvent.click(screen.getByRole("button", { name: "查看取值帧" }));
  await waitFor(() =>
    expect(loadDepthPreview).toHaveBeenLastCalledWith(
      expect.objectContaining({ frame: 2 }),
      expect.any(AbortSignal),
    ),
  );
  unmount();
  expect(revoke).toHaveBeenCalledWith("blob:official-depth");
});
it("does not expose a frame or read action without permission", () => {
  setup(false);
  expect(screen.getByText("缺少数据版本读取权限")).toBeVisible();
  expect(loadDepthPreview).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("button", { name: "查看取值帧" }),
  ).not.toBeInTheDocument();
});
