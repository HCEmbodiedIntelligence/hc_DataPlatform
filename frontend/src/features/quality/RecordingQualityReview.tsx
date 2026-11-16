import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Modal, Space } from "antd";
import { Link } from "react-router-dom";
import type { components } from "../../shared/api/generated/platform";
import { request } from "../../shared/api/http-client";
import type { IngestScope } from "../../entities/data-source";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { qualityFindingText } from "../../shared/lib/quality-presentation";
import { dataUploadRoutes, recordingPreviewPath } from "../../shared/routing/navigation-routes";
import { qualityReasonLabels, qualityRuleLabels } from "./labels";

type Envelope = components["schemas"]["ContinuousRecordingEnvelope"];

export function findingLabel(code: string, fallback?: string) {
  const title = qualityFindingText(code).title;
  if (title !== "数据质量异常") return title;
  return qualityReasonLabels[code] ?? qualityRuleLabels[code] ?? fallback ?? code;
}

export function findingMeasurement(code: string, value: unknown) {
  if (value == null) return "—";
  if (typeof value === "number") {
    if (["QC_COMPLETE_STEP_RATIO_LOW", "QC_IMAGE_BLACK", "QC_IMAGE_REPEATED"].includes(code))
      return (value * 100).toFixed(2) + "%";
    if (code === "QC_MODALITY_OFFSET") return Number((value / 1e6).toFixed(3)) + " 毫秒";
  }
  return String(value);
}

export function RecordingQualityReview({ scope, detail, actions = true }: {
  scope: IngestScope;
  detail: Envelope;
  actions?: boolean;
}) {
  const recording = detail.data;
  const summary = recording.quality_summary;
  const evidence = detail.quality_evidence;
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const client = useQueryClient();
  const caps = useCapabilities();
  const digest = summary?.report_id.match(/^recording_qc_([0-9a-f]{64})$/)?.[1];
  const change = useMutation({
    mutationFn: (action: "retry" | "discard") => request<Envelope>({
      method: "POST", scope,
      path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/continuous-recordings/${encodeURIComponent(recording.recording_id)}/quality:${action}`,
      body: action === "discard" ? { report_sha256: digest } : { request_id: crypto.randomUUID() },
    }),
    onSuccess: () => {
      setConfirmDiscard(false);
      for (const key of ["continuous-recording", "continuous-recordings", "qc-recording-source", "dashboard",
        "quality-problems", "capture-progress", "collection-task-progress", "auto-quality-processing"])
        void client.invalidateQueries({queryKey: [key]});
    },
  });
  if (recording.status !== "QC_FAILED" || !summary) return null;
  const duplicate = evidence?.duplicate_of_recording_id;
  const findings = evidence?.findings ?? [];
  const codes = [...new Set([...(summary.finding_codes ?? []), ...findings.map(f => f.code)])];
  const visibleCodes = codes.filter(c => c !== "QUALITY_REJECT" || codes.length === 1);
  const discarded = recording.quality_discarded_at || change.data?.data.quality_discarded_at;
  const queued = change.data?.data.status === "PENDING_QC";
  return <Alert showIcon type={discarded || queued ? "info" : "warning"}
    title={discarded ? "已丢弃 · 原始录制已保留" : queued ? "已提交重新质检" : "整段质检未通过"}
    style={{width: "100%"}} description={<Space orientation="vertical" style={{width: "100%"}}>
      {visibleCodes.map(code => {
        const rows = findings.filter(f => f.code === code);
        const to = recordingPreviewPath(recording.recording_id) + "&" + new URLSearchParams({report: digest ?? "", finding: code});
        return <div key={code}>
          <strong>{findingLabel(code, rows[0]?.message)}</strong>
          {code === "MCAP_DUPLICATE_RECORDING" && <div>影响整份文件。原始 MCAP 与已有录制的内容校验值相同。
            {duplicate && <div>匹配录制 <Link to={recordingPreviewPath(duplicate)}>{duplicate} · 核对已有录制</Link></div>}
            {evidence?.original_mcap_sha256 && <div style={{overflowWrap:"anywhere"}}>原始文件 SHA-256 <code>{evidence.original_mcap_sha256}</code></div>}
          </div>}
          {rows.map((f,i) => <div key={i}>{f.topic} · 实测 {findingMeasurement(code, f.observed)} · 阈值 {findingMeasurement(code, f.threshold)}</div>)}
          {digest && (rows.length > 0 || code === "MCAP_INTERIOR_ACTION_MISSING") &&
            <Link to={to}>{code === "MCAP_INTERIOR_ACTION_MISSING" ? "定位缺失动作帧" : "查看检测区间"}</Link>}
        </div>;
      })}
      {change.isError && <Alert type="error" title="处理未完成" description={change.error.message}/>}
      {!discarded && !queued && actions && caps.has("upload.manage") && <Space wrap>
        {digest && <Button danger onClick={() => setConfirmDiscard(true)} disabled={change.isPending}>{duplicate ? "丢弃当前重复录制" : "丢弃当前录制"}</Button>}
        <Button loading={change.isPending && change.variables === "retry"} disabled={change.isPending}
          onClick={() => change.mutate("retry")}>重新质检</Button>
        <Link to={dataUploadRoutes.newUpload}>上传修正数据</Link>
      </Space>}
      <Modal title="丢弃当前录制？" open={confirmDiscard} okText="确认丢弃" cancelText="取消"
        okButtonProps={{danger:true}} confirmLoading={change.isPending}
        onCancel={() => !change.isPending && setConfirmDiscard(false)} onOk={() => change.mutate("discard")}>
        <p>{recording.source_file_name ?? recording.recording_id}</p>
        <p>这条录制将退出待处理列表，原始文件和质检报告会保留。</p>
        {change.isError && <Alert type="error" title={change.error.message}/>}
      </Modal>
    </Space>} />;
}
