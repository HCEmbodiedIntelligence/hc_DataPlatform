export { chooseExportFile, writeExportFile, streamAuthorizedFile, saveAuthorizedFile as saveAuthorizedArchive, type ExportFile } from "../../shared/lib/save-file";

import { streamAuthorizedFile, type ExportFile } from "../../shared/lib/save-file";
import { transferWidth } from "../../shared/lib/ordered-transfer";
import type { downloadPackageJob } from "./package-deliveries";

export async function streamPackageArchive(
  file: Awaited<ReturnType<ExportFile["createWritable"]>>,
  grant: Awaited<ReturnType<typeof downloadPackageJob>>, count: number, renew: () => ReturnType<typeof downloadPackageJob>,
  signal: AbortSignal, report: (bytes: number) => void, checkpoint: () => Promise<void>,
  verifySmall = false,
) {
  if (transferWidth(count) === 1)
    return streamAuthorizedFile(file, { ...grant, sha256: verifySmall ? grant.artifact_sha256 : undefined }, signal, report, checkpoint);
  const { downloadFileRanges } = await import("./parallel-file");
  return downloadFileRanges(count, grant, renew, bytes => file.write(bytes), signal, checkpoint, report);
}
