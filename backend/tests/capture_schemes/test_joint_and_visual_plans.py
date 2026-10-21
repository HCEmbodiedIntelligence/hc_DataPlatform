"""One device selects actual joint and camera sample contracts for separate tasks."""

import psycopg

from hc_data_platform.capture_schemes import associations, plans, schemes
from hc_data_platform.capture_schemes.models import (
    AssociateScheme,
    DeviceMapping,
    PrepareRecording,
    SchemeDraft,
    VersionReference,
)

from .processing_fixture import make_source
from .test_sample_validation import lerobot_document, stored
from .test_transactions import environment, selection  # noqa: F401


def test_one_instance_uses_distinct_joint_and_visual_task_contracts(environment, tmp_path):  # noqa: F811
    dsn, scope, tasks = environment
    org, robot = scope[0], "robot-1"
    snapshots = []
    with psycopg.connect(dsn) as db:
        for index, variant in enumerate((0, 2)):
            root = tmp_path / str(variant)
            info = make_source(root, variant, count=3)
            validator, sample, _ = stored(root, "LEROBOT_V3", organization_id=org, robot_id=robot)
            name = "joints" if index == 0 else "vision"
            draft = SchemeDraft.model_validate(
                {
                    "expected_revision": 0,
                    "name": name,
                    "software": "fixture/v1",
                    **lerobot_document(info),
                }
            )
            saved = schemes.save(db, org, name, draft, "tester")
            evidence = validator.validate(org, saved["draft"], sample)
            assert evidence["status"] == "PASSED"
            schemes.record_validation(db, org, name, saved["revision"], evidence, "tester")
            version = schemes.publish(db, org, name, saved["revision"], "tester")
            ref = VersionReference.model_validate(
                {key: version[key] for key in ("scheme_id", "version", "content_sha256")}
            )
            links = associations.associate(
                db,
                org,
                robot,
                AssociateScheme(
                    expected_revision=0,
                    scheme=ref,
                    make_default=index == 0,
                ),
                "tester",
            )
            mapping = next(link["mapping"] for link in links if link["scheme_id"] == name)
            evidence = validator.validate(org, version, sample, DeviceMapping(**mapping["mapping"]))
            assert evidence["status"] == "PASSED"
            evidence["mapping_sha256"] = mapping["content_sha256"]
            associations.record_validation(db, org, robot, name, 1, evidence, "tester")
            plans.save_plan(db, scope, tasks[index], selection(ref, robot), "tester")
            snapshots.append(
                plans.prepare_recording(
                    db,
                    scope,
                    tasks[index],
                    robot,
                    PrepareRecording(
                        expected_plan_revision=1, recording_id=name, source_format="LEROBOT_V3"
                    ),
                    "tester",
                )
            )
        assert len(associations.list_links(db, org, robot)) == 2
        joints, vision = snapshots
        assert joints["dataset_id"] != vision["dataset_id"]
        assert joints["output_contract_sha256"] != vision["output_contract_sha256"]
        assert joints["output"]["features"]["observation.state"]["shape"] == [29]
        assert vision["output"]["features"]["observation.state"]["shape"] == [21]
        assert not any(f["role"] == "rgb" for f in joints["output"]["features"].values())
        assert any(f["role"] == "rgb" for f in vision["output"]["features"].values())
        assert joints["output"]["frequency_hz"] == vision["output"]["frequency_hz"] == 15
