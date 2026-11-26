from types import SimpleNamespace

import pytest
from temporalio.exceptions import ApplicationError

from hc_data_platform.episode_store.validation_cache import reuse_verified_package
from hc_data_platform.workflow import activities


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "expected_type", "non_retryable"),
    [
        (True, "EPISODE_PACKAGE_CHANGED_DURING_VALIDATION", False),
        (False, "VALIDATION_FAILED", True),
    ],
)
async def test_commit_distinguishes_changed_attempt_from_invalid_input(
    tmp_path, monkeypatch, mutate, expected_type, non_retryable
):
    monkeypatch.setattr(activities, "_heartbeat", lambda *args: None)
    monkeypatch.setattr(activities.activity, "is_cancelled", lambda: False)
    asset = tmp_path / "data"
    asset.write_bytes(b"frozen")

    @reuse_verified_package
    def validate(directory, source):
        if mutate:
            asset.write_bytes(b"changed while reading")
            return None
        raise ValueError("invalid package content")

    source = SimpleNamespace(model_dump_json=lambda: "input")
    with pytest.raises(ApplicationError) as caught:
        await activities._invoke("commit", lambda: validate(tmp_path, source))

    assert caught.value.type == expected_type
    assert caught.value.non_retryable is non_retryable
