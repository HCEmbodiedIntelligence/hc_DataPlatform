"""Real PostgreSQL regression coverage; never uses the application's database."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from hc_data_platform.core.context import RequestContext, bind_request_context, reset_request_context
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.storage.inventory_retention import InventoryRetention
from hc_data_platform.storage.models import CapacityInventoryFact, LifecyclePolicy
from hc_data_platform.storage.postgres import PostgresStorageRepository
from hc_data_platform.storage.service import StorageGovernanceService

@pytest.fixture
def env():
    dsn=os.getenv('HC_INVENTORY_TEST_DSN')
    if not dsn:
        pytest.skip('requires isolated inventory regression database')
    import psycopg
    project='inventory-'+uuid4().hex
    with psycopg.connect(dsn) as conn:
        conn.execute('INSERT INTO registry.organization_projects (organization_id,project_id,display_name) VALUES (%s,%s,%s)', ('inventory-test',project,'Inventory retention test'))
    token=bind_request_context(RequestContext(organization_id='inventory-test',project_id=project,region_code='global',service_identity=True))
    connections=psycopg_connection_factory(dsn)
    repo=PostgresStorageRepository(connections)
    now=datetime.now(timezone.utc)
    def snapshot(name,age):
        facts=(CapacityInventoryFact(project_id=project,snapshot_id=name,physical_instance_id='object',logical_object_id='raw',physical_bytes='100',disposition='PRIMARY',business_category='RAW',object_role='RAW',observed_at=now-timedelta(days=age)),)
        summary=StorageGovernanceService._reconcile(project_id=project,snapshot_id=name,facts=facts)
        repo.replace_inventory_snapshot(snapshot=summary,facts=facts)
        return summary
    def policy(action='CLEAN_INVENTORY_DETAILS',days=0,state='ENABLED'):
        policy=LifecyclePolicy(project_id=project,policy_id=uuid4().hex,name=uuid4().hex,business_category='PENDING_ANNOTATION',object_role='REBUILDABLE_DERIVATIVE',action=action,minimum_age_days=days,priority=100,state=state,version=1,etag=uuid4().hex,created_at=now,updated_at=now)
        with connections() as conn:
            conn.execute('''INSERT INTO storage.lifecycle_policies
             (project_id,policy_id,name,business_category,object_role,action,minimum_age_days,priority,state,version,etag,created_at,updated_at)
             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
             (project,policy.policy_id,policy.name,policy.business_category.value,policy.object_role.value,action,days,100,state,1,policy.etag,now,now))
        return policy
    yield project,connections,repo,InventoryRetention(connections),snapshot,policy
    reset_request_context(token)


def test_compact_preserves_latest_summary_trend_and_idempotency(env):
    project,connect,repo,retain,snapshot,policy=env
    old=snapshot('old',8);latest=snapshot('latest',1);policy()
    assert retain.run(project_id=project)==dict(deleted_entries=1,summarized_snapshots=1,deleted_summaries=0)
    assert repo.inventory_facts(project_id=project,snapshot_id='old')==()
    assert repo.capacity_summary(project_id=project,snapshot_id='old')==old
    assert len(repo.inventory_facts(project_id=project,snapshot_id='latest'))==1
    assert repo.capacity_summary(project_id=project,snapshot_id='latest')==latest
    assert retain.run(project_id=project)['deleted_entries']==0
    snapshot('old',8)  # Same immutable observation can be retried without resurrecting facts.
    assert repo.inventory_facts(project_id=project,snapshot_id='old')==()
    assert len(repo.list_capacity_history(project_id=project,window_start=old.observed_at-timedelta(days=1),window_end=latest.observed_at+timedelta(days=1),limit=100))==2
    with connect() as c:
        assert c.execute('SELECT count(*) FROM storage.lifecycle_audit_events WHERE project_id=%s',(project,)).fetchone()[0]==1


def test_paused_and_missing_policy_retain_details(env):
    project,connect,repo,retain,snapshot,policy=env
    snapshot('old',50);snapshot('new',1)
    assert retain.run(project_id=project)['deleted_entries']==0
    p=policy(state='PAUSED')
    assert retain.run(project_id=project)['deleted_entries']==0
    with connect() as c:
        c.execute("UPDATE storage.lifecycle_policies SET state='ENABLED' WHERE policy_id=%s",(p.policy_id,))
    assert retain.run(project_id=project)['deleted_entries']==1


def test_live_age_and_history_retention_are_independent(env):
    project,connect,repo,retain,snapshot,policy=env
    snapshot('old',20);snapshot('recent',2);snapshot('new',1)
    p=policy(days=30)
    assert retain.run(project_id=project)['deleted_entries']==0
    with connect() as c:
        c.execute('UPDATE storage.lifecycle_policies SET minimum_age_days=7,version=2 WHERE policy_id=%s',(p.policy_id,))
    assert retain.run(project_id=project)['deleted_entries']==1
    assert repo.capacity_summary(project_id=project,snapshot_id='old') is not None
    policy('CLEAN_INVENTORY_HISTORY',days=10)
    assert retain.run(project_id=project)['deleted_summaries']==1
    assert repo.capacity_summary(project_id=project,snapshot_id='old') is None
    assert len(repo.inventory_facts(project_id=project,snapshot_id='recent'))==1


def test_legacy_backfill_and_direct_mutation_guards(env):
    import psycopg
    project,connect,repo,retain,snapshot,policy=env
    old=snapshot('old',10);snapshot('new',1);policy()
    with connect() as c:
        # Simulate a pre-migration snapshot; only this isolated fixture bypasses guards.
        c.execute('SET LOCAL session_replication_role=replica')
        c.execute("UPDATE storage.inventory_snapshots SET summary=NULL WHERE project_id=%s AND snapshot_id='old'",(project,))
    with pytest.raises(psycopg.Error),connect() as c:
        c.execute("DELETE FROM storage.inventory_facts WHERE project_id=%s AND snapshot_id='new'",(project,))
    with pytest.raises(psycopg.Error),connect() as c:
        c.execute("UPDATE storage.inventory_snapshots SET details_expired_at=now() WHERE project_id=%s AND snapshot_id='new'",(project,))
    assert retain.run(project_id=project)['deleted_entries']==1
    assert repo.capacity_summary(project_id=project,snapshot_id='old')==old
    with pytest.raises(psycopg.Error),connect() as c:
        c.execute("UPDATE storage.inventory_snapshots SET physical_total_bytes=101 WHERE project_id=%s AND snapshot_id='old'",(project,))


def test_invalid_summary_rolls_back_and_other_scope_is_rejected(env):
    project,connect,repo,retain,snapshot,policy=env
    snapshot('old',10);snapshot('new',1);policy()
    with connect() as c:
        c.execute('SET LOCAL session_replication_role=replica')
        c.execute("UPDATE storage.inventory_snapshots SET summary=jsonb_set(summary,'{categories}','[]') WHERE project_id=%s AND snapshot_id='old'",(project,))
    with pytest.raises(ValueError):
        retain.run(project_id=project)
    assert len(repo.inventory_facts(project_id=project,snapshot_id='old'))==1
    with pytest.raises(PermissionError):
        retain.run(project_id='someone-else')
    with pytest.raises(PermissionError):
        retain('other-org/'+project+'/global')


def test_policy_day_validation():
    from hc_data_platform.storage.models import CreateLifecyclePolicy
    base=dict(name='stats',business_category='PENDING_ANNOTATION',object_role='REBUILDABLE_DERIVATIVE',minimum_age_days=0,priority=100)
    assert CreateLifecyclePolicy(action='CLEAN_INVENTORY_DETAILS',**base).minimum_age_days==0
    for action in ('CLEAN_INVENTORY_HISTORY','CLEAN_RECORDING_PREVIEWS'):
        with pytest.raises(ValueError):
            CreateLifecyclePolicy(action=action,**base)
