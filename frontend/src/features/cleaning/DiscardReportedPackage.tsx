import { useState } from "react";
import { Alert, Button, Modal } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ManualIssue, ManualIssueListItem } from "../../entities/manual-issue";
import { request } from "../../shared/api/http-client";
import { useCapabilities } from "../../shared/auth/use-capabilities";

export function DiscardReportedPackage({issue}: {issue: ManualIssue | ManualIssueListItem}) {
  const caps = useCapabilities();
  const client = useQueryClient();
  const [confirmation, confirm] = useState<string | null>(null);
  const discard = useMutation({
    mutationFn: () => request<void>({
      method: "POST",
      path: `/projects/${encodeURIComponent(issue.scope.projectId)}/regions/${encodeURIComponent(issue.scope.regionCode)}/manual-issues/${encodeURIComponent(issue.id)}:discard-package`,
      scope: issue.scope,
      ifMatch: issue.etag,
      idempotencyKey: confirmation!,
    }),
    onSuccess: () => {
      confirm(null);
      for (const key of ["manual-issues","dashboard","quality-problems","annotation","capture-progress","episode-delivery"]) {
        void client.invalidateQueries({queryKey:[key]});
      }
    },
  });
  if (issue.status.kind !== "known" || !["OPEN", "IN_PROGRESS"].includes(issue.status.value) ||
      !caps.has("manual_issue.triage") || !caps.has("quality.discard")) return null;
  return <>
    <Button danger onClick={() => {discard.reset(); confirm(crypto.randomUUID());}}>丢弃数据包</Button>
    <Modal title="丢弃这条上报对应的数据包？" open={confirmation !== null}
      okText="确认丢弃数据包" cancelText="取消" okButtonProps={{danger:true}}
      confirmLoading={discard.isPending} closable={!discard.isPending}
      onCancel={() => {if (!discard.isPending) confirm(null);}}
      onOk={() => {if (!discard.isPending) discard.mutate();}}>
      <p>{issue.note}</p><p>条目 {issue.source.episodeId}</p>
      <p>确认后，整份数据包将从后续训练数据和导出中移除，原始录制和上报记录会保留。</p>
      {discard.isError && <Alert type="error" title="丢弃失败" description={discard.error.message}/>}
    </Modal>
  </>;
}

