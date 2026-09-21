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


def test_isolated_minio_test_stack_applies_exact_origin_browser_upload_cors() -> None:
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

    services = _compose("deploy/compose/compose.test.yaml")["services"]
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
    assert any(
        (ROOT / "deploy/compose" / volume.split(":", 1)[0]).resolve() == CORS_PATH
        for volume in init["volumes"]
    )
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
