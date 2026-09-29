import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Collapse, Tag, Typography } from "antd";
import type { Scope } from "../../entities/scope";
import type { ReviewDataRef } from "./data-reviews";
import {
  loadEpisodeQuality,
  qualityReasonLabels,
  qualityRuleLabels,
} from "./episode-quality";

const states = {
  pass: { label: "通过", color: "success" },
  warn: { label: "警告", color: "warning" },
  fail: { label: "失败", color: "error" },
  skip: { label: "未验明 / 跳过", color: "default" },
  info: { label: "说明", color: "processing" },
};
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
                数据版本 {data.version} · 自动检查结构与信号。
              </Typography.Paragraph>
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
              <div aria-label="逐规则结果统计">
                {Object.entries(states).map(([key, value]) => (
                  <Tag key={key} color={value.color}>
                    {value.label}{" "}
                    {quality.rules.filter((item) => item.status === key).length}
                  </Tag>
                ))}
              </div>
              {quality.additional_finding_codes.length > 0 && (
                <Typography.Paragraph>
                  录制检查：{quality.additional_finding_codes.join("、")}
                </Typography.Paragraph>
              )}
              <Collapse
                items={quality.rules.map((item) => ({
                  key: `${item.code}:${item.topic ?? "all"}`,
                  label: (
                    <span>
                      <Tag color={states[item.status].color}>
                        {states[item.status].label}
                      </Tag>
                      {qualityRuleLabels[item.code] ?? item.code}{" "}
                      {item.topic ?? ""}
                    </span>
                  ),
                  children: (
                    <>
                      {item.reason && (
                        <Typography.Paragraph>
                          {qualityReasonLabels[item.reason] ?? item.reason}
                        </Typography.Paragraph>
                      )}
                      <Typography.Paragraph>
                        已评估 {item.evaluated_count} / {item.expected_count}{" "}
                        项观测
                      </Typography.Paragraph>
                      {item.detector_findings.map((finding, index) => (
                        <Typography.Paragraph key={index}>
                          {finding.message} · 观测 {String(finding.observed)} ·
                          阈值 {String(finding.threshold)}
                        </Typography.Paragraph>
                      ))}
                      {item.report_finding_indexes.map((index) => (
                        <Typography.Paragraph key={`report-${index}`}>
                          原报告：{quality.report?.findings[index]?.message}
                        </Typography.Paragraph>
                      ))}
                      <details>
                        <summary>观测与阈值</summary>
                        <pre
                          style={{
                            whiteSpace: "pre-wrap",
                            overflowWrap: "anywhere",
                          }}
                        >
                          {JSON.stringify(
                            {
                              observed: item.observed,
                              thresholds: item.thresholds,
                            },
                            null,
                            2,
                          )}
                        </pre>
                      </details>
                    </>
                  ),
                }))}
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
