"""Actual isolated SQL inventory; no migration or real-recording acceptance claim."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hc_data_platform.annotation.models import ReviewDecision
from hc_data_platform.episode_store.migration_inventory import (
    InventoryLimit,
    capture_inventory,
    read_inventory,
    save_inventory,
)
from hc_data_platform.episode_store.models import digest
from hc_data_platform.episode_store.postgres import PackageConflict
from tests.episode_store.test_annotation_bindings_services import actor
from tests.episode_store.test_annotation_revision_files_services import files  # noqa: F401
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_tag_derivation_services import selected  # noqa: F401
from tests.episode_store.test_tag_projection_services import projected  # noqa: F401

pytestmark = [
    pytest.mark.integration,
    pytest.mark.parametrize(
        "prepared_bundle",
        [{"identity": {"dataset_id": "dataset_inventory", "episode_id": "episode_inventory"}}],
        indirect=True,
    ),
]


def test_read_only_inventory_preserves_fixed_annotation_and_review_bindings(
    projected,  # noqa: F811
    files,  # noqa: F811
    target,
    tmp_path,
):
    source, packages, _, _, _ = projected
    _, task, _, _ = files
    annotation = target[2]
    with scoped(source):
        submission = annotation.submit_for_review(
            task.task_id,
            actor(source.identity),
            expected_revision=1,
            if_match=annotation.get_task(task.task_id).etag,
            idempotency_key="inventory-submit",
        )
        annotation.review(
            task.task_id,
            actor(source.identity, "reviewer"),
            ReviewDecision.APPROVE,
            revision=1,
            submission_id=submission.submission_id,
            if_match=annotation.get_task(task.task_id).etag,
        )
        inventory = capture_inventory(packages.repository.connections, source.identity.dataset_id)
        assert (
            capture_inventory(packages.repository.connections, source.identity.dataset_id)
            == inventory
        )
        tables = inventory["inventory"]["tables"]
        assert len(tables["annotation.annotation_revisions"]) == 2
        assert (
            tables["annotation.annotation_submissions"][0]["document"]["submission_id"]
            == submission.submission_id
        )
        review = tables["annotation.annotation_reviews"][0]["document"]
        assert review["decision"] == "APPROVE" and review["revision"] == 1
        revised = next(
            r["document"]
            for r in tables["annotation.annotation_revisions"]
            if r["document"]["revision"] == 1
        )
        assert revised["content_hash"] == submission.revision_content_hash
        assert revised["data_ref"] == review["data_ref"]
        assert revised["tags"][1]["parent_annotation_id"] == "parent"
        assert revised["tags"][1]["relations"][0]["target"]["object_id"] == "cup"
        assert not inventory["inventory"]["dry_run"]["visible_pointers_changed"]
        assert not inventory["inventory"]["dry_run"]["physical_objects_verified"]
        with pytest.raises(InventoryLimit):
            capture_inventory(
                packages.repository.connections, source.identity.dataset_id, max_rows=1
            )

    root = Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", tmp_path)) / "migration-inventory"
    output = root / (inventory["inventory_sha256"] + ".json")
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: save_inventory(output, inventory), range(3)))
    assert read_inventory(output) == inventory
    assert output.stat().st_mode & 0o777 == 0o600
    changed = {**inventory, "inventory_sha256": "0" * 64}
    with pytest.raises(PackageConflict):
        save_inventory(output, changed)
    assert read_inventory(output) == inventory
    other_snapshot = {**inventory["inventory"], "tables": {}}
    with pytest.raises(PackageConflict, match="different snapshot"):
        save_inventory(
            output, {"inventory_sha256": digest(other_snapshot), "inventory": other_snapshot}
        )
    assert read_inventory(output) == inventory
    damaged = tmp_path / "damaged.json"
    damaged.write_text(json.dumps(changed))
    with pytest.raises(PackageConflict):
        read_inventory(damaged)
    assert not list(root.glob(".inventory-*"))


@pytest.mark.parametrize("field", ["organization_id", "project_id", "region_code"])
def test_inventory_never_falls_back_to_a_foreign_scope(projected, field):  # noqa: F811
    source, packages, _, _, _ = projected
    foreign = source.model_copy(
        update={"identity": source.identity.model_copy(update={field: "foreign"})}
    )
    with scoped(foreign), pytest.raises(KeyError):
        capture_inventory(packages.repository.connections, source.identity.dataset_id)
