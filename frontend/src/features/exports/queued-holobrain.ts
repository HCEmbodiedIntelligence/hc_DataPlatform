import { chooseExportFile, streamPackageArchive, writeExportFile } from "./save-file";
import type { Scope } from "../../shared/scope/model";
import { createPackageBatch, loadPackageJob, downloadPackageJob, type PackageData, type PackageExportFilter } from "./package-deliveries";
import { exportToOss, type ExportDestination } from "./oss-export";

export async function exportHoloBrain(scope: Scope, data: PackageData[], filter: PackageExportFilter,
  destination: ExportDestination, signal: AbortSignal, report: (message: string) => void,
  suggestedName: string, enter: () => Promise<void>, saving?: () => void) {
  const phases: Record<string, string> = { queued: "排队", verifying: "校验数据", projecting: "转换数据",
    archiving: "生成压缩包", uploading: "保存服务端压缩包" };
  async function prepare() {
    await enter();
    signal.throwIfAborted();
    const started = performance.now();
    let job = await createPackageBatch(scope, data.map(ref => ({ data: ref, annotation: null })), "holobrain",
      crypto.randomUUID(), signal, filter);
    while (job.status === "PENDING" || job.status === "RUNNING") {
      let progress = job.total_files ? `，${job.processed_files}/${job.total_files} 个文件` : "";
      if (job.phase === "uploading" && job.size_bytes) {
        progress = `，${Math.floor(job.uploaded_bytes / 1048576)} / ${Math.ceil(job.size_bytes / 1048576)} MiB`;
      }
      report(`服务器正在${phases[job.phase] ?? "生成集合"}${progress}，已等待 ${Math.floor((performance.now() - started) / 1000)} 秒`);
      await new Promise<void>(resolve => window.setTimeout(resolve, 1500));
      signal.throwIfAborted();
      await enter();
      job = await loadPackageJob(scope, job, signal);
    }
    if (job.status !== "READY") throw new Error(`HoloBrain 集合生成失败（${job.error_code ?? job.status}）`);
    return job;
  }

  if (destination === "oss") {
    const job = await prepare();
    const receipt = await exportToOss(scope, { kind: "job", job_id: job.job_id, export_filter: filter }, signal,
      bytes => report(`已上传到 OSS ${Math.floor(bytes / 1048576)} MiB`));
    return { ...receipt, jobId: job.job_id };
  }

  const handle = await chooseExportFile(suggestedName);
  signal.throwIfAborted();
  if (!handle) return { kind: "cancelled" as const };
  const saved = await writeExportFile(handle, signal, async file => {
    const job = await prepare();
    await enter();
    const grant = await downloadPackageJob(scope, job, signal, filter);
    const total = (grant.size_bytes / 1048576).toFixed(1);
    let lastReport = 0;
    report(`正在下载 HoloBrain 集合（0 / ${total} MiB）`);
    await streamPackageArchive(file, grant, job.plan.members.length, () => downloadPackageJob(scope, job, signal, filter), signal, bytes => {
      if (performance.now() - lastReport >= 200 || bytes === grant.size_bytes) {
        report(`正在下载 HoloBrain 集合（${(bytes / 1048576).toFixed(1)} / ${total} MiB）`);
        lastReport = performance.now();
      }
    }, enter, true);
    report("校验通过，正在保存 HoloBrain ZIP…");
    return { jobId: job.job_id, bytes: grant.size_bytes };
  }, async () => { await enter(); saving?.(); });
  return { kind: "saved" as const, filename: handle.name, ...saved };
}
