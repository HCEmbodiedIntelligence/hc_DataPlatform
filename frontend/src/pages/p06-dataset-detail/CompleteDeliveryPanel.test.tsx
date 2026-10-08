// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { CompleteDeliveryPanel } from "./CompleteDeliveryPanel";
import {
  authorizeCompleteDelivery,
  commandCompleteDelivery,
  listCompleteDeliveries,
  requestCompleteDelivery,
} from "../../features/datasets/episodes/complete-deliveries";
import { completeDeliveryFixture } from "./testing/complete-delivery-fixture";

vi.mock("../../features/datasets/episodes/complete-deliveries", () => ({
  authorizeCompleteDelivery: vi.fn(),
  commandCompleteDelivery: vi.fn(),
  listCompleteDeliveries: vi.fn(),
  requestCompleteDelivery: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.resetAllMocks();
});
function mount(canDownload = true, canCreate = true) {
  const f = completeDeliveryFixture();
  vi.mocked(listCompleteDeliveries).mockResolvedValue([f.job]);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <CompleteDeliveryPanel
        scope={f.scope}
        data={f.data}
        canDownload={canDownload}
        canCreate={canCreate}
      />
    </QueryClientProvider>,
  );
  return f;
}
it("reuses one command key after an interrupted response", async () => {
  const f = mount();
  vi.mocked(requestCompleteDelivery)
    .mockRejectedValueOnce(new Error("连接中断"))
    .mockResolvedValue(f.job);
  const button = screen.getByRole("button", { name: "准备完整数据下载" });
  await userEvent.click(button);
  expect(await screen.findByText("连接中断")).toBeVisible();
  await userEvent.click(button);
  await waitFor(() => expect(requestCompleteDelivery).toHaveBeenCalledTimes(2));
  const [first, second] = vi.mocked(requestCompleteDelivery).mock.calls;
  expect(first![2]).toBe(second![2]);
  expect(first![1]).toEqual(f.data);
});
it("can download an existing archive without granting create permission", async () => {
  const f = mount(true, false);
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  vi.mocked(authorizeCompleteDelivery).mockResolvedValue({
    job_id: f.job.job_id,
    artifact_sha256: f.job.artifact_sha256!,
    size_bytes: 300,
    url: "http://127.0.0.1:49002/file",
    expires_seconds: 300,
  });
  expect(
    screen.getByRole("button", { name: "准备完整数据下载" }),
  ).toBeDisabled();
  await userEvent.click(
    await screen.findByRole("button", { name: "下载完整 ZIP" }),
  );
  await waitFor(() => expect(click).toHaveBeenCalledOnce());
  expect(commandCompleteDelivery).not.toHaveBeenCalled();
});
it("does not query download jobs without permission", () => {
  mount(false, false);
  expect(listCompleteDeliveries).not.toHaveBeenCalled();
  expect(screen.getByText("缺少完整包下载权限")).toBeVisible();
});
