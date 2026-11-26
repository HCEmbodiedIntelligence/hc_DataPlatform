"""Exercise the library, scheme version and annotation write against one isolated database."""

from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from hc_data_platform.annotation import (
    AnnotationActor,
    AnnotationService,
    AnnotationTag,
    AnnotationValidationError,
    PostgresAnnotationRepository,
)
from hc_data_platform.capture_schemes import plans
from hc_data_platform.capture_schemes.components import CaptureServices
from hc_data_platform.capture_schemes.models import VersionReference

from .test_application_dependencies import Samples, application, identity
from .test_contracts import draft
from .test_transactions import environment as environment
from .test_transactions import publish, selection


def test_only_tags_from_the_tasks_selected_scheme_can_be_saved(environment):
    dsn, scope, tasks = environment
    org, project, region = scope
    services = CaptureServices(lambda: psycopg.connect(dsn), Samples("tags"), lambda _: True)
    root = f"/api/v1/organizations/{org}"
    library = root + "/annotation-tags"
    scheme = root + "/capture-schemes/picking"
    context = identity(scope, "robot.manage", "robot.read", "upload.read")
    with TestClient(application(context, services)) as client:
        for name in ("抓取", "放置", "抓取"):
            assert client.post(library, json={"name": name}).status_code == 201
        assert set(client.get(library).json()) == {"抓取", "放置"}
        command = draft().model_dump(mode="json")
        assert (
            client.put(scheme, json={**command, "annotation_tags": ["未注册"]}).status_code == 422
        )
        assert client.put(scheme, json={**command, "annotation_tags": ["抓取"]}).status_code == 200
        result = client.post(
            scheme + ":validate",
            json={
                "expected_revision": 1,
                "sample": {
                    "project_id": project,
                    "region_code": region,
                    "raw_source_id": "sample",
                    "source_format": "LEROBOT_V3",
                },
            },
        )
        assert result.status_code == 200, result.text
        published = client.post(scheme + ":publish", json={"expected_revision": 1}).json()
        assert published["annotation_tags"] == ["抓取"]
        # A later draft cannot alter the published task vocabulary.
        assert (
            client.put(
                scheme, json={**command, "expected_revision": 1, "annotation_tags": ["放置"]}
            ).status_code
            == 200
        )
    with TestClient(application(identity(scope, "robot.read"), services)) as reader:
        assert reader.post(library, json={"name": "越权"}).status_code == 403
    with TestClient(
        application(identity((org + "-other", project, region), "robot.read"), services)
    ) as other:
        assert other.get(library.replace(org, org + "-other")).json() == []

    def connect():
        db = psycopg.connect(dsn)
        db.execute(
            """SELECT set_config('app.organization_id',%s,false),
                      set_config('app.project_id',%s,false),set_config('app.region_code',%s,false),
                      set_config('app.request_id','tag-test',false),
                      set_config('app.subject_id','annotator',false)""",
            scope,
        )
        return db

    ref = VersionReference(**{k: published[k] for k in VersionReference.model_fields})
    with connect() as db:
        empty_ref = publish(db, org, "empty")
        plans.save_plan(db, scope, tasks[0], selection(ref), "tester")
        plans.save_plan(db, scope, tasks[1], selection(empty_ref), "tester")
    service = AnnotationService(PostgresAnnotationRepository(connect))
    actor = AnnotationActor(
        actor_id="annotator",
        project_ids={project},
        capabilities={
            "annotation_task.read",
            "annotation_task.claim",
            "annotation.save",
            "annotation.submit",
        },
    )
    for collection_task, expected in [(tasks[0], ("抓取",)), (tasks[1], ())]:
        task_id = str(uuid4())
        service.create_task(
            task_id=task_id,
            project_id=project,
            region_code=region,
            dataset_id="dataset_capture_" + collection_task,
            dataset_version=1,
            rollout_id=str(uuid4()),
            source_workflow_id=str(uuid4()),
            base_step_count=20,
        )
        task = service.claim(task_id, actor)
        assert service.get_history(task_id, actor).task.allowed_tags == expected
        tag = AnnotationTag(
            annotation_id="interval",
            tag_id="node",
            label="放置",
            path=("node",),
            start_step=0,
            end_step=10,
        )
        save_args = dict(expected_revision=0, if_match=task.etag, client_mutation_id="save")
        with pytest.raises(AnnotationValidationError):
            service.save_draft(task_id, actor, (), tags=[tag], **save_args)
        if expected:
            accepted = tag.model_copy(update={"label": "抓取"})
            saved = service.save_draft(task_id, actor, (), tags=[accepted], **save_args)
            assert saved.tags == (accepted,)
            with connect() as db:
                plans.save_plan(db, scope, tasks[0], selection(empty_ref, revision=1), "tester")
            current = service.read_task(task_id, actor)
            assert current.allowed_tags == ()
            with pytest.raises(AnnotationValidationError):
                service.submit(task_id, actor, expected_revision=1, if_match=current.etag)
            assert service.get_revision(task_id, 1, actor).tags == (accepted,)
