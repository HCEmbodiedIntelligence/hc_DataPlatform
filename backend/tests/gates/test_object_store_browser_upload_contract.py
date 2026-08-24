from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree

import yaml

ROOT = Path(__file__).resolve().parents[3]
CORS_PATH = ROOT / "deploy" / "compose" / "minio-browser-upload-cors.xml"
S3_XML_NAMESPACE = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


def _compose(name: str) -> dict[str, object]:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def test_compose_keeps_server_operations_internal_and_api_presigning_public() -> None:
    development = _compose("compose.dev.yaml")["services"]
    real_api = _compose("compose.real-api.yaml")["services"]
    test = _compose("compose.test.yaml")["services"]

    dev_api = development["api"]["environment"]
    dev_worker = development["worker"]["environment"]
    assert "http://minio:9000" in dev_api["HC_OBJECT_STORE_ENDPOINT"]
    assert "http://127.0.0.1:9000" in dev_api["HC_OBJECT_STORE_PUBLIC_ENDPOINT"]
    assert "http://minio:9000" in dev_worker["HC_OBJECT_STORE_ENDPOINT"]
    assert "HC_OBJECT_STORE_PUBLIC_ENDPOINT" not in dev_worker
    assert (
        "http://127.0.0.1:9000" in real_api["api"]["environment"]["HC_OBJECT_STORE_PUBLIC_ENDPOINT"]
    )

    test_api = test["api"]["environment"]
    test_worker = test["worker"]["environment"]
    assert test_api["HC_OBJECT_STORE_ENDPOINT"] == "http://minio:9000"
    assert test_api["HC_OBJECT_STORE_PUBLIC_ENDPOINT"] == "http://127.0.0.1:9000"
    assert test_worker["HC_OBJECT_STORE_ENDPOINT"] == "http://minio:9000"
    assert "HC_OBJECT_STORE_PUBLIC_ENDPOINT" not in test_worker

    runtime = (ROOT / "backend/src/hc_data_platform/runtime.py").read_text(encoding="utf-8")
    assert '"aws_endpoint": resolved.object_store_endpoint' in runtime
    assert '"aws_endpoint": resolved.object_store_public_endpoint' not in runtime


def test_minio_init_applies_idempotent_exact_origin_browser_upload_cors() -> None:
    expected_origins = {
        "http://127.0.0.1:8088",
        "http://localhost:8088",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    }
    root = ElementTree.parse(CORS_PATH).getroot()
    origins = {value.text for value in root.findall(".//s3:AllowedOrigin", S3_XML_NAMESPACE)}
    methods = {value.text for value in root.findall(".//s3:AllowedMethod", S3_XML_NAMESPACE)}
    headers = {value.text for value in root.findall(".//s3:AllowedHeader", S3_XML_NAMESPACE)}
    exposed = {value.text for value in root.findall(".//s3:ExposeHeader", S3_XML_NAMESPACE)}

    assert origins == expected_origins
    assert "*" not in origins
    assert methods == {"PUT", "HEAD"}
    assert headers == {"content-type"}
    assert exposed == {"ETag"}
    assert root.find(".//s3:MaxAgeSeconds", S3_XML_NAMESPACE).text == "600"
    assert "AllowCredentials" not in CORS_PATH.read_text(encoding="utf-8")

    for compose_name in ("compose.dev.yaml", "compose.test.yaml"):
        services = _compose(compose_name)["services"]
        init = services["minio-init"]
        browser_edge = services["object-store-browser"]
        command = "\n".join(init["entrypoint"])
        assert (
            set(services["minio"]["environment"]["MINIO_API_CORS_ALLOW_ORIGIN"].split(","))
            == expected_origins
        )
        assert "mc mb --ignore-existing" in command
        assert "mc cors set" in command
        assert "functionality that is not implemented" in command
        assert any(str(CORS_PATH.relative_to(ROOT)) in volume for volume in init["volumes"])
        assert any("127.0.0.1:9000:9000" in str(port) for port in browser_edge["ports"])

    edge_config = (ROOT / "deploy/compose/object-store-browser.conf").read_text(encoding="utf-8")
    assert expected_origins == {
        line.split('"')[1]
        for line in edge_config.splitlines()
        if line.strip().startswith('"http://')
    }
    assert "Access-Control-Allow-Credentials" in edge_config
    assert "proxy_hide_header Access-Control-Allow-Credentials" in edge_config
    assert "add_header Access-Control-Allow-Credentials" not in edge_config
    assert "PUT PUT;" in edge_config
    assert "HEAD HEAD;" in edge_config
    assert "^(?:GET|PUT|HEAD)$" in edge_config


def test_helm_requires_https_public_endpoint_without_claiming_vendor_cors_management() -> None:
    values = yaml.safe_load(
        (ROOT / "deploy/helm/hc-data-platform/values.yaml").read_text(encoding="utf-8")
    )
    assert values["backend"]["config"]["objectStorePublicEndpoint"] == ""

    configmap = (ROOT / "deploy/helm/hc-data-platform/templates/backend-configmap.yaml").read_text(
        encoding="utf-8"
    )
    assert "HC_OBJECT_STORE_ENDPOINT" in configmap
    assert "HC_OBJECT_STORE_PUBLIC_ENDPOINT" in configmap
    assert 'required "backend.config.objectStorePublicEndpoint is required"' in configmap

    production = yaml.safe_load(
        (ROOT / "deploy/environments/production/values.example.yaml").read_text(encoding="utf-8")
    )
    public_endpoint = production["backend"]["config"]["objectStorePublicEndpoint"]
    assert urlparse(public_endpoint).scheme == "https"

    chart_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "deploy/helm/hc-data-platform/templates").iterdir()
        if path.is_file()
    )
    assert "mc cors set" not in chart_text
    assert "CORSConfiguration" not in chart_text
