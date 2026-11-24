import type { ExportFile } from "./save-file";

type MovableFile = FileSystemFileHandle & {
  move?: (directory: FileSystemDirectoryHandle, name: string) => Promise<void>;
};

async function availableName(directory: FileSystemDirectoryHandle, name: string, folder = false) {
  const dot = folder ? -1 : name.lastIndexOf(".");
  const base = dot > 0 ? name.slice(0, dot) : name;
  const extension = dot > 0 ? name.slice(dot) : "";
  for (let index = 0; ; index++) {
    const candidate = index === 0 ? name : `${base} (${index})${extension}`;
    try {
      await directory.getFileHandle(candidate);
    } catch (error) {
      if (error instanceof DOMException && error.name === "NotFoundError") return candidate;
      if (!(error instanceof DOMException && error.name === "TypeMismatchError")) throw error;
    }
  }
}

// A final filename enters the selected directory only after the browser commits
// the staging file. The two locations share a filesystem even on external drives.
export function stagedExportFile(directory: FileSystemDirectoryHandle, name: string): ExportFile {
  let publishedName = name;
  return {
    get name() { return publishedName; },
    async createWritable() {
      const archiveBase = name.toLowerCase().endsWith(".zip") ? name.slice(0, -4).replace(/[ .]+$/u, "") || "ZIP" : null;
      const target = archiveBase ? await navigator.locks.request("hc-publish-download", async () => {
        const folderName = await availableName(directory, archiveBase, true);
        return directory.getDirectoryHandle(folderName, { create: true });
      }) : directory;
      const removeEmptyOutput = async () => {
        if (target !== directory) await directory.removeEntry(target.name).catch(() => {});
      };
      const stagingName = `HC下载中-${crypto.randomUUID()}`;
      let staging: FileSystemDirectoryHandle;
      try {
        staging = await target.getDirectoryHandle(stagingName, { create: true });
      } catch (error) {
        await removeEmptyOutput();
        throw error;
      }
      const cleanup = () => target.removeEntry(stagingName).catch(() => {});
      const discard = async () => {
        await staging.removeEntry(name).catch(() => {});
        await cleanup();
        await removeEmptyOutput();
      };
      let file: MovableFile;
      let writer: FileSystemWritableFileStream;
      try {
        file = await staging.getFileHandle(name, { create: true });
        if (typeof file.move !== "function")
          throw new Error("当前浏览器不支持完整文件移入保存位置，请使用新版 Chrome 或 Edge。");
        writer = await file.createWritable();
      } catch (error) {
        await discard();
        throw error;
      }
      let closed = false;
      let written = 0;
      return {
        async write(data) {
          await writer.write(data as Blob | Uint8Array<ArrayBuffer>);
          written += ArrayBuffer.isView(data) ? data.byteLength : data.size;
        },
        async close() {
          await writer.close();
          closed = true;
          try {
            if ((await file.getFile()).size !== written) throw new Error("保存文件大小校验失败。");
            await navigator.locks.request("hc-publish-download", async () => {
              const filename = await availableName(target, name);
              await file.move!(target, filename);
              publishedName = filename;
            });
          } catch (error) {
            const location = target === directory ? stagingName : `${target.name}/${stagingName}`;
            const reason = error instanceof Error ? error.message : String(error);
            throw new Error(`文件未移到最终位置，请检查所选文件夹内的“${location}/${name}”。${reason}`, { cause: error });
          }
          await cleanup();
        },
        async abort() {
          if (closed) return;
          await writer.abort().catch(() => {});
          await discard();
        },
      };
    },
  };
}
