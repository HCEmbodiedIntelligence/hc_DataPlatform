"""One SQL snapshot for independently changing annotation/review/file pointers."""

from .errors import PackageConflict
from .models import canonical


def detail_state(
    repository, data, *, annotation_allowed, delivery_id=None, annotation_task_id=None
):
    # No project-wide task discovery and no latest-data-version lookup. A single
    # statement keeps mutable task pointers and their immutable children coherent.
    with repository.transaction() as cursor:
        cursor.execute(
            """WITH candidates AS (
                SELECT * FROM annotation.annotation_tasks
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND data_ref=%s::jsonb AND %s
                  AND (%s::text IS NULL OR task_id::text=%s)
                ORDER BY task_id LIMIT 101
            ), task AS (
                SELECT * FROM candidates WHERE (SELECT count(*) FROM candidates)=1
            ), submission AS (
                SELECT s.* FROM annotation.annotation_submissions s JOIN task t USING(task_id)
                WHERE s.data_ref=t.data_ref ORDER BY s.episode_version DESC LIMIT 1
            ), approval AS (
                SELECT r.*,s.revision_content_hash
                FROM annotation.annotation_reviews r JOIN task t USING(task_id)
                JOIN annotation.annotation_submissions s ON s.submission_id=r.submission_id
                    AND s.data_ref=t.data_ref AND s.revision=r.revision
                WHERE r.data_ref=t.data_ref AND r.decision='APPROVE'
                ORDER BY r.created_at DESC,r.review_id DESC LIMIT 1
            ), review AS (
                SELECT r.*,s.revision_content_hash
                FROM annotation.annotation_reviews r JOIN task t USING(task_id)
                JOIN annotation.annotation_submissions s ON s.submission_id=r.submission_id
                    AND s.data_ref=t.data_ref AND s.revision=r.revision
                WHERE r.data_ref=t.data_ref ORDER BY r.created_at DESC,r.review_id DESC LIMIT 1
            ), revision AS (
                SELECT r.task_id,r.revision,r.content_hash,r.author_id,r.created_at,r.data_ref
                FROM annotation.annotation_revisions r JOIN task t USING(task_id)
                WHERE r.data_ref=t.data_ref AND r.revision=t.current_revision
            ), files AS (
                SELECT f.task_id,f.revision,f.revision_content_hash,f.status,f.generation,
                       f.content_sha256,f.size_bytes,f.error_code
                FROM annotation.revision_files f JOIN task t USING(task_id)
                JOIN annotation.annotation_revisions r
                  ON r.task_id=f.task_id AND r.revision=f.revision
                WHERE r.data_ref=t.data_ref AND r.content_hash=f.revision_content_hash
                  AND f.revision IN (t.current_revision,
                    (SELECT revision FROM submission),(SELECT revision FROM approval))
            ), jobs AS (
                SELECT j.job_id,j.plan,j.status,j.phase,j.artifact_sha256,j.size_bytes,j.error_code
                FROM annotation.delivery_jobs j JOIN task t USING(task_id)
                WHERE j.plan->'binding'->'data'=t.data_ref
                  AND (%s::text IS NULL OR j.job_id=%s)
                ORDER BY j.created_at DESC,j.job_id DESC LIMIT 21
            )
            SELECT jsonb_build_object(
                'observed_at',statement_timestamp(),
                'tasks',COALESCE((SELECT jsonb_agg(to_jsonb(t)) FROM candidates t),'[]'::jsonb),
                'revision',(SELECT to_jsonb(r) FROM revision r),
                'submission',(SELECT to_jsonb(s) FROM submission s),
                'approval',(SELECT to_jsonb(a) FROM approval a),
                'review',(SELECT to_jsonb(r) FROM review r),
                'files',COALESCE((SELECT jsonb_agg(to_jsonb(f) ORDER BY f.revision)
                    FROM files f),'[]'::jsonb),
                'jobs',COALESCE((SELECT jsonb_agg(to_jsonb(j)) FROM jobs j),'[]'::jsonb),
                'migrations',COALESCE((SELECT jsonb_agg(jsonb_build_object(
                    'mapping_id',m.mapping_id,'source_task_id',m.source_task_id,
                    'source_revision',(m.plan->>'source_revision')::bigint,
                    'source_revision_content_hash',m.plan->>'source_revision_content_hash',
                    'source_data',m.plan->'source_data',
                    'source_status',m.plan->'source_snapshot'->'task'->>'status',
                    'source_submission_count',jsonb_array_length(
                        m.plan->'source_snapshot'->'history'->'annotation_submissions'),
                    'source_review_count',jsonb_array_length(
                        m.plan->'source_snapshot'->'history'->'annotation_reviews'),
                    'target_revision',1,
                    'target_revision_content_hash',m.result->>'target_revision_content_hash',
                    'document_sha256',m.document_sha256,'document_size',m.document_size,
                    'review_automatically_transferred',false) ORDER BY m.mapping_id)
                    FROM episode_store.annotation_migrations m JOIN task t
                        ON m.target_task_id=t.task_id AND m.target_data=t.data_ref
                    WHERE m.organization_id=t.organization_id AND m.project_id=t.project_id
                      AND m.region_code=t.region_code AND m.dataset_id=t.dataset_id
                      AND m.status='APPLIED'),'[]'::jsonb),
                'data_review',(SELECT document FROM episode_store.data_reviews
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s
                      AND data_version_id=%s ORDER BY sequence DESC LIMIT 1))""",
            (
                *data.identity.scope,
                data.model_dump_json(),
                annotation_allowed,
                annotation_task_id,
                annotation_task_id,
                delivery_id,
                delivery_id,
                *data.identity.scope,
                data.data_version_id,
            ),
        )
        state = cursor.fetchone()[0]
    if annotation_task_id is not None and not state["tasks"]:
        raise KeyError("annotation task does not belong to this data reference")
    if len(state["tasks"]) > 1:
        return state
    for name in ("revision", "submission", "approval", "review"):
        record = state[name]
        if record is not None and canonical(record["data_ref"]) != canonical(
            data.model_dump(mode="json")
        ):
            raise PackageConflict("annotation pointer differs from the fixed data reference")
    if state["tasks"] and state["revision"] is None:
        raise PackageConflict("current annotation revision is missing")
    if state["tasks"]:
        task = state["tasks"][0]
        for pointer, record, key in (
            ("current_submission_id", "submission", "submission_id"),
            ("approved_review_id", "approval", "review_id"),
        ):
            if task[pointer] is not None and (
                state[record] is None or state[record][key] != task[pointer]
            ):
                raise PackageConflict("annotation state pointer differs from its immutable record")
    return state
