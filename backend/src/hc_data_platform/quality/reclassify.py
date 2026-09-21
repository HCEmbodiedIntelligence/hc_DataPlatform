"""Preview/apply the approved risk policy to one scope's persisted QC evidence."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import Any

from hc_data_platform.core.config import get_settings
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory

from .models import CURRENT_QUALITY_ENGINE_VERSION, QcReportV1, QualityProfileV1
from .policy import LEGACY_QUALITY_ENGINE_VERSION, reclassify_report
from .postgres import PostgresQualityRepository


def reclassify_scope(
    connect: Callable[[], Any],
    *,
    organization_id: str,
    project_id: str,
    region_code: str,
    apply: bool = False,
) -> dict[str, int | bool]:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT profile_json FROM quality_profiles
               WHERE organization_id=%s AND project_id=%s
               ORDER BY profile_id, profile_version""",
            (organization_id, project_id),
        )
        profiles = [QualityProfileV1.model_validate(row[0]) for row in cursor.fetchall()]
        cursor.execute(
            """SELECT report.report_json
               FROM quality_rollout_summaries summary
               JOIN qc_reports report
                 ON report.organization_id=summary.organization_id
                AND report.project_id=summary.project_id
                AND report.region_code=summary.region_code
                AND report.report_sha256=summary.report_sha256
               WHERE summary.organization_id=%s AND summary.project_id=%s
                 AND summary.region_code=%s AND report.engine_version=%s
               ORDER BY summary.rollout_id""",
            (organization_id, project_id, region_code, LEGACY_QUALITY_ENGINE_VERSION),
        )
        reports = [QcReportV1.model_validate(row[0]) for row in cursor.fetchall()]
    revised_profiles: dict[tuple[str, int], QualityProfileV1] = {}
    # Upgrade every latest profile in this scope, including ones without reports yet.
    latest = {profile.profile_id: profile for profile in profiles}
    old_profiles = [
        profile
        for profile in latest.values()
        if profile.engine_version == LEGACY_QUALITY_ENGINE_VERSION
    ]
    for report in reports:
        previous = next(
            profile
            for profile in profiles
            if (profile.profile_id, profile.profile_version)
            == (report.profile_id, report.profile_version)
        )
        if previous not in old_profiles:
            old_profiles.append(previous)
    for previous in old_profiles:
        same_rules = previous.model_dump(exclude={"profile_version", "engine_version"})
        current = next(
            (
                profile
                for profile in profiles
                if (
                    profile.engine_version == CURRENT_QUALITY_ENGINE_VERSION
                    and profile.profile_version > previous.profile_version
                    and profile.model_dump(exclude={"profile_version", "engine_version"})
                    == same_rules
                )
            ),
            None,
        )
        if current is None:
            version = 1 + max(
                profile.profile_version
                for profile in profiles
                if profile.profile_id == previous.profile_id
            )
            current = previous.model_copy(
                update={
                    "profile_version": version,
                    "engine_version": CURRENT_QUALITY_ENGINE_VERSION,
                }
            )
            profiles.append(current)
        revised_profiles[(previous.profile_id, previous.profile_version)] = current
    revisions = []
    for report in reports:
        previous = next(
            profile
            for profile in profiles
            if (profile.profile_id, profile.profile_version)
            == (report.profile_id, report.profile_version)
        )
        current = revised_profiles[(previous.profile_id, previous.profile_version)]
        revisions.append((report, reclassify_report(report, previous, current)))
    if apply:
        repository = PostgresQualityRepository(connect)
        for current in revised_profiles.values():
            repository.put_profile(project_id, current)
        for original, revised in revisions:
            repository.put_report(
                project_id=project_id,
                region_code=region_code,
                report=revised,
                expected_previous_report_sha256=original.content_sha256,
            )
    return {
        "applied": apply,
        "reports": len(revisions),
        "released": sum(old.status != "PASS" and new.status == "PASS" for old, new in revisions),
        "risks": sum(new.status == "RISK" for _, new in revisions),
        "profiles": len({(p.profile_id, p.profile_version) for p in revised_profiles.values()}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--region-code", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    token = bind_request_context(
        RequestContext(
            organization_id=args.organization_id,
            project_id=args.project_id,
            region_code=args.region_code,
            subject_id="quality-policy-upgrade",
            service_identity=True,
        )
    )
    try:
        result = reclassify_scope(
            psycopg_connection_factory(get_settings().postgres_dsn),
            organization_id=args.organization_id,
            project_id=args.project_id,
            region_code=args.region_code,
            apply=args.apply,
        )
        print(json.dumps(result))
    finally:
        reset_request_context(token)


if __name__ == "__main__":
    main()
