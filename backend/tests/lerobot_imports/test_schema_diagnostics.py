from dataclasses import replace

from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot
from hc_data_platform.lerobot_imports.profiles import NativeProfile, quality_profile
from hc_data_platform.lerobot_imports.schema_diagnostics import schema_conflict_detail


def snapshot(fields, frequency=15, semantic_contract=None):
    return DatasetSchemaSnapshot.create(
        project_id="project",
        dataset_id="dataset",
        schema_snapshot_id="schema",
        frequency_hz=frequency,
        fields=fields,
        semantic_contract=semantic_contract,
    )


def test_conflict_explains_missing_added_and_frequency_without_changing_schema():
    existing = snapshot({"/camera/head/image": "json", "/humanoid/observation/state": "json"})
    new = snapshot({"/humanoid/observation/state": "json", "/humanoid/action": "json"}, 30)
    before = existing.model_dump_json()
    detail = schema_conflict_detail(new, existing)
    assert "缺少 observation.images.head（头部相机视频）" in detail
    assert "新增 action（关节动作命令）" in detail
    assert "已有 15Hz，本次 30Hz" in detail
    assert existing.model_dump_json() == before


def test_semantic_conflict_with_same_topics_is_explained():
    fields = {"/humanoid/observation/state": "json"}
    old = snapshot(
        fields, semantic_contract={"features": {"observation.state": {"units": ["rad"]}}}
    )
    new = snapshot(fields, semantic_contract={"features": {"observation.state": {"units": ["m"]}}})
    assert "字段语义不一致" in schema_conflict_detail(new, old)
    assert "observation.state（关节状态）" in schema_conflict_detail(new, old)


def test_generic_quality_profile_identity_distinguishes_layouts_and_preserves_legacy_g1():
    base = NativeProfile(
        "native-generic-v3", (), ("/state", "/action"), ("j",), ("j",), "/state", "/action"
    )
    assert (
        quality_profile(base, 15).profile_id
        != quality_profile(
            replace(base, topics=(*base.topics, "/camera/head/image")), 15
        ).profile_id
    )
    assert (
        quality_profile(base, 15).profile_id
        == quality_profile(replace(base, topics=tuple(reversed(base.topics))), 15).profile_id
    )
    legacy = quality_profile(replace(base, legacy_g1=True), 30)
    assert (legacy.profile_id, legacy.profile_version) == ("native-g1-v3-30hz-v1", 5)
