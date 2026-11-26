import { testDownloadFolder } from "../../shared/lib/test-save-picker";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "../../shared/api/http-client";
import { useShellStore } from "../../shared/scope/shell-store";
import { exportArchiveName } from "./archive-name";
import { downloadRawSources } from "./raw-download";

vi.mock("../../shared/api/http-client", () => ({ request: vi.fn() }));
const scope = {
  organizationId: "org",
  projectId: "project",
  regionCode: "local",
};
const file = { write: vi.fn(), close: vi.fn(), abort: vi.fn() };
const createFolder = vi.fn(async () => testDownloadFolder(file).directory);
const picker = vi.fn(createFolder);

beforeEach(() => {
  vi.stubGlobal("window", { showDirectoryPicker: picker });
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
    filename: expect.stringMatching(/\.zip$/u),
    bytes: 4,
  });
});

it("does not request an archive when the save dialog is cancelled", async () => {
  picker.mockRejectedValueOnce(new DOMException("Cancelled", "AbortError"));
  await expect(
    downloadRawSources(scope, ["raw-a"], new AbortController().signal, vi.fn()),
  ).resolves.toEqual({ kind: "cancelled" });
  expect(request).not.toHaveBeenCalled();
  expect(file.write).not.toHaveBeenCalled();
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

it("uses format, distinct dataset numbers and the Beijing export minute across midnight", () => {
  const now = new Date("2026-11-18T16:05:59Z");
  expect(exportArchiveName("holobrain", ["30", "30"], now)).toBe("holobrain_30_20261119_0005.zip");
  expect(exportArchiveName("rgbd", ["31", "30"], now)).toBe("lerobot_rgbd_30+31_20261119_0005.zip");
});
