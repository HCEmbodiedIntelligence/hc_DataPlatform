"""Print a read-only, per-account comparison of project grants before and after role migration."""

import argparse
import json
import os

import psycopg

from hc_data_platform.security.capabilities import expand_project_roles

# Frozen from the pre-migration upload policy at 3fc8a519.
LEGACY_UPLOAD_CAPABILITIES = frozenset(
    {
        "annotation.edit",
        "annotation.read",
        "annotation.review",
        "annotation.save",
        "annotation.submit",
        "annotation_draft.edit",
        "annotation_set.read",
        "annotation_task.claim",
        "annotation_task.create",
        "annotation_task.read",
        "cleaning.create",
        "cleaning.edit",
        "cleaning.preview",
        "cleaning.read",
        "cleaning.submit",
        "dashboard.read",
        "data_schema.create",
        "data_schema.import",
        "data_schema.publish",
        "data_schema.read",
        "data_schema.validate",
        "dataset.create",
        "dataset.read",
        "dataset_version.download_manifest",
        "dataset_version.publish",
        "dataset_version.read",
        "dataset_version.review",
        "episode.read",
        "export.create",
        "export.download",
        "export.read",
        "ingest.import",
        "ingest_source.read",
        "manual_issue.create",
        "manual_issue.read",
        "manual_issue.resolve",
        "manual_issue.triage",
        "robot.read",
        "robot_model.asset.download",
        "robot_model.read",
        "robot_model_binding.read",
        "upload.manage",
        "upload.read",
    }
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn-env", default="HC_POSTGRES_DSN")
    parser.add_argument("--organization")
    parser.add_argument("--project")
    args = parser.parse_args()
    dsn = os.environ.get(args.dsn_env)
    if not dsn:
        parser.error(f"Set {args.dsn_env} to the database connection string")
    with psycopg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://", 1)) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = db.execute(
            """
            SELECT a.principal_id::text, a.display_username, m.organization_id, m.project_id,
                   EXISTS (SELECT FROM access_control.platform_capability_grants p
                     WHERE p.principal_id=a.principal_id AND p.active AND p.capability_key='platform.admin'),
                   COALESCE(array_agg(DISTINCT g.capability_key) FILTER (WHERE g.capability_key IS NOT NULL),
                            ARRAY[]::text[])
            FROM access_control.memberships m JOIN access_control.accounts a USING (principal_id)
            LEFT JOIN access_control.capability_grants g ON
                (g.principal_id,g.organization_id,g.project_id)=(m.principal_id,m.organization_id,m.project_id)
                AND g.active AND COALESCE((to_jsonb(g)->>'expires_at')::timestamptz,'infinity')>now()
            WHERE m.active AND a.status='ACTIVE' AND a.deleted_at IS NULL
              AND (%s::text IS NULL OR m.organization_id=%s) AND (%s::text IS NULL OR m.project_id=%s)
            GROUP BY a.principal_id,m.organization_id,m.project_id
            ORDER BY m.organization_id,m.project_id,a.principal_id
        """,
            (args.organization, args.organization, args.project, args.project),
        )
        for principal, username, org, project, admin, keys in rows:
            explicit = set(keys)
            old = explicit | LEGACY_UPLOAD_CAPABILITIES if "upload.manage" in explicit else explicit
            new = expand_project_roles(keys)
            print(
                json.dumps(
                    dict(
                        principal_id=principal,
                        username=username,
                        organization_id=org,
                        project_id=project,
                        platform_admin=admin,
                        explicit_grants=sorted(keys),
                        project_grants_added=sorted(new - old) if not admin else [],
                        project_grants_removed=sorted(old - new) if not admin else [],
                    ),
                    ensure_ascii=False,
                )
            )


if __name__ == "__main__":
    main()
