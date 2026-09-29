import { afterEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import { imagePixel, loadDepthPixel } from "./depth-pixels";
import { dataReviewFixture } from "./testing/data-review-fixture";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
afterEach(() => vi.resetAllMocks());
function fixture() {
  const f = dataReviewFixture();
  const target = {
    scope: f.scope,
    data: f.data,
    feature: "observation.images.depth",
    frame: 0,
  };
  const pixel = {
    data: f.data,
    feature: target.feature,
    frame_index: 0,
    x: 1,
    y: 2,
    shape: [16, 16],
    value: 0.01,
    unit: "m",
    valid: true,
    saturated_low: false,
    saturated_high: false,
    flags: 1,
    quantized: true,
    coordinate_space: "depth_native",
    profile: {
      bits: 12,
      depth_min: 0.01,
      depth_max: 10,
      use_log: true,
      profile_sha256: "a".repeat(64),
    },
    source: {},
  };
  return { target, pixel };
}
it("maps a scaled image to native pixels without accepting its outer boundary", () => {
  const rect = { left: 10, top: 20, width: 320, height: 240 };
  expect(imagePixel(170, 140, rect, 640, 480)).toEqual({ x: 320, y: 240 });
  expect(imagePixel(10, 20, rect, 640, 480)).toEqual({ x: 0, y: 0 });
  expect(imagePixel(330, 20, rect, 640, 480)).toBeNull();
  expect(imagePixel(10, 260, rect, 640, 480)).toBeNull();
});
it.each(["frame", "version", "mask", "coordinates", "unit", "scope"])(
  "rejects mismatched %s",
  async (kind) => {
    const { target, pixel } = fixture();
    if (kind === "frame") pixel.frame_index = 1;
    if (kind === "version") pixel.data = { ...pixel.data, version: 99 };
    if (kind === "mask") pixel.flags = 0;
    if (kind === "coordinates") pixel.x = 15;
    if (kind === "unit") pixel.unit = "mm";
    if (kind === "scope")
      target.scope = { ...target.scope, organizationId: "foreign" };
    vi.mocked(request).mockResolvedValue(pixel);
    await expect(loadDepthPixel(target, 1, 2)).rejects.toThrow();
  },
);
it("retains a valid minimum-range measurement and uses the exact frame and scope", async () => {
  const { target, pixel } = fixture();
  vi.mocked(request).mockResolvedValue(pixel);
  expect((await loadDepthPixel(target, 1, 2)).value).toBe(0.01);
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      scope: target.scope,
      path: expect.stringContaining(
        `/${target.data.data_version_id}/depth/${target.feature}/0/pixel`,
      ),
      query: { x: 1, y: 2, unit: "m" },
      cache: "no-store",
    }),
  );
});
