import { transferInOrder, transferWidth } from "../../shared/lib/ordered-transfer";
import { isDomainError } from "../../shared/api/domain-error";
import { BlobReader, BlobWriter, ZipReader, ZipWriter } from "@zip.js/zip.js/lib/zip-core-native.js";

type Layout = "mcap" | "nested" | "raw";

async function readEpisode(blob: Blob, signal: AbortSignal, layout: Layout) {
  const reader = new ZipReader(new BlobReader(blob), {
    checkCrc32: true, filenameValidation: "strict", useWebWorkers: false,
  });
  try {
    const files: { path: string; data: Blob }[] = [];
    let root: string | undefined;
    let records: Record<string, unknown> = {};
    for (const entry of await reader.getEntries()) {
      signal.throwIfAborted();
      const path = entry.filename;
      const segments = path.split("/");
      if (entry.directory || entry.encrypted || entry.symlink || path.includes("\\") ||
          segments.some(s => !s || s === "." || s === "..")) throw new Error("导出分包内的文件路径异常。");
      if (layout !== "mcap" && path === "collection_records.json") {
        const content = await entry.getData(new BlobWriter(), { signal });
        const document = JSON.parse(await content.text());
        if (document.time_zone !== "Asia/Shanghai" || !document.records || typeof document.records !== "object" ||
            Array.isArray(document.records)) throw new Error("采集记录格式异常。");
        records = document.records;
        continue;
      }
      const folder = segments[0]!;
      if (layout === "mcap" && (!folder.startsWith("000001-") || segments.length < 2 || (root && root !== folder)))
        throw new Error("MCAP 包内的目录结构异常。");
      if (layout === "nested" && (segments.length !== 1 || !/^000001-.+\.zip$/u.test(path)))
        throw new Error("Episode 分包文件名异常。");
      root = folder;
      const data = await entry.getData(new BlobWriter(), { signal });
      files.push({ path, data });
    }
    if (layout === "mcap" && !files.some(file => file.path.endsWith(".mcap") && file.data.size > 0))
      throw new Error("Episode 内缺少 MCAP 数据。");
    if (files.length === 0 || (layout === "nested" && files.length !== 1)) throw new Error("导出分包内容不完整。");
    return { files, records };
  } finally {
    await reader.close();
  }
}

async function checkedEpisode(load: () => Promise<Blob>, signal: AbortSignal, layout: Layout, report: (attempt: number) => void) {
  for (let attempt = 1; ; attempt++) {
    signal.throwIfAborted();
    report(attempt);
    try {
      return await readEpisode(await load(), signal, layout);
    } catch (error) {
      signal.throwIfAborted();
      if (isDomainError(error) && error.code !== "NETWORK_ERROR" && ![502, 503, 504].includes(error.httpStatus ?? 0))
        throw error;
      if (attempt === 3)
        throw new Error("当前 Episode 下载或校验失败，已尝试 3 次。", { cause: error });
    }
    await new Promise(resolve => setTimeout(resolve, 500 * attempt));
  }
}

// Each request returns one complete, independently verifiable Episode archive.
// Only a verified Episode may add entries to the selected output file.
export async function writeSelectionBundle(
  ids: readonly string[], load: (id: string, signal: AbortSignal) => Promise<Blob>, sink: WritableStream<Uint8Array>,
  signal: AbortSignal, report: (message: string) => void, layout: Layout = "mcap",
) {
  if (!ids.length || new Set(ids).size !== ids.length) throw new Error("MCAP 导出条目为空或重复。");
  const output = new ZipWriter(sink, { level: 0, useWebWorkers: false, bufferedWrite: false });
  const records = new Map<string, unknown>();
  let fileCount = 0;
  await transferInOrder(ids.length, transferWidth(ids.length), signal,
    (index, active) => checkedEpisode(() => load(ids[index]!, active), active, layout, attempt => {
      report(`${attempt === 1 ? "正在下载" : "正在重试"} ${layout === "raw" ? "批次" : "Episode"} ${index + 1}/${ids.length}`);
    }),
    async (index, part, active) => {
      const name = (path: string) => layout === "raw" ? path : String(index + 1).padStart(6, "0") + path.slice(6);
      for (const [key, value] of Object.entries(part.records)) {
        if (records.has(name(key))) throw new Error("采集记录重复。");
        records.set(name(key), value);
      }
      for (const { path, data } of part.files) {
        active.throwIfAborted();
        if (layout === "raw" && ++fileCount > 100_000) throw new Error("一次最多下载 100000 个原始文件。");
        await output.add(name(path), new BlobReader(data), { signal: active });
      }
    });
  if (records.size) {
    const contents = JSON.stringify({ time_zone: "Asia/Shanghai", records: Object.fromEntries(records) }, null, 2);
    await output.add("collection_records.json", new BlobReader(new Blob([contents])), { signal });
  }
  signal.throwIfAborted();
  await output.close();
}
