import { testDownloadFolder } from "../../shared/lib/test-save-picker";
// @vitest-environment node
import { BlobReader, BlobWriter, ZipReader, ZipWriter } from "@zip.js/zip.js/lib/zip-core-native.js";
import { expect, it, vi } from "vitest";
import { createDomainError } from "../../shared/api/domain-error";
import { writeSelectionBundle } from "./mcap-bundle";

async function fixture(episode: string) {
  const zip = new ZipWriter(new BlobWriter(), { level: 0, useWebWorkers: false, zip64: true });
  for (const [file, content] of [["sample.mcap", "mcap-content"], ["frames.json", "{}"]] as const)
    await zip.add(`000001-${episode}/collection/${file}`, new BlobReader(new Blob([content])));
  return zip.close();
}

it("retries only a truncated or CRC-damaged Episode and preserves order and companion files", async () => {
  const [a, b] = await Promise.all([fixture("a"), fixture("b")]);
  const corrupt = new Uint8Array(await b.arrayBuffer());
  const marker = Buffer.from(corrupt).indexOf("mcap-content");
  expect(marker).toBeGreaterThan(0);
  corrupt[marker] = 0;
  const load = vi.fn().mockResolvedValueOnce(a).mockResolvedValueOnce(b.slice(0, -20))
    .mockResolvedValueOnce(new Blob([corrupt])).mockResolvedValueOnce(b);
  const chunks: Uint8Array<ArrayBuffer>[] = [];
  await writeSelectionBundle(["a", "b"], load, new WritableStream({ write: bytes => { chunks.push(Uint8Array.from(bytes)); } }),
    new AbortController().signal, vi.fn());
  expect(load.mock.calls.map(call => call[0])).toEqual(["a", "b", "b", "b"]);
  const result = new ZipReader(new BlobReader(new Blob(chunks)), { checkCrc32: true, useWebWorkers: false });
  const entries = await result.getEntries();
  expect(entries.map(entry => entry.filename)).toEqual([
    "000001-a/collection/sample.mcap", "000001-a/collection/frames.json",
    "000002-b/collection/sample.mcap", "000002-b/collection/frames.json",
  ]);
  for (const entry of entries) {
    if (entry.directory) throw new Error("expected file");
    expect(await (await entry.getData(new BlobWriter())).text()).toBe(entry.filename.endsWith(".mcap") ? "mcap-content" : "{}");
  }
  await result.close();
});

it("does not retry revoked access or a destination write failure", async () => {
  const signal = new AbortController().signal;
  const denied = createDomainError({ code: "FORBIDDEN", httpStatus: 403, message: "denied",
    retryable: false, requestId: null, fieldErrors: [], operationErrors: [], blockedReasons: [] });
  const load = vi.fn().mockRejectedValue(denied);
  await expect(writeSelectionBundle(["a"], load, new WritableStream(), signal, vi.fn())).rejects.toBe(denied);
  expect(load).toHaveBeenCalledOnce();
  const disk = vi.fn().mockResolvedValue(await fixture("a"));
  await expect(writeSelectionBundle(["a", "b"], disk, new WritableStream({ write() { throw new Error("disk full"); } }),
    signal, vi.fn())).rejects.toThrow("disk full");
  expect(disk).toHaveBeenCalledOnce();
});

it("saves through the picker and resumes preparation with only the missing selection", async () => {
  const http = await import("../../shared/api/http-client");
  const { useShellStore } = await import("../../shared/scope/shell-store");
  const { packageDeliveryFixture } = await import("../../pages/p21-data-export/testing/package-delivery-fixture");
  const { downloadEpisodeBundle } = await import("./package-deliveries");
  const { data, scope } = packageDeliveryFixture();
  const second = { ...data, identity: { ...data.identity, episode_id: "second" } };
  const ids = ["a".repeat(64), "b".repeat(64)];
  const parts = await Promise.all([fixture("first"), fixture("second")]);
  const file = { write: vi.fn(), close: vi.fn(), abort: vi.fn() };
  const picker = vi.fn(async () => testDownloadFolder(file).directory);
  const session = useShellStore.getState();
  session.setSession({ actorId: "reader", displayName: "Reader", roleIds: [] }, "mcap-test");
  session.setScope(scope);
  session.setSessionScopes([{ ...scope, regionCodes: [scope.regionCode], projectWide: false, capabilities: [] }], 1);
  vi.stubGlobal("window", { showDirectoryPicker: picker });
  let preparations = 0;
  const loads: string[] = [];
  const filter = { stage: "qc_passed" as const, all_batches: true };
  const mock = vi.spyOn(http, "request").mockImplementation(async options => {
    if (options.path.endsWith("/bundle-parts")) {
      const selection = preparations++ === 0 ? [data, second] : [second];
      expect(options.body).toEqual({ data: selection, mode: "mcap", export_filter: filter });
      const row = { index: 0, part_id: ids[preparations - 1], reused: true };
      await new Response(JSON.stringify(row) + "\n").body!.pipeTo(options.downloadTo!);
      return undefined as never;
    }
    expect(options.path).toMatch(/\/bundle$/);
    const body = options.body as { part_ids: string[]; export_filter: typeof filter };
    expect(body.part_ids).toHaveLength(1);
    expect(body.export_filter).toEqual(filter);
    loads.push(...body.part_ids);
    await parts[ids.indexOf(body.part_ids[0]!)]!.stream().pipeTo(options.downloadTo!);
    return undefined as never;
  });
  try {
    await expect(downloadEpisodeBundle(scope, [data, second], "mcap", filter, new AbortController().signal, vi.fn()))
      .resolves.toMatchObject({ kind: "saved", filename: expect.stringMatching(/^mcap_.*\.zip$/u) });
    expect(loads).toEqual(ids);
    expect(picker).toHaveBeenCalledOnce();
    expect(file.close).toHaveBeenCalledOnce();
    expect(file.abort).not.toHaveBeenCalled();
  } finally {
    mock.mockRestore();
    session.clearSensitiveState();
    vi.unstubAllGlobals();
  }
});

it("prepares one thousand and one selected Episodes in pages and publishes one ordered OSS archive", async () => {
  const http = await import("../../shared/api/http-client");
  const { useShellStore } = await import("../../shared/scope/shell-store");
  const { packageDeliveryFixture } = await import("../../pages/p21-data-export/testing/package-delivery-fixture");
  const { downloadEpisodeBundle } = await import("./package-deliveries");
  const { data, scope } = packageDeliveryFixture();
  const selection = Array.from({ length: 1001 }, (_, i) => ({
    ...data, data_version_id: String(i), identity: { ...data.identity, episode_id: String(i) },
  }));
  const id = (i: string) => Number(i).toString(16).padStart(64, "0");
  const sizes: number[] = [];
  const uri = "oss://test/exports/output.zip";
  const store = useShellStore.getState();
  store.setSession({ actorId: "reader", displayName: "Reader", roleIds: [] }, "export-test");
  store.setScope(scope);
  store.setSessionScopes([{ ...scope, regionCodes: [scope.regionCode], projectWide: false, capabilities: [] }], 1);
  const mock = vi.spyOn(http, "request").mockImplementation(async options => {
    let events;
    if (options.path.endsWith("/bundle-parts")) {
      const { data: page } = options.body as { data: typeof selection };
      sizes.push(page.length);
      events = page.map((item, index) => ({ index, part_id: id(item.identity.episode_id), reused: true })).reverse();
    } else {
      expect(options.path).toMatch(/\/oss$/);
      expect(options.body).toMatchObject({ kind: "bundle", part_ids: selection.map(item => id(item.identity.episode_id)) });
      events = [{ uploaded_bytes: 17, oss_uri: uri }];
    }
    await new Response(events.map(event => JSON.stringify(event)).join("\n") + "\n").body!.pipeTo(options.downloadTo!);
    return undefined as never;
  });
  try {
    await expect(downloadEpisodeBundle(scope, selection, "mcap", { stage: "qc_passed" }, new AbortController().signal, vi.fn(), "oss"))
      .resolves.toMatchObject({ kind: "oss", bytes: 17, uri });
    expect(sizes).toEqual([256, 256, 256, 233]);
    expect(mock).toHaveBeenCalledTimes(5);
  } finally {
    mock.mockRestore();
    store.clearSensitiveState();
  }
});
