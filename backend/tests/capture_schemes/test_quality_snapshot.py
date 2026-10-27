import psycopg
from hc_data_platform.capture_schemes import schemes, plans
from hc_data_platform.capture_schemes.models import SchemeDraft, PrepareRecording, VersionReference
from hc_data_platform.capture_schemes.common import digest
from hc_data_platform.capture_schemes.quality import from_snapshot
from .test_contracts import draft
from .test_transactions import environment, associate, selection  # noqa: F401


def test_published_quality_changes_follow_task_plan_but_not_old_recording(environment):
    dsn, scope, tasks = environment
    snapshots = []
    with psycopg.connect(dsn) as db:
        for revision, limit in enumerate((1, 2)):
            command = draft(revision).model_dump(mode='json')
            command['quality_policy'] = {'joint_limits': {'left': [-limit, limit]}}
            saved = schemes.save(db, scope[0], 'quality-rules', SchemeDraft.model_validate(command), 'tester')
            schemes.record_validation(db, scope[0], 'quality-rules', saved['revision'], {
                'source_format': 'LEROBOT_V3', 'parser_version': 'lerobot-v3/1',
                'sample_sha256': 'a'*64, 'draft_sha256': digest(saved['draft']),
                'status':'PASSED', 'test_fixture':True}, 'tester')
            published = schemes.publish(db, scope[0], 'quality-rules', saved['revision'], 'tester')
            ref = VersionReference(**{k:published[k] for k in ('scheme_id','version','content_sha256')})
            associate(db, scope[0], 'robot-1', ref, revision=revision)
            plans.save_plan(db, scope, tasks[0], selection(ref, revision=revision), 'tester')
            snap = plans.prepare_recording(db, scope, tasks[0], 'robot-1', PrepareRecording(
                expected_plan_revision=revision+1, recording_id=f'quality-{revision}', source_format='LEROBOT_V3'), 'tester')
            snapshots.append(snap)
    first, second = [from_snapshot(s, {'/observation/state','/action'}, 15) for s in snapshots]
    assert first.joint_limits['left'] == (-1, 1)
    assert second.joint_limits['left'] == (-2, 2)
    assert first.profile_id != second.profile_id
    assert snapshots[0]['quality_policy']['joint_limits']['left'] == [-1, 1]
