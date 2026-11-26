import { testDownloadFolder } from "../../shared/lib/test-save-picker";
import { afterEach, expect, it, vi } from "vitest";
import { chooseExportFile, saveAuthorizedArchive } from "./save-file";

afterEach(() => vi.unstubAllGlobals());

it("explains how to choose a location when the browser has no picker", async () => {
  vi.stubGlobal("window", {});
  await expect(chooseExportFile("data.zip")).rejects.toThrow(/HTTPS/);
});

it("discards a truncated authorized download without committing the file", async () => {
  vi.stubGlobal("window", { location: new URL("https://platform.test") });
  const file = { write: vi.fn(), close: vi.fn(), abort: vi.fn(async () => {}) };
  const handle = { name: "collection.zip", createWritable: async () => file };
  vi.stubGlobal("fetch", vi.fn(async () => new Response(new Uint8Array([80, 75]))));
  await expect(saveAuthorizedArchive(handle, { url: "/file", size_bytes: 4 }, new AbortController().signal))
    .rejects.toThrow(/不完整/);
  expect(file.write).toHaveBeenCalledOnce();
  expect(file.abort).toHaveBeenCalledOnce();
  expect(file.close).not.toHaveBeenCalled();
});


it("leaves the destination untouched when the user cancels a single-file download", async () => {
  const { saveFileFromClick } = await import("../../shared/lib/save-file");
  vi.stubGlobal("window", { showDirectoryPicker: vi.fn(async () => { throw new DOMException("cancel", "AbortError"); }) });
  const authorize = vi.fn();
  expect(await saveFileFromClick("info.json", new AbortController().signal, authorize)).toBeNull();
  expect(authorize).not.toHaveBeenCalled();
});


it("waits for the selected destination before authorizing and streams an OSS file through the platform", async () => {
  const { saveFileFromClick } = await import("../../shared/lib/save-file");
  const http = await import("../../shared/api/http-client");
  const url = "https://data.oss-cn-beijing.aliyuncs.com/file.json?Signature=example";
  const bytes = new Uint8Array([1, 2, 3]);
  const file = { write: vi.fn(), close: vi.fn(), abort: vi.fn() };
  const { directory: handle, files } = testDownloadFolder(file);
  let choose!: (value: typeof handle) => void;
  const picker = vi.fn(() => new Promise<typeof handle>(resolve => { choose = resolve; }));
  vi.stubGlobal("window", { location: new URL("https://platform.test"), showDirectoryPicker: picker });
  const request = vi.spyOn(http, "request").mockImplementationOnce(async options => {
    const writer = options.downloadTo!.getWriter();
    await writer.write(bytes);
    await writer.close();
    return undefined as never;
  });
  const authorize = vi.fn(async () => ({ url, size_bytes: 3 }));
  try {
    const result = saveFileFromClick("metadata/info.json", new AbortController().signal, authorize);
    expect(picker).toHaveBeenCalledWith({ id: "hc-downloads", mode: "readwrite" });
    expect(authorize).not.toHaveBeenCalled();
    choose(handle);
    expect(await result).toBe("info.json");
    expect(files.get("info.json")).toBe(3);
    expect(request).toHaveBeenCalledWith(expect.objectContaining({
      method: "POST", path: "/storage-downloads/content", body: { url }, cache: "no-store",
    }));
    expect(file.write).toHaveBeenCalledWith(bytes);
    expect(file.close).toHaveBeenCalledOnce();
  } finally { request.mockRestore(); }
});
