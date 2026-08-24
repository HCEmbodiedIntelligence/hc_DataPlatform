from __future__ import annotations

import pytest

from hc_data_platform.core.context import current_request_context
from hc_data_platform.workflow import outbox_worker


class _StopOutboxLoop(Exception):
    pass


class _RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None, bool]] = []

    async def dispatch_one(
        self, *, organization_id: str, project_id: str, region_code: str
    ) -> bool:
        context = current_request_context()
        assert organization_id == context.organization_id
        self.calls.append(
            (
                project_id,
                region_code,
                context.organization_id,
                context.service_identity,
            )
        )
        return False


def test_parse_scope_fails_closed_for_legacy_or_ambiguous_values() -> None:
    assert outbox_worker._parse_scope(" organization-a/project-a/cn-east ") == (
        "organization-a",
        "project-a",
        "cn-east",
    )
    for invalid in (
        "project-a/cn-east",
        "organization-a/project-a/cn-east/extra",
        "organization-a//cn-east",
    ):
        with pytest.raises(ValueError, match="organization_id/project_id/region_code"):
            outbox_worker._parse_scope(invalid)


@pytest.mark.asyncio
async def test_worker_binds_exact_organization_scope_and_resets_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = _RecordingDispatcher()

    async def stop_after_empty_poll(_seconds: float) -> None:
        raise _StopOutboxLoop

    monkeypatch.setattr(outbox_worker.asyncio, "sleep", stop_after_empty_poll)

    with pytest.raises(_StopOutboxLoop):
        await outbox_worker.serve_outbox(
            dispatcher,  # type: ignore[arg-type]
            scopes=("organization-a/project-a/cn-east",),
            poll_interval_seconds=0.01,
            batch_size=2,
        )

    assert dispatcher.calls == [("project-a", "cn-east", "organization-a", True)]
    with pytest.raises(RuntimeError, match="no RequestContext"):
        current_request_context()
