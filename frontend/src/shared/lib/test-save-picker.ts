import { vi } from "vitest";

type TestWriter = { write(data: Uint8Array | Blob): unknown; close(): unknown; abort(): unknown };

export function testDownloadFolder(writer: TestWriter) {
  const files = new Map<string, number>();
  const pending = new Map<string, Map<string, number>>();
  const directories = new Set<string>();
  const move = vi.fn(async (_from: string, _to: string) => {});
  const missing = () => new DOMException("Missing", "NotFoundError");
  const mismatch = () => new DOMException("Wrong entry type", "TypeMismatchError");
  function folder(path: string) {
    const child = (name: string) => path ? path + "/" + name : name;
    return {
      name: path.split("/").at(-1)!, path,
      async getFileHandle(name: string, options?: { create?: boolean }) {
        const key = child(name);
        if (directories.has(key)) throw mismatch();
        const working = pending.get(path);
        if (!working) {
          if (!files.has(key)) throw missing();
          return { getFile: async () => ({ size: files.get(key) }) };
        }
        if (!working.has(name) && !options?.create) throw missing();
        working.set(name, working.get(name) ?? 0);
        return {
          getFile: async () => ({ size: working.get(name) }),
          createWritable: async () => ({
            async write(data: Uint8Array | Blob) {
              await writer.write(data);
              working.set(name, working.get(name)! + (ArrayBuffer.isView(data) ? data.byteLength : data.size));
            },
            close: () => writer.close(), abort: () => Promise.resolve(writer.abort()),
          }),
          async move(target: { path: string }, filename: string) {
            const final = target.path ? target.path + "/" + filename : filename;
            await move(key, final);
            files.set(final, working.get(name)!);
            working.delete(name);
          },
        };
      },
      async getDirectoryHandle(name: string, options?: { create?: boolean }) {
        const key = child(name);
        if (files.has(key)) throw mismatch();
        if (!directories.has(key) && !options?.create) throw missing();
        directories.add(key);
        if (name.startsWith("HC下载中-") && !pending.has(key)) pending.set(key, new Map());
        return folder(key);
      },
      async removeEntry(name: string) {
        const key = child(name);
        if (directories.has(key)) {
          if (pending.get(key)?.size || [...directories, ...files.keys()].some(p => p.startsWith(key + "/")))
            throw new DOMException("not empty", "InvalidModificationError");
          directories.delete(key);
          pending.delete(key);
        } else if (!pending.get(path)?.delete(name) && !files.delete(key)) throw missing();
      },
    };
  }
  if (!navigator.locks) Object.defineProperty(navigator, "locks", { configurable: true,
    value: { request: async (_: string, action: () => unknown) => action() } });
  return { directory: folder(""), files, pending, directories, move };
}

export function mockSavePicker() {
  const write = vi.fn(async (_chunk: Uint8Array | Blob) => {});
  const close = vi.fn(async () => {});
  const abort = vi.fn(async () => {});
  const mock = testDownloadFolder({ write, close, abort });
  const picker = vi.fn(async () => mock.directory);
  vi.stubGlobal("showDirectoryPicker", picker);
  return { write, close, abort, picker, ...mock };
}
