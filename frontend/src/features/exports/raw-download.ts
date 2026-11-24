import { transferWidth } from "../../shared/lib/ordered-transfer";
import type { IngestScope } from "../../entities/data-source";
import { request } from "../../shared/api/http-client";
import { createSessionScopeLease } from "../../shared/api/session-scope-lease";
import { nativeRoot } from "../ingest/processing/lerobot-processing";

import { exportToOss, type ExportDestination, type OssReceipt } from "./oss-export";

import { chooseExportFile, writeExportFile } from "./save-file";

export type RawDownloadResult = OssReceipt | { kind: "saved"; filename: string; bytes: number };

type ArchiveDownloadOutcome = RawDownloadResult | { kind: "cancelled" };

/** One archive request; supported browsers write large downloads directly to disk. */
export async function downloadRawSources(
  scope: IngestScope,
  importIds: readonly string[],
  signal: AbortSignal,
  onProgress: (bytes: number) => void,
  filter?: { stage: "dataset"; batch_numbers: readonly number[]; uploaded_from?: string; uploaded_before?: string; all_batches?: boolean },
  destination: ExportDestination = "zip",
  suggestedName?: string,
  checkpoint?: () => Promise<void>, saving?: () => void,
): Promise<ArchiveDownloadOutcome> {
  const name = suggestedName ?? `原始数据-${new Date().toISOString().slice(0, 10)}.zip`;
  const path = `${nativeRoot(scope)}/archive`;
  const body = { import_ids: importIds, ...(suggestedName && { download_name: suggestedName }), ...(filter && { export_filter: filter }) };
  return downloadArchive(scope, signal, onProgress, name, async lease => ({
    path, body, ...(destination === "zip" && transferWidth(importIds.length) > 1 && {
      async writeLocal(sink: WritableStream<Uint8Array>) {
        const { writeSelectionBundle } = await import("./mcap-bundle");
        await writeSelectionBundle(importIds, (id, active) =>
          fetchArchivePart(scope, path, { ...body, import_ids: [id] }, active, checkpoint), sink, lease, () => {}, "raw");
      },
    }),
  }), "raw", destination, checkpoint, undefined, saving);
}

export async function fetchArchivePart(scope: IngestScope, path: string, body: Record<string, unknown>,
  signal: AbortSignal, checkpoint?: () => Promise<void>) {
  await checkpoint?.();
  signal.throwIfAborted();
  const stream = new TransformStream<Uint8Array, Uint8Array>({
    async transform(chunk, output) {
      await checkpoint?.();
      signal.throwIfAborted();
      output.enqueue(chunk);
    },
  });
  const download = request<void>({ method: "POST", path, scope, scopeMode: "explicit", body,
    signal, cache: "no-store", downloadTo: stream.writable });
  try {
    const [, blob] = await Promise.all([download, new Response(stream.readable).blob()]);
    return blob;
  } catch (error) {
    await stream.writable.abort(error).catch(() => {});
    throw error;
  }
}

export async function downloadArchive(
  scope: IngestScope, signal: AbortSignal, onProgress: (bytes: number) => void,
  name: string, prepare: (signal: AbortSignal, suggestedName: string) => Promise<{
    path: string; body: Record<string, unknown>; writeLocal?: (sink: WritableStream<Uint8Array>) => Promise<void>;
  }>,
  kind: "raw" | "bundle", destination: ExportDestination, beforeStart?: () => Promise<void>,
  onSaving?: (bytes: number) => void, saving?: () => void,
): Promise<ArchiveDownloadOutcome> {
  const lease = createSessionScopeLease(scope, signal);
  try {
    lease.signal.throwIfAborted();
    if (destination === "oss") {
      await beforeStart?.();
      lease.signal.throwIfAborted();
      const { body } = await prepare(lease.signal, name);
      return await exportToOss(scope, { ...body, kind }, lease.signal, onProgress);
    }
    const handle = await chooseExportFile(name);
    lease.signal.throwIfAborted();
    if (!handle) return { kind: "cancelled" };
    let received = 0;
    let lastReport = 0;
    await writeExportFile(handle, lease.signal, async file => {
      await beforeStart?.();
      const archive = await prepare(lease.signal, name);
      const sink = new WritableStream<Uint8Array>({
        async write(chunk) {
          await beforeStart?.();
          lease.signal.throwIfAborted();
          await file.write(chunk);
          received += chunk.byteLength;
          if (performance.now() - lastReport > 100) {
            onProgress(received);
            lastReport = performance.now();
          }
        },
      });
      if (archive.writeLocal) await archive.writeLocal(sink);
      else await request<void>({
        method: "POST", path: archive.path, scope, scopeMode: "explicit",
        signal: lease.signal, cache: "no-store", body: archive.body, downloadTo: sink,
      });
      lease.signal.throwIfAborted();
    }, async () => {
      await beforeStart?.();
      saving?.();
      onSaving?.(received);
    });
    if (!onSaving) onProgress(received);
    return { kind: "saved", filename: handle.name, bytes: received };
  } finally {
    lease.release();
  }
}
