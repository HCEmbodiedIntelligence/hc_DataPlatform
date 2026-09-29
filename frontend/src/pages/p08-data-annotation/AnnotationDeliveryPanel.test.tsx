// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { AnnotationDeliveryPanel } from "./AnnotationDeliveryPanel";
import {
  listAnnotationDeliveries,
  createAnnotationDelivery,
  commandAnnotationDelivery,
  authorizeAnnotationDelivery,
} from "./annotation-deliveries";
import { deliveryFixture } from "./testing/delivery-fixture";

vi.mock("./annotation-deliveries", () => ({
  listAnnotationDeliveries: vi.fn(),
  createAnnotationDelivery: vi.fn(),
  commandAnnotationDelivery: vi.fn(),
  authorizeAnnotationDelivery: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
function setup(status = "READY", canCreate = true) {
  const f = deliveryFixture();
  const job = {
    ...f.job,
    status,
    phase: status === "FAILED" ? "failed" : "completed",
    error_code: status === "FAILED" ? "PACKAGING_FAILED" : null,
  } as typeof f.job;
  vi.mocked(listAnnotationDeliveries).mockResolvedValue([job]);
  vi.mocked(createAnnotationDelivery).mockResolvedValue(job);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <AnnotationDeliveryPanel
        scope={f.scope}
        history={f.history}
        canCreate={canCreate}
        canDownload
        dirty
      />
    </QueryClientProvider>,
  );
  return { ...f, job, ...view };
}
it("requires an explicit track and unreviewed choice, and keeps media inclusion visible", async () => {
  const f = setup();
  const button = screen.getByRole("button", { name: "生成带标注完整包" });
  expect(button).toBeDisabled();
  expect(
    screen.getByText(/已采集深度视频、深度 profile、有效性掩码/),
  ).toBeVisible();
  await userEvent.click(screen.getByRole("checkbox", { name: /第一段/ }));
  expect(button).toBeDisabled();
  await userEvent.click(
    screen.getByRole("checkbox", { name: /明确下载未审核/ }),
  );
  await userEvent.click(button);
  expect(createAnnotationDelivery).toHaveBeenCalledWith(
    f.scope,
    f.history,
    expect.objectContaining({
      revision: f.revision.revision,
      allow_unreviewed: true,
      track: expect.objectContaining({ annotation_ids: ["first"] }),
    }),
    expect.any(String),
    expect.any(AbortSignal),
  );
  expect(screen.getByText(/未保存的修改不包含/)).toBeVisible();
});
it("rejects overlap only within the chosen track", async () => {
  setup();
  await userEvent.click(
    screen.getByRole("checkbox", { name: /明确下载未审核/ }),
  );
  await userEvent.click(screen.getByRole("checkbox", { name: /第一段/ }));
  expect(
    screen.getByRole("button", { name: "生成带标注完整包" }),
  ).toBeEnabled();
  await userEvent.click(screen.getByRole("checkbox", { name: /交叉标签/ }));
  expect(screen.getByRole("alert")).toHaveTextContent("重叠区间");
  expect(
    screen.getByRole("button", { name: "生成带标注完整包" }),
  ).toBeDisabled();
  expect(createAnnotationDelivery).not.toHaveBeenCalled();
});
it("retries the frozen generation without creating a new revision", async () => {
  const f = setup("FAILED");
  await userEvent.click(
    await screen.findByRole("button", { name: "重试同一版本" }),
  );
  expect(commandAnnotationDelivery).toHaveBeenCalledWith(
    f.scope,
    f.history,
    f.job,
    "retry",
    expect.any(AbortSignal),
  );
  expect(createAnnotationDelivery).not.toHaveBeenCalled();
  expect(authorizeAnnotationDelivery).not.toHaveBeenCalled();
});
it("keeps command buttons disabled without export.create", async () => {
  setup("FAILED", false);
  expect(
    await screen.findByRole("button", { name: "重试同一版本" }),
  ).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "生成带标注完整包" }),
  ).toBeDisabled();
});
it("keeps the idempotency key after an ambiguous network failure", async () => {
  setup();
  vi.mocked(createAnnotationDelivery).mockRejectedValueOnce(
    new Error("连接中断"),
  );
  await userEvent.click(screen.getByRole("checkbox", { name: /第一段/ }));
  await userEvent.click(
    screen.getByRole("checkbox", { name: /明确下载未审核/ }),
  );
  const button = screen.getByRole("button", { name: "生成带标注完整包" });
  await userEvent.click(button);
  await screen.findByText("连接中断");
  await waitFor(() => expect(button).toBeEnabled());
  await userEvent.click(button);
  expect(vi.mocked(createAnnotationDelivery).mock.calls[0]![3]).toBe(
    vi.mocked(createAnnotationDelivery).mock.calls[1]![3],
  );
});
