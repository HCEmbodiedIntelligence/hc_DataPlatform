import { chooseExportFile, writeExportFile } from "./save-file";
import { Button } from "antd";
import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import type { Scope } from "../../shared/scope/model";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { request } from "../../shared/api/http-client";

const schema = z.object({
  version: z.string().regex(/^[a-f0-9]{64}$/),
  phase: z.enum(["running", "ready", "failed"]),
  completed: z.number(),
  total: z.number(),
  message: z.string().nullable().optional(),
});

export function BatchQCDownload({
  scope,
  number,
  numbers,
}: { scope: Scope } & (
  | { number: number; numbers?: never }
  | { numbers: number[]; number?: never }
)) {
  const selected = [...new Set(numbers ?? [number!])].sort((a, b) => a - b);
  const caps = useCapabilities();
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const ref = useRef<AbortController | null>(null);
  const key = JSON.stringify([
    scope.organizationId,
    scope.projectId,
    scope.regionCode,
    selected,
  ]);
  useEffect(() => {
    setProgress("");
    setError("");
    return () => {
      ref.current?.abort();
      ref.current = null;
    };
  }, [key]);
  if (
    !caps.has("upload.read") ||
    !caps.has("export.download") ||
    !scope.projectId ||
    !selected.length ||
    !scope.regionCode
  )
    return null;
  const filename =
    selected.length === 1
      ? `第${selected[0]}次上传-录制缺失清单.xlsx`
      : `上传批次质检报告（${selected.length}批）.xlsx`;
  async function download() {
    if (ref.current) return;
    const ctl = new AbortController();
    ref.current = ctl;
    setError("");
    setProgress("正在准备");
    const options = {
      scope,
      scopeMode: "explicit" as const,
      cache: "no-store" as const,
      signal: ctl.signal,
    };
    const batches = `/projects/${encodeURIComponent(scope.projectId!)}/regions/${encodeURIComponent(scope.regionCode!)}/lerobot-imports/upload-batches`;
    async function fetchReport() {
      if (selected.length > 1) {
        return request<Blob>({
          ...options,
          method: "POST",
          path: batches + "/qc-reports/download",
          body: { batch_numbers: selected },
          responseType: "blob",
        });
      }
      const base = batches + `/${selected[0]}/qc-report`;
      let state = schema.parse(
        await request({ ...options, path: base, method: "POST" }),
      );
      const path = base + "/" + state.version;
      while (state.phase === "running") {
        setProgress(`正在核对 ${state.completed}/${state.total}`);
        await new Promise((resolve) => setTimeout(resolve, 2000));
        ctl.signal.throwIfAborted();
        state = schema.parse(
          await request({ ...options, path, method: "GET" }),
        );
      }
      if (state.phase !== "ready") throw Error(state.message || "报告生成失败");
      return request<Blob>({
        ...options,
        method: "GET",
        path: path + "/download",
        responseType: "blob",
      });
    }
    try {
      const handle = await chooseExportFile(filename);
      if (!handle) return;
      ctl.signal.throwIfAborted();
      const blob = await fetchReport();
      await writeExportFile(handle, ctl.signal, file => file.write(blob));
    } catch (e) {
      if (!ctl.signal.aborted)
        setError(e instanceof Error ? e.message : "报告下载失败");
    } finally {
      if (ref.current === ctl) {
        ref.current = null;
        setProgress("");
      }
    }
  }
  return (
    <div>
      <Button
        size="small"
        loading={Boolean(progress)}
        onClick={() => void download()}
      >
        {progress || "下载质检报告"}
      </Button>
      {error && <span role="alert">{error}</span>}
    </div>
  );
}
