-- BR01 / PD-BR-07: immutable, exact rollout-to-publication region lineage.
-- Region is resolved only by an exact (project_id, rollout_id) ingest relation. No project
-- default or request-region fallback is permitted. Reapplying this migration is safe.

CREATE TABLE IF NOT EXISTS publishing.rollout_publication_lineage (
    project_id text NOT NULL,
    region_code text NOT NULL,
    rollout_id text NOT NULL,
    dataset_id text NOT NULL,
    dataset_version text NOT NULL,
    base_lance_version text NOT NULL,
    publication_identity char(64) NOT NULL
        CHECK (publication_identity ~ '^[0-9a-f]{64}$'),
    published_at timestamptz NOT NULL,
    lineage_source text NOT NULL CHECK (lineage_source IN ('FORWARD', 'BACKFILL')),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, dataset_id, dataset_version, rollout_id),
    UNIQUE (project_id, publication_identity, rollout_id),
    FOREIGN KEY (project_id, dataset_id, dataset_version)
        REFERENCES publishing.dataset_versions(project_id, dataset_id, dataset_version),
    FOREIGN KEY (project_id, rollout_id)
        REFERENCES ingest.rollouts(project_id, rollout_id)
);

CREATE INDEX IF NOT EXISTS rollout_publication_lineage_scope_published_idx
ON publishing.rollout_publication_lineage (
    project_id,
    region_code,
    published_at DESC,
    publication_identity DESC,
    rollout_id DESC
)
INCLUDE (dataset_id, dataset_version, base_lance_version);

-- Deterministic, reentrant history repair. Malformed manifests and rollout IDs that do not
-- resolve through the exact ingest relation remain absent and are reported as unresolved by
-- the summary function below. Disabling row_security here makes an under-privileged migration
-- fail instead of silently treating every FORCE-RLS rollout as unresolvable.
SET row_security = off;

INSERT INTO publishing.rollout_publication_lineage (
    project_id,
    region_code,
    rollout_id,
    dataset_id,
    dataset_version,
    base_lance_version,
    publication_identity,
    published_at,
    lineage_source
)
SELECT DISTINCT
    version.project_id,
    rollout.region_code,
    manifest_rollout.rollout_id,
    version.dataset_id,
    version.dataset_version,
    version.base_lance_version,
    version.content_hash,
    version.created_at,
    'BACKFILL'
FROM publishing.dataset_versions version
CROSS JOIN LATERAL jsonb_to_recordset(
    CASE
        WHEN jsonb_typeof(version.manifest_json -> 'rollouts') = 'array'
        THEN version.manifest_json -> 'rollouts'
        ELSE '[]'::jsonb
    END
) AS manifest_rollout(rollout_id text)
JOIN ingest.rollouts rollout
  ON rollout.project_id = version.project_id
 AND rollout.rollout_id = manifest_rollout.rollout_id
WHERE manifest_rollout.rollout_id IS NOT NULL
  AND manifest_rollout.rollout_id <> ''
ON CONFLICT (project_id, dataset_id, dataset_version, rollout_id) DO NOTHING;

RESET row_security;

-- The publication HTTP contract is project-scoped, while rollouts are region-scoped. This
-- narrowly scoped function resolves the persisted rollout facts and inserts every exact region
-- in the same transaction as dataset_versions. row_security=off fails closed unless the
-- migration owner is explicitly allowed to perform this trusted cross-region derivation.
CREATE OR REPLACE FUNCTION publishing.materialize_rollout_publication_lineage(
    requested_project_id text,
    requested_dataset_id text,
    requested_dataset_version text
) RETURNS TABLE(expected_count bigint, resolved_count bigint)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, publishing, ingest
SET row_security = off
AS $function$
DECLARE
    selected_project_id text := NULLIF(current_setting('app.project_id', true), '');
    selected_subject_id text := NULLIF(current_setting('app.subject_id', true), '');
BEGIN
    IF selected_project_id IS DISTINCT FROM requested_project_id
       OR selected_subject_id IS NULL THEN
        RAISE EXCEPTION 'publication lineage scope denied'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    INSERT INTO publishing.rollout_publication_lineage (
        project_id,
        region_code,
        rollout_id,
        dataset_id,
        dataset_version,
        base_lance_version,
        publication_identity,
        published_at,
        lineage_source
    )
    SELECT DISTINCT
        version.project_id,
        rollout.region_code,
        manifest_rollout.rollout_id,
        version.dataset_id,
        version.dataset_version,
        version.base_lance_version,
        version.content_hash,
        version.created_at,
        'FORWARD'
    FROM publishing.dataset_versions version
    CROSS JOIN LATERAL jsonb_to_recordset(
        CASE
            WHEN jsonb_typeof(version.manifest_json -> 'rollouts') = 'array'
            THEN version.manifest_json -> 'rollouts'
            ELSE '[]'::jsonb
        END
    ) AS manifest_rollout(rollout_id text)
    JOIN ingest.rollouts rollout
      ON rollout.project_id = version.project_id
     AND rollout.rollout_id = manifest_rollout.rollout_id
    WHERE version.project_id = requested_project_id
      AND version.dataset_id = requested_dataset_id
      AND version.dataset_version = requested_dataset_version
      AND manifest_rollout.rollout_id IS NOT NULL
      AND manifest_rollout.rollout_id <> ''
    ON CONFLICT (project_id, dataset_id, dataset_version, rollout_id) DO NOTHING;

    RETURN QUERY
    WITH expected AS (
        SELECT DISTINCT manifest_rollout.rollout_id
        FROM publishing.dataset_versions version
        CROSS JOIN LATERAL jsonb_to_recordset(
            CASE
                WHEN jsonb_typeof(version.manifest_json -> 'rollouts') = 'array'
                THEN version.manifest_json -> 'rollouts'
                ELSE '[]'::jsonb
            END
        ) AS manifest_rollout(rollout_id text)
        WHERE version.project_id = requested_project_id
          AND version.dataset_id = requested_dataset_id
          AND version.dataset_version = requested_dataset_version
          AND manifest_rollout.rollout_id IS NOT NULL
          AND manifest_rollout.rollout_id <> ''
    )
    SELECT
        (SELECT count(*) FROM expected),
        (
            SELECT count(*)
            FROM publishing.rollout_publication_lineage lineage
            WHERE lineage.project_id = requested_project_id
              AND lineage.dataset_id = requested_dataset_id
              AND lineage.dataset_version = requested_dataset_version
        );
END
$function$;

-- Dashboard reads are always one principal + project + region + [from,to). Known counts use
-- only exact region lineage. Unresolved history is a conservative project/range completeness
-- signal and is never added to a region count.
CREATE OR REPLACE FUNCTION publishing.dashboard_publication_lineage_summary(
    requested_principal_id text,
    requested_project_id text,
    requested_region_code text,
    range_start timestamptz,
    range_end timestamptz
) RETURNS TABLE(
    lineage_count bigint,
    publication_count bigint,
    unresolved_history_count bigint,
    latest_published_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, publishing
SET row_security = off
AS $function$
DECLARE
    selected_project_id text := NULLIF(current_setting('app.project_id', true), '');
    selected_region_code text := NULLIF(current_setting('app.region_code', true), '');
    selected_subject_id text := NULLIF(current_setting('app.subject_id', true), '');
BEGIN
    IF selected_project_id IS DISTINCT FROM requested_project_id
       OR selected_region_code IS DISTINCT FROM requested_region_code
       OR selected_subject_id IS DISTINCT FROM requested_principal_id
       OR range_start >= range_end THEN
        RAISE EXCEPTION 'dashboard publication lineage scope denied'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    RETURN QUERY
    WITH expected AS (
        SELECT DISTINCT
            version.project_id,
            version.dataset_id,
            version.dataset_version,
            manifest_rollout.rollout_id
        FROM publishing.dataset_versions version
        CROSS JOIN LATERAL jsonb_to_recordset(
            CASE
                WHEN jsonb_typeof(version.manifest_json -> 'rollouts') = 'array'
                THEN version.manifest_json -> 'rollouts'
                ELSE '[]'::jsonb
            END
        ) AS manifest_rollout(rollout_id text)
        WHERE version.project_id = requested_project_id
          AND version.created_at >= range_start
          AND version.created_at < range_end
          AND manifest_rollout.rollout_id IS NOT NULL
          AND manifest_rollout.rollout_id <> ''
    ),
    known AS (
        SELECT lineage.*
        FROM publishing.rollout_publication_lineage lineage
        WHERE lineage.project_id = requested_project_id
          AND lineage.region_code = requested_region_code
          AND lineage.published_at >= range_start
          AND lineage.published_at < range_end
    )
    SELECT
        (SELECT count(*) FROM known),
        (SELECT count(DISTINCT publication_identity) FROM known),
        (
            SELECT count(*)
            FROM expected fact
            WHERE NOT EXISTS (
                SELECT 1
                FROM publishing.rollout_publication_lineage lineage
                WHERE lineage.project_id = fact.project_id
                  AND lineage.dataset_id = fact.dataset_id
                  AND lineage.dataset_version = fact.dataset_version
                  AND lineage.rollout_id = fact.rollout_id
            )
        ),
        (SELECT max(published_at) FROM known);
END
$function$;

DO $block$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger
        WHERE tgname = 'rollout_publication_lineage_immutable'
          AND tgrelid = 'publishing.rollout_publication_lineage'::regclass
    ) THEN
        CREATE TRIGGER rollout_publication_lineage_immutable
        BEFORE UPDATE OR DELETE ON publishing.rollout_publication_lineage
        FOR EACH ROW EXECUTE FUNCTION publishing.reject_immutable_change();
    END IF;
END
$block$;

DO $block$
BEGIN
    IF to_regprocedure('core.apply_project_rls(regclass)') IS NOT NULL THEN
        PERFORM core.apply_project_rls('publishing.rollout_publication_lineage'::regclass);
    ELSE
        ALTER TABLE publishing.rollout_publication_lineage ENABLE ROW LEVEL SECURITY;
        ALTER TABLE publishing.rollout_publication_lineage FORCE ROW LEVEL SECURITY;
    END IF;
END
$block$;
