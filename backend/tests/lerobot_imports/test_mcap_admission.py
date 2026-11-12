import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest

from hc_data_platform.continuous_recordings.models import RecordingScope
from hc_data_platform.lerobot_imports.mcap_admission import existing_recording, verified_original
from tests.continuous_recordings.test_content_duplicates import register


def test_unknown_oss_digest_is_read_without_a_copy_or_receipt():
    body = b"the same recording at a different path"
    calls = []

    def chunks(key):
        calls.append(key)
        yield body[:8]
        yield body[8:]

    entry = dict(
        path="renamed.mcap",
        object_key="moved/object",
        size=len(body),
        source_etag="v1",
        sha256=None,
        verification_key="unused/receipt",
    )
    store = SimpleNamespace(
        head=lambda key: None
        if key == entry["verification_key"]
        else SimpleNamespace(size=len(body), etag="v1"),
        read_chunks=chunks,
    )
    result = verified_original(store, entry, lambda: None)
    assert result["sha256"] == hashlib.sha256(body).hexdigest()
    assert calls == [entry["object_key"]]


@pytest.mark.integration
def test_concurrent_duplicate_sees_registration_before_conversion():
    dsn = os.environ.get("HC_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    scope = RecordingScope(
        organization_id="org-" + uuid4().hex, project_id="project", region_code="global"
    )
    tenant = (scope.organization_id, scope.project_id, scope.region_code)
    body = b"identical bytes"
    original = dict(path="recording.mcap", sha256=hashlib.sha256(body).hexdigest())
    waiting = Event()
    with psycopg.connect(dsn) as setup:
        setup.execute(
            "INSERT INTO registry.organization_projects (organization_id,project_id,display_name) "
            "VALUES (%s,%s,'test')",
            tenant[:2],
        )

    def second():
        with psycopg.connect(dsn) as lock:
            return existing_recording(
                lock, tenant, "different-raw", dict(original, path="renamed.mcap"), waiting.set
            )

    with ThreadPoolExecutor(max_workers=1) as pool, psycopg.connect(dsn) as first:
        assert existing_recording(first, tenant, "raw", original, lambda: None) is None
        future = pool.submit(second)
        try:
            assert waiting.wait(5)
            with psycopg.connect(dsn) as writer, writer.cursor() as cursor:
                winner = register(cursor, scope, body)
        finally:
            first.commit()
        assert future.result(timeout=5) == winner["recording_id"]
