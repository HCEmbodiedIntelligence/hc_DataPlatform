"""Collection-task vocabularies survive capture-format changes in PostgreSQL."""

import os
from uuid import uuid4

import psycopg
import pytest

from hc_data_platform.annotation import (
    AnnotationActor,
    AnnotationService,
    PostgresAnnotationRepository,
    TagSchemaDocument,
    TagSchemaTarget,
)
from hc_data_platform.annotation.automation import (
    AutomaticAnnotationRequest,
    AutomaticAnnotationTaskService,
    PostgresAutomaticAnnotationRepository,
)
from hc_data_platform.annotation.models import AnnotationTaskKind
from hc_data_platform.annotation.task_labels import collection_task_label_binding
from hc_data_platform.lerobot_imports import configuration


@pytest.fixture
def task_labels():
    dsn = os.environ.get("HC_ANNOTATION_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("isolated PostgreSQL DSN required")
    org = "task-labels-" + uuid4().hex
    project, region, dataset, task = "p", "global", "dataset_task_labels", uuid4().hex

    def connect(*, selected_org=org, selected_project=project, selected_region=region):
        db = psycopg.connect(dsn)
        db.execute(
            """SELECT set_config('app.organization_id',%s,false),
               set_config('app.project_id',%s,false),set_config('app.region_code',%s,false),
               set_config('app.subject_id','task-label-test',false)""",
            (selected_org, selected_project, selected_region),
        )
        return db

    with connect() as db:
        db.execute(
            "INSERT INTO registry.organization_projects VALUES(%s,%s,'Task label test')",
            (org, project),
        )
        db.execute(
            """INSERT INTO collection_tasks.collection_tasks
            (organization_id,project_id,collection_task_id,task_code,name,task_type,scenario,
             create_fingerprint,dataset_id,upload_region_code)
            VALUES(%s,%s,%s,lpad(nextval('collection_tasks.task_code_sequence')::text,8,'0'),
                   'Task label test','test','test',%s,%s,%s)""",
            (org, project, task, "a" * 64, dataset, region),
        )
    actor = AnnotationActor(
        actor_id="publisher",
        capabilities=frozenset({"data_schema.publish"}),
        project_ids=frozenset({project}),
    )
    service = AnnotationService(PostgresAnnotationRepository(connect))
    schema = service.create_tag_schema_version(
        project_id=project, schema_id="task-vocabulary-" + org, version=1,
        name="Task vocabulary", actor=actor,
        document=TagSchemaDocument(nodes=(
            {"tag_id": "pick", "code": "pick", "display_name": "抓取"},
            {"tag_id": "place", "code": "place", "display_name": "放置"},
        )),
        compatible_targets=(TagSchemaTarget(
            region_code=region, dataset_id=dataset, dataset_schema_snapshot_id="old-format"
        ),),
    )
    service.publish_tag_schema_version(
        project_id=project, schema_id=schema.schema_id, version=1, actor=actor
    )
    return connect, schema, org, project, region, dataset, task


def resolve(factory, snapshot="new-format", *, dataset="dataset_task_labels"):
    return PostgresAnnotationRepository(factory).resolve_published_schema(
        project_id="p", region_code="global", dataset_id=dataset,
        dataset_schema_snapshot_id=snapshot, task_kind=AnnotationTaskKind.TAGGING,
    )


def test_capture_format_change_uses_the_same_task_vocabulary(task_labels, monkeypatch):
    connect, schema, _, project, region, dataset, _ = task_labels
    assert resolve(connect, "old-format").schema_id == schema.schema_id
    assert resolve(connect, "capture-format-17-axis").schema_id == schema.schema_id
    assert resolve(connect, "capture-format-next-camera").schema_id == schema.schema_id
    monkeypatch.setattr(configuration, "connections", lambda: connect)
    config = configuration.processing_configuration(project, region, dataset)
    assert config.configured and config.schema_id == schema.schema_id
    assert config.schema_snapshot_id is None


def test_new_format_creates_one_annotation_task_and_replays_idempotently(task_labels):
    connect, schema, _, project, region, dataset, _ = task_labels
    service = AutomaticAnnotationTaskService(PostgresAutomaticAnnotationRepository(connect))
    request = AutomaticAnnotationRequest(
        project_id=project, region_code=region, dataset_id=dataset,
        rollout_id="recording:new-format", dataset_version=1, lance_version=1,
        dataset_schema_snapshot_id="capture-format-17-axis", base_step_count=100,
        source_workflow_id="task-label-workflow",
    )
    first = service.ensure_task(request)
    assert first.tag_schema_id == schema.schema_id
    assert first.status.value == "DRAFT"
    assert service.ensure_task(request).task_id == first.task_id
    with connect() as db:
        assert db.execute(
            "SELECT count(*) FROM annotation.annotation_tasks WHERE task_id=%s", (first.task_id,)
        ).fetchone()[0] == 1


@pytest.mark.parametrize("scope", [
    {"selected_org": "other-org"},
    {"selected_project": "other-project"},
    {"selected_region": "other-region"},
])
def test_task_vocabulary_never_crosses_scope(task_labels, scope):
    connect, *_ = task_labels
    assert resolve(lambda: connect(**scope)) is None


def test_task_rules_do_not_spill_to_other_datasets(task_labels):
    connect, *_ = task_labels
    assert resolve(connect, dataset="dataset_another_task_labels") is None


def test_standalone_dataset_keeps_explicit_format_matching(task_labels):
    connect, schema, org, project, region, _, _ = task_labels
    with connect() as db:
        db.execute(
            """INSERT INTO annotation.tag_schema_bindings
            (organization_id,project_id,region_code,dataset_id,dataset_schema_snapshot_id,
             task_kind,schema_id,schema_version,created_at)
            VALUES(%s,%s,%s,'standalone','old-format','TAGGING',%s,1,now())""",
            (org, project, region, schema.schema_id),
        )
    assert resolve(connect, "old-format", dataset="standalone").schema_id == schema.schema_id
    assert resolve(connect, "new-format", dataset="standalone") is None


def test_new_task_defaults_are_owned_by_the_task(task_labels):
    from hc_data_platform.annotation.ingest_queries import ensure_dataset_tag_schema_binding

    connect, _, org, project, region, _, _ = task_labels
    with connect() as db, db.cursor() as cursor:
        cursor.execute(
            """INSERT INTO collection_tasks.collection_tasks
            (organization_id,project_id,collection_task_id,task_code,name,task_type,scenario,
             create_fingerprint,dataset_id,upload_region_code)
            VALUES(%s,%s,%s,lpad(nextval('collection_tasks.task_code_sequence')::text,8,'0'),
                   'New task','test','test',%s,'dataset_new_task_labels',%s)""",
            (org, project, uuid4().hex, "b" * 64, region),
        )
        ensure_dataset_tag_schema_binding(
            cursor, organization_id=org, project_id=project,
            storage_region_code=region, dataset_id="dataset_new_task_labels",
        )
        assert collection_task_label_binding(
            cursor, project_id=project, region_code=region, dataset_id="dataset_new_task_labels"
        ) is not None
    assert resolve(connect, "future-format", dataset="dataset_new_task_labels") is not None
