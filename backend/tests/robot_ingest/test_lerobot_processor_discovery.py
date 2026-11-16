from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.robot_ingest import lerobot_processor as module
from hc_data_platform.robot_ingest.lerobot_processor import NativeLeRobotProcessor
from hc_data_platform.robot_ingest.processing_contract import ProcessingFailure


def _raw(content_hash: str) -> Mock:
    raw = Mock(upload_id="upload")
    raw.model_dump_json.return_value = f'{{"content_hash":"{content_hash}"}}'
    return raw


def _processor(raw_sources: Mock) -> NativeLeRobotProcessor:
    return NativeLeRobotProcessor(
        Mock(),
        Mock(),
        Mock(),
        task_queue="queue",
        raw_sources=raw_sources,
        quality_reports=Mock(),
        workflow_results=Mock(),
        publication_receipts=Mock(),
    )


def _source() -> SimpleNamespace:
    target = SimpleNamespace(organization_id="org", project_id="project", region_code="region")
    return SimpleNamespace(
        upload=SimpleNamespace(target=target, raw_source_id="raw", upload_id="upload"),
        storage=Mock(),
    )


def test_episodes_of_one_committed_source_share_one_discovery(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        module, "discover_committed_source", lambda storage, raw: calls.append(raw) or object()
    )
    raw_sources = Mock()
    raw_sources.get_source.return_value = _raw("a")
    processor = _processor(raw_sources)
    token = bind_request_context(
        RequestContext(organization_id="org", project_id="project", region_code="region")
    )
    try:
        first = processor._discover(_source())
        assert processor._discover(_source()) is first
        assert len(calls) == 1
        # Raw facts are re-read every time; a changed source is discovered again.
        raw_sources.get_source.return_value = _raw("b")
        assert processor._discover(_source()) is not first
        assert len(calls) == 2
    finally:
        reset_request_context(token)


def test_failed_discovery_is_not_cached(monkeypatch) -> None:
    outcomes = [ValueError("LEROBOT_ASSET_INTEGRITY"), object()]

    def discover(storage, raw):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(module, "discover_committed_source", discover)
    raw_sources = Mock()
    raw_sources.get_source.return_value = _raw("a")
    processor = _processor(raw_sources)
    token = bind_request_context(
        RequestContext(organization_id="org", project_id="project", region_code="region")
    )
    try:
        with pytest.raises(ProcessingFailure):
            processor._discover(_source())
        assert processor._discover(_source()) is not None
    finally:
        reset_request_context(token)
