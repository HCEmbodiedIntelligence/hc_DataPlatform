import { mockSavePicker } from "../../shared/lib/test-save-picker";
// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { BatchQCDownload } from "./BatchQCDownload";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../../shared/api/http-client", () => ({ request }));
vi.mock("../../shared/auth/use-capabilities", () => ({
  useCapabilities: () => ({ has: () => true }),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  request.mockReset();
});

it("downloads the selected batch and releases the previous file after switching batches", async () => {
  const scope = { organizationId: "01", projectId: "01", regionCode: "global" };
  const v = "a".repeat(64);
  const { write, files } = mockSavePicker();
  request
    .mockResolvedValueOnce({
      version: v,
      phase: "ready",
      total: 103,
      completed: 103,
    })
    .mockResolvedValueOnce(new Blob(["xlsx"]));
  const ui = render(<BatchQCDownload scope={scope} number={882} />);
  fireEvent.click(screen.getByRole("button", { name: "下载质检报告" }));
  await waitFor(() =>
    expect([...files.keys()]).toEqual(["第882次上传-录制缺失清单.xlsx"]),
  );
  const base =
    "/projects/01/regions/global/lerobot-imports/upload-batches/882/qc-report";
  expect(request.mock.calls[0]?.[0]).toMatchObject({
    path: base,
    scope,
    method: "POST",
  });
  expect(request.mock.calls[1]?.[0]).toMatchObject({
    path: base + "/" + v + "/download",
    scope,
    method: "GET",
    responseType: "blob",
  });
  ui.rerender(<BatchQCDownload scope={scope} numbers={[883, 882, 883]} />);
  request.mockResolvedValueOnce(new Blob(["combined"]));
  fireEvent.click(screen.getByRole("button", { name: "下载质检报告" }));
  await waitFor(() =>
    expect([...files.keys()].at(-1)).toBe("上传批次质检报告（2批）.xlsx"),
  );
  expect(request.mock.calls[2]?.[0]).toMatchObject({
    method: "POST",
    path: "/projects/01/regions/global/lerobot-imports/upload-batches/qc-reports/download",
    body: { batch_numbers: [882, 883] },
    responseType: "blob",
  });
  ui.rerender(<BatchQCDownload scope={scope} number={881} />);
  expect(write).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("link", { name: "保存 Excel" })).toBeNull();
});
