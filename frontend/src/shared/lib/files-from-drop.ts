export interface BrowserFileCandidate {
  readonly file: File;
  readonly relativePath?: string;
}

interface FileSystemEntryLike {
  readonly isFile: boolean;
  readonly isDirectory: boolean;
  readonly name: string;
}

interface FileSystemFileEntryLike extends FileSystemEntryLike {
  file(
    successCallback: (file: File) => void,
    errorCallback?: (error: DOMException) => void,
  ): void;
}

interface FileSystemDirectoryReaderLike {
  readEntries(
    successCallback: (entries: readonly FileSystemEntryLike[]) => void,
    errorCallback?: (error: DOMException) => void,
  ): void;
}

interface FileSystemDirectoryEntryLike extends FileSystemEntryLike {
  createReader(): FileSystemDirectoryReaderLike;
}

function fileFromEntry(entry: FileSystemFileEntryLike): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

function directoryBatch(
  reader: FileSystemDirectoryReaderLike,
): Promise<readonly FileSystemEntryLike[]> {
  return new Promise((resolve, reject) => reader.readEntries(resolve, reject));
}

async function allDirectoryEntries(
  directory: FileSystemDirectoryEntryLike,
): Promise<readonly FileSystemEntryLike[]> {
  const reader = directory.createReader();
  const entries: FileSystemEntryLike[] = [];
  let batch: readonly FileSystemEntryLike[];
  do {
    batch = await directoryBatch(reader);
    entries.push(...batch);
  } while (batch.length);
  return entries;
}

async function candidatesFromEntry(
  entry: FileSystemEntryLike,
  parentPath: string,
): Promise<readonly BrowserFileCandidate[]> {
  if (entry.isFile) {
    const file = await fileFromEntry(entry as FileSystemFileEntryLike);
    return [{ file, relativePath: `${parentPath}${file.name}` }];
  }
  if (!entry.isDirectory) return [];
  const directory = entry as FileSystemDirectoryEntryLike;
  const children = await allDirectoryEntries(directory);
  const directoryPath = `${parentPath}${directory.name}/`;
  return (
    await Promise.all(
      children.map((child) => candidatesFromEntry(child, directoryPath)),
    )
  ).flat();
}

export async function filesFromDrop(
  dataTransfer: DataTransfer,
): Promise<readonly BrowserFileCandidate[]> {
  const items = Array.from(dataTransfer.items ?? []).filter(
    (item) => item.kind === "file",
  );
  if (!items.length) {
    return Array.from(dataTransfer.files ?? []).map((file) => ({ file }));
  }
  // Read each handle during the drop event, before the browser protects the data store.
  const batches = items.map((item) => {
    const entry = item.webkitGetAsEntry?.();
    if (entry) return candidatesFromEntry(entry, "");
    const file = item.getAsFile();
    if (!file)
      throw new Error("无法读取拖入的项目，请使用文件或文件夹选择器。");
    return [{ file }];
  });
  return (await Promise.all(batches)).flat();
}
