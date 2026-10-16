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
}) => Promise<{ createWritable: () => Promise<DownloadFile> }>;

/** One archive request; supported browsers write large downloads directly to disk. */
export async function downloadRawSources(
  scope: IngestScope,
  importIds: readonly string[],
  signal: AbortSignal,
  onProgress: (bytes: number) => void,
) {
  const lease = createSessionScopeLease(scope, signal);
  const name = `原始数据-${new Date().toISOString().slice(0, 10)}.zip`;
  const picker = (window as Window & { showSaveFilePicker?: SavePicker })
    .showSaveFilePicker;
  let file: DownloadFile | undefined;
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
    } else {
      const url = URL.createObjectURL(
        new Blob(chunks, { type: "application/zip" }),
      );
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = name;
      anchor.rel = "noopener noreferrer";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    }
    onProgress(received);
  } finally {
    await file?.abort().catch(() => {});
    lease.release();
  }
}
