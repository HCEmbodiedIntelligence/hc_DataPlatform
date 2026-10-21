"""Read-only by default. Apply only an exact reviewed inventory and explicit IDs.

No Episode/QC/Raw processing, retries or default publication are performed here.
"""

import argparse
import json
import os
from pathlib import Path

import psycopg

from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.security.audit import canonical_hash

from .models import Draft
from .service import execute, save


def inventory(db):
    rows = execute(
        db,
        """SELECT organization_id,robot_id,project_id,region_code,configuration,updated_at
        FROM ingest.mcap_robot_profiles ORDER BY organization_id,robot_id,project_id,region_code""",
    ).fetchall()
    groups = {}
    for org, robot, project, region, config, updated in rows:
        groups.setdefault((org, robot), {}).setdefault(canonical_hash(config), []).append(
            {
                "project_id": project,
                "region_code": region,
                "configuration": config,
                "updated_at": updated.isoformat(),
            }
        )
    candidates = []
    for (org, robot), variants in groups.items():
        history = execute(
            db,
            (
                """SELECT
                project_id,region_code,raw_source_id,processing_id,status,configuration,recording_id
                FROM ingest.mcap_processing_files WHERE organization_id=%s AND robot_id=%s
                ORDER BY project_id,region_code,raw_source_id,created_at,processing_id"""
            ),
            (org, robot),
        ).fetchall()
        snapshots = [
            dict(
                zip(
                    (
                        "project_id",
                        "region_code",
                        "raw_source_id",
                        "processing_id",
                        "status",
                        "configuration",
                        "recording_id",
                    ),
                    r,
                    strict=True,
                )
            )
            for r in history
        ]
        for digest, sources in variants.items():
            candidates.append(
                {
                    "candidate_id": canonical_hash([org, robot, digest]),
                    "organization_id": org,
                    "robot_id": robot,
                    "scheme_id": "legacy-" + digest[:24],
                    "configuration": sources[0]["configuration"],
                    "sources": sources,
                    "conflict": len(variants) > 1,
                    "state": "NEEDS_VALIDATION",
                    "processing_snapshots": snapshots,
                }
            )
    links = []
    for org, project, region, raw, dataset, episode, digest in execute(
        db,
        (
            """SELECT
            r.organization_id,r.project_id,r.region_code,r.raw_source_id,r.dataset_id,e.episode_id,canonical.content_hash
            FROM ingest.raw_sources r JOIN ingest.raw_sources canonical ON
            (canonical.organization_id,canonical.project_id,canonical.region_code,canonical.dataset_id,canonical.robot_id,canonical.collection_task_id)=
            (r.organization_id,r.project_id,r.region_code,r.dataset_id,r.robot_id,r.collection_task_id)
            JOIN ingest.raw_source_episodes e ON
            (e.organization_id,e.project_id,e.region_code,e.raw_source_id)=
            (canonical.organization_id,canonical.project_id,canonical.region_code,canonical.raw_source_id)
            WHERE r.raw_source_id='raw-4909658c01345e0ab025fb0c0ec6d433' AND
            e.episode_id='rie-54102ae0b172579eafa375a28550c2b1' AND e.status='READY'"""
        ),
    ).fetchall():
        link = dict(
            organization_id=org,
            project_id=project,
            region_code=region,
            raw_source_id=raw,
            dataset_id=dataset,
            canonical_episode_id=episode,
            canonical_raw_content_sha256=digest,
            evidence="User-confirmed same recording; original MCAP contains no image messages",
        )
        links.append({**link, "candidate_id": canonical_hash(link)})
    result = {
        "schema_version": "robot-config-migration/v1",
        "candidates": candidates,
        "source_links": links,
        "protected_recovery": [
            {
                "raw_upload_id": "riu-445ff5ed97e8529db900a3af16cd0ff5",
                "raw_source_id": "raw-4909658c01345e0ab025fb0c0ec6d433",
                "canonical_episode_id": "rie-54102ae0b172579eafa375a28550c2b1",
                ("reason"): (
                    "confirmed same recording; original MCAP has no image "
                    "messages; retain successful LeRobot result"
                ),
            }
        ],
    }
    return {**result, "inventory_sha256": canonical_hash(result)}


def apply(db, report, approval, actor):
    if approval.get("inventory_sha256") != report["inventory_sha256"]:
        raise ValueError("inventory changed; review a new dry-run before applying")
    approved = set(approval.get("candidate_ids", []))
    candidates = {c["candidate_id"]: c for c in report["candidates"]}
    if not approved <= candidates.keys():
        raise ValueError("approval contains unknown candidates")
    results = []
    for key in sorted(approved):
        c = candidates[key]
        org, robot, scheme = c["organization_id"], c["robot_id"], c["scheme_id"]
        old = execute(
            db,
            """SELECT provenance FROM ingest.robot_processing_schemes
            WHERE organization_id=%s AND robot_id=%s AND scheme_id=%s""",
            (org, robot, scheme),
        ).fetchone()
        if old:
            if old[0].get("migration_candidate_id") != key:
                raise ValueError("migration destination exists with different provenance")
            results.append({"candidate_id": key, "result": "ALREADY_APPLIED"})
            continue
        save(
            db,
            org,
            robot,
            scheme,
            Draft(
                expected_revision=0,
                name="历史配置 " + scheme[-8:],
                software="legacy-unknown",
                configuration=c["configuration"],
            ),
            actor,
            provenance={
                "migration_candidate_id": key,
                "conflict": c["conflict"],
                "sources": c["sources"],
                "inventory_sha256": report["inventory_sha256"],
            },
        )
        results.append({"candidate_id": key, "result": "DRAFT_CREATED"})
    return results


def apply_links(db, report, approval, actor):
    from hc_data_platform.lerobot_imports.mcap_processing import lock_source

    if approval.get("inventory_sha256") != report["inventory_sha256"]:
        raise ValueError("inventory changed; review a new dry-run")
    selected = set(approval.get("source_link_ids", []))
    candidates = {c["candidate_id"]: c for c in report["source_links"]}
    if not selected <= candidates.keys():
        raise ValueError("approval contains unknown source links")
    result = []
    for ident in sorted(selected):
        link = candidates[ident]
        scope = tuple(link[k] for k in ("organization_id", "project_id", "region_code"))
        lock_source(db, scope, link["raw_source_id"])
        if execute(
            db,
            """SELECT 1 FROM ingest.mcap_processing_files WHERE organization_id=%s AND project_id=%s
            AND region_code=%s AND raw_source_id=%s AND status IN ('RUNNING','PENDING','READY')""",
            (*scope, link["raw_source_id"]),
        ).fetchone():
            raise ValueError("source has active or completed preparation; review before linking")
        old = execute(
            db,
            (
                """SELECT canonical_episode_id FROM ingest.recording_source_links WHERE
                organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s"""
            ),
            (*scope, link["raw_source_id"]),
        ).fetchone()
        if old and old[0] != link["canonical_episode_id"]:
            raise ValueError("source already linked to a different Episode")
        execute(
            db,
            (
                """INSERT INTO ingest.recording_source_links
                VALUES(%s,%s,%s,%s,%s,%s::jsonb,now()) ON CONFLICT DO NOTHING"""
            ),
            (
                *scope,
                link["raw_source_id"],
                link["canonical_episode_id"],
                json.dumps(
                    {**link, "actor": actor, "inventory_sha256": report["inventory_sha256"]}
                ),
            ),
        )
        result.append(
            {"candidate_id": ident, "result": "ALREADY_APPLIED" if old else "LINK_CREATED"}
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--approval", help="Explicit reviewed candidate IDs and inventory digest; omit for dry-run"
    )
    parser.add_argument("--actor", default="migration-cli")
    args = parser.parse_args()
    dsn = os.environ.get("HC_POSTGRES_DSN")
    if not dsn:
        parser.error("HC_POSTGRES_DSN must explicitly select the administrative migration database")
    # This CLI deliberately inventories legacy scopes across organizations using
    # the operator's database role. HTTP factories require a request context and
    # would either fail here or accidentally restrict a multi-tenant inventory.
    with psycopg.connect(normalize_postgres_dsn(dsn)) as db:
        # Administrative CLI uses its explicit configured DB role; never guesses tenant scope.
        execute(db, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        if not args.approval:
            execute(db, "SET TRANSACTION READ ONLY")
        report = inventory(db)
        if args.approval:
            approval = json.loads(Path(args.approval).read_text())
            report["applied"] = apply(db, report, approval, args.actor)
            report["linked"] = apply_links(db, report, approval, args.actor)
            db.commit()
        Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(
            json.dumps(
                {
                    "output": args.output,
                    "candidates": len(report["candidates"]),
                    "conflicts": sum(c["conflict"] for c in report["candidates"]),
                    "applied": len(report.get("applied", [])),
                }
            )
        )


if __name__ == "__main__":
    main()
