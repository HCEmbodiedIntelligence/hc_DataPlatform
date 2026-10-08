// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { DataReviewPanel } from "./DataReviewPanel";
import { loadDataReviews, recordDataReview } from "../../features/datasets/episodes/data-reviews";
import { dataReviewFixture } from "./testing/data-review-fixture";

vi.mock("../../features/datasets/episodes/data-reviews", () => ({
  loadDataReviews: vi.fn(),
  recordDataReview: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
function setup(canReview = true, canRead = true) {
  const f = dataReviewFixture();
  vi.mocked(loadDataReviews).mockResolvedValue(f.page);
  vi.mocked(recordDataReview).mockResolvedValue(f.review);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <DataReviewPanel
        scope={f.scope}
        data={f.data}
        canReview={canReview}
        canRead={canRead}
      />
    </QueryClientProvider>,
  );
  return { ...f, ...view };
}
it("shows human review separately and disables writes without permission", async () => {
  setup(false);
  expect(await screen.findByText("人工审核：需返工")).toBeVisible();
  expect(screen.getByText("需补齐任务覆盖")).toBeVisible();
  expect(screen.queryByRole("button", { name: "提交人工审核" })).toBeNull();
  expect(screen.getByText("当前账号没有人工数据审核权限")).toBeVisible();
});
it("does not request records without data read capability", () => {
  setup(true, false);
  expect(loadDataReviews).not.toHaveBeenCalled();
  expect(screen.getByText("缺少数据版本读取权限")).toBeVisible();
});
it("preserves the original command and key after an ambiguous failure", async () => {
  const f = setup();
  vi.mocked(recordDataReview).mockRejectedValueOnce(new Error("连接中断"));
  await userEvent.type(
    await screen.findByLabelText("审核意见"),
    "确认任务覆盖符合要求",
  );
  await userEvent.selectOptions(screen.getByLabelText("审核决定"), "APPROVED");
  const button = screen.getByRole("button", { name: "提交人工审核" });
  await userEvent.click(button);
  await screen.findByText("连接中断");
  await waitFor(() => expect(button).toBeEnabled());
  await userEvent.click(button);
  const [first, second] = vi.mocked(recordDataReview).mock.calls;
  expect(first?.[2]).toEqual(
    expect.objectContaining({
      expected_content_hash: f.data.content_hash,
      expected_sequence: 1,
      decision: "APPROVED",
      comment: "确认任务覆盖符合要求",
    }),
  );
  expect(second?.[2]).toEqual(first?.[2]);
  expect(second?.[3]).toBe(first?.[3]);
});
it("rejects a frame range outside this data version", async () => {
  const f = setup();
  await userEvent.type(await screen.findByLabelText("审核意见"), "定位检查");
  await userEvent.click(screen.getByText("定位到帧区间（可选）"));
  await userEvent.type(screen.getByLabelText("起始帧"), "0");
  await userEvent.type(
    screen.getByLabelText("结束帧"),
    String(f.data.frame_count + 1),
  );
  expect(screen.getByRole("button", { name: "提交人工审核" })).toBeDisabled();
  expect(recordDataReview).not.toHaveBeenCalled();
});
