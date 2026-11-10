import { expect, it } from "vitest";
import {
  BlobWriter,
  TextReader,
  ZipWriter,
} from "@zip.js/zip.js/lib/zip-core-native.js";
import {
  prepareCaptureFiles,
  withRelativePath,
} from "../../features/ingest/upload/capture-files";
import { inspectLocalUploadSelection } from "./upload-flow";

async function zip(entries: string[]) {
  const writer = new ZipWriter(new BlobWriter(), { useWebWorkers: false });
  for (const path of entries)
    await writer.add(path, new TextReader("captured bytes"));
  return new File([await writer.close()], "recording.ZIP");
}

it("inspects compressed ROS2 files as one native capture and preserves their bytes", async () => {
  const files = await prepareCaptureFiles([
    await zip(["capture/metadata.yaml", "capture/sensors/record.db3"]),
  ]);
  expect(files.map((f) => f.webkitRelativePath)).toEqual([
    "capture/metadata.yaml",
    "capture/sensors/record.db3",
  ]);
  expect(await files[1]!.text()).toBe("captured bytes");
  const selection = await inspectLocalUploadSelection({
    files,
    sourceType: "BROWSER_MULTIPART",
    browserSelectionMode: "folder",
    objectStorageUri: "",
  });
  expect(selection.problems).toEqual([]);
  expect(selection.lerobot?.format).toBe("rosbag");
});

it("rejects invalid ZIP selections before returning any files", async () => {
  await expect(prepareCaptureFiles([await zip([])])).rejects.toThrow(
    "没有可上传",
  );
  await expect(
    prepareCaptureFiles([new File(["broken"], "broken.zip")]),
  ).rejects.toThrow("ZIP 读取");
  await expect(
    prepareCaptureFiles([new File(["broken"], "broken.7z")]),
  ).rejects.toThrow("目前支持 ZIP");
  await expect(
    prepareCaptureFiles([await zip(["a.mcap"]), new File(["b"], "b.mcap")]),
  ).rejects.toThrow("一次选择一个");
});

it("preserves a ZIP that is part of a selected folder", async () => {
  const file = withRelativePath(
    new File(["auxiliary"], "extra.zip"),
    "source/extra.zip",
  );
  expect(await prepareCaptureFiles([file])).toEqual([file]);
});
