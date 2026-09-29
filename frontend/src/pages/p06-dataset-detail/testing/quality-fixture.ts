import type { EpisodeQuality } from "../episode-quality";
import { dataReviewFixture } from "./data-review-fixture";

export function qualityFixture() {
  const { scope, data } = dataReviewFixture();
  const quality: EpisodeQuality = {
    data,
    status: "PASS",
    evidence_status: "AVAILABLE",
    additional_finding_codes: [],
    report: {
      schema_version: "qc-report/v1",
      rollout_id: data.identity.rollout_id,
      source_sha256: "a".repeat(64),
      content_sha256: "b".repeat(64),
      profile_sha256: "c".repeat(64),
      engine_version: "be06-qc/2",
      status: "PASS",
      findings: [],
    },
    evidence: {
      format: "hc-qc-rule-evidence/1",
      rollout_id: data.identity.rollout_id,
      source_sha256: "a".repeat(64),
      report_sha256: "b".repeat(64),
      content_sha256: "d".repeat(64),
      rules: [],
    },
    rules: [
      {
        code: "QC_IMAGE_BLACK",
        topic: "/depth",
        status: "skip",
        reason: "NO_OBSERVATIONS",
        participates_in_verdict: false,
        evaluated_count: 0,
        expected_count: 10,
        observed: {},
        thresholds: {},
        detector_findings: [],
        report_finding_indexes: [],
      },
    ],
  };
  quality.evidence!.rules = structuredClone(quality.rules);
  return { scope, data, quality };
}
