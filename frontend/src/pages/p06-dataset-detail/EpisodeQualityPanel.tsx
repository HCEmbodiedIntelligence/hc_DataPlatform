import { QualityEvidenceDetails } from "../../features/quality/QualityEvidenceDetails";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Typography } from "antd";
import type { Scope } from "../../shared/scope/model";
import type { ReviewDataRef } from "../../features/datasets/episodes/data-reviews";
import { loadEpisodeQuality } from "./episode-quality";

const verdict = {
  PASS: "PASS",
  RISK: "RISK · 存在风险",
  REJECT: "REJECT · 拒绝",
  UNKNOWN: "尚无可验证报告",
};

export function EpisodeQualityPanel({
  scope,
  data,
  canRead,
}: {
  scope: Scope;
  data: ReviewDataRef;
  canRead: boolean;
}) {
  const query = useQuery({
    queryKey: ["episode-fixed-quality", scope, data],
    queryFn: ({ signal }) => loadEpisodeQuality(scope, data, signal),
    enabled: canRead,
  });
  const quality = query.data;
  return (
    <Card title="自动数据质检" size="small">
      {!canRead ? (
        <Alert type="warning" title="缺少数据版本读取权限" />
      ) : (
        <>
          {query.isPending && (
            <Typography.Paragraph>加载固定版本质检…</Typography.Paragraph>
          )}
          {query.error && (
            <Alert
              type="error"
              title="质检报告加载失败"
              description={query.error.message}
            />
          )}
          {quality && (
            <>
              <Typography.Paragraph strong>
                自动 QC 总判定：{verdict[quality.status]}
              </Typography.Paragraph>
              <Typography.Paragraph>
                数据 {data.identity.episode_id} · 数据集 {data.identity.dataset_id} · 版本 {data.version}
              </Typography.Paragraph>
              {quality.report?.profile_id === "hc-tag-aligned-qc" && (
                <Typography.Paragraph>
                  检查范围：当前 Tag
                  区间的已对齐数据与视频。原始录制的质检结果保留在来源附件中。
                </Typography.Paragraph>
              )}
              {quality.evidence_status !== "AVAILABLE" && (
                <Alert
                  type="warning"
                  title={
                    quality.evidence_status === "LEGACY_MISSING"
                      ? "历史报告缺少逐规则执行证据"
                      : "尚无可验证的 QC 报告"
                  }
                  description="未记录的检查显示为未验明；请分别查看人工数据审核与标注审核。"
                />
              )}
              <QualityEvidenceDetails
                rules={quality.rules}
                findingCodes={quality.additional_finding_codes}
                findings={quality.report?.findings}
                verdict={quality.status}
              />
            </>
          )}
          <Button
            onClick={() => void query.refetch()}
            loading={query.isFetching}
          >
            刷新质检
          </Button>
        </>
      )}
    </Card>
  );
}
