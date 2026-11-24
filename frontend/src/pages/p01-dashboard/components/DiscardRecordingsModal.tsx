import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Modal } from "antd";
import { useRef } from "react";
import { captureError } from "../../../features/capture-schemes/presentation";
import type { DashboardScope } from "../../../features/dashboard/types";
import {
  discardQualityRecording,
  readQualityRecording,
  recordingReportDigest,
  refreshRecordingQuality,
} from "../../../features/quality/recording-quality-api";

export interface DiscardTarget {
  id: string;
  label: string;
}

async function mapRecordings<T, R>(
  rows: readonly T[],
  run: (row: T) => Promise<R>,
) {
  const results: R[] = [];
  const queue = rows.entries();
  async function worker() {
    for (const [index, row] of queue) results[index] = await run(row);
  }
  await Promise.all(Array.from({ length: Math.min(4, rows.length) }, worker));
  return results;
}

export function DiscardRecordingsModal({
  scope,
  targets,
  onDiscarded,
  onClose,
}: {
  scope: DashboardScope;
  targets: DiscardTarget[];
  onDiscarded: (ids: string[]) => void;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const submitting = useRef(false);
  const reports = useQuery({
    queryKey: ["batch-discard-preview", scope, targets],
    queryFn: ({ signal }) =>
      mapRecordings(targets, async (target) => {
        signal.throwIfAborted();
        try {
          const { data } = await readQualityRecording(scope, target.id, signal);
          const digest = recordingReportDigest(data.quality_summary?.report_id);
          if (data.quality_discarded_at) return { ...target, reason: "已丢弃" };
          if (data.status !== "QC_FAILED" || !digest)
            return { ...target, reason: "当前没有可丢弃的整段质检报告" };
          return { ...target, digest };
        } catch (error) {
          return { ...target, reason: captureError(error) };
        }
      }),
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const rows = reports.data ?? [];
  const ready = rows.filter((row) => "digest" in row);
  const discard = useMutation({
    retry: false,
    mutationFn: () =>
      mapRecordings(ready, async (row) => {
        try {
          await discardQualityRecording(scope, row.id, row.digest);
          return { ...row, error: null };
        } catch (error) {
          return { ...row, error: captureError(error) };
        }
      }),
    onSuccess: (results) => {
      onDiscarded(results.filter((row) => !row.error).map((row) => row.id));
      refreshRecordingQuality(client);
    },
  });
  const completed = discard.data;
  const errors = completed?.filter((row) => row.error) ?? [];
  return (
    <Modal
      open
      title="批量丢弃当前录制"
      cancelText="关闭"
      okText={completed ? "完成" : `确认丢弃（${ready.length}）`}
      okButtonProps={{
        danger: !completed,
        disabled: !completed && (!ready.length || reports.isFetching),
      }}
      cancelButtonProps={{ disabled: discard.isPending }}
      confirmLoading={discard.isPending}
      onCancel={() => {
        if (!submitting.current || completed) onClose();
      }}
      onOk={() => {
        if (completed) return onClose();
        if (!submitting.current && ready.length && !reports.isFetching) {
          submitting.current = true;
          discard.mutate();
        }
      }}
    >
      {completed ? (
        <Alert
          showIcon
          type={errors.length ? "warning" : "success"}
          title={`已丢弃 ${completed.length - errors.length} 条录制，失败 ${errors.length} 条，跳过 ${rows.length - ready.length} 条`}
          description={
            errors.length > 0 && (
              <ul>
                {errors.map((row) => (
                  <li key={row.id}>
                    {row.label} · {row.error}
                  </li>
                ))}
              </ul>
            )
          }
        />
      ) : (
        <p>
          已选 {targets.length}{" "}
          条录制。确认后，可丢弃的录制将退出待处理列表，原始文件和质检报告会保留。
        </p>
      )}
      {reports.isFetching && <p role="status">正在核对质检报告…</p>}
      {reports.isError && (
        <Alert type="error" title={captureError(reports.error)} />
      )}
      {!completed && (
        <ul
          style={{ maxHeight: 240, overflow: "auto", overflowWrap: "anywhere" }}
        >
          {rows.map((row) => (
            <li key={row.id}>
              {row.label}
              {"reason" in row && ` · 跳过：${row.reason}`}
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}
