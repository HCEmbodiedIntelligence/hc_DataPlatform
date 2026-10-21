"""Disposable browser fixtures; refuses a non-test database name."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from hc_data_platform.continuous_recordings.mcap_native import inspect
from hc_data_platform.continuous_recordings.registration import PostgresRecordingRegistration
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.dataset_registry.models import DatasetPageRecord
from hc_data_platform.robot_ingest.recording_bridge import RecordingOutboxHandler
from hc_data_platform.robot_processing import service as configs
from hc_data_platform.robot_processing.models import Draft, Publish
from tests.continuous_recordings.test_mcap_native import synthetic_mcap
from tests.episode_store.test_commit_services import services
from tests.episode_store.test_native_mcap_pipeline_services import upload_robot_mcap
from tests.robot_ingest.test_raw_derivation_services import uploaded_raw
from tests.workflow.test_mcap_camera import MESSAGE_TYPE


def ensure_model(repository, org, project, version):
    """Complete only this synthetic fixture's published model input contract."""
    import hashlib
    from uuid import uuid4

    from hc_data_platform.registry.models import RobotModelAsset
    from hc_data_platform.robotics.scene_models import scene_hash

    assert os.environ["HC_G1_TEST_POSTGRES_DSN"].endswith("/hc_config")
    body = (
        b'<robot name="fixture"><link name="base"/><link '
        b'name="arm"/><joint name="joint" type="continuous"><parent '
        b'link="base"/><child link="arm"/><axis xyz="0 0 '
        b'1"/></joint></robot>'
    )
    with repository.connections() as db:
        if db.execute(
            "SELECT 1 FROM registry.robot_model_assets WHERE organization_id=%s AND version_id=%s",
            (org, version),
        ).fetchone():
            return
        asset = RobotModelAsset(
            asset_id=str(uuid4()),
            relative_path="fixture.urdf",
            role="URDF",
            media_type="application/xml",
            size_bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            created_at=datetime.now(timezone.utc),
        )
        db.execute(
            (
                """INSERT INTO
                registry.robot_model_assets(organization_id,project_id,asset_id,version_id,relative_path,role,media_type,size_bytes,sha256,object_key,created_at,content)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
            ),
            (
                org,
                project,
                asset.asset_id,
                version,
                asset.relative_path,
                asset.role.value,
                asset.media_type,
                asset.size_bytes,
                asset.sha256,
                "fixture/" + org + "/fixture.urdf",
                asset.created_at,
                body,
            ),
        )
        db.execute(
            (
                """UPDATE registry.robot_model_versions SET
                asset_manifest_hash=%s,validation_input_hash=%s WHERE organization_id=%s AND
                version_id=%s"""
            ),
            (scene_hash([asset.model_dump(mode="json")]), scene_hash([]), org, version),
        )


def main():
    assert os.environ["HC_G1_TEST_POSTGRES_DSN"].endswith("/hc_config")
    repository, storage = services.__wrapped__()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        fixture = uploaded_raw.__wrapped__((repository, storage), root)
        _, _, old, _, command, _ = next(fixture)
        try:
            org, project, region = (
                old.target.organization_id,
                old.target.project_id,
                old.target.region_code,
            )
            robot = command.robot_id
            model = "browser-model"
            version = "browser-model-v1"
            rgb = synthetic_mcap(root / "rgb.mcap")
            joints = synthetic_mcap(root / "joints.mcap", joint_only=True)
            cfg = dict(
                state_topic="/joint_states",
                action_topic="/joint_cmd",
                primary_camera="/color",
                joint_names=["joint"],
                joint_units=["rad"],
                depth_scale_m_per_unit=0.001,
                depth_unit_evidence="Synthetic PNG fixture: 1000 = 1 meter",
                cameras=[
                    dict(
                        camera_id="head",
                        topic="/color",
                        message_type=MESSAGE_TYPE,
                        kind="rgb",
                        required=True,
                    ),
                    dict(
                        camera_id="head_depth",
                        topic="/depth",
                        message_type=MESSAGE_TYPE,
                        kind="depth",
                        required=True,
                    ),
                ],
            )
            now = datetime.now(timezone.utc)
            record = DatasetPageRecord(
                scope=dict(organization_id=org, project_id=project, region_code=region),
                dataset_id=old.target.dataset_id,
                name="配置与纯关节验收",
                description="仅限隔离数据库的合成样本",
                availability="ACTIVE",
                owner=dict(id="test", display_name="Test"),
                created_at=now,
                updated_at=now,
                activity_at=now,
                etag='"v1"',
                metadata=dict(asset_state="READY", storage_class="STANDARD", robot_id=robot),
            )
            with repository.connections() as db:
                db.execute(
                    (
                        """INSERT INTO
                        dataset_registry.datasets(organization_id,project_id,region_code,dataset_id,name,description,availability,owner_id,owner_display_name,asset_state,storage_class,version,dataset_document,created_at,updated_at,activity_at)
                        VALUES(%s,%s,%s,%s,%s,%s,'ACTIVE','test','Test','READY','STANDARD',1,%s::jsonb,%s,%s,%s)"""
                    ),
                    (
                        org,
                        project,
                        region,
                        old.target.dataset_id,
                        record.name,
                        record.description,
                        record.model_dump_json(),
                        now,
                        now,
                        now,
                    ),
                )
                db.execute(
                    (
                        "INSERT INTO registry.organization_projects "
                        "VALUES(%s,'config-project-b','跨项目验收 B')"
                    ),
                    (org,),
                )
                db.execute(
                    (
                        """UPDATE registry.organization_projects SET display_name='配置验收 A' WHERE
                        organization_id=%s AND project_id=%s"""
                    ),
                    (org, project),
                )
                db.execute(
                    (
                        "UPDATE collection_tasks.collection_tasks SET name='无相机录制验收' "
                        "WHERE collection_task_id=%s"
                    ),
                    (command.collection_task_id,),
                )
                db.execute(
                    (
                        """INSERT INTO
                        registry.robot_models(organization_id,model_id,manufacturer,model_code,display_name)
                        VALUES(%s,%s,'Fixture','test','验收型号')"""
                    ),
                    (org, model),
                )
                db.execute(
                    (
                        """INSERT INTO
                        registry.robot_model_versions(organization_id,version_id,robot_model_id,version_label,lifecycle,asset_availability,publish_readiness,etag)
                        VALUES(%s,%s,%s,'v1','PUBLISHED','AVAILABLE','READY','v1')"""
                    ),
                    (org, version, model),
                )
                db.execute(
                    (
                        """UPDATE registry.robot_models SET current_published_version_id=%s WHERE
                        organization_id=%s AND model_id=%s"""
                    ),
                    (version, org, model),
                )
                db.execute(
                    (
                        """UPDATE robotics.robot_assets SET
                        display_name='关节采集验收机器人',robot_model_version_id=%s,binding_id=gen_random_uuid(),binding_valid_from=now(),binding_etag='v1'
                        WHERE organization_id=%s AND robot_id=%s"""
                    ),
                    (version, org, robot),
                )
                db.execute(
                    (
                        """INSERT INTO
                        robotics.project_robot_assignments(organization_id,project_id,region_code,robot_id,assigned_by)
                        VALUES(%s,%s,%s,%s,'fixture')"""
                    ),
                    (org, project, region, robot),
                )
                configs.save(
                    db,
                    org,
                    robot,
                    "ros",
                    Draft(
                        expected_revision=0, name="ROS MCAP", software="fixture", configuration=cfg
                    ),
                    "fixture",
                )
                configs.validate(
                    db,
                    org,
                    robot,
                    "ros",
                    1,
                    inspect(rgb),
                    {"source": "actual synthetic MCAP"},
                    "fixture",
                )
                configs.publish(db, org, robot, "ros", Publish(expected_revision=1), "fixture")
                template = dict(
                    template_id="fixture-joints-v1",
                    name="纯关节模板 v1",
                    software="fixture",
                    model_version_id=version,
                    configuration={
                        **cfg,
                        "collection_mode": "JOINT_ONLY",
                        "time_source": "STATE_LOG_TIME",
                        "primary_camera": None,
                        "cameras": [],
                        "depth_scale_m_per_unit": None,
                        "depth_unit_evidence": None,
                    },
                )
                db.execute(
                    "INSERT INTO ingest.robot_processing_templates VALUES(%s,%s,%s::jsonb,now())",
                    (org, template["template_id"], json.dumps(template)),
                )
                db.commit()
            ensure_model(repository, org, project, version)
            sources = {}
            handler = RecordingOutboxHandler(
                repository.connections, storage, registrations=PostgresRecordingRegistration()
            )
            for name, path in [("rgb", rgb), ("joints", joints)]:
                up = upload_robot_mcap(
                    repository,
                    storage,
                    old,
                    command,
                    path.read_bytes(),
                    configure=False,
                    asset_path=name + ".mcap",
                )
                with repository.connections() as db:
                    event = DomainEventEnvelope.model_validate(
                        db.execute(
                            (
                                "SELECT envelope FROM core.outbox_events WHERE "
                                "envelope->>'aggregate_id'=%s"
                            ),
                            (up.raw_source_id,),
                        ).fetchone()[0]
                    )
                try:
                    handler.adopt(event)
                except ValueError as exc:
                    assert name == "joints" and "MCAP_REQUIRED_RGB_MISSING" in str(exc), str(exc)
                sources[name] = dict(raw=up.raw_source_id, upload=up.upload_id, file=name + ".mcap")
            print(
                json.dumps(
                    dict(
                        organizationId=org,
                        projectId=project,
                        regionCode=region,
                        robotId=robot,
                        taskId=command.collection_task_id,
                        datasetId=old.target.dataset_id,
                        modelVersionId=version,
                        sources=sources,
                    ),
                    ensure_ascii=False,
                )
            )
        finally:
            fixture.close()


if __name__ == "__main__":
    main()
