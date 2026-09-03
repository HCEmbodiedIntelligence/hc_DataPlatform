from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from hc_data_platform.backup import restore_cli


def test_restore_plan_uses_dedicated_target_object_store_identity(monkeypatch: Any) -> None:
    client = object()
    prefixes: list[str] = []

    def client_for(prefix: str) -> object:
        prefixes.append(prefix)
        return client

    monkeypatch.setattr(restore_cli, "_restore_s3_client", client_for)
    monkeypatch.setenv("HC_RESTORE_OBJECT_STORE_BUCKET_REFERENCE", "s3://target-reference")
    monkeypatch.setenv("HC_RESTORE_OBJECT_STORE_PREFIX", "restore/target-1")
    monkeypatch.setenv("HC_RESTORE_OBJECT_STORE_BUCKET", "physical-target-bucket")
    request = SimpleNamespace(
        target_object_store_bucket_reference="s3://target-reference",
        target_object_store_prefix="restore/target-1",
    )

    adapter = restore_cli._target_object_store(cast(Any, request))

    assert prefixes == ["HC_RESTORE_OBJECT_STORE"]
    assert adapter._client is client
