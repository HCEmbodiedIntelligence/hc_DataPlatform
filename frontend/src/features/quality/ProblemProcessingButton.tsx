import { useState } from "react";
import { Alert, Button, Spin } from "antd";
import type { IngestScope } from "../../entities/data-source";
import { EntityDrawer } from "../../shared/ui";
import { progressNotice, useAutoQualityProblems } from "../cleaning/api";
import { AutoQualityProblemActions } from "../cleaning/AutoQualityProblemActions";

export function ProblemProcessingButton({
  scope,
  recordingId,
  digest,
  canManage,
  canReadDataset,
}: {
  scope: IngestScope;
  recordingId: string;
  digest: string;
  canManage: boolean;
  canReadDataset: boolean;
}) {
  const [open, setOpen] = useState(false);
  const problems = useAutoQualityProblems(open);
  const item = problems.data?.items.find(
    (p) => p.recordingId === recordingId && p.reportSha256 === digest,
  );
  return (
    <>
      <Button type="primary" onClick={() => setOpen(true)}>
        处理
      </Button>
      <EntityDrawer
        open={open}
        title="处理问题数据"
        onClose={() => setOpen(false)}
      >
        {open &&
          (problems.isPending ? (
            <Spin />
          ) : problems.isError ? (
            <Alert type="error" title="读取处理状态失败" />
          ) : !item ? (
            <Alert type="info" title="这条质检结果已更新，请返回问题列表查看" />
          ) : item.progress !== "OPEN" ? (
            <Alert type="info" title={progressNotice(item)} />
          ) : (
            <AutoQualityProblemActions
              scope={scope}
              problem={item}
              canManage={canManage}
              canReadDataset={canReadDataset}
            />
          ))}
      </EntityDrawer>
    </>
  );
}
