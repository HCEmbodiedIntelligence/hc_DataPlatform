// @vitest-environment node
import { expect, it, vi } from "vitest";
import { writeExportFile } from "./save-file";
import { stagedExportFile } from "./staged-export-file";
import { testDownloadFolder } from "./test-save-picker";

function setup() {
  const writer = { write: vi.fn(), close: vi.fn(), abort: vi.fn() };
  const folder = testDownloadFolder(writer);
  return { ...folder, writer, handle: stagedExportFile(folder.directory as unknown as FileSystemDirectoryHandle, "result.zip") };
}
const signal = new AbortController().signal;
it("publishes only after the OS finishes closing, then removes the empty staging directory", async () => {
  const { files, pending, directories, handle, writer } = setup();
  directories.add("result");
  files.set("result/original.zip", 500);
  let finish!: () => void;
  writer.close.mockImplementation(() => new Promise<void>(resolve => { finish = resolve; }));
  const done = writeExportFile(handle, signal, file => file.write(new Uint8Array(32)));
  await vi.waitFor(() => expect(writer.close).toHaveBeenCalledOnce());
  expect([...files]).toEqual([["result/original.zip", 500]]);
  expect(directories.has("result (1)")).toBe(true);
  expect(pending.size).toBe(1);
  finish();
  await done;
  expect(handle.name).toBe("result.zip");
  expect([...files]).toEqual([["result/original.zip", 500], ["result (1)/result.zip", 32]]);
  expect(pending.size).toBe(0);
});

it("cleans the current transfer on failure and leaves the chosen folder without a placeholder", async () => {
  const { files, pending, directories, handle, writer } = setup();
  await expect(writeExportFile(handle, signal, async file => {
    await file.write(new Uint8Array(3));
    throw Error("network");
  })).rejects.toThrow("network");
  expect(writer.abort).toHaveBeenCalledOnce();
  expect(files.size).toBe(0);
  expect(pending.size).toBe(0);
  expect(directories.size).toBe(0);
});

it("preserves the completed staged file if the final directory becomes read-only", async () => {
  const { move, files, pending, handle } = setup();
  move.mockRejectedValue(new DOMException("read only", "NotAllowedError"));
  await expect(writeExportFile(handle, signal, file => file.write(new Uint8Array(19)))).rejects.toThrow(/HC下载中-.+\/result\.zip/);
  expect(files.size).toBe(0);
  expect([...pending.values()][0]?.get("result.zip")).toBe(19);
});
