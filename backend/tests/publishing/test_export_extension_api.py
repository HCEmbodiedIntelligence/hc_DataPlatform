"""A format outside the legacy enum exercises the public extension contract."""

import hashlib
import json
from unittest.mock import Mock

import pytest

from hc_data_platform.capabilities.registry import CapabilityDescriptor, ImplementationRef
from hc_data_platform.publishing.contracts import FROZEN_MANIFEST, ExportPresentation
from hc_data_platform.publishing.memory import InMemoryArtifactSink, InMemoryLanceSnapshotExporter
from hc_data_platform.publishing.models import ExportResultV1
from hc_data_platform.publishing.ports import ExportSourcePort
from hc_data_platform.publishing.service import ExportCoordinator
from tests.publishing.test_export_job_api import _export_url, _publisher_auth
from tests.publishing.test_export_job_api import export_api as export_api


class MetadataExporter:
    format = "metadata_json"
    presentation = ExportPresentation("元数据 JSON", "无需读取机器人、媒体或对齐行。")

    def __init__(self, version="v1", *, failing=False):
        self.descriptor = CapabilityDescriptor(
            ImplementationRef(self.format, version), frozenset({FROZEN_MANIFEST})
        )
        self.failing = failing
        self.calls = 0

    def preflight(self, *, manifest, source):
        return len(manifest.rollouts)

    def export(self, *, manifest, source, sink, attempt_id):
        self.calls += 1
        if self.failing:
            raise RuntimeError("Temporary worker failure")
        content = json.dumps(
            {"hash": manifest.content_hash, "version": self.descriptor.ref.version}
        ).encode()
        digest = hashlib.sha256(content).hexdigest()
        uri = (
            f"exports/{manifest.project_id}/{manifest.dataset_id}/{manifest.dataset_version}/"
            f"{self.format}/{self.descriptor.ref.version}.json"
        )
        sink.stage_attempt(attempt_id, content)
        download = sink.publish_attempt(
            attempt_id=attempt_id, artifact_uri=uri, expected_sha256=digest
        )
        return ExportResultV1(
            format=self.format,
            project_id=manifest.project_id,
            dataset_id=manifest.dataset_id,
            dataset_version=manifest.dataset_version,
            manifest_content_hash=manifest.content_hash,
            attempt_id=attempt_id,
            artifact_uri=uri,
            download_uri=download,
            artifact_content_hash=digest,
            row_count=0,
            media_type="application/json",
        )


@pytest.fixture
def extension_api(export_api, monkeypatch):
    import hc_data_platform.publishing.router as router_module

    client, current, _ = export_api
    current["auth"] = _publisher_auth()
    source = Mock(spec=ExportSourcePort)
    source.read_steps.side_effect = AssertionError("metadata export must not require steps")
    sink = InMemoryArtifactSink()

    def install(*exporters, default="v1"):
        coordinator = ExportCoordinator(
            source=source,
            sink=sink,
            exporters=[InMemoryLanceSnapshotExporter(), *exporters],
            defaults={"lance_snapshot": "test-lance-json/v1", "metadata_json": default},
        )
        monkeypatch.setattr(router_module, "_export_coordinator", coordinator)

    yield client, current, install, source
    source.read_steps.assert_not_called()


def create(client, key="first", **body):
    return client.post(
        _export_url(),
        json={"project_id": "project-a", "format": "metadata_json", **body},
        headers={"Idempotency-Key": key},
    )


def test_registered_format_is_discovered_exported_queried_and_downloaded(extension_api):
    client, current, install, _ = extension_api
    exporter = MetadataExporter()
    install(exporter)
    catalog = client.get("/api/v1/datasets/export-formats", params={"project_id": "project-a"})
    assert catalog.status_code == 200
    assert catalog.headers["cache-control"] == "no-store"
    assert next(item for item in catalog.json()["items"] if item["format"] == "metadata_json") == {
        "format": "metadata_json",
        "implementation_version": "v1",
        "display_name": "元数据 JSON",
        "description": exporter.presentation.description,
        "required_inputs": [FROZEN_MANIFEST],
        "available": True,
        "missing_inputs": [],
    }
    result = create(client, implementation_version="v1")
    assert result.status_code == 202
    job = result.json()
    assert job["status"] == "SUCCEEDED" and job["format"] == "metadata_json"
    assert "artifact_uri" not in job["result"]
    assert create(client, implementation_version="v1").json()["job_id"] == job["job_id"]
    assert exporter.calls == 1
    loaded = client.get(_export_url(job["job_id"]), params={"project_id": "project-a"})
    assert loaded.json() == job
    download = client.get(
        _export_url(job["job_id"]) + "/download", params={"project_id": "project-a"}
    )
    assert download.status_code == 200
    assert download.json()["format"] == "metadata_json"
    assert download.json()["media_type"] == "application/json"
    current["auth"] = _publisher_auth("project-b")
    assert (
        client.get(
            "/api/v1/datasets/export-formats", params={"project_id": "project-a"}
        ).status_code
        == 403
    )
    assert (
        client.get(
            _export_url(job["job_id"]) + "/download", params={"project_id": "project-a"}
        ).status_code
        == 403
    )


def test_retry_keeps_original_version_after_default_changes_and_never_falls_forward(extension_api):
    client, _, install, _ = extension_api
    original = MetadataExporter(failing=True)
    install(original)
    failed = create(client).json()
    assert failed["status"] == "FAILED"
    upgraded = MetadataExporter("v2")
    install(upgraded, default="v2")
    retry_url = _export_url(failed["job_id"]) + ":retry"
    request = {"params": {"project_id": "project-a"}, "headers": {"Idempotency-Key": "retry"}}
    rejected = client.post(retry_url, **request)
    assert rejected.status_code == 503 and rejected.json()["code"] == "EXPORTER_UNAVAILABLE"
    assert upgraded.calls == 0
    original.failing = False
    install(original, upgraded, default="v2")
    retried = client.post(retry_url, **request)
    assert retried.status_code == 202 and retried.json()["status"] == "SUCCEEDED"
    assert original.calls == 2 and upgraded.calls == 0
    assert client.post(retry_url, **request).json()["job_id"] == retried.json()["job_id"]
    assert original.calls == 2
    assert create(client, "new-job").json()["status"] == "SUCCEEDED"
    assert upgraded.calls == 1


@pytest.mark.parametrize(
    "format,status",
    [("missing_format", 503), ("../metadata_json", 422), ("", 422), ("a" * 129, 422)],
)
def test_unknown_or_unsafe_format_is_rejected_before_export(extension_api, format, status):
    client, _, install, _ = extension_api
    exporter = MetadataExporter()
    install(exporter)
    assert create(client, format=format).status_code == status
    assert exporter.calls == 0


def test_new_implementation_cannot_return_a_different_scope(extension_api):
    client, _, install, _ = extension_api

    class WrongScope(MetadataExporter):
        def export(self, **kwargs):
            return super().export(**kwargs).model_copy(update={"project_id": "project-b"})

    install(WrongScope())
    response = create(client)
    assert response.status_code == 202
    assert response.json()["status"] == "FAILED"
    assert response.json()["error_code"] == "EXPORT_RESULT_IDENTITY_MISMATCH"
