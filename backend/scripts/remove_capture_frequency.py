"""Remove frequency requirements from existing schemes and their current references.

Run in the API environment with --organization and --backup. By default the full
transaction is rehearsed then rolled back; --apply commits it. Published history
and recording snapshots are immutable. Existing structural validation evidence
is explicitly carried forward for this relaxation, never claimed as a new decode.
"""

import argparse
import copy
import json
import os
import re
from datetime import datetime
from pathlib import Path

import psycopg

from hc_data_platform.capture_schemes import associations, plans, schemes
from hc_data_platform.capture_schemes.common import audit, digest, encoded, now
from hc_data_platform.capture_schemes.models import (
    AssociateScheme,
    CaptureDefinition,
    SchemeDraft,
    VersionReference,
)
from hc_data_platform.capture_schemes.schema import structure_contract

ACTOR = "operator:remove-capture-frequency-20261022"
KIND = "REMOVE_FREQUENCY_REQUIREMENT"
TABLES = (
    "ingest.capture_schemes",
    "ingest.capture_scheme_versions",
    "ingest.capture_scheme_validations",
    "ingest.capture_scheme_links",
    "ingest.capture_mapping_versions",
    "ingest.capture_mapping_validations",
    "collection_tasks.capture_plans",
    "collection_tasks.capture_plan_heads",
    "episode_store.dataset_capture_contracts",
    "ingest.capture_recording_snapshots",
    "ingest.raw_capture_snapshots",
)


def definition(document):
    return {
        key: copy.deepcopy(document[key])
        for key in CaptureDefinition.model_fields
        if key in document
    }


def without_frequency(document):
    result = definition(document)
    result["output"]["frequency_hz"] = None
    for feature in result["output"]["features"].values():
        feature.get("media", {}).pop("video.fps", None)
    for adapter in result["adapters"]:
        if "configuration" in adapter:
            adapter["configuration"]["fps"] = None
            if adapter["configuration"].get("semantic_output") is not None:
                adapter["configuration"]["semantic_output"] = copy.deepcopy(result["output"])
    result["name"] = re.sub(r"\d+(?:\.\d+)?\s*Hz[，、, ]*", "", result["name"], flags=re.I)
    result["description"] = re.sub(
        r"(?:保持)?\d+(?:\.\d+)?\s*Hz[，、, ]*",
        "",
        result.get("description", ""),
        flags=re.I,
    )
    assert structure_contract(result["output"]) == structure_contract(document["output"])
    CaptureDefinition.model_validate(result)
    return result


def carry_evidence(evidence, **changes):
    result = copy.deepcopy(evidence)
    result["constraint_relaxation"] = {
        "kind": KIND,
        "source_validation_id": evidence["validation_id"],
        "source_validation_sha256": digest(evidence),
        "carried_at": now(),
        "reason": (
            "Only rate requirements and their labels were removed; "
            "fields, units and sources are unchanged."
        ),
    }
    result.pop("validation_id", None)
    result.update(changes)
    return result


def snapshot(db, org):
    return {
        table: db.execute(
            f"SELECT to_jsonb(t) FROM {table} t WHERE organization_id=%s", (org,)
        ).fetchall()
        for table in TABLES
    }


def migrate(db, org, backup):
    db.execute("SET LOCAL lock_timeout='5s'")
    # Serialize the maintenance transaction with normal writes, including freezes.
    for table in TABLES:
        db.execute(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE")
    before = snapshot(db, org)
    backup.write_text(json.dumps(before, ensure_ascii=False, indent=2, default=str))
    originals = [row[0] for row in before["ingest.capture_schemes"] if not row[0]["archived"]]
    new_versions = {}
    report = {"schemes": [], "mappings": [], "tasks": []}
    for current in originals:
        scheme = current["scheme_id"]
        if not current["latest_version"]:
            cleaned = without_frequency(current["draft"])
            if cleaned != current["draft"]:
                schemes.save(
                    db,
                    org,
                    scheme,
                    SchemeDraft(**cleaned, expected_revision=current["revision"]),
                    ACTOR,
                )
            continue
        old = schemes.published_version(db, org, scheme, current["latest_version"])
        cleaned = without_frequency(old)
        if cleaned == definition(old) and without_frequency(current["draft"]) == current["draft"]:
            continue
        # Do not silently advance a task to an unrelated structural version.
        heads = [
            r[0]
            for r in before["collection_tasks.capture_plans"]
            if any(
                h[0]["project_id"] == r[0]["project_id"]
                and h[0]["collection_task_id"] == r[0]["collection_task_id"]
                and h[0]["revision"] == r[0]["revision"]
                for h in before["collection_tasks.capture_plan_heads"]
            )
            and r[0]["scheme_id"] == scheme
        ]
        assert all(row["scheme_version"] == old["version"] for row in heads), (
            "Historical task version needs a separate migration"
        )
        saved = schemes.save(
            db, org, scheme, SchemeDraft(**cleaned, expected_revision=current["revision"]), ACTOR
        )
        for evidence in old["validations"].values():
            if evidence["status"] == "PASSED":
                schemes.record_validation(
                    db,
                    org,
                    scheme,
                    saved["revision"],
                    carry_evidence(evidence, draft_sha256=digest(saved["draft"])),
                    ACTOR,
                )
        published = schemes.publish(db, org, scheme, saved["revision"], ACTOR)
        new_versions[scheme] = published
        report["schemes"].append(
            {"scheme_id": scheme, "old_version": old["version"], "version": published["version"]}
        )
        # Preserve any unpublished edits independently of the published definition.
        draft = without_frequency(current["draft"])
        normalized = SchemeDraft(**draft, expected_revision=saved["revision"]).model_dump(
            mode="json", exclude={"expected_revision"}
        )
        if normalized != saved["draft"]:
            latest = schemes.save(
                db, org, scheme, SchemeDraft(**draft, expected_revision=saved["revision"]), ACTOR
            )
            for row in before["ingest.capture_scheme_validations"]:
                evidence = row[0]
                if evidence["scheme_id"] == scheme and evidence["revision"] == current["revision"]:
                    schemes.record_validation(
                        db,
                        org,
                        scheme,
                        latest["revision"],
                        carry_evidence(evidence["document"], draft_sha256=digest(latest["draft"])),
                        ACTOR,
                    )
        audit(
            db,
            org,
            scheme,
            ACTOR,
            KIND,
            {"old_version": old["version"], "new_version": published["version"]},
        )

    new_mappings = {}
    for item in before["ingest.capture_scheme_links"]:
        link = item[0]
        scheme, robot = link["scheme_id"], link["robot_id"]
        if scheme not in new_versions or not link["active"]:
            continue
        old_mapping = associations.mapping_version(db, org, robot, scheme, link["mapping_version"])
        assert old_mapping["scheme"]["version"] == link["scheme_version"]
        published = new_versions[scheme]
        assert link["scheme_version"] + 1 == published["version"]
        reference = VersionReference.model_validate(
            {k: published[k] for k in ("scheme_id", "version", "content_sha256")}
        )
        values = associations.associate(
            db,
            org,
            robot,
            AssociateScheme(
                expected_revision=link["revision"],
                scheme=reference,
                mapping=old_mapping["mapping"],
                make_default=link["is_default"],
            ),
            ACTOR,
        )
        new = next(v for v in values if v["scheme_id"] == scheme)["mapping"]
        assert new["mapping"] == old_mapping["mapping"]
        for row in before["ingest.capture_mapping_validations"]:
            evidence = row[0]
            if (evidence["robot_id"], evidence["scheme_id"], evidence["mapping_version"]) != (
                robot,
                scheme,
                link["mapping_version"],
            ):
                continue
            if evidence["document"]["status"] != "PASSED":
                continue
            associations.record_validation(
                db,
                org,
                robot,
                scheme,
                new["mapping_version"],
                carry_evidence(
                    evidence["document"],
                    mapping_sha256=new["content_sha256"],
                    scheme_sha256=published["content_sha256"],
                ),
                ACTOR,
            )
        associations.require_mapping(
            db, org, robot, published, new["mapping_version"], new_use=True
        )
        new_mappings[(robot, scheme, link["mapping_version"])] = new
        report["mappings"].append(
            {"robot_id": robot, "scheme_id": scheme, "mapping_version": new["mapping_version"]}
        )

    for row in before["collection_tasks.capture_plan_heads"]:
        head = row[0]
        old = next(
            item[0]["document"]
            for item in before["collection_tasks.capture_plans"]
            if all(
                item[0][key] == head[key]
                for key in ("organization_id", "project_id", "collection_task_id", "revision")
            )
        )
        scheme = old["scheme"]["scheme_id"]
        if scheme not in new_versions:
            continue
        published = new_versions[scheme]
        new = copy.deepcopy(old)
        new.pop("plan_sha256")
        new["revision"] += 1
        new["scheme"] = {k: published[k] for k in ("scheme_id", "version", "content_sha256")}
        new["selection"]["scheme"] = new["scheme"]
        for participant in new["participants"]:
            mapping = new_mappings[
                (participant["robot_id"], scheme, participant["mapping_version"])
            ]
            participant.update(
                mapping_version=mapping["mapping_version"], mapping_sha256=mapping["content_sha256"]
            )
        new["selection"]["participants"] = [
            {k: p[k] for k in ("robot_id", "mapping_version")} for p in new["participants"]
        ]
        new["output"] = published["output"]
        new["output_contract_sha256"] = published["output_contract_sha256"]
        new.update(created_at=now(), created_by=ACTOR)
        new["plan_sha256"] = digest(new)
        scope = (org, new["project_id"], new["region_code"])
        plans.check_dataset(db, scope, new["dataset_id"], new["collection_task_id"], new["output"])
        plans.store_plan(db, scope, new["collection_task_id"], new)
        expected_lock = datetime.fromisoformat(head["locked_at"]) if head["locked_at"] else None
        assert plans.get_plan(db, scope, new["collection_task_id"])["locked_at"] == expected_lock
        audit(
            db,
            org,
            scheme,
            ACTOR,
            KIND,
            {
                "task": new["collection_task_id"],
                "old_plan_sha256": old["plan_sha256"],
                "new_plan_sha256": new["plan_sha256"],
            },
        )
        report["tasks"].append({"task": new["collection_task_id"], "revision": new["revision"]})

    after = snapshot(db, org)
    for table in (
        "ingest.capture_recording_snapshots",
        "ingest.raw_capture_snapshots",
        "episode_store.dataset_capture_contracts",
    ):
        assert sorted(map(encoded, before[table])) == sorted(map(encoded, after[table]))
    # Every pre-existing immutable row must remain byte-for-byte identical.
    for table in TABLES:
        if table.endswith(("capture_schemes", "capture_scheme_links", "capture_plan_heads")):
            continue
        assert set(map(encoded, before[table])) <= set(map(encoded, after[table]))
    report["immutable_history_preserved"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization", required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.backup.exists():
        parser.error("Backup already exists; choose a fresh path")
    dsn = os.environ["HC_POSTGRES_DSN"].replace("postgresql+asyncpg:", "postgresql:")
    with psycopg.connect(dsn) as db:
        report = migrate(db, args.organization, args.backup)
        if not args.apply:
            db.rollback()
        report["committed"] = args.apply
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
