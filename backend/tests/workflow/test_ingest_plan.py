from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hc_data_platform.ingest.manifest import preflight_manifest
from hc_data_platform.ingest.models import RolloutManifestV1
from hc_data_platform.lance_catalog import InMemoryLanceCatalog
from hc_data_platform.quality import QualityProfileV1
from hc_data_platform.workflow.ingest_dispatch import IngestWorkflowPlanBlocked
from hc_data_platform.workflow.ingest_plan import PostgresIngestWorkflowInputResolver

FIXTURE_ROOT = Path(__file__).parents[1] / "system/wave2/data"


class _Storage:
    def open_reader(self, object_key: str) -> Any:
        assert object_key == "raw/legal.mcap"
        return (FIXTURE_ROOT / "packages/legal.mcap").open("rb")


class _Cursor:
    def __init__(self, responses: list[object]) -> None:
        self._responses = iter(responses)
        self._response: object = None

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, _query: str, _params: tuple[object, ...]) -> None:
        self._response = next(self._responses)

    def fetchone(self) -> object:
        return self._response

    def fetchall(self) -> object:
        return self._response


class _Connection:
    def __init__(self, responses: list[object]) -> None:
        self._cursor = _Cursor(responses)
        self.closed = False

    def cursor(self) -> _Cursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def _manifest() -> RolloutManifestV1:
    original = RolloutManifestV1.model_validate(
        json.loads((FIXTURE_ROOT / "manifests/legal.json").read_text())
    )
    return original.model_copy(
        update={
            "project_id": "project-a",
            "rollout_id": "rollout-a",
            "data_package_id": "package-a",
        }
    )


def _profile(manifest: RolloutManifestV1) -> QualityProfileV1:
    return QualityProfileV1(
        profile_id="manifest-30hz",
        required_topics=frozenset(manifest.expected_topics),
        action={
            "minimum_observation_count_risk": 0,
            "minimum_observation_count_reject": 0,
        },
    )


def test_resolver_builds_exact_persisted_plan_and_registers_dataset_schema() -> None:
    manifest = _manifest()
    preflight = preflight_manifest(manifest)
    connection = _Connection(
        [
            (
                "raw/legal.mcap",
                "raw/rollout_manifest.json",
                manifest.sha256,
                preflight.manifest_fingerprint,
                preflight.model_dump(mode="json"),
            ),
            [(_profile(manifest).model_dump(mode="json"),)],
            [("dataset-a", "snapshot-a")],
        ]
    )
    catalog = InMemoryLanceCatalog()
    resolver = PostgresIngestWorkflowInputResolver(
        lambda: connection,
        _Storage(),
        catalog,
    )

    request = resolver.resolve(
        project_id="project-a",
        region_code="region-a",
        session_id="session-a",
        rollout_id="rollout-a",
        data_package_id="package-a",
    )

    assert connection.closed
    assert request.dataset_id == "dataset-a"
    assert request.alignment.schema_snapshot_id == "snapshot-a"
    assert request.quality.profile.profile_id == "manifest-30hz"
    assert set(request.alignment.data.streams) == set(manifest.actual_topics)
    assert sum(len(stream.samples) for stream in request.alignment.data.streams.values()) == 90
    assert ("project-a", "dataset-a") in catalog._schemas


def test_resolver_fails_closed_when_quality_plan_is_ambiguous() -> None:
    manifest = _manifest()
    preflight = preflight_manifest(manifest)
    profile = _profile(manifest).model_dump(mode="json")
    connection = _Connection(
        [
            (
                "raw/legal.mcap",
                "raw/rollout_manifest.json",
                manifest.sha256,
                preflight.manifest_fingerprint,
                preflight.model_dump(mode="json"),
            ),
            [(profile,), (profile,)],
        ]
    )
    resolver = PostgresIngestWorkflowInputResolver(
        lambda: connection,
        _Storage(),
        InMemoryLanceCatalog(),
    )

    with pytest.raises(IngestWorkflowPlanBlocked, match="exactly one quality profile"):
        resolver.resolve(
            project_id="project-a",
            region_code="region-a",
            session_id="session-a",
            rollout_id="rollout-a",
            data_package_id="package-a",
        )
