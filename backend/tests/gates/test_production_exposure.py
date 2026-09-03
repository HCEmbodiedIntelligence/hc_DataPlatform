from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CHART = REPOSITORY_ROOT / "deploy/helm/hc-data-platform"
PRODUCTION_VALUES = REPOSITORY_ROOT / "deploy/environments/production/values.example.yaml"


def _render_production_chart() -> list[dict[str, Any]]:
    helm = shutil.which("helm")
    assert helm is not None, "Helm is required for the production exposure gate"
    completed = subprocess.run(
        [
            helm,
            "template",
            "br03-gate",
            str(CHART),
            "-f",
            str(PRODUCTION_VALUES),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return [document for document in yaml.safe_load_all(completed.stdout) if document]


def _by_kind(documents: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [document for document in documents if document.get("kind") == kind]


def test_compose_public_gateway_denies_metrics_and_documentation_without_ip_headers() -> None:
    config = (REPOSITORY_ROOT / "deploy/compose/gateway.conf").read_text(encoding="utf-8")
    denied = re.search(
        r"location\s+~\s+\^/\(\?:metrics.*?\}\s*",
        config,
        flags=re.DOTALL,
    )
    assert denied is not None
    block = denied.group(0)
    assert "return 404" in block
    assert "proxy_pass" not in block
    assert "X-Forwarded-For" not in block
    assert "allow " not in block and "deny " not in block


def test_production_helm_has_public_deny_and_selector_bound_internal_metrics_path() -> None:
    documents = _render_production_chart()
    services = {item["metadata"]["name"]: item for item in _by_kind(documents, "Service")}
    ingress_documents = _by_kind(documents, "Ingress")
    assert len(ingress_documents) == 1
    ingress = ingress_documents[0]

    rules = ingress["spec"]["rules"][0]["http"]["paths"]
    backends = {
        (item["path"], item["pathType"]): item["backend"]["service"]["name"] for item in rules
    }
    metrics_backend = backends[("/metrics", "Exact")]
    assert metrics_backend.endswith("-public-deny")
    assert services[metrics_backend]["spec"]["selector"]["app.kubernetes.io/component"] == (
        "public-deny"
    )

    api_backend = backends[("/docs", "Exact")]
    assert api_backend.endswith("-backend-api")
    for path in ("/docs/oauth2-redirect", "/redoc", "/openapi.json"):
        assert backends[(path, "Exact")] == api_backend

    metrics_services = [
        service for name, service in services.items() if name.endswith("-backend-metrics")
    ]
    assert len(metrics_services) == 1
    internal_metrics = metrics_services[0]
    assert internal_metrics["spec"]["type"] == "ClusterIP"
    assert internal_metrics["spec"]["selector"]["app.kubernetes.io/component"] == "api"
    assert internal_metrics["spec"]["ports"] == [
        {"name": "metrics", "port": 9090, "targetPort": "http", "protocol": "TCP"}
    ]
    assert internal_metrics["metadata"]["annotations"]["prometheus.io/path"] == "/metrics"

    deployments = _by_kind(documents, "Deployment")
    pod_components = {
        deployment["spec"]["template"]["metadata"]["labels"].get("app.kubernetes.io/component")
        for deployment in deployments
    }
    assert "public-deny" not in pod_components


def test_production_network_policy_allows_only_selected_monitor_to_api_metrics_port() -> None:
    documents = _render_production_chart()
    policies = {item["metadata"]["name"]: item for item in _by_kind(documents, "NetworkPolicy")}
    name = next(name for name in policies if name.endswith("-allow-internal-metrics"))
    policy = policies[name]
    assert policy["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/component"] == ("api")
    ingress_rule = policy["spec"]["ingress"]
    assert ingress_rule == [
        {
            "from": [
                {
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": "observability"}
                    },
                    "podSelector": {"matchLabels": {"app.kubernetes.io/name": "prometheus"}},
                }
            ],
            "ports": [{"protocol": "TCP", "port": 8000}],
        }
    ]

    gateway_name = next(name for name in policies if name.endswith("-allow-gateway"))
    gateway_expressions = policies[gateway_name]["spec"]["podSelector"]["matchExpressions"]
    assert gateway_expressions == [
        {
            "key": "app.kubernetes.io/component",
            "operator": "In",
            "values": ["frontend", "api"],
        }
    ]

    config_maps = _by_kind(documents, "ConfigMap")
    backend_config_maps = [
        item for item in config_maps if item["metadata"]["name"].endswith("-backend")
    ]
    assert len(backend_config_maps) == 1
    assert backend_config_maps[0]["data"]["HC_API_DOCS_ENABLED"] == "false"
    observability_config_maps = {
        item["metadata"]["name"]
        for item in config_maps
        if "observability" in item["metadata"]["name"]
    }
    assert len(observability_config_maps) == 4
    assert any(name.endswith("-collector") for name in observability_config_maps)
    assert any(name.endswith("-agent") for name in observability_config_maps)
    assert any(name.endswith("-dashboard") for name in observability_config_maps)
    assert any(name.endswith("-prometheus-rules") for name in observability_config_maps)

    observability_policy_name = next(
        name for name in policies if name.endswith("-allow-observability-internal")
    )
    observability_policy = policies[observability_policy_name]
    assert observability_policy["spec"]["policyTypes"] == ["Ingress"]
    assert observability_policy["spec"]["podSelector"]["matchExpressions"][1] == {
        "key": "app.kubernetes.io/component",
        "operator": "In",
        "values": ["otel-collector", "loki", "grafana"],
    }
