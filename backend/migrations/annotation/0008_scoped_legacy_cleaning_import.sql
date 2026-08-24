-- P08 one-way cutover for durable P11 CleaningDraft history.
--
-- The annotation model is step-based while the P11 EDL is nanosecond-based.
-- This migration must not guess a sampling conversion.  It therefore imports
-- each immutable EDL snapshot as a LEGACY_CLEANING revision with the complete
-- source payload in legacy_audit.  Native annotation operations remain empty;
-- an operator can inspect the old rule without the platform silently applying
-- a different interval.  Every draft is admitted only when its fixed Episode
-- revision resolves one selected stream and one exact Lance lineage.

-- The original mapping key predated organization/project/region scope and made
-- equal draft IDs in different tenants collide.  Backfill scope only through
-- the already-owned target task; no source identity is inferred from the ID.
ALTER TABLE annotation.legacy_cleaning_migrations
    ADD COLUMN IF NOT EXISTS organization_id text,
    ADD COLUMN IF NOT EXISTS project_id text,
    ADD COLUMN IF NOT EXISTS region_code text;

ALTER TABLE annotation.legacy_cleaning_migrations
    DISABLE TRIGGER USER;

UPDATE annotation.legacy_cleaning_migrations AS migration
   SET organization_id = task.organization_id,
       project_id = task.project_id,
       region_code = task.region_code
  FROM annotation.annotation_tasks AS task
 WHERE task.task_id = migration.target_task_id
   AND (
       migration.organization_id IS NULL
       OR migration.project_id IS NULL
       OR migration.region_code IS NULL
   );

ALTER TABLE annotation.legacy_cleaning_migrations
    ENABLE TRIGGER USER;

DO $mapping_preflight$
DECLARE
    invalid_mappings text;
BEGIN
    SELECT string_agg(
               source_draft_id || ':' || source_revision::text,
               ', ' ORDER BY source_draft_id, source_revision
           )
      INTO invalid_mappings
      FROM annotation.legacy_cleaning_migrations
     WHERE organization_id IS NULL
        OR project_id IS NULL
        OR region_code IS NULL;

    IF invalid_mappings IS NOT NULL THEN
        RAISE EXCEPTION
            'legacy cleaning mapping scope cannot be resolved from its target task: %',
            invalid_mappings;
    END IF;
END
$mapping_preflight$;

ALTER TABLE annotation.legacy_cleaning_migrations
    ALTER COLUMN organization_id SET NOT NULL,
    ALTER COLUMN project_id SET NOT NULL,
    ALTER COLUMN region_code SET NOT NULL,
    DROP CONSTRAINT IF EXISTS legacy_cleaning_migrations_pkey,
    ADD CONSTRAINT legacy_cleaning_migrations_pkey PRIMARY KEY (
        organization_id, project_id, region_code, source_draft_id, source_revision
    );

DO $mapping_constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'legacy_cleaning_migrations_organization_nonempty'
           AND conrelid = 'annotation.legacy_cleaning_migrations'::regclass
    ) THEN
        ALTER TABLE annotation.legacy_cleaning_migrations
            ADD CONSTRAINT legacy_cleaning_migrations_organization_nonempty
                CHECK (organization_id <> ''),
            ADD CONSTRAINT legacy_cleaning_migrations_project_nonempty
                CHECK (project_id <> ''),
            ADD CONSTRAINT legacy_cleaning_migrations_region_nonempty
                CHECK (region_code <> ''),
            ADD CONSTRAINT legacy_cleaning_migrations_organization_project_fk
                FOREIGN KEY (organization_id, project_id)
                REFERENCES registry.organization_projects (organization_id, project_id);
    END IF;
END
$mapping_constraints$;

CREATE INDEX IF NOT EXISTS legacy_cleaning_migrations_scoped_target_idx
ON annotation.legacy_cleaning_migrations (
    organization_id, project_id, region_code, target_task_id, target_revision
);

DROP POLICY IF EXISTS legacy_cleaning_migration_project_scope
ON annotation.legacy_cleaning_migrations;
SELECT core.apply_project_rls('annotation.legacy_cleaning_migrations'::regclass);

-- A system-created annotation task and its imported cleaning history may share
-- the same immutable Lance target.  They are different workflows, so uniqueness
-- includes creation_source instead of forcing a migration to mutate a live task.
DROP INDEX IF EXISTS annotation.annotation_tasks_automatic_target_uidx;
CREATE UNIQUE INDEX annotation_tasks_automatic_target_uidx
ON annotation.annotation_tasks (
    organization_id, project_id, region_code, rollout_id, dataset_id,
    dataset_version, base_lance_version, task_kind, creation_source
)
WHERE region_code IS NOT NULL;

-- Select only source revisions that do not already have an exact scoped mapping.
CREATE TEMP TABLE hc_pending_legacy_cleaning_revisions
ON COMMIT DROP
AS
SELECT
    draft.organization_id,
    draft.project_id,
    draft.region_code,
    draft.draft_id,
    draft.dataset_id,
    draft.base_version_id,
    draft.base_revision_id,
    draft.selected_stream_id,
    draft.created_at AS draft_created_at,
    edl.edl_revision AS source_revision,
    edl.client_mutation_id,
    edl.operation_hash,
    edl.operations_document,
    edl.actor_id AS source_actor_id,
    edl.created_at AS source_created_at
FROM manual_cleaning.cleaning_workbench_drafts AS draft
JOIN manual_cleaning.cleaning_draft_edl_revisions AS edl
  ON edl.organization_id = draft.organization_id
 AND edl.project_id = draft.project_id
 AND edl.region_code = draft.region_code
 AND edl.draft_id = draft.draft_id
WHERE NOT EXISTS (
    SELECT 1
      FROM annotation.legacy_cleaning_migrations AS mapped
     WHERE mapped.organization_id = draft.organization_id
       AND mapped.project_id = draft.project_id
       AND mapped.region_code = draft.region_code
       AND mapped.source_draft_id = draft.draft_id
       AND mapped.source_revision = edl.edl_revision
);

-- A resolved row proves all identities needed by annotation: the fixed source
-- revision, selected stream, logical rollout, catalog version, Lance version,
-- and positive whole-rollout step count.  JSON numeric casts are guarded so a
-- malformed historic document is reported by the preflight instead of being
-- partially imported.
CREATE TEMP TABLE hc_legacy_cleaning_resolution_candidates
ON COMMIT DROP
AS
WITH selected_streams AS (
    SELECT
        pending.organization_id,
        pending.project_id,
        pending.region_code,
        pending.draft_id,
        pending.dataset_id,
        pending.base_version_id,
        pending.base_revision_id,
        stream.value AS stream_document
    FROM (
        SELECT DISTINCT
            organization_id, project_id, region_code, draft_id, dataset_id,
            base_version_id, base_revision_id, selected_stream_id
        FROM hc_pending_legacy_cleaning_revisions
    ) AS pending
    JOIN dataset_registry.dataset_version_episode_revisions AS revision
      ON revision.organization_id = pending.organization_id
     AND revision.project_id = pending.project_id
     AND revision.region_code = pending.region_code
     AND revision.dataset_id = pending.dataset_id
     AND revision.version_id = pending.base_version_id
     AND revision.revision_id = pending.base_revision_id
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(revision.revision_document -> 'streams') = 'array'
            THEN revision.revision_document -> 'streams'
            ELSE '[]'::jsonb
        END
    ) AS stream(value)
    WHERE stream.value ->> 'episode_stream_id' = pending.selected_stream_id
), parsed_bindings AS (
    SELECT
        selected.*,
        COALESCE(
            selected.stream_document -> 'preview_binding',
            selected.stream_document -> 'data_binding'
        ) AS binding_document
    FROM selected_streams AS selected
), typed_bindings AS (
    SELECT
        parsed.*,
        NULLIF(parsed.binding_document ->> 'rollout_id', '') AS rollout_id,
        CASE
            WHEN parsed.binding_document ->> 'lance_version' ~ '^[1-9][0-9]*$'
            THEN (parsed.binding_document ->> 'lance_version')::bigint
        END AS binding_lance_version,
        CASE
            WHEN parsed.binding_document ->> 'start_step' ~ '^[0-9]+$'
            THEN (parsed.binding_document ->> 'start_step')::bigint
        END AS binding_start_step,
        CASE
            WHEN parsed.binding_document ->> 'end_step' ~ '^[1-9][0-9]*$'
            THEN (parsed.binding_document ->> 'end_step')::bigint
        END AS binding_end_step
    FROM parsed_bindings AS parsed
)
SELECT
    binding.organization_id,
    binding.project_id,
    binding.region_code,
    binding.draft_id,
    binding.dataset_id,
    binding.base_version_id,
    binding.base_revision_id,
    binding.rollout_id,
    version.version AS dataset_version,
    version.lance_version AS base_lance_version,
    lineage.step_count AS base_step_count
FROM typed_bindings AS binding
JOIN public.lance_rollout_lineage AS lineage
  ON lineage.organization_id = binding.organization_id
 AND lineage.project_id = binding.project_id
 AND lineage.dataset_id = binding.dataset_id
 AND lineage.rollout_id = binding.rollout_id
JOIN public.lance_dataset_versions AS version
  ON version.organization_id = lineage.organization_id
 AND version.project_id = lineage.project_id
 AND version.dataset_id = lineage.dataset_id
 AND version.version = lineage.version_added
 AND version.rollout_id = lineage.rollout_id
WHERE binding.binding_lance_version = version.lance_version
  AND binding.binding_start_step = 0
  AND binding.binding_end_step = lineage.step_count
  AND lineage.step_count > 0;

DO $source_preflight$
DECLARE
    invalid_sources text;
BEGIN
    WITH pending_drafts AS (
        SELECT DISTINCT
            organization_id, project_id, region_code, draft_id
        FROM hc_pending_legacy_cleaning_revisions
    ), resolution_counts AS (
        SELECT
            pending.organization_id,
            pending.project_id,
            pending.region_code,
            pending.draft_id,
            count(candidate.draft_id) AS resolution_count
        FROM pending_drafts AS pending
        LEFT JOIN hc_legacy_cleaning_resolution_candidates AS candidate
          ON candidate.organization_id = pending.organization_id
         AND candidate.project_id = pending.project_id
         AND candidate.region_code = pending.region_code
         AND candidate.draft_id = pending.draft_id
        GROUP BY
            pending.organization_id, pending.project_id,
            pending.region_code, pending.draft_id
    )
    SELECT string_agg(
               organization_id || '/' || project_id || '/' || region_code || '/' ||
               draft_id || '(matches=' || resolution_count::text || ')',
               ', ' ORDER BY organization_id, project_id, region_code, draft_id
           )
      INTO invalid_sources
      FROM resolution_counts
     WHERE resolution_count <> 1;

    IF invalid_sources IS NOT NULL THEN
        RAISE EXCEPTION
            'legacy cleaning import requires one exact selected-stream Lance lineage per pending draft: %',
            invalid_sources;
    END IF;
END
$source_preflight$;

CREATE TEMP TABLE hc_legacy_cleaning_sources
ON COMMIT DROP
AS
SELECT
    pending.*,
    resolved.rollout_id,
    resolved.dataset_version,
    resolved.base_lance_version,
    resolved.base_step_count,
    'legacy-cleaning:' || md5(concat_ws(
        E'\x1f', pending.organization_id, pending.project_id, pending.region_code,
        pending.dataset_id, resolved.rollout_id, resolved.dataset_version::text,
        resolved.base_lance_version::text
    )) AS target_task_id,
    'legacy-cleaning-import:v1:' || md5(concat_ws(
        E'\x1f', pending.organization_id, pending.project_id, pending.region_code,
        pending.dataset_id, resolved.rollout_id, resolved.dataset_version::text,
        resolved.base_lance_version::text
    )) AS source_workflow_id
FROM hc_pending_legacy_cleaning_revisions AS pending
JOIN hc_legacy_cleaning_resolution_candidates AS resolved
  ON resolved.organization_id = pending.organization_id
 AND resolved.project_id = pending.project_id
 AND resolved.region_code = pending.region_code
 AND resolved.draft_id = pending.draft_id;

-- Refuse task-ID collisions and an existing non-import LEGACY task on the same
-- target.  The migration never claims or rewrites an operator's live task.
DO $target_preflight$
DECLARE
    invalid_targets text;
BEGIN
    WITH desired AS (
        SELECT DISTINCT
            organization_id, project_id, region_code, dataset_id, rollout_id,
            dataset_version, base_lance_version, base_step_count,
            target_task_id, source_workflow_id
        FROM hc_legacy_cleaning_sources
    ), conflicts AS (
        SELECT desired.target_task_id, task.task_id AS existing_task_id
        FROM desired
        JOIN annotation.annotation_tasks AS task
          ON task.task_id = desired.target_task_id
        WHERE task.organization_id <> desired.organization_id
           OR task.project_id <> desired.project_id
           OR task.region_code <> desired.region_code
           OR task.dataset_id <> desired.dataset_id
           OR task.rollout_id <> desired.rollout_id
           OR task.dataset_version <> desired.dataset_version
           OR task.base_lance_version <> desired.base_lance_version
           OR task.base_step_count IS DISTINCT FROM desired.base_step_count
           OR task.task_kind <> 'TAGGING'
           OR task.creation_source <> 'LEGACY'
           OR task.source_workflow_id IS DISTINCT FROM desired.source_workflow_id
        UNION ALL
        SELECT desired.target_task_id, task.task_id
        FROM desired
        JOIN annotation.annotation_tasks AS task
          ON task.organization_id = desired.organization_id
         AND task.project_id = desired.project_id
         AND task.region_code = desired.region_code
         AND task.dataset_id = desired.dataset_id
         AND task.rollout_id = desired.rollout_id
         AND task.dataset_version = desired.dataset_version
         AND task.base_lance_version = desired.base_lance_version
         AND task.task_kind = 'TAGGING'
         AND task.creation_source = 'LEGACY'
        WHERE task.source_workflow_id IS DISTINCT FROM desired.source_workflow_id
           OR task.task_id <> desired.target_task_id
    )
    SELECT string_agg(
               target_task_id || '->' || existing_task_id,
               ', ' ORDER BY target_task_id, existing_task_id
           )
      INTO invalid_targets
      FROM conflicts;

    IF invalid_targets IS NOT NULL THEN
        RAISE EXCEPTION
            'legacy cleaning import target collides with a non-import annotation task: %',
            invalid_targets;
    END IF;
END
$target_preflight$;

INSERT INTO annotation.annotation_tasks (
    organization_id, task_id, project_id, region_code, dataset_id, dataset_version,
    rollout_id, assignee_id, base_lance_version, base_step_count,
    tag_schema_id, tag_schema_version, task_kind, creation_source,
    source_workflow_id, current_revision, state_version, status,
    submitted_revision, submitted_by, current_submission_id,
    approved_revision, approved_review_id, etag, created_at, updated_at
)
SELECT
    source.organization_id,
    source.target_task_id,
    source.project_id,
    source.region_code,
    source.dataset_id,
    source.dataset_version,
    source.rollout_id,
    'legacy-cleaning-migration',
    source.base_lance_version,
    source.base_step_count,
    'legacy-flat',
    1,
    'TAGGING',
    'LEGACY',
    source.source_workflow_id,
    0,
    0,
    'DRAFT',
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    format('"annotation:%s:0:0"', source.target_task_id),
    min(source.draft_created_at),
    min(source.draft_created_at)
FROM hc_legacy_cleaning_sources AS source
GROUP BY
    source.organization_id, source.target_task_id, source.project_id,
    source.region_code, source.dataset_id, source.dataset_version,
    source.rollout_id, source.base_lance_version, source.base_step_count,
    source.source_workflow_id
ON CONFLICT (task_id) DO NOTHING;

INSERT INTO annotation.annotation_revisions (
    task_id, revision, parent_revision, author_id, client_mutation_id,
    base_lance_version, tag_schema_id, tag_schema_version, tags,
    origin, legacy_audit, content_hash, created_at
)
SELECT
    task.task_id,
    0,
    NULL,
    'system',
    'initial',
    task.base_lance_version,
    task.tag_schema_id,
    task.tag_schema_version,
    '[]'::jsonb,
    'ANNOTATION',
    NULL,
    repeat('0', 64),
    task.created_at
FROM annotation.annotation_tasks AS task
JOIN (
    SELECT DISTINCT target_task_id
    FROM hc_legacy_cleaning_sources
) AS target ON target.target_task_id = task.task_id
WHERE NOT EXISTS (
    SELECT 1
      FROM annotation.annotation_revisions AS revision
     WHERE revision.task_id = task.task_id
       AND revision.revision = 0
);

CREATE TEMP TABLE hc_legacy_cleaning_import_plan
ON COMMIT DROP
AS
SELECT
    source.*,
    task.current_revision + row_number() OVER (
        PARTITION BY source.target_task_id
        ORDER BY source.source_created_at, source.draft_id, source.source_revision
    ) AS target_revision,
    audit.audit_id AS source_audit_event_id
FROM hc_legacy_cleaning_sources AS source
JOIN annotation.annotation_tasks AS task
  ON task.task_id = source.target_task_id
LEFT JOIN LATERAL (
    SELECT event.audit_id::text AS audit_id
    FROM core.audit_events AS event
    WHERE event.organization_id = source.organization_id
      AND event.project_id = source.project_id
      AND event.region_code = source.region_code
      AND event.resource_type = 'CLEANING_DRAFT'
      AND event.resource_id = source.draft_id
      AND event.action = 'cleaning.draft.updated'
      AND event.actor_id = source.source_actor_id
      AND event.occurred_at = source.source_created_at
      AND event.details ->> 'client_mutation_id' = source.client_mutation_id
    ORDER BY event.audit_id
    LIMIT 1
) AS audit ON TRUE;

INSERT INTO annotation.annotation_revisions (
    task_id, revision, parent_revision, author_id, client_mutation_id,
    base_lance_version, tag_schema_id, tag_schema_version, tags,
    origin, legacy_audit, content_hash, created_at
)
SELECT
    plan.target_task_id,
    plan.target_revision,
    plan.target_revision - 1,
    plan.source_actor_id,
    'legacy-cleaning:' || plan.draft_id || ':' || plan.source_revision::text,
    plan.base_lance_version,
    'legacy-flat',
    1,
    '[]'::jsonb,
    'LEGACY_CLEANING',
    jsonb_build_object(
        'draft_id', plan.draft_id,
        'source_revision', plan.source_revision,
        'source_actor_id', plan.source_actor_id,
        'source_created_at', plan.source_created_at,
        'source_audit_event_id', plan.source_audit_event_id,
        'source_payload', jsonb_build_object(
            'schema_version', 1,
            'scope', jsonb_build_object(
                'organization_id', plan.organization_id,
                'project_id', plan.project_id,
                'region_code', plan.region_code
            ),
            'draft_id', plan.draft_id,
            'edl_revision', plan.source_revision,
            'client_mutation_id', plan.client_mutation_id,
            'operation_hash', plan.operation_hash,
            'operations', plan.operations_document,
            'migration_mode', 'PRESERVED_NANOSECOND_EDL'
        )
    ),
    repeat('0', 64),
    plan.source_created_at
FROM hc_legacy_cleaning_import_plan AS plan
ORDER BY plan.target_task_id, plan.target_revision;

INSERT INTO annotation.legacy_cleaning_migrations (
    organization_id, project_id, region_code,
    source_draft_id, source_revision, source_audit_event_id,
    source_actor_id, source_created_at, target_task_id,
    target_revision, source_payload, migrated_at
)
SELECT
    plan.organization_id,
    plan.project_id,
    plan.region_code,
    plan.draft_id,
    plan.source_revision,
    plan.source_audit_event_id,
    plan.source_actor_id,
    plan.source_created_at,
    plan.target_task_id,
    plan.target_revision,
    jsonb_build_object(
        'schema_version', 1,
        'scope', jsonb_build_object(
            'organization_id', plan.organization_id,
            'project_id', plan.project_id,
            'region_code', plan.region_code
        ),
        'draft_id', plan.draft_id,
        'edl_revision', plan.source_revision,
        'client_mutation_id', plan.client_mutation_id,
        'operation_hash', plan.operation_hash,
        'operations', plan.operations_document,
        'migration_mode', 'PRESERVED_NANOSECOND_EDL'
    ),
    now()
FROM hc_legacy_cleaning_import_plan AS plan;

WITH imported AS (
    SELECT
        target_task_id,
        count(*)::bigint AS imported_count,
        max(target_revision)::bigint AS latest_revision,
        max(source_created_at) AS latest_created_at
    FROM hc_legacy_cleaning_import_plan
    GROUP BY target_task_id
)
UPDATE annotation.annotation_tasks AS task
   SET current_revision = imported.latest_revision,
       state_version = task.state_version + imported.imported_count,
       status = 'DRAFT',
       submitted_revision = NULL,
       submitted_by = NULL,
       current_submission_id = NULL,
       approved_revision = NULL,
       approved_review_id = NULL,
       etag = format(
           '"annotation:%s:%s:%s"',
           task.task_id,
           imported.latest_revision,
           task.state_version + imported.imported_count
       ),
       updated_at = greatest(task.updated_at, imported.latest_created_at)
  FROM imported
 WHERE task.task_id = imported.target_task_id;

-- A redacted migration fact is written in the same transaction as every
-- imported revision.  The raw EDL remains only in the scoped annotation row.
INSERT INTO core.audit_events (
    audit_id, organization_id, project_id, region_code, actor_id, action,
    resource_type, resource_id, request_id, details, occurred_at
)
SELECT
    (
        substr(identity.hash, 1, 8) || '-' || substr(identity.hash, 9, 4) || '-' ||
        substr(identity.hash, 13, 4) || '-' || substr(identity.hash, 17, 4) || '-' ||
        substr(identity.hash, 21, 12)
    )::uuid,
    plan.organization_id,
    plan.project_id,
    plan.region_code,
    'legacy-cleaning-migration',
    'annotation.legacy_cleaning.imported',
    'annotation_revision',
    plan.target_task_id || ':' || plan.target_revision::text,
    'migration:annotation/0008_scoped_legacy_cleaning_import',
    jsonb_build_object(
        'source_revision', plan.source_revision,
        'target_revision', plan.target_revision,
        'operation_count', jsonb_array_length(plan.operations_document),
        'migration_mode', 'PRESERVED_NANOSECOND_EDL'
    ),
    now()
FROM hc_legacy_cleaning_import_plan AS plan
CROSS JOIN LATERAL (
    SELECT md5(concat_ws(
        E'\x1f', 'annotation-legacy-cleaning-import-v1', plan.organization_id,
        plan.project_id, plan.region_code, plan.draft_id, plan.source_revision::text
    )) AS hash
) AS identity
ON CONFLICT (audit_id) DO NOTHING;

