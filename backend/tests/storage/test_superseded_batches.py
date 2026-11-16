from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from hc_data_platform.storage.models import CreateLifecyclePolicy
from hc_data_platform.storage.superseded_batches import SupersededBatches, batch_prefix


def test_zero_days_only_for_selected_generation_policy():
    base = dict(name="old batches", business_category="PENDING_ANNOTATION",
                object_role="REBUILDABLE_DERIVATIVE", minimum_age_days=0, priority=100)
    assert CreateLifecyclePolicy(action="CLEAN_SUPERSEDED_MCAP_BATCHES", **base).minimum_age_days == 0
    with pytest.raises(ValueError):
        CreateLifecyclePolicy(action="CLEAN_PROCESSING_CACHE", **base)


def test_never_plan_escaping_listing_or_source():
    prefix = batch_prefix(("org","proj","global"), "a"*64)
    store = SimpleNamespace(list_object_keys=lambda value: ["raw/org/private.mcap"])
    collector = SupersededBatches(None,store,None,clock=lambda:datetime.now(timezone.utc))
    with pytest.raises(ValueError,match="escaped"):
        collector.objects(prefix,set())
    with pytest.raises(ValueError):
        batch_prefix(("org","proj","global"),"../raw")


def test_empty_or_paused_policy_never_reclaims():
    from hc_data_platform.storage.models import LifecyclePolicyState
    policy = SimpleNamespace(action="CLEAN_SUPERSEDED_MCAP_BATCHES",
            state=LifecyclePolicyState.PAUSED,minimum_age_days=0,priority=100,policy_id="p")
    service = SupersededBatches(None,None,SimpleNamespace(all_policies=lambda **kw:[policy]),
                              clock=lambda:datetime.now(timezone.utc))
    assert service.policy(("o","p","r"),datetime.now(timezone.utc)) is None
