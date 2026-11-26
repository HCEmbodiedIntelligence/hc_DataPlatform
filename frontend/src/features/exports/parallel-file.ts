import { bytesToHex } from "@noble/hashes/utils.js";
import { sha256 } from "@noble/hashes/sha2.js";
import { transferInOrder, transferWidth } from "../../shared/lib/ordered-transfer";
import { request } from "../../shared/api/http-client";
import { localObjectStorageUrl } from "../../shared/lib/object-storage-url";

type Grant = { url: string; size_bytes: number; artifact_sha256: string; expires_seconds: number };

export async function downloadFileRanges(
  count: number, first: Grant, renew: () => Promise<Grant>,
  write: (chunk: Uint8Array) => Promise<void>, signal: AbortSignal,
  checkpoint: () => Promise<void>, report: (bytes: number) => void,
) {
  const width = transferWidth(count);
  const chunkSize = Math.min(8 * 1024 ** 2, Math.max(1, Math.ceil(first.size_bytes / width)));
  let grant = first;
  let expires = performance.now() + (first.expires_seconds - 10) * 1000;
  let refreshing: Promise<void> | undefined;
  async function currentUrl() {
    if (performance.now() >= expires) {
      refreshing ??= renew().then(next => {
        if (next.artifact_sha256 !== first.artifact_sha256 || next.size_bytes !== first.size_bytes)
          throw new Error("续期后的文件与原归档不一致。");
        grant = next;
        expires = performance.now() + (next.expires_seconds - 10) * 1000;
      }).finally(() => { refreshing = undefined; });
      await refreshing;
    }
    const url = new URL(localObjectStorageUrl(grant.url), window.location.href);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("下载地址无效。");
    return url;
  }
  const hash = sha256.create();
  let received = 0;
  await transferInOrder(Math.ceil(first.size_bytes / chunkSize), width, signal, async (index, active) => {
    await checkpoint();
    active.throwIfAborted();
    const url = await currentUrl();
    const start = index * chunkSize;
    const end = Math.min(start + chunkSize, first.size_bytes) - 1;
    let content: Blob;
    if (url.origin !== window.location.origin && /[.]oss-[a-z0-9-]+[.]aliyuncs[.]com$/u.test(url.hostname)) {
      content = await request<Blob>({ method: "POST", path: "/storage-downloads/content", scopeMode: "active",
        body: { url: url.href, byte_range: [start, end, first.size_bytes] }, responseType: "blob", signal: active, cache: "no-store" });
    } else {
      const response = await fetch(url, { headers: { Range: `bytes=${start}-${end}` }, signal: active, credentials: "same-origin",
        cache: "no-store", referrerPolicy: "no-referrer" });
      if (response.status !== 206 || response.headers.get("Content-Range") !== `bytes ${start}-${end}/${first.size_bytes}`) {
        await response.body?.cancel();
        throw new Error(`未收到请求的归档分段（HTTP ${response.status}）。`);
      }
      content = await response.blob();
    }
    if (content.size !== end - start + 1) throw new Error("下载分段长度与归档不符。");
    return content;
  }, async (_, content, active) => {
    await checkpoint();
    active.throwIfAborted();
    const chunk = new Uint8Array(await content.arrayBuffer());
    hash.update(chunk);
    await write(chunk);
    received += chunk.byteLength;
    report(received);
  });
  if (bytesToHex(hash.digest()) !== first.artifact_sha256) throw new Error("下载文件的 SHA-256 校验失败。");
}
