"""Fixed-version QC reads and immutable scoped execution receipts."""

from typing import Literal

from hc_data_platform.core.context import current_request_context
from hc_data_platform.quality.evidence import QcEvaluation, QcRuleEvidence, RuleExecution
from hc_data_platform.quality.models import QcReportV1, QualityCode

from .models import DataVersionRef, FrozenModel
from .postgres import PackageConflict


class EpisodeQualityView(FrozenModel):
    data: DataVersionRef
    status: Literal["PASS", "RISK", "REJECT", "UNKNOWN"]
    evidence_status: Literal["AVAILABLE", "LEGACY_MISSING", "REPORT_MISSING"]
    report: QcReportV1 | None
    evidence: QcRuleEvidence | None
    rules: tuple[RuleExecution, ...]
    additional_finding_codes: tuple[str, ...] = ()


def read_episode_quality(package):
    data = package.receipt.data
    raw = package.document("hc/quality.json")
    if not isinstance(raw, dict):
        raise PackageConflict("QC package document must be an object")
    document = raw.get("quality_report", raw)
    report, evidence = None, None
    status, evidence_status = "UNKNOWN", "REPORT_MISSING"
    additional = ()
    try:
        if document.get("schema_version") == "qc-report/v1":
            report = QcReportV1.model_validate(document)
            if (
                report.rollout_id != data.identity.rollout_id
                or report.source_sha256 != package.manifest.input.source_sha256
            ):
                raise ValueError("report belongs to another source")
            status, evidence_status = report.status.value, "LEGACY_MISSING"
            if raw.get("schema_version") == "continuous-episode-qc/v1":
                if (
                    raw["source_sha256"] != report.source_sha256
                    or raw["rollout_id"] != report.rollout_id
                ):
                    raise ValueError("recording report binding mismatch")
                status, additional = raw["status"], tuple(raw["finding_codes"])
        if "hc/quality_rules.json" in package.entries:
            if report is None:
                raise ValueError("execution evidence requires a bound QC report")
            evidence = QcRuleEvidence.model_validate(package.document("hc/quality_rules.json"))
            evidence.validate_report(report)
            if (
                raw.get("rule_evidence") is not None
                and QcRuleEvidence.model_validate(raw["rule_evidence"]) != evidence
            ):
                raise ValueError("recording and package rule evidence differ")
            evidence_status = "AVAILABLE"
        if evidence is not None:
            rules = evidence.rules
        else:
            # Findings are observed historical failures/warnings. Every other
            # rule is unknown, regardless of the report's overall PASS status.
            rules = []
            for code in QualityCode:
                topics = (
                    sorted({f.topic for f in report.findings if f.code == code}) if report else []
                )
                for topic in topics or [None]:
                    indexes = (
                        tuple(
                            i
                            for i, f in enumerate(report.findings)
                            if f.code == code and f.topic == topic
                        )
                        if report
                        else ()
                    )
                    rule_status = (
                        "fail"
                        if any(report.findings[i].severity.value == "error" for i in indexes)
                        else "warn"
                        if indexes
                        else "skip"
                    )
                    rules.append(
                        RuleExecution(
                            code=code,
                            topic=topic,
                            status=rule_status,
                            reason="LEGACY_EXECUTION_EVIDENCE_MISSING"
                            if report
                            else "QC_REPORT_MISSING",
                            participates_in_verdict=True if indexes else None,
                            evaluated_count=0,
                            expected_count=0,
                            observed={"recorded_findings": len(indexes)},
                            thresholds={},
                            detector_findings=(),
                            report_finding_indexes=indexes,
                        )
                    )
            rules = tuple(sorted(rules, key=lambda r: (r.code.value, r.topic or "")))
        return EpisodeQualityView(
            data=data,
            status=status,
            evidence_status=evidence_status,
            report=report,
            evidence=evidence,
            rules=rules,
            additional_finding_codes=additional,
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise PackageConflict("fixed-version QC report/evidence binding is invalid") from exc


class EpisodeQualityEvidence:
    def __init__(self, repository):
        self.repository = repository

    def put(self, evaluation: QcEvaluation):
        # Revalidate objects too: model_copy is not a validation boundary.
        evaluation = QcEvaluation.model_validate(evaluation.model_dump(mode="json"))
        context = current_request_context()
        scope = context.organization_id, context.project_id, context.region_code
        if not all(scope):
            raise PermissionError("QC evidence requires the exact tenant scope")
        evidence = evaluation.evidence
        with self.repository.transaction() as cursor:
            cursor.execute(
                """INSERT INTO episode_store.qc_evidence
                (organization_id,project_id,region_code,rollout_id,source_sha256,
                 report_sha256,evidence_sha256,document)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                (
                    *scope,
                    evidence.rollout_id,
                    evidence.source_sha256,
                    evidence.report_sha256,
                    evidence.content_sha256,
                    evaluation.model_dump_json(),
                ),
            )
            cursor.execute(
                """SELECT document FROM episode_store.qc_evidence
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND evidence_sha256=%s""",
                (*scope, evidence.content_sha256),
            )
            row = cursor.fetchone()
            if row is None or QcEvaluation.model_validate(row[0]) != evaluation:
                raise PackageConflict("QC execution evidence is immutable")
