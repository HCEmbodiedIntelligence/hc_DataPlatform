import { afterEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import {
  loadDataReviews,
  recordDataReview,
  reviewDataReference,
} from "../../features/datasets/episodes/data-reviews";
import { dataReviewFixture } from "./testing/data-review-fixture";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
afterEach(() => vi.resetAllMocks());

it.each(["data", "status", "head", "range", "cursor", "scope"])(
  "rejects inconsistent %s",
  async (field) => {
    const f = dataReviewFixture();
    const page = structuredClone(f.page);
    if (field === "data") page.items[0]!.data.content_hash = "9".repeat(64);
    if (field === "status") page.status = "APPROVED";
    if (field === "head") page.current_sequence = 2;
    if (field === "range")
      page.items[0]!.locations = [
        {
          start_step: 0,
          end_step: f.data.frame_count + 1,
          tag: null,
          comment: "overflow",
        },
      ];
    if (field === "cursor") page.next_before = 10;
    vi.mocked(request).mockResolvedValue(page);
    await expect(
      loadDataReviews(
        field === "scope" ? { ...f.scope, organizationId: "foreign" } : f.scope,
        f.data,
      ),
    ).rejects.toThrow();
    if (field === "scope") expect(request).not.toHaveBeenCalled();
  },
);
it("keeps the fixed version and pagination on the request", async () => {
  const f = dataReviewFixture();
  vi.mocked(request).mockResolvedValue(f.page);
  expect(await loadDataReviews(f.scope, f.data, undefined, 2)).toEqual(f.page);
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      method: "GET",
      scope: f.scope,
      query: { limit: 10, before: 2 },
      cache: "no-store",
      path: expect.stringContaining(
        `/data-versions/${f.data.data_version_id}/data-reviews`,
      ),
    }),
  );
});
it("rejects mixed stream versions and does not invent a version for legacy streams", () => {
  const { data } = dataReviewFixture();
  expect(reviewDataReference([])).toBeNull();
  expect(reviewDataReference([data, structuredClone(data)])).toEqual(data);
  expect(() => reviewDataReference([data, { ...data, version: 99 }])).toThrow(
    /不同版本/,
  );
});
it("checks the recorded decision against the submitted command", async () => {
  const f = dataReviewFixture();
  vi.mocked(request).mockResolvedValue(f.review);
  await expect(
    recordDataReview(
      f.scope,
      f.data,
      {
        expected_content_hash: f.data.content_hash,
        expected_sequence: 0,
        decision: "APPROVED",
        comment: f.review.comment,
        locations: [],
      },
      "key",
    ),
  ).rejects.toThrow(/提交内容/);
});
