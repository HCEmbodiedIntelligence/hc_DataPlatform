from __future__ import annotations

from pathlib import Path

import yaml

from hc_data_platform.core.openapi import aggregate_fragments, digest
from hc_data_platform.quality import QualityProfileV1

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_openapi_exposes_versioned_profile_full_report_and_traceable_finding() -> None:
    document = yaml.safe_load((BACKEND_ROOT / "openapi" / "quality.yaml").read_text())
    schemas = document["components"]["schemas"]

    assert {
        "QualityProfileV1",
        "TopicTimingProfileV1",
        "QcFinding",
        "TopicTimingMetricsV1",
        "QcReportV1",
        "QualitySummaryV1",
    } <= schemas.keys()
    assert "profile_version" in schemas["QualityProfileV1"]["required"]
    assert {
        "code",
        "severity",
        "topic",
        "start_ns",
        "end_ns",
        "observed",
        "threshold",
    } <= set(schemas["QcFinding"]["required"])
    assert {
        "unique_frame_count",
        "actual_frequency_hz",
        "interval_p50_ns",
        "interval_p95_ns",
        "interval_p99_ns",
        "maximum_gap_ns",
        "maximum_consecutive_missing",
        "coverage_ratio",
    } <= set(schemas["TopicTimingMetricsV1"]["required"])
    assert "content_sha256" in schemas["QcReportV1"]["required"]


def test_openapi_profile_defaults_match_runtime_contract() -> None:
    document = yaml.safe_load((BACKEND_ROOT / "openapi" / "quality.yaml").read_text())
    timing = document["components"]["schemas"]["TopicTimingProfileV1"]["properties"]
    runtime = QualityProfileV1(profile_id="p", required_topics={"/camera"}).default_timing

    assert timing["target_frequency_hz"]["default"] == runtime.target_frequency_hz
    assert timing["minimum_frequency_hz_risk"]["default"] == runtime.minimum_frequency_hz_risk
    assert timing["maximum_gap_ns_reject"]["default"] == runtime.maximum_gap_ns_reject


def test_quality_fragment_aggregates_without_component_conflicts_deterministically() -> None:
    first = aggregate_fragments(BACKEND_ROOT / "openapi")
    second = aggregate_fragments(BACKEND_ROOT / "openapi")

    quality_path = (
        "/api/v1/projects/{project_id}/regions/{region_code}/rollouts/{rollout_id}/quality"
    )
    assert quality_path in first["paths"]
    assert digest(first) == digest(second)


def test_quality_migration_models_profile_versions_reports_and_metadata_separately() -> None:
    migration = (BACKEND_ROOT / "migrations" / "quality" / "0001_quality_reports.sql").read_text()

    assert migration.count("CREATE TABLE IF NOT EXISTS") == 3
    assert "profile_version integer NOT NULL" in migration
    assert "profile_sha256 char(64) NOT NULL" in migration
    assert "quality_rollout_summaries" in migration
    assert "report_json jsonb NOT NULL" in migration
    assert "ALTER TABLE quality_profiles ENABLE ROW LEVEL SECURITY" in migration
    assert "ALTER TABLE qc_reports ENABLE ROW LEVEL SECURITY" in migration
    assert "ALTER TABLE quality_rollout_summaries ENABLE ROW LEVEL SECURITY" in migration

    tenant_identity = (
        BACKEND_ROOT / "migrations" / "quality" / "0002_tenant_report_identity.sql"
    ).read_text()
    assert "PRIMARY KEY (project_id, region_code, report_sha256)" in tenant_identity
    assert "FOREIGN KEY (project_id, region_code, report_sha256)" in tenant_identity
