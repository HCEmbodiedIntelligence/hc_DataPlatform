import copy

import pytest

from hc_data_platform.lerobot_imports.capabilities import (
    NATIVE_PROCESSING_REVISION,
    LeRobotCapabilityRequest,
    detect_capabilities,
)
from hc_data_platform.lerobot_imports.models import CreateLeRobotImportV1
from hc_data_platform.lerobot_imports.service import _canonical_manifest
from tests.lerobot_imports.test_lerobot_web_upload import _info, _source_files


def test_float64_uses_the_same_native_processing_profile_and_explains_unsupported_input():
    info = _info()
    info.update(
        robot_type="generic",
        features={
            key: dict(dtype="float64", shape=[2], names=["x", "y"])
            for key in ("observation.state", "action")
        },
    )
    result = detect_capabilities(info)
    assert result.storage_supported and result.processing_supported
    assert result.profile_id == "native-generic-v3"
    assert result.output_storage_format == "lerobot_v3"
    assert result.capability_revision == NATIVE_PROCESSING_REVISION
    bad = copy.deepcopy(info)
    bad["features"]["action"]["names"] = ["x", "x"]
    unsupported = detect_capabilities(bad)
    assert unsupported.storage_supported and not unsupported.processing_supported
    assert unsupported.reasons[0].stage == "PROCESSING"
    assert unsupported.reasons[0].code == "LEROBOT_FEATURE_SCHEMA"
    assert unsupported.info_sha256 != result.info_sha256
    bad["codebase_version"] = "v99"
    assert detect_capabilities(bad).reasons[0].stage == "STORAGE"


def test_revision_validation_and_old_upload_manifest_identity_remain_stable():
    old = CreateLeRobotImportV1(
        dataset_id="dataset-a",
        collection_task_id="task-a",
        robot_id="robot-a",
        processing_mode="PROCESS",
        info=_info(),
        files=_source_files(),
    )
    historical = old.model_dump(mode="json", exclude={"processing_capability_revision"})
    assert _canonical_manifest(old) == {
        **historical,
        "files": sorted(historical["files"], key=lambda x: x["path"]),
    }
    with pytest.raises(ValueError, match="CAPABILITY_REVISION_UNSUPPORTED"):
        CreateLeRobotImportV1.model_validate(
            {**historical, "processing_capability_revision": "future/9"}
        )
    for value in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            LeRobotCapabilityRequest(info={"fps": value})
