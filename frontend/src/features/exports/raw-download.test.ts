import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import { useShellStore } from "../../shared/scope/shell-store";
import { downloadRawSources } from "./raw-download";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
const scope = {
  organizationId: "org",
  projectId: "project",
  regionCode: "local",
};
const file = { write: vi.fn(), close: vi.fn(), abort: vi.fn() };
const createWritable = vi.fn(async () => file);
const picker = vi.fn(async () => ({
  name: "我的原始数据.zip",
  createWritable,
}));

beforeEach(() => {
  vi.stubGlobal("window", { showSaveFilePicker: picker });
  file.write.mockResolvedValue(undefined);
  file.close.mockResolvedValue(undefined);
  file.abort.mockResolvedValue(undefined);
  const store = useShellStore.getState();
  store.setSession(
    { actorId: "reader", displayName: "Reader", roleIds: [] },
    "test-session",
  );
  store.setScope(scope);
  store.setSessionScopes(
    [
      {
        ...scope,
        regionCodes: [scope.regionCode],
        projectWide: false,
        capabilities: [],
      },
    ],
    1,
  );
});

afterEach(() => {
  useShellStore.getState().clearSensitiveState();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

it("writes the archive to the chosen file and commits it only after the full response", async () => {
  const bytes = new Uint8Array([80, 75, 3, 4]);
  vi.mocked(request).mockImplementationOnce(async (options) => {
    expect(options.body).toEqual({ import_ids: ["raw-a", "raw-b"] });
    expect(file.close).not.toHaveBeenCalled();
    await new Response(bytes).body!.pipeTo(options.downloadTo!, {
      signal: options.signal,
    });
    expect(file.close).not.toHaveBeenCalled();
    return undefined as never;
  });
  const progress = vi.fn();
  const result = await downloadRawSources(
    scope,
    ["raw-a", "raw-b"],
    new AbortController().signal,
    progress,
  );
  expect(picker).toHaveBeenCalledOnce();
  expect(file.write).toHaveBeenCalledWith(bytes);
  expect(file.close).toHaveBeenCalledOnce();
  expect(file.abort).not.toHaveBeenCalled();
  expect(progress).toHaveBeenLastCalledWith(4);
  expect(result).toEqual({
    kind: "saved",
    filename: "我的原始数据.zip",
    bytes: 4,
  });
});

it("does not request an archive when the save dialog is cancelled", async () => {
  picker.mockRejectedValueOnce(new DOMException("Cancelled", "AbortError"));
  await expect(
    downloadRawSources(scope, ["raw-a"], new AbortController().signal, vi.fn()),
  ).rejects.toMatchObject({ name: "AbortError" });
  expect(request).not.toHaveBeenCalled();
  expect(createWritable).not.toHaveBeenCalled();
});

it("aborts a partial file when the archive transfer fails", async () => {
  vi.mocked(request).mockImplementationOnce(async (options) => {
    const writer = options.downloadTo!.getWriter();
    await writer.write(new Uint8Array([80, 75]));
    writer.releaseLock();
    throw new Error("transfer interrupted");
  });
  await expect(
    downloadRawSources(scope, ["raw-a"], new AbortController().signal, vi.fn()),
  ).rejects.toThrow("transfer interrupted");
  expect(file.write).toHaveBeenCalledOnce();
  expect(file.abort).toHaveBeenCalledOnce();
  expect(file.close).not.toHaveBeenCalled();
});

it("does not commit a completed response after cancellation", async () => {
  const controller = new AbortController();
  vi.mocked(request).mockImplementationOnce(async () => {
    controller.abort();
    return undefined as never;
  });
  await expect(
    downloadRawSources(scope, ["raw-a"], controller.signal, vi.fn()),
  ).rejects.toMatchObject({ name: "AbortError" });
  expect(file.abort).toHaveBeenCalledOnce();
  expect(file.close).not.toHaveBeenCalled();
});
