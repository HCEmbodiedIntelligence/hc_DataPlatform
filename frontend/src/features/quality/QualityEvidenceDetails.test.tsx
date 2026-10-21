// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { QualityEvidenceDetails } from "./QualityEvidenceDetails";

afterEach(cleanup);
it("shows recorder rejection even when all general checks pass, without counts", () => {
  render(
    <QualityEvidenceDetails
      rules={[]}
      verdict="REJECT"
      findingCodes={[
        "OPENARM_CAPTURE_FRAME_INVALID",
        "OPENARM_CAPTURE_VECTOR_INVALID",
      ]}
    />,
  );
  expect(screen.getAllByRole("listitem")).toHaveLength(2);
  expect(screen.getByText(/关节或动作数据包含缺失值/)).toBeVisible();
  expect(screen.queryByLabelText("逐规则结果统计")).not.toBeInTheDocument();
  expect(screen.queryByText("未发现失败项。")).not.toBeInTheDocument();
});
it("retains blocking risks and old report errors while hiding informational findings", () => {
  render(
    <QualityEvidenceDetails
      rules={[]}
      verdict="REJECT"
      findingCodes={["QUALITY_RISK"]}
      findings={[
        {
          code: "QC_GAP_EXCESSIVE",
          severity: "warning",
          topic: "/camera",
          message: "gap",
          observed: 67,
          threshold: 50,
        },
        {
          code: "info",
          severity: "info",
          topic: "/camera",
          message: "ignore-me",
          observed: 1,
          threshold: null,
        },
      ]}
    />,
  );
  expect(screen.getByText("采样间隔")).toBeVisible();
  expect(screen.getByText(/实测 67 · 阈值 50/)).toBeVisible();
  expect(screen.queryByText("ignore-me")).not.toBeInTheDocument();
});

it.each(["PASS", "REJECT"])(
  "shows image checks as not applicable alongside %s",
  (verdict) => {
    render(
      <QualityEvidenceDetails
        verdict={verdict}
        findingCodes={[]}
        rules={[
          {
            code: "QC_IMAGE_BLACK",
            topic: null,
            status: "skip",
            reason: "NOT_APPLICABLE_NO_IMAGES",
            participates_in_verdict: false,
            evaluated_count: 0,
            expected_count: 0,
            observed: {},
            thresholds: {},
            detector_findings: [],
            report_finding_indexes: [],
          },
        ]}
      />,
    );
    expect(screen.getByLabelText("不适用的质检项目")).toHaveTextContent(
      "RGB 黑帧：不适用（本方案不含图像）",
    );
    expect(screen.queryByLabelText("质检失败项目")).not.toBeInTheDocument();
  },
);
