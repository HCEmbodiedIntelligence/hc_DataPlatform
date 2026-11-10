import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from hc_data_platform.core.context import RequestContext, bind_request_context, reset_request_context
from hc_data_platform.storage.cache_lifecycle import ACTIONS, CacheLifecycle
from hc_data_platform.storage.local_cache import CacheRoutingBucket, LocalCacheBucket
from hc_data_platform.storage.models import LifecyclePolicyState


@pytest.fixture
def bound_scope():
    token = bind_request_context(RequestContext(organization_id='01', project_id='01', region_code='global'))
    yield
    reset_request_context(token)


@pytest.mark.parametrize('kind', list(ACTIONS))
def test_live_policy_pause_change_access_scope_and_audit(tmp_path, bound_scope, kind):
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)
    cache = LocalCacheBucket(tmp_path, 'test', clock=lambda: now.timestamp())
    key = kind + '/01/01/global/test.bin'
    cache.put_object(key, b'cache')
    meta = cache.directory(key) / 'meta.json'
    old = now.timestamp() - 8 * 86400
    os.utime(meta, (old, old))
    policy = SimpleNamespace(action=ACTIONS[kind], state=LifecyclePolicyState.PAUSED,
        minimum_age_days=7, priority=100, policy_id='p', version=1)
    events = []
    repo = SimpleNamespace(all_policies=lambda **kw: [policy], append_audit=events.append)
    remote = SimpleNamespace(bucket_name='b', key_prefix='')
    storage = SimpleNamespace(_bucket=CacheRoutingBucket(remote, cache))
    collector = CacheLifecycle(None, storage, repo, clock=lambda: now)
    collector.processing = lambda scope: None
    collector.test_copies = lambda scope: None
    collector.rebuildable = lambda *args: True
    collector('01/01/global')
    assert meta.exists() and not events
    policy.state = LifecyclePolicyState.ENABLED
    policy.minimum_age_days = 30
    collector('01/01/global')
    assert meta.exists()
    policy.minimum_age_days = 7
    # A recent authorized read renews last use; inventory/head do not.
    cache.get_object(key).close()
    collector('01/01/global')
    assert meta.exists()
    os.utime(meta, (old, old))
    collector.rebuildable = lambda *args: False
    collector('01/01/global')
    assert meta.exists()
    collector.rebuildable = lambda *args: True
    collector('01/01/global')
    assert not meta.exists()
    assert events[0].details['reclaimed_bytes'] == 5
    assert events[0].details['minimum_age_days'] == 7
    with pytest.raises(PermissionError):
        collector('other/01/global')


def test_default_cache_has_no_independent_expiry(tmp_path):
    clock = [1800000000.0]
    cache = LocalCacheBucket(tmp_path, 'test', clock=lambda: clock[0])
    key = 'episode-previews/retained.mp4'
    cache.put_object(key, b'video')
    clock[0] += 366 * 86400
    assert cache.sweep(force=True) == 0
    assert cache.head_object(key).content_length == 5


def test_processing_routes_only_reconstructible_files(tmp_path):
    remote = SimpleNamespace(bucket_name='b', key_prefix='')
    bucket = CacheRoutingBucket(remote, LocalCacheBucket(tmp_path, 'test'), local_processing=True)
    prefix = 'derived-mcap/01/01/global/' + 'a'*64 + '/' + 'b'*64 + '/'
    assert bucket.local_key(prefix + 'source/selected.mcap')
    assert bucket.local_key(prefix + 'videos/head_depth.mp4')
    for protected in ['sensors/robot.mcap','hc/depth/head/validity/flags.parquet','source/frames.jsonl','recording-config.json']:
        assert bucket.local_key(prefix + protected) is None


def test_media_staging_pause_longer_retention_and_active_lease(tmp_path):
    from datetime import timedelta
    from hc_data_platform.aligned_media.maintenance import MediaStagingSweeper
    from hc_data_platform.storage.staging_lease import staging_lease
    now = datetime.now(timezone.utc)
    old = now.timestamp() - 8 * 86400
    directory = tmp_path / 'finished'
    directory.mkdir()
    (directory / 'video.mp4').write_bytes(b'video')
    os.utime(directory, (old, old))
    class Policy:
        days = None
        events = []
        def __call__(self):
            return timedelta(days=self.days) if self.days is not None else None
        def audit(self, count):
            self.events.append(count)
    policy = Policy()
    sweep = MediaStagingSweeper((tmp_path,), ttl=timedelta(days=1), retention=policy, clock=lambda: now)
    assert sweep.run_once() == 0
    policy.days = 14
    assert sweep.run_once() == 0
    assert directory.stat().st_mtime == old
    policy.days = 7
    with staging_lease(directory):
        assert sweep.run_once() == 0
    assert sweep.run_once() == 1
    assert not directory.exists() and policy.events[-1] == 1


def test_local_minio_access_ledger_and_policy_delete(tmp_path, bound_scope):
    import json
    from hc_data_platform.storage.s3_cache_access import S3CacheAccessClient
    from hc_data_platform.storage.local_cache import local_cache_for
    from hc_data_platform.object_storage.providers import S3ObjectStorage
    key = 'episode-deliveries/owner/job/test.zip'
    deleted = []
    remote = SimpleNamespace(head_object=lambda **kw: {'ContentLength': 5},
        get_object=lambda **kw: {'Body': b'cache'}, delete_object=lambda **kw: deleted.append(kw['Key']))
    cache = LocalCacheBucket(tmp_path, 'test')
    client = S3CacheAccessClient(remote, cache, 'bucket')
    storage = S3ObjectStorage(client, 'bucket')
    assert local_cache_for(storage) is cache
    client.get_object(Bucket='bucket', Key=key)
    path = cache.directory(key) / 'meta.json'
    assert json.loads(path.read_text())['remote'] is True
    now = datetime.now(timezone.utc)
    old = now.timestamp() - 8 * 86400
    os.utime(path, (old, old))
    policy = SimpleNamespace(action=ACTIONS['episode-deliveries'],state=LifecyclePolicyState.ENABLED,
        minimum_age_days=7,priority=100,policy_id='p',version=2)
    events = []
    repo = SimpleNamespace(all_policies=lambda **kw:[policy],append_audit=events.append)
    collector = CacheLifecycle(None,storage,repo,clock=lambda: now)
    collector.processing = lambda scope: None
    collector.test_copies = lambda scope: None
    collector.rebuildable = lambda *args: True
    collector('01/01/global')
    assert deleted == [key] and not path.exists()
    assert events[0].details['policy_version'] == 2


def test_automatic_policy_requires_positive_retention_and_rebuildable_target():
    from pydantic import ValidationError
    from hc_data_platform.storage.models import CreateLifecyclePolicy
    command = dict(name='preview',business_category='PENDING_ANNOTATION',object_role='REBUILDABLE_DERIVATIVE',
        action='CLEAN_RECORDING_PREVIEWS',minimum_age_days=7,priority=100)
    assert CreateLifecyclePolicy(**command).minimum_age_days == 7
    with pytest.raises(ValidationError):
        CreateLifecyclePolicy(**{**command,'minimum_age_days':0})
    with pytest.raises(ValidationError):
        CreateLifecyclePolicy(**{**command,'object_role':'RAW'})
