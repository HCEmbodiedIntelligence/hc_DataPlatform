// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { BlobReader, BlobWriter, ZipReader, ZipWriter } from "@zip.js/zip.js/lib/zip-core-native.js";
import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";
import { writeSelectionBundle } from "./mcap-bundle";
import { downloadFileRanges } from "./parallel-file";

afterEach(() => vi.unstubAllGlobals());

it.each([50, 51, 1001])("downloads and merges %i Episodes with bounded concurrency", async count => {
  const ids = Array.from({ length: count }, (_, i) => String(i));
  const zip = new ZipWriter(new BlobWriter(), { level: 0 });
  await zip.add("000001-sample.zip", new BlobReader(new Blob(["sample"])));
  await zip.add("collection_records.json", new BlobReader(new Blob([JSON.stringify({
    time_zone: "Asia/Shanghai", records: { "000001-sample.zip": { task: "test" } },
  })])));
  const input = await zip.close();
  const chunks: Uint8Array<ArrayBuffer>[] = [];
  const sink = new WritableStream<Uint8Array>({ write(c) { chunks.push(Uint8Array.from(c)); } });
  let active = 0, peak = 0;
  let unblock!: () => void;
  const gate = new Promise<void>(resolve => { unblock = resolve; });
  let allowOthers!: () => void;
  const otherGate = new Promise<void>(resolve => { allowOthers = resolve; });
  const finished: string[] = [];
  const transfer = writeSelectionBundle(ids, async id => {
    peak = Math.max(peak, ++active);
    if (id === "0") await gate;
    else await otherGate;
    active--;
    finished.push(id);
    return input;
  }, sink, new AbortController().signal, () => {}, "nested");
  await vi.waitFor(() => expect(peak).toBe(count === 50 ? 1 : 4));
  allowOthers();
  await vi.waitFor(() => expect(finished.length).toBe(count === 50 ? 0 : 3));
  unblock();
  await transfer;
  expect(peak).toBe(count === 50 ? 1 : 4);
  const reader = new ZipReader(new BlobReader(new Blob(chunks)));
  const entries = await reader.getEntries();
  const names = ids.map((_, i) => String(i + 1).padStart(6, "0") + "-sample.zip");
  expect(entries.map(e => e.filename)).toEqual([...names, "collection_records.json"]);
  const record = entries.at(-1)!;
  if (record.directory) throw new Error("invalid manifest");
  expect(Object.keys(JSON.parse(await (await record.getData(new BlobWriter())).text()).records)).toEqual(names);
  await reader.close();
}, 60_000);

it("downloads four archive ranges and verifies the ordered result", async () => {
  const bytes = Uint8Array.from({ length: 96 }, (_, i) => i);
  vi.stubGlobal("window", { location: new URL("https://platform.test") });
  const grant = { url: "/archive", size_bytes: bytes.length, artifact_sha256: bytesToHex(sha256(bytes)), expires_seconds: 300 };
  let unblock!: () => void;
  const gate = new Promise<void>(resolve => { unblock = resolve; });
  const ranges: number[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url, options) => {
    const [, a, b] = /bytes=(\d+)-(\d+)/.exec(options.headers.Range)!;
    const start = Number(a), end = Number(b);
    ranges.push(start);
    if (start === 0) await gate;
    return new Response(bytes.slice(start, end + 1), { status: 206, headers: { "Content-Range": `bytes ${start}-${end}/96` } });
  }));
  const written: number[] = [];
  const transfer = downloadFileRanges(51, grant, async () => grant,
    async chunk => { written.push(...chunk); }, new AbortController().signal, async () => {}, () => {});
  await vi.waitFor(() => expect(ranges).toEqual([0, 24, 48, 72]));
  expect(written).toEqual([]);
  unblock();
  await transfer;
  expect(written).toEqual([...bytes]);
});

it("aborts outstanding transfers if the output disk fails", async () => {
  const { transferInOrder, transferWidth } = await import("../../shared/lib/ordered-transfer");
  const started: number[] = [], cancelled: number[] = [];
  const failure = new Error("disk full");
  const transfer = transferInOrder(51, transferWidth(51), new AbortController().signal, async (index, signal) => {
    started.push(index);
    if (!index) return index;
    return new Promise<number>((_, reject) => signal.addEventListener("abort", () => {
      cancelled.push(index);
      reject(signal.reason);
    }, { once: true }));
  }, async () => { throw failure; });
  await expect(transfer).rejects.toBe(failure);
  expect(started).toEqual([0, 1, 2, 3]);
  expect(cancelled).toEqual([1, 2, 3]);
});
