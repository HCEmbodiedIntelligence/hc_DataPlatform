from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

BACKEND = Path(__file__).parents[2]
REPOSITORY = BACKEND.parent
OBSERVABILITY = BACKEND / "observability"
CHART = REPOSITORY / "deploy" / "helm" / "hc-data-platform"
RUNBOOK = REPOSITORY / "deploy" / "runbooks" / "observability-alerts.md"
FORBIDDEN_LABELS = {
    "project_id",
    "user_id",
    "object_key",
    "resource_id",
    "workflow_id",
    "request_id",
}
REQUIRED_METRICS = {
    "hc_data_upload_backlog",
    "hc_data_workflow_failures_total",
    "hc_data_qc_outcomes_total",
    "hc_data_lance_commits_total",
    "hc_data_transcode_duration_seconds",
    "hc_data_exports_total",
    "hc_platform_http_requests_total",
    "hc_platform_http_request_duration_seconds",
}
REQUIRED_ALERTS = {
    "HcPlatformApi5xxRateHigh",
    "HcPlatformApi429RateHigh",
    "HcPlatformOutboxLagHigh",
    "HcPlatformTemporalTaskQueueLagHigh",
    "HcPlatformBackupStale",
    "HcPlatformBackupFailure",
    "HcPlatformBackupDeepVerificationFailure",
    "HcPlatformInstanceStale",
    "HcPlatformRuntimeConfigDrift",
    "HcPlatformNodeVersionDrift",
    "HcPlatformMigrationFailure",
    "HcPlatformObjectReplicationLagHigh",
    "HcPlatformAuditIntegrityFailure",
    "HcPlatformDatabasePoolSaturated",
    "HcPlatformDiskSpaceLow",
    "HcObservabilityDeliveryCanary",
}


def _yaml(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))


def _render(*arguments: str) -> list[dict[str, Any]]:
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("helm is not installed")
    command = [
        helm,
        "template",
        "obs-contract",
        str(CHART),
        "-f",
        str(CHART / "values-ci.yaml"),
        *arguments,
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return [
        cast(dict[str, Any], document)
        for document in yaml.safe_load_all(result.stdout)
        if document is not None
    ]


def test_metric_contract_is_bounded_and_never_uses_entity_identifiers() -> None:
    contract = _yaml(OBSERVABILITY / "metrics-contract.yaml")
    metrics = {entry["name"]: entry for entry in contract["metrics"]}
    assert metrics.keys() >= REQUIRED_METRICS
    assert set(contract["cardinality_policy"]["forbidden_labels"]) == FORBIDDEN_LABELS
    for metric in metrics.values():
        assert FORBIDDEN_LABELS.isdisjoint(metric["labels"]), metric["name"]
    assert metrics["hc_platform_http_requests_total"]["labels"] == [
        "route",
        "method",
        "status_class",
        "status_code",
    ]
    assert contract["cardinality_policy"]["correlation_surface"] == "logs_and_traces"


def test_alerts_cover_the_platform_failure_matrix_and_link_real_runbooks() -> None:
    groups = _yaml(OBSERVABILITY / "prometheus-rules.yaml")["groups"]
    rules = [rule for group in groups for rule in group["rules"]]
    by_name = {rule["alert"]: rule for rule in rules}
    assert by_name.keys() >= REQUIRED_ALERTS
    runbook = RUNBOOK.read_text(encoding="utf-8").lower()
    forbidden_pattern = re.compile(
        r"(?:project_id|user_id|object_key|resource_id|workflow_id|request_id)\s*[=,)]"
    )
    for rule in rules:
        assert rule["labels"]["severity"] in {"warning", "critical"}
        assert not forbidden_pattern.search(str(rule["expr"]))
        url = rule["annotations"]["runbook_url"]
        assert url.startswith("/runbooks/observability-alerts#")
        anchor = url.rsplit("#", maxsplit=1)[1].replace("-", " ")
        assert f"## {anchor}" in runbook
    assert 'status_code="429"' in str(by_name["HcPlatformApi429RateHigh"]["expr"])
    assert by_name["HcObservabilityDeliveryCanary"]["labels"] == {
        "severity": "critical",
        "component": "observability",
        "synthetic": "true",
    }


def test_dashboard_uses_bounded_metrics_and_structured_loki_filters() -> None:
    dashboard = json.loads((OBSERVABILITY / "grafana-dashboard.json").read_text(encoding="utf-8"))
    assert dashboard["editable"] is False
    variables = {item["name"] for item in dashboard["templating"]["list"]}
    assert variables == {"service", "release", "event_code"}
    assert FORBIDDEN_LABELS.isdisjoint(variables)
    panels = dashboard["panels"]
    assert panels
    assert any(panel["type"] == "logs" for panel in panels)
    serialized = json.dumps(dashboard, sort_keys=True)
    assert not any(f"${label}" in serialized for label in FORBIDDEN_LABELS)
    log_expression = next(
        target["expr"] for panel in panels if panel["type"] == "logs" for target in panel["targets"]
    )
    assert "| json" in log_expression
    assert "$release" in log_expression and "$event_code" in log_expression
    for panel in panels:
        assert panel["links"][0]["url"].startswith("/runbooks/")


def test_collector_and_node_agent_form_a_fail_closed_durable_log_path() -> None:
    collector = _yaml(OBSERVABILITY / "otel-collector-config.yaml")
    log_pipeline = collector["service"]["pipelines"]["logs"]
    assert log_pipeline["receivers"] == ["otlp"]
    assert log_pipeline["exporters"] == ["otlp_http/loki"]
    assert "filter/runtime_contract" in log_pipeline["processors"]
    assert "debug" not in collector["exporters"]
    assert collector["exporters"]["otlp_http/loki"]["sending_queue"]["enabled"] is True
    assert collector["exporters"]["otlp_http/loki"]["retry_on_failure"]["enabled"] is True

    agent = _yaml(OBSERVABILITY / "otel-agent-config.yaml")
    receiver = agent["receivers"]["file_log/frontend"]
    assert receiver["storage"] == "file_storage"
    assert receiver["start_at"] == "beginning"
    exporter = agent["exporters"]["otlp_grpc/gateway"]
    assert exporter["sending_queue"]["storage"] == "file_storage"
    assert exporter["retry_on_failure"]["max_elapsed_time"] == "0s"
    assert "file_storage" in agent["service"]["extensions"]


def test_chart_assets_are_semantically_identical_to_reviewed_sources() -> None:
    chart_files = CHART / "files" / "observability"
    chart_dashboard = json.loads(
        (chart_files / "grafana-dashboard.json").read_text(encoding="utf-8")
    )
    source_dashboard = json.loads(
        (OBSERVABILITY / "grafana-dashboard.json").read_text(encoding="utf-8")
    )
    assert chart_dashboard == source_dashboard
    assert _yaml(chart_files / "prometheus-rules.yaml") == _yaml(
        OBSERVABILITY / "prometheus-rules.yaml"
    )


def test_helm_observability_stack_is_opt_in_pinned_and_ha_safe() -> None:
    assert not any("observability" in document["metadata"]["name"] for document in _render())
    rendered = _render(
        "--set",
        "backend.config.environment=test",
        "--set",
        "observability.enabled=true",
        "--set",
        "observability.grafana.managed.enabled=true",
        "--set",
        "observability.grafana.managed.existingAdminSecret.name=grafana-admin",
        "--set",
        "observability.prometheusOperator.enabled=true",
    )
    kinds = {(item["kind"], item["metadata"]["name"]) for item in rendered}
    suffixes = {
        "-otel-collector": "Deployment",
        "-otel-agent": "DaemonSet",
        "-loki": "StatefulSet",
        "-grafana": "Deployment",
    }
    for suffix, kind in suffixes.items():
        assert any(item_kind == kind and name.endswith(suffix) for item_kind, name in kinds)
    assert any(kind == "PrometheusRule" for kind, _ in kinds)
    assert any(kind == "ServiceMonitor" for kind, _ in kinds)
    assert not any(kind == "Secret" for kind, _ in kinds)

    backend_config = next(
        item
        for item in rendered
        if item["kind"] == "ConfigMap" and item["metadata"]["name"].endswith("-backend")
    )
    assert backend_config["data"]["HC_OBSERVABILITY_LOG_QUERY_URL"].endswith(
        "-loki:3100/loki/api/v1/query_range"
    )

    collector = next(
        item
        for item in rendered
        if item["kind"] == "Deployment" and item["metadata"]["name"].endswith("-otel-collector")
    )
    assert collector["spec"]["replicas"] == 2
    collector_image = collector["spec"]["template"]["spec"]["containers"][0]["image"]
    assert re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", collector_image)
    pdb = next(
        item
        for item in rendered
        if item["kind"] == "PodDisruptionBudget"
        and item["metadata"]["name"].endswith("-otel-collector")
    )
    assert pdb["spec"]["minAvailable"] == 1
    loki = next(item for item in rendered if item["kind"] == "StatefulSet")
    assert loki["spec"]["volumeClaimTemplates"][0]["metadata"]["name"] == "data"
    agent = next(item for item in rendered if item["kind"] == "DaemonSet")
    assert agent["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    assert (
        agent["spec"]["template"]["spec"]["containers"][0]["securityContext"][
            "readOnlyRootFilesystem"
        ]
        is True
    )


def test_production_requires_managed_loki_and_exports_operator_assets() -> None:
    production_path = REPOSITORY / "deploy/environments/production/values.example.yaml"
    production = _yaml(production_path)
    assert production["observability"]["enabled"] is True
    assert production["observability"]["loki"]["embedded"] is False
    assert production["observability"]["loki"]["endpoint"].endswith("/otlp")
    assert production["observability"]["loki"]["query"] == {
        "enabled": True,
        "existingBearerTokenSecret": {"name": "hc-platform-loki-query", "key": "token"},
    }
    assert production["observability"]["prometheusOperator"]["enabled"] is True

    rendered = _render(
        "-f",
        str(production_path),
        "--set",
        "backend.capacityGate.enabled=false",
    )
    assert not any(
        item["kind"] == "StatefulSet" and item["metadata"]["name"].endswith("-loki")
        for item in rendered
    )
    assert any(item["kind"] == "PrometheusRule" for item in rendered)
    assert any(item["kind"] == "ServiceMonitor" for item in rendered)
    backend_config = next(
        item
        for item in rendered
        if item["kind"] == "ConfigMap" and item["metadata"]["name"].endswith("-backend")
    )
    assert backend_config["data"]["HC_OBSERVABILITY_LOG_QUERY_URL"] == (
        "https://loki-gateway.observability.svc/loki/api/v1/query_range"
    )
    api = next(
        item
        for item in rendered
        if item["kind"] == "Deployment" and item["metadata"]["name"].endswith("-backend-api")
    )
    env = {item["name"]: item for item in api["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["HC_OBSERVABILITY_LOG_QUERY_BEARER_TOKEN"]["valueFrom"]["secretKeyRef"] == {
        "name": "hc-platform-loki-query",
        "key": "token",
    }


def test_retained_obs5_02_drill_proves_node_loss_query_and_alert_delivery() -> None:
    summary = json.loads(
        (BACKEND / "tests/system/results/obs5-02-drill-summary.json").read_text(encoding="utf-8")
    )
    assert summary["schema_version"] == "hc-observability-drill/v1"
    assert summary["passed"] is True
    assert summary["environment"]["nodes"] >= 3
    assert summary["environment"]["producer_node"] != summary["environment"]["observability_node"]
    retention = summary["log_retention"]
    assert retention["node_ready_during_query"] == "Unknown"
    assert retention["node_reason_during_query"] == "NodeStatusUnknown"
    assert retention["central_query_result_equal"] is True
    assert retention["before_node_loss"] == retention["after_node_loss"]
    delivery = summary["alert_delivery"]
    assert delivery["firing"]["status"] == "firing"
    assert delivery["resolved"]["status"] == "resolved"
    assert delivery["labels"] == {
        "component": "observability",
        "severity": "critical",
        "synthetic": "true",
    }
    assert delivery["fingerprint"]
    assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", item) for item in summary["images"].values())
