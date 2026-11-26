import { stagedExportFile } from "./staged-export-file";
import { request } from "../api/http-client";
import { localObjectStorageUrl } from "./object-storage-url";

type ExportWriter = {
  write: (data: Uint8Array | Blob) => Promise<void>;
  close: () => Promise<void>;
  abort: () => Promise<void>;
};

export type ExportFile = {
  name: string;
  createWritable: () => Promise<ExportWriter>;
};

// Call from the click handler, before waiting for packaging or authorization.
export async function chooseExportFile(suggestedName: string): Promise<ExportFile | null> {
  const browser = window as Window & {
    showDirectoryPicker?: (options: { id: string; mode: "readwrite" }) => Promise<FileSystemDirectoryHandle>;
  };
  if (!browser.showDirectoryPicker)
    throw new Error("当前页面无法选择保存位置。请用 Chrome 或 Edge 直接打开平台的 HTTPS 地址后重试。");
  try {
    const basename = suggestedName.split(/[\\/]/u).at(-1)?.replace(/[<>:"|?*]/gu, "_").replace(/\p{Cc}/gu, "_");
    const directory = await browser.showDirectoryPicker({ id: "hc-downloads", mode: "readwrite" });
    const name = basename?.slice(0, 220).replace(/[. ]+$/u, "") || "download";
    return stagedExportFile(directory, /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/iu.test(name) ? "_" + name : name);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") return null;
    if (error instanceof DOMException && error.name === "SecurityError")
      throw new Error("浏览器阻止了保存对话框。请在独立标签页打开平台的 HTTPS 地址后重试。");
    throw error;
  }
}

export async function writeExportFile<T>(
  handle: ExportFile, signal: AbortSignal, transfer: (file: ExportWriter) => Promise<T>,
  beforeCommit?: () => Promise<void>,
) {
  signal.throwIfAborted();
  const file = await handle.createWritable();
  try {
    signal.throwIfAborted();
    const result = await transfer(file);
    signal.throwIfAborted();
    await beforeCommit?.();
    signal.throwIfAborted();
    await file.close();
    return result;
  } catch (error) {
    await file.abort().catch(() => {});
    throw error;
  }
}

export type AuthorizedFile = { url: string; size_bytes?: number; sha256?: string };

export async function saveAuthorizedFile(
  handle: ExportFile, grant: AuthorizedFile, signal: AbortSignal,
) {
  return writeExportFile(handle, signal, file => streamAuthorizedFile(file, grant, signal));
}

export async function streamAuthorizedFile(
  file: ExportWriter, grant: AuthorizedFile, signal: AbortSignal, onProgress?: (bytes: number) => void,
  checkpoint?: () => Promise<void>,
) {
  const hash = grant.sha256 ? (await import("@noble/hashes/sha2.js")).sha256.create() : undefined;
  let received = 0;
  const sink = new WritableStream<Uint8Array>({
    async write(chunk) {
      await checkpoint?.();
      signal.throwIfAborted();
      received += chunk.byteLength;
      if (grant.size_bytes !== undefined && received > grant.size_bytes)
        throw new Error("文件大小与授权不符。");
      hash?.update(chunk);
      await file.write(chunk);
      onProgress?.(received);
    },
  });
  const url = new URL(localObjectStorageUrl(grant.url), window.location.href);
  if (!["https:", "http:"].includes(url.protocol) || url.username || url.password)
    throw new Error("下载授权地址无效。");
  if (url.origin !== window.location.origin && /[.]oss-[a-z0-9-]+[.]aliyuncs[.]com$/u.test(url.hostname)) {
    await request<void>({
      method: "POST", path: "/storage-downloads/content", scopeMode: "active",
      body: { url: url.href }, downloadTo: sink, cache: "no-store", signal,
    });
  } else {
    const response = await fetch(url.href, { signal, cache: "no-store", credentials: "same-origin", referrerPolicy: "no-referrer" });
    if (!response.ok || !response.body)
      throw new Error(`读取文件失败（HTTP ${response.status}），请重新下载。`);
    await response.body.pipeTo(sink, { signal });
  }
  if (grant.size_bytes !== undefined && received !== grant.size_bytes)
    throw new Error("文件不完整，请重新下载。");
  if (hash && Array.from(hash.digest(), byte => byte.toString(16).padStart(2, "0")).join("") !== grant.sha256)
    throw new Error("文件校验失败，请重新下载。");
}

export async function saveFileFromClick(
  name: string, signal: AbortSignal, load: (signal: AbortSignal) => Promise<AuthorizedFile | Blob>,
) {
  const handle = await chooseExportFile(name);
  signal.throwIfAborted();
  if (!handle) return null;
  await writeExportFile(handle, signal, async file => {
    const source = await load(signal);
    if ("url" in source) await streamAuthorizedFile(file, source, signal);
    else await file.write(source);
  });
  return handle.name;
}
