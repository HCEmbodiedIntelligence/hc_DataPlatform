"""PostgreSQL quality profile, immutable report, and latest-summary adapter."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hc_data_platform.collection_tasks.captures import read_captures
from hc_data_platform.continuous_recordings.current_qc import CURRENT_EPISODE_QC
from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from hc_data_platform.continuous_recordings.presentation import decoded_filename

from .models import AutoQualityProblemV1, QcReportV1, QualityProfileV1, QualitySummaryV1


class PostgresQualityRepository:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def put_profile(self, project_id: str, profile: QualityProfileV1) -> None:
        connection = self._connection_factory()
        digest = profile.content_sha256()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO quality_profiles (
                        project_id, profile_id, profile_version, schema_version,
                        profile_sha256, profile_json
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (project_id, profile_id, profile_version) DO NOTHING
                    """,
                    (
                        project_id,
                        profile.profile_id,
                        profile.profile_version,
                        profile.schema_version,
                        digest,
                        profile.model_dump_json(),
                    ),
                )
                cursor.execute(
                    """
                    SELECT profile_sha256 FROM quality_profiles
                    WHERE project_id = %s AND profile_id = %s AND profile_version = %s
                    """,
                    (project_id, profile.profile_id, profile.profile_version),
                )
                row = cursor.fetchone()
                if row is None or str(row[0]) != digest:
                    raise problem(
                        status=409,
                        code="QUALITY_PROFILE_IMMUTABLE",
                        title="Quality profile is immutable",
                        detail="The profile version already exists with different content.",
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_profile(
        self, project_id: str, profile_id: str, profile_version: int
    ) -> QualityProfileV1 | None:
        row = self._fetchone(
            """
            SELECT profile_json FROM quality_profiles
            WHERE project_id = %s AND profile_id = %s AND profile_version = %s
            """,
            (project_id, profile_id, profile_version),
        )
        return None if row is None else QualityProfileV1.model_validate(row[0])

    def put_report(
        self,
        *,
        project_id: str,
        region_code: str,
        report: QcReportV1,
        expected_previous_report_sha256: str | None = None,
    ) -> None:
        summary = QualitySummaryV1.from_report(report)
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO qc_reports (
                        report_sha256, project_id, region_code, rollout_id,
                        source_sha256, profile_id, profile_version, profile_sha256,
                        engine_version, schema_version, status, report_json
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                    ) ON CONFLICT (
                        project_id, region_code, rollout_id, source_sha256,
                        profile_id, profile_version, engine_version
                    ) DO NOTHING
                    """,
                    (
                        report.content_sha256,
                        project_id,
                        region_code,
                        report.rollout_id,
                        report.source_sha256,
                        report.profile_id,
                        report.profile_version,
                        report.profile_sha256,
                        report.engine_version,
                        report.schema_version,
                        report.status.value,
                        report.model_dump_json(),
                    ),
                )
                cursor.execute(
                    """
                    SELECT report_json FROM qc_reports
                    WHERE project_id = %s AND region_code = %s AND rollout_id = %s
                      AND source_sha256 = %s AND profile_id = %s
                      AND profile_version = %s AND engine_version = %s
                    """,
                    (
                        project_id,
                        region_code,
                        report.rollout_id,
                        report.source_sha256,
                        report.profile_id,
                        report.profile_version,
                        report.engine_version,
                    ),
                )
                row = cursor.fetchone()
                if row is None or self._report(row[0]) != report:
                    raise problem(
                        status=409,
                        code="QUALITY_REPORT_IMMUTABLE",
                        title="Quality report is immutable",
                        detail="This evaluation identity already has different report content.",
                    )
                summary_guard = (
                    " WHERE quality_rollout_summaries.report_sha256 IN (%s, %s)"
                    if expected_previous_report_sha256 is not None
                    else ""
                )
                cursor.execute(
                    """
                    INSERT INTO quality_rollout_summaries (
                        project_id, region_code, rollout_id, source_sha256,
                        profile_id, profile_version, engine_version, status, report_sha256
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (project_id, region_code, rollout_id) DO UPDATE SET
                        source_sha256 = EXCLUDED.source_sha256,
                        profile_id = EXCLUDED.profile_id,
                        profile_version = EXCLUDED.profile_version,
                        engine_version = EXCLUDED.engine_version,
                        status = EXCLUDED.status,
                        report_sha256 = EXCLUDED.report_sha256,
                        updated_at = now()
                    """
                    + summary_guard,
                    (
                        project_id,
                        region_code,
                        summary.rollout_id,
                        summary.source_sha256,
                        summary.profile_id,
                        summary.profile_version,
                        summary.engine_version,
                        summary.status.value,
                        summary.report_sha256,
                    )
                    + (
                        (expected_previous_report_sha256, report.content_sha256)
                        if expected_previous_report_sha256 is not None
                        else ()
                    ),
                )
                if expected_previous_report_sha256 is not None and cursor.rowcount != 1:
                    raise problem(
                        status=409,
                        code="QUALITY_SUMMARY_CHANGED",
                        title="Quality summary changed",
                        detail="A newer evaluation replaced the selected report.",
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_report(
        self, *, project_id: str, region_code: str, rollout_id: str
    ) -> QcReportV1 | None:
        row = self._fetchone(
            """
            SELECT report.report_json
            FROM quality_rollout_summaries summary
            JOIN qc_reports report
              ON report.project_id = summary.project_id
             AND report.region_code = summary.region_code
             AND report.report_sha256 = summary.report_sha256
            WHERE summary.project_id = %s AND summary.region_code = %s
              AND summary.rollout_id = %s
            """,
            (project_id, region_code, rollout_id),
        )
        return None if row is None else self._report(row[0])

    def problem_locations(self, project_id, region_code, digest, code, recording_id, rollout_id):
        from .models import ProblemLocation, action_gap_segments

        scope = (current_request_context().organization_id, project_id, region_code)
        if recording_id:
            key = (*scope, recording_id)
            row = self._fetchone(
                """WITH reports AS (
                     SELECT report_document FROM ingest.recording_episode_qc_reports
                     WHERE (organization_id,project_id,region_code,recording_id,report_sha256)=(%s,%s,%s,%s,%s)
                     UNION ALL
                     SELECT report_document FROM ingest.recording_quality_reports
                     WHERE (organization_id,project_id,region_code,recording_id,report_id)=(%s,%s,%s,%s,%s)
                   )
                   SELECT report_document, (extract(epoch from capture_started_at)*1000000000)::bigint
                   FROM reports, ingest.continuous_recordings
                   WHERE (organization_id,project_id,region_code,recording_id)=(%s,%s,%s,%s)""",
                (*key, digest, *key, "recording_qc_" + digest, *key),
            )
            if not row:
                return None
            document, origin = row
            report = document["quality_report"]
            if code == "MCAP_INTERIOR_ACTION_MISSING":
                return [ProblemLocation(topic="/action", start_ns=str(g.first_offset_ns),
                                        end_ns=str(g.last_offset_ns), first_frame=g.first_frame,
                                        last_frame=g.last_frame) for g in action_gap_segments(
                    document.get("capture_quality", {}).get("interior_action_gaps", []))]
        else:
            row = self._fetchone(
                """SELECT report_json FROM qc_reports WHERE
                (organization_id,project_id,region_code,rollout_id,report_sha256)=(%s,%s,%s,%s,%s)""",
                (*scope, rollout_id, digest),
            )
            if not row:
                return None
            report = row[0]
            origin = int(report["start_ns"])
        return [ProblemLocation(topic=f["topic"], start_ns=str(int(f["start_ns"])-origin),
                                end_ns=str(int(f["end_ns"])-origin))
                for f in report["findings"] if f["code"] == code and f["severity"] != "info"
                and int(f["start_ns"]) >= origin]

    def action_gaps(self, project_id, region_code, recording_id, report_sha256):
        from .models import action_gap_segments

        scope = (current_request_context().organization_id, project_id, region_code, recording_id)
        row = self._fetchone(
            """SELECT report_document #> '{capture_quality,interior_action_gaps}'
            FROM ingest.recording_episode_qc_reports
            WHERE (organization_id,project_id,region_code,recording_id,report_sha256)=(%s,%s,%s,%s,%s)
            UNION ALL
            SELECT report_document #> '{capture_quality,interior_action_gaps}'
            FROM ingest.recording_quality_reports
            WHERE (organization_id,project_id,region_code,recording_id,report_id)=(%s,%s,%s,%s,%s)""",
            (*scope, report_sha256, *scope, "recording_qc_" + report_sha256),
        )
        return action_gap_segments(row[0]) if row and row[0] else None

    def list_problem_reports(
        self, *, project_id: str, region_code: str, include_discarded: bool = False
    ) -> tuple[AutoQualityProblemV1, ...]:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT report.report_json,
                           session.session_id,
                           COALESCE(session.data_package_id, native.episode_id),
                           summary.updated_at,
                           native.source_import_id,
                           native.source_episode_index, COALESCE(native.collection_task_id, job.task_id),
                           CASE WHEN native.status='DISCARDED' OR resolution.status='DISCARDED' THEN 'DISCARDED'
                                WHEN resolution.status IN ('PENDING','RUNNING') THEN 'IN_PROGRESS'
                                WHEN resolution.status='SUCCEEDED' AND resolution.updated_at >= summary.updated_at THEN 'RESOLVED'
                                ELSE 'OPEN' END
                      FROM quality_rollout_summaries AS summary
                      JOIN qc_reports AS report
                        ON report.organization_id = summary.organization_id
                       AND report.project_id = summary.project_id
                       AND report.region_code = summary.region_code
                       AND report.report_sha256 = summary.report_sha256
                      LEFT JOIN ingest.rollouts AS rollout
                        ON (rollout.organization_id, rollout.project_id, rollout.region_code, rollout.rollout_id)
                         = (summary.organization_id, summary.project_id, summary.region_code, summary.rollout_id)
                      LEFT JOIN ingest.collection_jobs AS job
                        ON (job.organization_id, job.project_id, job.region_code, job.collection_job_id)
                         = (rollout.organization_id, rollout.project_id, rollout.region_code, rollout.collection_job_id)
                      LEFT JOIN LATERAL (
                           SELECT upload.session_id, upload.data_package_id
                             FROM ingest.upload_sessions AS upload
                            WHERE upload.organization_id = summary.organization_id
                              AND upload.project_id = summary.project_id
                              AND upload.region_code = summary.region_code
                              AND upload.rollout_id = summary.rollout_id
                            ORDER BY upload.created_at DESC, upload.session_id DESC
                            LIMIT 1
                      ) AS session ON TRUE
                      LEFT JOIN LATERAL (
                           SELECT source.raw_source_id AS source_import_id,
                                  episode.source_episode_index, episode.episode_id, source.collection_task_id, episode.status
                             FROM ingest.raw_source_episodes AS episode
                             JOIN ingest.raw_sources AS source
                               ON source.organization_id = episode.organization_id
                              AND source.project_id = episode.project_id
                              AND source.region_code = episode.region_code
                              AND source.raw_source_id = episode.raw_source_id
                            WHERE episode.organization_id = summary.organization_id
                              AND episode.project_id = summary.project_id
                              AND episode.region_code = summary.region_code
                              AND episode.episode_id = summary.rollout_id
                              AND source.raw_status = 'COMMITTED'
                              AND source.source_format = 'LEROBOT_V3'
                            ORDER BY source.committed_at DESC, source.raw_source_id
                            LIMIT 1
                      ) AS native ON TRUE
                      LEFT JOIN LATERAL (
                           SELECT status, updated_at FROM ingest.episode_resolutions d
                           WHERE (d.organization_id,d.project_id,d.region_code,d.episode_id) =
                                 (summary.organization_id,summary.project_id,summary.region_code,summary.rollout_id)
                           ORDER BY created_at DESC,resolution_id DESC LIMIT 1
                      ) resolution ON TRUE
                     WHERE summary.project_id = %s
                       AND summary.region_code = %s
                       AND summary.status IN ('RISK', 'REJECT')
                       AND rollout.duplicate_of_rollout_id IS NULL
                       AND NOT EXISTS (
                           SELECT 1 FROM ingest.recording_episode_processing p
                           WHERE (p.organization_id,p.project_id,p.region_code)
                             = (summary.organization_id,summary.project_id,summary.region_code)
                             AND p.recording_id || ':' || p.episode_id = summary.rollout_id
                             AND (p.status='DISCARDED' OR EXISTS (
                                 SELECT 1 FROM ingest.mcap_processing_files d
                                 WHERE (d.organization_id,d.project_id,d.region_code,d.recording_id)
                                     = (p.organization_id,p.project_id,p.region_code,p.recording_id)
                                   AND d.duplicate_of IS NOT NULL))
                       )
                     ORDER BY summary.updated_at DESC, summary.rollout_id DESC
                    """,
                    (project_id, region_code),
                )
                result: list[AutoQualityProblemV1] = []
                for (
                    report_json,
                    session_id,
                    data_package_id,
                    updated_at,
                    source_import_id,
                    source_episode_index,
                    task_id, progress,
                ) in cursor.fetchall():
                    if progress == "RESOLVED":
                        continue
                    report = self._report(report_json)
                    if not report.findings:
                        continue
                    result.append(
                        AutoQualityProblemV1.from_report(
                            report,
                            session_id=None if session_id is None else str(session_id),
                            data_package_id=(
                                None if data_package_id is None else str(data_package_id)
                            ),
                            updated_at=updated_at,
                            source_import_id=source_import_id,
                            source_episode_index=source_episode_index,
                            collection_task_id=task_id,
                        ).model_copy(update={"progress": progress, "discarded": progress == "DISCARDED"})
                    )
                cursor.execute(
                    """
                    WITH problems AS (
                    SELECT q.report_document, q.report_sha256 AS digest, r.recording_id,
                           r.data_package_id, q.created_at AS checked_at, r.collection_task_id,
                           (p.status='DISCARDED' OR EXISTS (
                               SELECT 1 FROM episode_store.qc_retired_versions v
                               WHERE (v.organization_id,v.project_id,v.region_code,v.recording_id,v.report_id)
                                   =(p.organization_id,p.project_id,p.region_code,p.recording_id,q.report_id)
                           )) AS discarded, r.recording_upload_id, r.superseded_by_processing_id
                    FROM ingest.recording_episode_processing p
                    JOIN """ + CURRENT_EPISODE_QC + """ q ON TRUE
                    JOIN ingest.continuous_recordings r
                      ON (r.organization_id,r.project_id,r.region_code,r.recording_id)
                       = (p.organization_id,p.project_id,p.region_code,p.recording_id)
                    WHERE p.organization_id=%s AND p.project_id=%s AND p.region_code=%s
                      AND q.status='REJECT'
                      AND (p.status <> 'READY' OR q.report_document->'finding_codes' ? 'MCAP_INTERIOR_ACTION_MISSING')
                      AND p.finalized_revision=r.finalized_revision
                    UNION ALL
                    SELECT q.report_document, substr(q.report_id,14), r.recording_id,
                           r.data_package_id, r.updated_at, r.collection_task_id,
                           r.recording_document->>'quality_discarded_at' IS NOT NULL, r.recording_upload_id, r.superseded_by_processing_id
                    FROM ingest.continuous_recordings r
                    JOIN ingest.recording_quality_reports q USING
                        (organization_id,project_id,region_code,recording_id)
                    WHERE r.organization_id=%s AND r.project_id=%s AND r.region_code=%s
                      AND r.status='QC_FAILED'
                      AND q.report_id=r.recording_document->'quality_summary'->>'report_id'
                    )
                    SELECT p.report_document, p.digest, p.recording_id, p.data_package_id,
                           p.checked_at, p.collection_task_id, p.discarded,
                           COALESCE(m.source_path, source.asset_path), m.raw_source_id
                    FROM problems p
                    LEFT JOIN LATERAL (
                        SELECT raw_source_id,source_path,duplicate_of FROM ingest.mcap_processing_files
                        WHERE organization_id=%s AND project_id=%s AND region_code=%s
                          AND recording_id=p.recording_id
                        ORDER BY created_at DESC, processing_id DESC LIMIT 1
                    ) m ON TRUE
                    LEFT JOIN LATERAL (
                        SELECT asset_path FROM ingest.recording_upload_assets
                        WHERE organization_id=%s AND project_id=%s AND region_code=%s
                          AND upload_id=p.recording_upload_id AND lower(asset_path) LIKE '%%.mcap'
                        ORDER BY (asset_path LIKE 'source/original/%%') DESC, asset_path LIMIT 1
                    ) source ON TRUE
                    WHERE m.duplicate_of IS NULL
                      AND (p.superseded_by_processing_id IS NULL OR m.raw_source_id IS NOT NULL)
                    ORDER BY p.checked_at DESC, p.recording_id, p.digest
                    """,
                    (current_request_context().organization_id, project_id, region_code) * 4,
                )
                reports = cursor.fetchall()
                captures = (
                    read_captures(cursor, current_request_context().organization_id, project_id, region_code)
                    if any(row[-1] for row in reports) else ()
                )
                if any(capture.stage == "DISCARDED" for capture in captures):
                    cursor.execute("""
                        SELECT recording_id FROM episode_store.qc_retired_versions
                        WHERE organization_id=%s AND project_id=%s AND region_code=%s
                          AND starts_with(report_id, 'issue_')
                    """, (current_request_context().organization_id, project_id, region_code))
                    manual_retirements = {row[0] for row in cursor.fetchall()}
                    captures = [capture for capture in captures if not (
                        capture.stage == "DISCARDED" and capture.recording_id in manual_retirements
                    )]
                continuous = _recording_problems(reports, captures)
                # Suppress the generic copy even when the recording report was folded into a retry.
                covered = {row[0]["quality_report"]["rollout_id"] for row in reports}
                result = [item for item in result if item.rollout_id not in covered] + continuous
                return tuple(
                    sorted((item for item in result if include_discarded or item.progress == "OPEN"),
                           key=lambda item: (item.updated_at, item.id), reverse=True)
                )
        finally:
            connection.close()

    def _fetchone(self, query: str, params: tuple[object, ...]) -> Any | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                return cursor.fetchone()
        finally:
            connection.close()

    @staticmethod
    def _report(value: object) -> QcReportV1:
        if isinstance(value, str):
            return QcReportV1.model_validate_json(value)
        return QcReportV1.model_validate(value)


def _recording_problems(reports, captures):
    """Reconcile failed reports with the same current MCAP member used by the dashboard."""
    current = {(c.source_import_id, c.source_path): c for c in captures if c.source_path}
    by_member = {}
    standalone = []
    for row in reports:
        source_path, raw_source_id = row[-2:]
        if raw_source_id is None:
            standalone.append((row, "DISCARDED" if row[6] else "OPEN"))
        else:
            by_member.setdefault((raw_source_id, source_path), []).append(row)

    selected = list(standalone)
    for key, history in by_member.items():
        capture = current.get(key)
        if capture is None or capture.canonical_episode_id or capture.stage == "CANCELLED":
            continue
        # Rows are newest first. Prefer evidence for the active recording. Keep
        # separate current Episode reports so their diagnostic actions stay scoped.
        active = [row for row in history if row[2] == capture.recording_id]
        if capture.stage == "DISCARDED":
            selected.append(((active or history)[0], "DISCARDED"))
        elif capture.stage == "READY" or capture.qc_outcome == "PASS":
            continue
        elif capture.stage in {"PENDING_QC", "QC_RUNNING", "PREPARING", "PROCESSING"} and capture.qc_outcome not in {"REJECT", "RISK"}:
            selected.append(((active or history)[0], "IN_PROGRESS"))
        elif active and (capture.stage == "QC_FAILED" or capture.qc_outcome in {"REJECT", "RISK"}):
            selected.extend((row, "OPEN") for row in active if not row[6])

    return [
        AutoQualityProblemV1.from_recording_report(
            document, report_sha256=str(digest), recording_id=recording_id,
            data_package_id=package, updated_at=created, collection_task_id=task_id,
        ).model_copy(update={
            "progress": progress, "discarded": progress == "DISCARDED",
            "source_file_name": decoded_filename(source_path),
        })
        for (document, digest, recording_id, package, created, task_id, _, source_path, _), progress in selected
    ]
