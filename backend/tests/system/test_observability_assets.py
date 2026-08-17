from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import yaml

BACKEND = Path(__file__).parents[2]
OBSERVABILITY = BACKEND / "observability"
REQUIRED_DIMENSIONS = {"project_id", "resource_id", "workflow_id"}
REQUIRED_METRICS = {
    "hc_data_upload_backlog",
    "hc_data_workflow_failures_total",
    "hc_data_qc_outcomes_total",
    "hc_data_lance_commits_total",
    "hc_data_transcode_duration_seconds",
    "hc_data_exports_total",
}


def _yaml(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))


def test_metric_contract_covers_every_required_stage_and_locator() -> None:
    contract = _yaml(OBSERVABILITY / "metrics-contract.yaml")
    metrics = {entry["name"]: entry for entry in contract["metrics"]}
    assert metrics.keys() >= REQUIRED_METRICS
    for metric in metrics.values():
        assert set(metric["labels"]) >= REQUIRED_DIMENSIONS, metric["name"]
    assert {"authorization", "jwt", "signed_url", "object_store_secret_key"} <= set(
        contract["logging"]["forbidden_fields"]
    )


def test_every_alert_retains_locators_and_links_to_a_real_runbook_anchor() -> None:
    groups = _yaml(OBSERVABILITY / "prometheus-rules.yaml")["groups"]
    rules = [rule for group in groups for rule in group["rules"]]
    assert rules
    runbook = (BACKEND / "runbooks" / "pipeline-alerts.md").read_text(encoding="utf-8")
    for rule in rules:
        expression = str(rule["expr"])
        assert all(dimension in expression for dimension in REQUIRED_DIMENSIONS), rule["alert"]
        annotations = rule["annotations"]
        assert all(dimension in annotations["description"] for dimension in REQUIRED_DIMENSIONS)
        url = annotations["runbook_url"]
        assert url.startswith("/runbooks/pipeline-alerts#")
        anchor = url.rsplit("#", maxsplit=1)[1].replace("-", " ")
        assert f"## {anchor}".lower() in runbook.lower()


def test_dashboard_panels_are_scoped_and_actionable() -> None:
    dashboard = json.loads((OBSERVABILITY / "grafana-dashboard.json").read_text(encoding="utf-8"))
    variables = {item["name"] for item in dashboard["templating"]["list"]}
    assert variables >= REQUIRED_DIMENSIONS
    assert dashboard["panels"]
    for panel in dashboard["panels"]:
        expressions = "\n".join(target["expr"] for target in panel["targets"])
        assert all(f"${dimension}" in expressions for dimension in REQUIRED_DIMENSIONS)
        assert panel["links"][0]["url"].startswith("/runbooks/pipeline-alerts#")


def test_collector_preserves_trace_to_metric_locator_dimensions() -> None:
    collector = _yaml(OBSERVABILITY / "otel-collector-config.yaml")
    dimensions = {item["name"] for item in collector["connectors"]["spanmetrics"]["dimensions"]}
    assert dimensions >= REQUIRED_DIMENSIONS
    assert "prometheus" in collector["service"]["pipelines"]["metrics"]["exporters"]


def test_retained_pilot_otlp_summary_proves_transport_but_not_domain_metrics() -> None:
    summary = json.loads(
        (BACKEND / "tests" / "system" / "results" / "pilot-otlp-summary.json").read_text(
            encoding="utf-8"
        )
    )

    assert set(summary["services"]) == {"hc-data-backend-api", "hc-data-backend-worker"}
    assert summary["spans"] > 0
    assert summary["metrics"] > 0
    assert summary["log_records"] > 0
    assert summary["log_bodies_retained"] is False
    assert REQUIRED_METRICS.isdisjoint(summary["metric_names"])
