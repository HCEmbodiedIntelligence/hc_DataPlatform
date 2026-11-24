// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { testDownloadFolder } from "../../shared/lib/test-save-picker";
import { packageDeliveryFixture } from "../../pages/p21-data-export/testing/package-delivery-fixture";
import { createPackageBatch, downloadPackageJob } from "./package-deliveries";
import { exportHoloBrain } from "./queued-holobrain";

vi.mock("./package-deliveries", () => ({ createPackageBatch: vi.fn(), downloadPackageJob: vi.fn(), loadPackageJob: vi.fn() }));
afterEach(() => { vi.unstubAllGlobals(); vi.resetAllMocks(); });

it.each([true, false])("creates the working folder before the server job and cleans it after success=%s", async success => {
  const f = packageDeliveryFixture();
  const folder = testDownloadFolder({ write: vi.fn(), close: vi.fn(), abort: vi.fn() });
  const picker = vi.fn(async () => folder.directory);
  vi.stubGlobal("window", { showDirectoryPicker: picker, location: new URL("https://test.local"), setTimeout });
  vi.stubGlobal("fetch", vi.fn(async () => new Response("abc")));
  let finish!: () => void;
  vi.mocked(createPackageBatch).mockImplementationOnce(async () => {
    await new Promise<void>(resolve => { finish = resolve; });
    if (!success) throw Error("server failed");
    return { ...f.job, status: "READY" };
  });
  vi.mocked(downloadPackageJob).mockResolvedValue({ job_id: f.job.job_id, url: "/sample.zip", expires_seconds: 300,
    artifact_sha256: "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", size_bytes: 3 });
  const report = vi.fn();
  const result = exportHoloBrain(f.scope, [f.data], { stage: "qc_passed", all_batches: true }, "zip",
    new AbortController().signal, report, "sample.zip", async () => {});
  await vi.waitFor(() => expect(createPackageBatch).toHaveBeenCalledOnce());
  expect(folder.directories.has("sample")).toBe(true);
  expect(folder.pending.size).toBe(1);
  expect(folder.files.size).toBe(0);
  finish();
  if (success) {
    await expect(result).resolves.toMatchObject({ kind: "saved", bytes: 3, filename: "sample.zip" });
    expect(folder.files.get("sample/sample.zip")).toBe(3);
    expect(report).toHaveBeenCalledWith(expect.stringContaining("正在下载"));
  } else {
    await expect(result).rejects.toThrow("server failed");
    expect(folder.files.size).toBe(0);
    expect(folder.directories.size).toBe(0);
  }
  expect(folder.pending.size).toBe(0);
});
