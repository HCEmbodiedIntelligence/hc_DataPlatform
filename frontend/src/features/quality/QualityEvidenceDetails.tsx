import { Typography } from "antd";
import { qualityReasonLabels, qualityRuleLabels } from "./labels";
import type { components } from "../../shared/api/generated/platform";

const recordingReasons: Record<string, string> = {
  OPENARM_CAPTURE_FRAME_INVALID:
    "采集帧无效：图像时间对齐、关节或动作检查未满足录制要求",
  OPENARM_CAPTURE_VECTOR_INVALID:
    "关节或动作数据包含缺失值、无效数值或错误的向量结构",
  OPENARM_CAPTURE_VECTOR_LAYOUT_MISMATCH:
    "有效关节或动作数据缺失，或两者的关节名称 / 单位不一致",
  QUALITY_RISK: "存在风险项，当前入库规则要求全部通过",
  QUALITY_REJECT: "自动检查存在失败项",
};
type Finding = {
  message: string;
  code: string;
  topic: string | null;
  severity?: "warning" | "error" | "info";
  observed: unknown;
  threshold: unknown;
};
type Rule = Omit<
  components["schemas"]["RuleExecution"],
  "code" | "detector_findings"
> & { code: string; detector_findings: readonly Finding[] };
const valueText = (value: unknown) =>
  typeof value === "object" ? JSON.stringify(value) : String(value);

/** Show rejection reasons together, including recorder checks outside the QC rules. */
export function QualityEvidenceDetails({
  rules,
  findingCodes,
  findings = [],
  verdict,
}: {
  rules: readonly Rule[];
  findingCodes: readonly string[];
  findings?: readonly Finding[];
  verdict?: string;
}) {
  const riskBlocks = findingCodes.includes("QUALITY_RISK") || verdict === "RISK";
  const failedRules = rules.filter(
    (rule) => rule.status === "fail" ||
      (riskBlocks && rule.status === "warn" && rule.participates_in_verdict !== false),
  );
  const unlistedFindings = findings.filter(
    (finding) => finding.severity !== "info" && (finding.severity !== "warning" || riskBlocks) &&
      !failedRules.some((rule) => rule.code === finding.code && rule.topic === finding.topic),
  );
  const hasRuleDetails = failedRules.length + unlistedFindings.length > 0;
  const codes = [...new Set(findingCodes)].filter(
    (code) => !(hasRuleDetails && ["QUALITY_REJECT", "QUALITY_RISK"].includes(code)),
  );
  const details = (finding: Finding) => (
    <span>
      {finding.topic ? `${finding.topic} · ` : ""}
      实测 {valueText(finding.observed)} · 阈值 {valueText(finding.threshold)}
    </span>
  );
  if (!hasRuleDetails && codes.length === 0) {
    return verdict === "PASS" ? (
      <Typography.Paragraph>未发现失败项。</Typography.Paragraph>
    ) : verdict === "REJECT" ? (
      <Typography.Paragraph>质检未通过，但当前报告未提供失败明细。</Typography.Paragraph>
    ) : null;
  }
  return (
    <ul aria-label="质检失败项目" style={{ paddingInlineStart: 20 }}>
      {codes.map((code) => (
        <li key={code} style={{ marginBlock: 12 }}>
          {recordingReasons[code] ?? qualityRuleLabels[code] ?? code}
        </li>
      ))}
      {failedRules.map((rule) => {
        const evidence = rule.detector_findings.length
          ? rule.detector_findings
          : rule.report_finding_indexes.flatMap((index) => findings[index] ? [findings[index]] : []);
        return (
          <li key={`${rule.code}:${rule.topic}`} style={{ marginBlock: 12 }}>
            <strong>{qualityRuleLabels[rule.code] ?? rule.code}</strong>
            {rule.topic && <span> · {rule.topic}</span>}
            {rule.reason && <div>{qualityReasonLabels[rule.reason] ?? rule.reason}</div>}
            {evidence.map((finding, index) => <div key={index}>{details(finding)}</div>)}
            {!evidence.length && (Object.keys(rule.observed).length > 0 || Object.keys(rule.thresholds).length > 0) && (
              <div>实测 {valueText(rule.observed)} · 阈值 {valueText(rule.thresholds)}</div>
            )}
          </li>
        );
      })}
      {unlistedFindings.map((finding, index) => (
        <li key={`${finding.code}:${finding.topic}:${index}`} style={{ marginBlock: 12 }}>
          <strong>{qualityRuleLabels[finding.code] ?? finding.message}</strong>
          <div>{details(finding)}</div>
        </li>
      ))}
    </ul>
  );
}
