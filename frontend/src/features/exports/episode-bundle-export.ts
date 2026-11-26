import { transferLabel } from "../../shared/lib/ordered-transfer";
import { exportArchiveName } from "./archive-name";
import { exportHoloBrain } from "./queued-holobrain";
import type { Scope } from "../../shared/scope/model";
import { downloadEpisodeBundle, type BatchMode, type PackageData, type PackageExportFilter } from "./package-deliveries";
import { episodeItem, saveExportRecord } from "./export-history";
import type { ExportDestination } from "./oss-export";
import { runExportTask } from "./export-tasks";
export { clearExportTask as clearEpisodeBundleExport } from "./export-tasks";

export function startEpisodeBundleExport(
  scope: Scope, members: PackageData[], mode: BatchMode, filter: PackageExportFilter,
  destination: ExportDestination = "zip", datasetNumbers?: ReadonlyMap<string, string>,
  history?: { format: string; batchesByVersion: ReadonlyMap<string, number[]> },
) {
  const label = `${history?.format ?? mode.toUpperCase()} · ${members.length} 条`;
  return runExportTask(scope, transferLabel(label, members.length), destination, async control => {
    const { signal, report, checkpoint } = control;
    const result = mode === "holobrain"
      ? await exportHoloBrain(scope, members, filter, destination, signal, report,
          exportArchiveName(mode, members.map(({ identity }) => datasetNumbers?.get(identity.dataset_id) ?? identity.dataset_id)), checkpoint, control.saving)
      : await downloadEpisodeBundle(scope, members, mode, filter, signal, report, destination, datasetNumbers, checkpoint, control.saving);
    signal.throwIfAborted();
    if (result.kind === "cancelled") return result;
    saveExportRecord(scope, {
      source: "bundle", format: history?.format ?? mode,
      items: members.map(data => ({ ...episodeItem(data), batches: history?.batchesByVersion.get(data.data_version_id) })),
      selectedBatches: filter.batch_numbers,
      jobId: "jobId" in result && typeof result.jobId === "string" ? result.jobId : undefined,
      ...(result.kind === "oss" ? { uri: result.uri } : { filename: result.filename }),
    });
    return result.kind === "oss" ? result : { kind: "saved", message: `下载完成 · ${members.length} 条 Episode · ${result.filename}` };
  });
}
