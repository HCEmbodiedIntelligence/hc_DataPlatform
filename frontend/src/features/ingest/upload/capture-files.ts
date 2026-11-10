export function withRelativePath(file: File, path: string): File {
  Object.defineProperty(file, "webkitRelativePath", {
    value: path,
    configurable: true,
  });
  return file;
}

class ArchiveSelectionError extends Error {}

export async function prepareCaptureFiles(
  files: readonly File[],
): Promise<readonly File[]> {
  // A ZIP inside a selected capture folder is part of that folder's original contents.
  const loose = files.filter(
    (file) => !file.webkitRelativePath || file.webkitRelativePath === file.name,
  );
  if (loose.some((file) => /\.(rar|7z|tar|tgz|gz|bz2|xz)$/i.test(file.name))) {
    throw new Error("压缩包目前支持 ZIP。其他格式请先解压，再选择采集文件夹。");
  }
  const archive = loose.find((file) => /\.zip$/i.test(file.name));
  if (!archive) return files;
  if (files.length !== 1) throw new Error("请一次选择一个 ZIP 压缩包。");

  const { BlobReader, BlobWriter, ZipReader } = await import(
    "@zip.js/zip.js/lib/zip-core-native.js"
  );
  const reader = new ZipReader(new BlobReader(archive), {
    useWebWorkers: false,
    checkCrc32: true,
    filenameValidation: "strict",
    normalizeFilename: (path) => path.replaceAll("\\", "/"),
  });
  const extracted: File[] = [];
  const paths = new Set<string>();
  let size = 0;
  let count = 0;
  try {
    for await (const entry of reader.getEntriesGenerator()) {
      if (++count > 10000)
        throw new ArchiveSelectionError(
          "ZIP 最多支持 10000 个条目。请解压后选择文件夹。",
        );
      if (entry.encrypted || entry.symlink)
        throw new ArchiveSelectionError("请选择不含加密文件或符号链接的 ZIP。");
      if (entry.directory) continue;
      const path = entry.filename;
      if (paths.has(path))
        throw new ArchiveSelectionError(
          "ZIP 内存在重复文件路径，请检查后重新打包。",
        );
      paths.add(path);
      size += entry.uncompressedSize;
      if (size > 2 * 1024 ** 3)
        throw new ArchiveSelectionError(
          "ZIP 解压后最多支持 2 GiB。请解压后选择采集文件夹。",
        );
      const content = await entry.getData(new BlobWriter());
      const file = new File([content], path.split("/").at(-1)!, {
        lastModified: entry.lastModDate.getTime(),
      });
      extracted.push(withRelativePath(file, path));
    }
    if (!extracted.length)
      throw new ArchiveSelectionError("ZIP 内没有可上传的文件。");
    return extracted;
  } catch (error) {
    if (error instanceof ArchiveSelectionError) throw error;
    throw new Error(
      "ZIP 读取或校验失败，请检查压缩包，或解压后选择采集文件夹。",
      { cause: error },
    );
  } finally {
    await reader.close();
  }
}
