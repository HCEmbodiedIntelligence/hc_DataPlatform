from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from hc_data_platform.continuous_recordings.cache_lifecycle import enabled_policy, expired
from hc_data_platform.storage.models import LifecyclePolicyAction as Action
from hc_data_platform.storage.models import LifecyclePolicyState as State


def policy(days=7, state=State.ENABLED):
    return SimpleNamespace(
        minimum_age_days=days,
        state=state,
        action=Action.CLEAN_PROCESSING_CACHE,
        version=1,
        priority=100,
        policy_id="test",
    )


def test_cache_expiry_uses_completed_ingest_time_and_current_policy():
    now = datetime(2026, 11, 5, tzinfo=timezone.utc)
    assert not expired(policy(), now - timedelta(days=7) + timedelta(microseconds=1), now)
    assert expired(policy(), now - timedelta(days=7), now)
    assert not expired(policy(state=State.PAUSED), now - timedelta(days=30), now)
    assert not expired(policy(days=14), now - timedelta(days=8), now)
    assert not expired(policy(), None, now)
    assert not expired(None, now - timedelta(days=30), now)


def test_multiple_policies_keep_longest_retention_and_pause_is_effective():
    repo = SimpleNamespace(
        all_policies=lambda **kw: [policy(7), policy(14), policy(30, State.PAUSED)]
    )
    assert enabled_policy(repo, "project").minimum_age_days == 14
    repo.all_policies = lambda **kw: [policy(7, State.PAUSED)]
    assert enabled_policy(repo, "project") is None


def test_failed_cache_expiry_preserves_original_and_recent_failures():
    import hashlib
    from contextlib import contextmanager

    from hc_data_platform.continuous_recordings.cache_lifecycle import ProcessingCacheLifecycle
    from hc_data_platform.object_storage.memory import InMemoryObjectStorage

    now = datetime(2026, 11, 5, tzinfo=timezone.utc)
    ident = "a" * 64
    key = "derived-mcap/o/p/r/" + ident + "/partial.mp4"
    store = InMemoryObjectStorage()
    store.objects.update({"raw/original": b"original", key: b"cached"})
    original = {
        "object_key": "raw/original",
        "size": 8,
        "sha256": hashlib.sha256(b"original").hexdigest(),
    }
    age = [6]
    audits = []

    def execute(sql, params):
        if "pg_try" in sql:
            return SimpleNamespace(fetchone=lambda: (True,))
        if "SELECT processing_id" in sql:
            return SimpleNamespace(fetchall=lambda: [(ident,)])
        if "SELECT updated_at" in sql:
            return SimpleNamespace(fetchone=lambda: (now - timedelta(days=age[0]), original))
        if "SELECT status" in sql:
            return SimpleNamespace(fetchone=lambda: ("FAILED", None))
        return SimpleNamespace(fetchone=lambda: None)

    @contextmanager
    def connections():
        yield SimpleNamespace(execute=execute)

    repository = SimpleNamespace(all_policies=lambda **kw: [policy()], append_audit=audits.append)
    lifecycle = ProcessingCacheLifecycle(connections, store, repository, clock=lambda: now)
    lifecycle.retire_failed(("o", "p", "r"))
    assert key in store.objects and not audits
    age[0] = 8
    lifecycle.retire_failed(("o", "p", "r"))
    assert store.objects == {"raw/original": b"original"}
    assert len(audits) == 1 and audits[0].details["reclaimed_bytes"] == 6
