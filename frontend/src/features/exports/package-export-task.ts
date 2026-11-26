import { transferLabel } from "../../shared/lib/ordered-transfer";
import type { Scope } from "../../shared/scope/model";
import { formatBytes } from "../ingest/upload/upload-contract";
import { exportArchiveName } from "./archive-name";
import { runExportTask } from "./export-tasks";
import { saveExportRecord } from "./export-history";
import { chooseExportFile, streamPackageArchive, writeExportFile } from "./save-file";
import { exportToOss, type ExportDestination } from "./oss-export";
import { commandPackageJob, createPackageBatch, downloadPackageJob, loadPackageJob,
  type BatchMode, type PackageExportFilter, type PackageJob, type PackageSelection } from "./package-deliveries";

interface Options {
  scope: Scope;
  label: string;
  mode: BatchMode;
  selections: PackageSelection[];
  filter?: PackageExportFilter;
  datasetNumbers?: ReadonlyMap<string, string>;
  destination: ExportDestination;
  action: "create" | "retry" | "download";
  job?: PackageJob;
  onJob: (job: PackageJob) => void;
  key: string;
}

export function startPackageExport(options: Options) {
  const { scope, mode, selections, destination, filter, action, onJob } = options;
  const data = options.job ? options.job.plan.members.map(member => member.data) : selections.map(item => item.data);
  const format = options.job
    ? options.job.plan.archive_format.startsWith("lerobot_collection") ? options.job.plan.modalities : options.job.plan.archive_format.split("_")[0]!
    : mode;
  const name = exportArchiveName(format, data.map(({ identity }) => options.datasetNumbers?.get(identity.dataset_id) ?? identity.dataset_id));
  return runExportTask(scope, transferLabel(options.label, data.length), destination, async control => {
    const { signal, checkpoint, report } = control;
    const file = destination === "zip" ? await chooseExportFile(name) : null;
    if (destination === "zip" && !file) return { kind: "cancelled" };
    await checkpoint();
    let job = action === "create" ? await createPackageBatch(scope, selections, mode, options.key, signal, filter)
      : action === "retry" ? await commandPackageJob(scope, options.job!, "retry", signal) : options.job!;
    let pollErrors = 0;
    while (true) {
      signal.throwIfAborted();
      onJob(job);
      if (job.status !== "RUNNING" && job.status !== "PENDING") break;
      report(`服务器正在处理 · ${job.processed_files}/${job.total_files ?? "—"} 个文件`, job.processed_files, job.total_files ?? undefined);
      try {
        await checkpoint();
        job = await loadPackageJob(scope, job, signal);
        pollErrors = 0;
      } catch (error) {
        signal.throwIfAborted();
        if (++pollErrors >= 3) throw error;
        report("暂时无法读取导出进度，正在重试");
      }
      if (job.status === "RUNNING" || job.status === "PENDING")
        await new Promise(resolve => setTimeout(resolve, 1500));
    }
    if (job.status !== "READY") throw new Error(`服务端任务${job.status === "CANCELLED" ? "已取消" : "失败"} · ${job.error_code ?? job.status}`);
    await checkpoint();
    if (!file) {
      const result = await exportToOss(scope, { kind: "job", job_id: job.job_id, ...(filter && { export_filter: filter }) },
        signal, bytes => report(`已上传到 OSS ${formatBytes(bytes)}`));
      signal.throwIfAborted();
      saveExportRecord(scope, { source: "job", jobId: job.job_id, uri: result.uri });
      return result;
    }
    const authorize = () => downloadPackageJob(scope, job, signal, filter?.stage === "qc_passed" ? filter : undefined);
    const grant = await authorize();
    let lastReport = 0;
    await writeExportFile(file, signal, writer => streamPackageArchive(writer, grant, job.plan.members.length, authorize, signal, bytes => {
      if (performance.now() - lastReport > 150 || bytes === grant.size_bytes) {
        report(`已下载 ${formatBytes(bytes)} / ${formatBytes(grant.size_bytes)}`, bytes, grant.size_bytes);
        lastReport = performance.now();
      }
    }, checkpoint), async () => { await checkpoint(); control.saving(); });
    return { kind: "saved", message: `已保存到所选位置：${file.name}` };
  });
}
