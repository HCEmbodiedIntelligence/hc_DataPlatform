"""Project verified soft Episodes into the common rollout publication relation."""

from hc_data_platform.core.errors import problem


def materialize_rollout(cursor, *, project_id, dataset_id, rollout_id, source_sha256):
    cursor.execute(
        """SELECT recording.organization_id,recording.region_code,recording.collection_job_id,
                            recording.robot_id,recording.recording_id,episode.created_at
        FROM ingest.continuous_recordings recording
        JOIN ingest.recording_episode_processing episode
          ON episode.organization_id=recording.organization_id
          AND episode.project_id=recording.project_id

         AND episode.region_code=recording.region_code
         AND episode.recording_id=recording.recording_id
        JOIN ingest.recording_episode_qc_reports qc
          ON qc.report_id=episode.qc_report_id AND qc.organization_id=episode.organization_id
         AND qc.project_id=episode.project_id AND qc.region_code=episode.region_code
         AND qc.recording_id=episode.recording_id AND qc.episode_id=episode.episode_id
        JOIN lance_rollout_lineage lineage
          ON lineage.organization_id=episode.organization_id
          AND lineage.project_id=episode.project_id
         AND lineage.dataset_id=episode.dataset_id AND lineage.version_added=episode.dataset_version
         AND lineage.rollout_id=episode.recording_id || ':' || episode.episode_id
         AND lineage.source_sha256=qc.report_document->>'source_sha256'
        WHERE episode.project_id=%s AND episode.dataset_id=%s AND lineage.rollout_id=%s
          AND lineage.source_sha256=%s AND episode.status='READY' AND qc.status='PASS' """,
        (project_id, dataset_id, rollout_id, source_sha256),
    )
    rows = cursor.fetchall()
    if not rows:
        return  # Existing single-package/native LeRobot rollouts own their relation.
    if len(rows) != 1:
        raise problem(
            status=409,
            code="CONTINUOUS_ROLLOUT_LINEAGE_AMBIGUOUS",
            title="Ambiguous recording lineage",
        )
    organization, region, job, robot, recording, created = rows[0]
    cursor.execute(
        """SELECT robot_id FROM ingest.collection_jobs
        WHERE organization_id=%s
        AND project_id=%s
        AND region_code=%s
        AND collection_job_id=%s FOR UPDATE""",
        (organization, project_id, region, job),
    )
    owner = cursor.fetchone()
    if owner is None or owner[0] != robot:
        raise problem(
            status=409,
            code="CONTINUOUS_COLLECTION_JOB_MISSING",
            title="Recording collection job is unavailable",
        )
    cursor.execute(
        """SELECT region_code,collection_job_id,robot_id,source_sha256 FROM ingest.rollouts
        WHERE organization_id=%s AND project_id=%s AND rollout_id=%s""",
        (organization, project_id, rollout_id),
    )
    existing = cursor.fetchone()
    if existing:
        if tuple(existing) != (region, job, robot, source_sha256):
            raise problem(
                status=409, code="CONTINUOUS_ROLLOUT_IMMUTABLE", title="Rollout lineage conflicts"
            )
        return
    cursor.execute(
        """SELECT COALESCE(MAX(sequence_no),0)+1 FROM ingest.rollouts
        WHERE organization_id=%s AND project_id=%s AND collection_job_id=%s""",
        (organization, project_id, job),
    )
    sequence = cursor.fetchone()[0]
    cursor.execute(
        """INSERT INTO ingest.rollouts
        (organization_id,project_id,region_code,collection_job_id,rollout_id,sequence_no,robot_id,
         source_sha256,status,collection_session_id,recording_request_id,data_package_id,created_at,updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'RAW_VERIFIED',%s,%s,%s,%s,%s)""",
        (
            organization,
            project_id,
            region,
            job,
            rollout_id,
            sequence,
            robot,
            source_sha256,
            recording,
            recording,
            rollout_id,
            created,
            created,
        ),
    )
