import type { IngestScope } from "../../entities/data-source";
import { request } from "../../shared/api/http-client";
import { createSessionScopeLease } from "../../shared/api/session-scope-lease";
import { nativeRoot } from "../ingest/processing/lerobot-processing";

type DownloadFile = {
  write: (chunk: Uint8Array) => Promise<void>;
  close: () => Promise<void>;
  abort: () => Promise<void>;
};

type SavePicker = (options: {
  suggestedName: string;
  types: { description: string; accept: Record<string, string[]> }[];
}) => Promise<{ name: string; createWritable: () => Promise<DownloadFile> }>;

export type RawDownloadResult =
  | { kind: "saved"; filename: string; bytes: number }
  | { kind: "browser"; filename: string; bytes: number; url: string };

/** One archive request; supported browsers write large downloads directly to disk. */
export async function downloadRawSources(
  scope: IngestScope,
  importIds: readonly string[],
  signal: AbortSignal,
  onProgress: (bytes: number) => void,
): Promise<RawDownloadResult> {
  const lease = createSessionScopeLease(scope, signal);
  const name = `原始数据-${new Date().toISOString().slice(0, 10)}.zip`;
  const picker = (window as Window & { showSaveFilePicker?: SavePicker })
    .showSaveFilePicker;
  let file: DownloadFile | undefined;
  let filename = name;
  const chunks: BlobPart[] = [];
  try {
    lease.signal.throwIfAborted();
    // Invoke the picker before awaiting network work so the user gesture is retained.
    if (picker) {
      const handle = await picker.call(window, {
        suggestedName: name,
        types: [
          {
            description: "ZIP 压缩包",
            accept: { "application/zip": [".zip"] },
          },
        ],
      });
      lease.signal.throwIfAborted();
      filename = handle.name;
      file = await handle.createWritable();
    }
    lease.signal.throwIfAborted();
    let received = 0;
    let lastReport = 0;
    const sink = new WritableStream<Uint8Array>({
      async write(chunk) {
        lease.signal.throwIfAborted();
        if (file) await file.write(chunk);
        else chunks.push(new Uint8Array(chunk));
        received += chunk.byteLength;
        if (performance.now() - lastReport > 100) {
          onProgress(received);
          lastReport = performance.now();
        }
      },
    });
    await request<void>({
      method: "POST",
      path: `${nativeRoot(scope)}/archive`,
      scope,
      scopeMode: "explicit",
      signal: lease.signal,
      cache: "no-store",
      body: { import_ids: importIds },
      downloadTo: sink,
    });
    lease.signal.throwIfAborted();
    if (file) {
      await file.close();
      file = undefined;
      onProgress(received);
      return { kind: "saved", filename, bytes: received };
    }
    // Keep the blob available for a real user click if automatic downloading
    // is blocked. The caller releases it when this result leaves the page.
    const url = URL.createObjectURL(
      new Blob(chunks, { type: "application/zip" }),
    );
    try {
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = name;
      anchor.rel = "noopener noreferrer";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
    } catch (error) {
      URL.revokeObjectURL(url);
      throw error;
    }
    onProgress(received);
    return { kind: "browser", filename, bytes: received, url };
  } finally {
    await file?.abort().catch(() => {});
    lease.release();
  }
}
