-- P11 server-owned CleaningDraft workbench.  P09's source handoff table stays
-- immutable; this migration adds an append-only EDL/preview/commit history and
-- a distinct row type for P07 review-return successors.

CREATE TABLE IF NOT EXISTS manual_cleaning.cleaning_workbench_drafts (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    draft_id text NOT NULL,
    origin_type text NOT NULL,
    source_issue_id text,
    dataset_id text NOT NULL,
    base_version_id text NOT NULL,
    episode_id text NOT NULL,
    base_revision_id text NOT NULL,
    selected_stream_id text,
    selected_channel_path text,
    start_ns numeric(30, 0),
    end_ns numeric(30, 0),
    schema_snapshot_id text NOT NULL,
    robot_model_version_id text,
    calibration_set_id text,
    supersedes_draft_id text,
    returned_from_version_id text,
    returned_from_review_decision_id text,
    status text NOT NULL,
    workbench_version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, draft_id),
    FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id),
    FOREIGN KEY (organization_id, project_id, region_code, dataset_id, base_version_id)
        REFERENCES dataset_registry.dataset_versions (
            organization_id, project_id, region_code, dataset_id, version_id
        ),
    CHECK (draft_id ~ '^draft_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (origin_type IN ('ISSUE_DERIVED', 'REVIEW_RETURN')),
    CHECK (status IN ('EDITING', 'COMMITTED')),
    CHECK (workbench_version > 0),
    CHECK (schema_snapshot_id <> ''),
    CHECK (selected_channel_path IS NULL OR length(selected_channel_path) <= 512),
    CHECK (updated_at >= created_at),
    CHECK (
        (
            origin_type = 'ISSUE_DERIVED'
            AND source_issue_id IS NOT NULL
            AND selected_stream_id IS NOT NULL
            AND start_ns IS NOT NULL
            AND end_ns IS NOT NULL
            AND end_ns > start_ns
            AND supersedes_draft_id IS NULL
            AND returned_from_version_id IS NULL
            AND returned_from_review_decision_id IS NULL
        )
        OR (
            origin_type = 'REVIEW_RETURN'
            AND source_issue_id IS NULL
            AND selected_stream_id IS NOT NULL
            AND start_ns IS NOT NULL
            AND end_ns IS NOT NULL
            AND end_ns > start_ns
            AND supersedes_draft_id IS NOT NULL
            AND returned_from_version_id IS NOT NULL
            AND returned_from_review_decision_id IS NOT NULL
            AND supersedes_draft_id <> draft_id
        )
    )
);

CREATE INDEX IF NOT EXISTS cleaning_workbench_drafts_page_idx
ON manual_cleaning.cleaning_workbench_drafts (
    organization_id, project_id, region_code, updated_at DESC, draft_id DESC
);

CREATE INDEX IF NOT EXISTS cleaning_workbench_drafts_return_idx
ON manual_cleaning.cleaning_workbench_drafts (
    organization_id, project_id, region_code, supersedes_draft_id
)
WHERE origin_type = 'REVIEW_RETURN';

CREATE TABLE IF NOT EXISTS manual_cleaning.cleaning_draft_edl_revisions (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    draft_id text NOT NULL,
    edl_revision bigint NOT NULL,
    client_mutation_id text NOT NULL,
    operation_hash text NOT NULL,
    operations_document jsonb NOT NULL,
    actor_id text NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, draft_id, edl_revision),
    FOREIGN KEY (organization_id, project_id, region_code, draft_id)
        REFERENCES manual_cleaning.cleaning_workbench_drafts (
            organization_id, project_id, region_code, draft_id
        ),
    CHECK (edl_revision >= 0),
    CHECK (client_mutation_id <> ''),
    CHECK (operation_hash ~ '^sha256:[a-f0-9]{64}$'),
    CHECK (jsonb_typeof(operations_document) = 'array'),
    CHECK (actor_id <> '')
);

CREATE INDEX IF NOT EXISTS cleaning_draft_edl_revisions_head_idx
ON manual_cleaning.cleaning_draft_edl_revisions (
    organization_id, project_id, region_code, draft_id, edl_revision DESC
);

CREATE UNIQUE INDEX IF NOT EXISTS cleaning_draft_edl_revisions_mutation_uq
ON manual_cleaning.cleaning_draft_edl_revisions (
    organization_id, project_id, region_code, draft_id, client_mutation_id
);

CREATE TABLE IF NOT EXISTS manual_cleaning.cleaning_draft_previews (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    preview_id text NOT NULL,
    draft_id text NOT NULL,
    base_revision_id text NOT NULL,
    edl_revision bigint NOT NULL,
    operation_hash text NOT NULL,
    preview_document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz,
    PRIMARY KEY (organization_id, project_id, region_code, preview_id),
    FOREIGN KEY (organization_id, project_id, region_code, draft_id)
        REFERENCES manual_cleaning.cleaning_workbench_drafts (
            organization_id, project_id, region_code, draft_id
        ),
    CHECK (preview_id ~ '^preview_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (base_revision_id ~ '^revision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (edl_revision >= 0),
    CHECK (operation_hash ~ '^sha256:[a-f0-9]{64}$'),
    CHECK (jsonb_typeof(preview_document) = 'object'),
    CHECK (preview_document ->> 'preview_id' = preview_id),
    CHECK (preview_document ->> 'draft_id' = draft_id),
    CHECK (preview_document ->> 'operation_hash' = operation_hash),
    CHECK (NOT (preview_document ? 'credential')),
    CHECK (NOT (preview_document ? 'secret')),
    CHECK (NOT (preview_document ? 'token')),
    CHECK (NOT (preview_document ? 'object_locator')),
    CHECK (NOT (preview_document ? 'object_path'))
);

CREATE INDEX IF NOT EXISTS cleaning_draft_previews_head_idx
ON manual_cleaning.cleaning_draft_previews (
    organization_id, project_id, region_code, draft_id, created_at DESC, preview_id DESC
);

CREATE TABLE IF NOT EXISTS manual_cleaning.cleaning_workbench_commits (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    commit_id text NOT NULL,
    draft_id text NOT NULL,
    preview_id text NOT NULL,
    job_id text NOT NULL,
    output_version_id text,
    operation_hash text,
    status text NOT NULL,
    materialization_status text NOT NULL,
    commit_document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    completed_at timestamptz,
    PRIMARY KEY (organization_id, project_id, region_code, commit_id),
    FOREIGN KEY (organization_id, project_id, region_code, draft_id)
        REFERENCES manual_cleaning.cleaning_workbench_drafts (
            organization_id, project_id, region_code, draft_id
        ),
    CHECK (commit_id ~ '^commit_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')),
    CHECK (materialization_status IN ('NOT_STARTED', 'RUNNING', 'SUCCEEDED', 'FAILED')),
    CHECK ((status = 'SUCCEEDED') = (output_version_id IS NOT NULL)),
    CHECK ((status = 'SUCCEEDED') = (operation_hash IS NOT NULL)),
    CHECK ((status = 'SUCCEEDED') = (completed_at IS NOT NULL)),
    CHECK (operation_hash IS NULL OR operation_hash ~ '^sha256:[a-f0-9]{64}$'),
    CHECK (jsonb_typeof(commit_document) = 'object'),
    CHECK (commit_document ->> 'commit_id' = commit_id),
    CHECK (commit_document ->> 'draft_id' = draft_id),
    CHECK (NOT (commit_document ? 'credential')),
    CHECK (NOT (commit_document ? 'secret')),
    CHECK (NOT (commit_document ? 'token')),
    CHECK (NOT (commit_document ? 'object_locator')),
    CHECK (NOT (commit_document ? 'object_path'))
);

CREATE UNIQUE INDEX IF NOT EXISTS cleaning_workbench_commits_output_version_uq
ON manual_cleaning.cleaning_workbench_commits (
    organization_id, project_id, region_code, output_version_id
)
WHERE output_version_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS cleaning_workbench_commits_head_idx
ON manual_cleaning.cleaning_workbench_commits (
    organization_id, project_id, region_code, draft_id, created_at DESC, commit_id DESC
);

CREATE TABLE IF NOT EXISTS manual_cleaning.cleaning_workbench_jobs (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    job_id text NOT NULL,
    draft_id text NOT NULL,
    job_kind text NOT NULL,
    job_document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, job_id),
    FOREIGN KEY (organization_id, project_id, region_code, draft_id)
        REFERENCES manual_cleaning.cleaning_workbench_drafts (
            organization_id, project_id, region_code, draft_id
        ),
    CHECK (job_kind IN ('CLEANING_PREVIEW', 'CLEANING_COMMIT')),
    CHECK (jsonb_typeof(job_document) = 'object'),
    CHECK (job_document ->> 'job_id' = job_id),
    CHECK (job_document ->> 'kind' = job_kind),
    CHECK (NOT (job_document ? 'credential')),
    CHECK (NOT (job_document ? 'secret')),
    CHECK (NOT (job_document ? 'token')),
    CHECK (NOT (job_document ? 'object_locator')),
    CHECK (NOT (job_document ? 'object_path'))
);

CREATE TABLE IF NOT EXISTS manual_cleaning.cleaning_commit_output_revisions (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    commit_id text NOT NULL,
    revision_id text NOT NULL,
    source_revision_id text NOT NULL,
    episode_id text NOT NULL,
    ordinal bigint NOT NULL,
    member_mode text NOT NULL,
    episode_stream_ids jsonb NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, commit_id, revision_id),
    FOREIGN KEY (organization_id, project_id, region_code, commit_id)
        REFERENCES manual_cleaning.cleaning_workbench_commits (
            organization_id, project_id, region_code, commit_id
        ),
    CHECK (revision_id ~ '^revision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (source_revision_id ~ '^revision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (episode_id ~ '^episode_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (ordinal >= 0),
    CHECK (member_mode IN ('EDIT_RESULT', 'CARRY_FORWARD')),
    CHECK (jsonb_typeof(episode_stream_ids) = 'array'),
    CHECK (jsonb_array_length(episode_stream_ids) > 0)
);

-- P09 drafts are initialized atomically at insertion time.  This means a
-- bootstrap is always a read and never creates a hidden browser-owned draft.
CREATE OR REPLACE FUNCTION manual_cleaning.seed_issue_workbench_draft()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    source_issue record;
BEGIN
    SELECT schema_snapshot_id, robot_model_version_id, calibration_set_id
      INTO source_issue
      FROM manual_cleaning.manual_issues
     WHERE organization_id = NEW.organization_id
       AND project_id = NEW.project_id
       AND region_code = NEW.region_code
       AND manual_issue_id = NEW.source_issue_id;
    IF source_issue IS NULL THEN
        RAISE EXCEPTION 'CleaningDraft source ManualIssue is missing' USING ERRCODE = '23503';
    END IF;

    INSERT INTO manual_cleaning.cleaning_workbench_drafts (
        organization_id, project_id, region_code, draft_id, origin_type, source_issue_id,
        dataset_id, base_version_id, episode_id, base_revision_id, selected_stream_id,
        selected_channel_path, start_ns, end_ns, schema_snapshot_id,
        robot_model_version_id, calibration_set_id, status, workbench_version,
        created_at, updated_at
    ) VALUES (
        NEW.organization_id, NEW.project_id, NEW.region_code, NEW.draft_id,
        'ISSUE_DERIVED', NEW.source_issue_id, NEW.dataset_id, NEW.base_version_id,
        NEW.episode_id, NEW.base_revision_id, NEW.selected_stream_id,
        NEW.selected_channel_path, NEW.start_ns, NEW.end_ns, source_issue.schema_snapshot_id,
        source_issue.robot_model_version_id, source_issue.calibration_set_id, NEW.status, 1,
        NEW.created_at, NEW.updated_at
    ) ON CONFLICT (organization_id, project_id, region_code, draft_id) DO NOTHING;

    INSERT INTO manual_cleaning.cleaning_draft_edl_revisions (
        organization_id, project_id, region_code, draft_id, edl_revision, client_mutation_id,
        operation_hash, operations_document, actor_id, created_at
    ) VALUES (
        NEW.organization_id, NEW.project_id, NEW.region_code, NEW.draft_id, 0,
        'system:manual-issue-handoff',
        'sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
        '[]'::jsonb, 'system:manual-issue-handoff', NEW.created_at
    ) ON CONFLICT (organization_id, project_id, region_code, draft_id, edl_revision)
      DO NOTHING;
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS cleaning_drafts_seed_workbench ON manual_cleaning.cleaning_drafts;
CREATE TRIGGER cleaning_drafts_seed_workbench
AFTER INSERT ON manual_cleaning.cleaning_drafts
FOR EACH ROW EXECUTE FUNCTION manual_cleaning.seed_issue_workbench_draft();

-- Backfill the same durable baseline for rows created before P11 was deployed.
INSERT INTO manual_cleaning.cleaning_workbench_drafts (
    organization_id, project_id, region_code, draft_id, origin_type, source_issue_id,
    dataset_id, base_version_id, episode_id, base_revision_id, selected_stream_id,
    selected_channel_path, start_ns, end_ns, schema_snapshot_id,
    robot_model_version_id, calibration_set_id, status, workbench_version,
    created_at, updated_at
)
SELECT draft.organization_id, draft.project_id, draft.region_code, draft.draft_id,
       'ISSUE_DERIVED', draft.source_issue_id, draft.dataset_id, draft.base_version_id,
       draft.episode_id, draft.base_revision_id, draft.selected_stream_id,
       draft.selected_channel_path, draft.start_ns, draft.end_ns, issue.schema_snapshot_id,
       issue.robot_model_version_id, issue.calibration_set_id, draft.status, 1,
       draft.created_at, draft.updated_at
  FROM manual_cleaning.cleaning_drafts AS draft
  JOIN manual_cleaning.manual_issues AS issue
    ON issue.organization_id = draft.organization_id
   AND issue.project_id = draft.project_id
   AND issue.region_code = draft.region_code
   AND issue.manual_issue_id = draft.source_issue_id
ON CONFLICT (organization_id, project_id, region_code, draft_id) DO NOTHING;

INSERT INTO manual_cleaning.cleaning_draft_edl_revisions (
    organization_id, project_id, region_code, draft_id, edl_revision, client_mutation_id,
    operation_hash, operations_document, actor_id, created_at
)
SELECT draft.organization_id, draft.project_id, draft.region_code, draft.draft_id, 0,
       'system:p11-backfill',
       'sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
       '[]'::jsonb, 'system:p11-backfill', draft.created_at
  FROM manual_cleaning.cleaning_workbench_drafts AS draft
ON CONFLICT (organization_id, project_id, region_code, draft_id, edl_revision) DO NOTHING;

-- P07's RETURNED decision creates exactly one editable successor in the same
-- database transaction.  The service rejects multi-revision returns before
-- this trigger; the trigger repeats that invariant as a durable backstop.
CREATE OR REPLACE FUNCTION manual_cleaning.seed_review_return_workbench_draft()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    target record;
    selected_stream record;
    schema_id text;
    revision_count integer;
BEGIN
    SELECT COUNT(DISTINCT finding.output_revision_id)
      INTO revision_count
      FROM dataset_registry.dataset_version_review_findings AS finding
     WHERE finding.organization_id = NEW.organization_id
       AND finding.project_id = NEW.project_id
       AND finding.region_code = NEW.region_code
       AND finding.dataset_id = NEW.dataset_id
       AND finding.version_id = NEW.version_id
       AND finding.review_decision_id = NEW.review_decision_id;

    IF revision_count <> 1 THEN
        RAISE EXCEPTION 'Review Return requires exactly one editable base revision'
            USING ERRCODE = '23514';
    END IF;

    SELECT finding.output_revision_id,
           MIN(finding.episode_stream_id) AS episode_stream_id,
           MIN((finding.finding_document ->> 'start_ns')::numeric) AS start_ns,
           MAX((finding.finding_document ->> 'end_ns')::numeric) AS end_ns,
           revision.episode_id,
           revision.revision_document
      INTO target
      FROM dataset_registry.dataset_version_review_findings AS finding
      JOIN dataset_registry.dataset_version_episode_revisions AS revision
        ON revision.organization_id = finding.organization_id
       AND revision.project_id = finding.project_id
       AND revision.region_code = finding.region_code
       AND revision.dataset_id = finding.dataset_id
       AND revision.version_id = finding.version_id
       AND revision.revision_id = finding.output_revision_id
     WHERE finding.organization_id = NEW.organization_id
       AND finding.project_id = NEW.project_id
       AND finding.region_code = NEW.region_code
       AND finding.dataset_id = NEW.dataset_id
       AND finding.version_id = NEW.version_id
       AND finding.review_decision_id = NEW.review_decision_id
     GROUP BY finding.output_revision_id, revision.episode_id, revision.revision_document;

    IF target IS NULL THEN
        RAISE EXCEPTION 'Review Return requires exactly one editable base revision'
            USING ERRCODE = '23514';
    END IF;

    SELECT stream.value ->> 'channel_path' AS channel_path
      INTO selected_stream
      FROM jsonb_array_elements(target.revision_document -> 'streams') AS stream(value)
     WHERE stream.value ->> 'episode_stream_id' = target.episode_stream_id
     LIMIT 1;
    IF selected_stream IS NULL THEN
        RAISE EXCEPTION 'Review Return finding stream is not in its revision'
            USING ERRCODE = '23503';
    END IF;

    SELECT schema_snapshot_id
      INTO schema_id
      FROM dataset_registry.dataset_version_schema_details
     WHERE organization_id = NEW.organization_id
       AND project_id = NEW.project_id
       AND region_code = NEW.region_code
       AND dataset_id = NEW.dataset_id
       AND version_id = NEW.version_id;
    IF schema_id IS NULL THEN
        RAISE EXCEPTION 'Review Return source schema is missing' USING ERRCODE = '23503';
    END IF;

    INSERT INTO manual_cleaning.cleaning_workbench_drafts (
        organization_id, project_id, region_code, draft_id, origin_type,
        dataset_id, base_version_id, episode_id, base_revision_id, selected_stream_id,
        selected_channel_path, start_ns, end_ns, schema_snapshot_id,
        supersedes_draft_id, returned_from_version_id, returned_from_review_decision_id,
        status, workbench_version, created_at, updated_at
    ) VALUES (
        NEW.organization_id, NEW.project_id, NEW.region_code, NEW.successor_draft_id,
        'REVIEW_RETURN', NEW.dataset_id, NEW.version_id, target.episode_id,
        target.output_revision_id, target.episode_stream_id, selected_stream.channel_path,
        target.start_ns, target.end_ns, schema_id, NEW.supersedes_draft_id,
        NEW.version_id, NEW.review_decision_id, 'EDITING', 1, NEW.created_at, NEW.created_at
    ) ON CONFLICT (organization_id, project_id, region_code, draft_id) DO NOTHING;

    INSERT INTO manual_cleaning.cleaning_draft_edl_revisions (
        organization_id, project_id, region_code, draft_id, edl_revision, client_mutation_id,
        operation_hash, operations_document, actor_id, created_at
    ) VALUES (
        NEW.organization_id, NEW.project_id, NEW.region_code, NEW.successor_draft_id, 0,
        'system:review-return',
        'sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
        '[]'::jsonb, 'system:review-return', NEW.created_at
    ) ON CONFLICT (organization_id, project_id, region_code, draft_id, edl_revision)
      DO NOTHING;
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS dataset_version_successors_seed_workbench
ON dataset_registry.dataset_version_successor_drafts;
CREATE TRIGGER dataset_version_successors_seed_workbench
AFTER INSERT ON dataset_registry.dataset_version_successor_drafts
FOR EACH ROW EXECUTE FUNCTION manual_cleaning.seed_review_return_workbench_draft();

CREATE OR REPLACE FUNCTION manual_cleaning.reject_cleaning_workbench_history_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'Cleaning workbench history is append-only' USING ERRCODE = '55000';
END;
$function$;

CREATE OR REPLACE FUNCTION manual_cleaning.protect_cleaning_workbench_draft_source()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'CleaningDraft source facts are immutable' USING ERRCODE = '55000';
    END IF;
    IF ROW(
        NEW.organization_id, NEW.project_id, NEW.region_code, NEW.draft_id,
        NEW.origin_type, NEW.source_issue_id, NEW.dataset_id, NEW.base_version_id,
        NEW.episode_id, NEW.base_revision_id, NEW.selected_stream_id,
        NEW.selected_channel_path, NEW.start_ns, NEW.end_ns, NEW.schema_snapshot_id,
        NEW.robot_model_version_id, NEW.calibration_set_id, NEW.supersedes_draft_id,
        NEW.returned_from_version_id, NEW.returned_from_review_decision_id, NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.organization_id, OLD.project_id, OLD.region_code, OLD.draft_id,
        OLD.origin_type, OLD.source_issue_id, OLD.dataset_id, OLD.base_version_id,
        OLD.episode_id, OLD.base_revision_id, OLD.selected_stream_id,
        OLD.selected_channel_path, OLD.start_ns, OLD.end_ns, OLD.schema_snapshot_id,
        OLD.robot_model_version_id, OLD.calibration_set_id, OLD.supersedes_draft_id,
        OLD.returned_from_version_id, OLD.returned_from_review_decision_id, OLD.created_at
    ) THEN
        RAISE EXCEPTION 'CleaningDraft source facts are immutable' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS cleaning_workbench_draft_source_immutable
ON manual_cleaning.cleaning_workbench_drafts;
CREATE TRIGGER cleaning_workbench_draft_source_immutable
BEFORE UPDATE OR DELETE ON manual_cleaning.cleaning_workbench_drafts
FOR EACH ROW EXECUTE FUNCTION manual_cleaning.protect_cleaning_workbench_draft_source();

DROP TRIGGER IF EXISTS cleaning_draft_edl_revisions_append_only
ON manual_cleaning.cleaning_draft_edl_revisions;
CREATE TRIGGER cleaning_draft_edl_revisions_append_only
BEFORE UPDATE OR DELETE ON manual_cleaning.cleaning_draft_edl_revisions
FOR EACH ROW EXECUTE FUNCTION manual_cleaning.reject_cleaning_workbench_history_mutation();

DROP TRIGGER IF EXISTS cleaning_draft_previews_append_only
ON manual_cleaning.cleaning_draft_previews;
CREATE TRIGGER cleaning_draft_previews_append_only
BEFORE UPDATE OR DELETE ON manual_cleaning.cleaning_draft_previews
FOR EACH ROW EXECUTE FUNCTION manual_cleaning.reject_cleaning_workbench_history_mutation();

DROP TRIGGER IF EXISTS cleaning_workbench_commits_append_only
ON manual_cleaning.cleaning_workbench_commits;
CREATE TRIGGER cleaning_workbench_commits_append_only
BEFORE UPDATE OR DELETE ON manual_cleaning.cleaning_workbench_commits
FOR EACH ROW EXECUTE FUNCTION manual_cleaning.reject_cleaning_workbench_history_mutation();

DROP TRIGGER IF EXISTS cleaning_workbench_jobs_append_only
ON manual_cleaning.cleaning_workbench_jobs;
CREATE TRIGGER cleaning_workbench_jobs_append_only
BEFORE UPDATE OR DELETE ON manual_cleaning.cleaning_workbench_jobs
FOR EACH ROW EXECUTE FUNCTION manual_cleaning.reject_cleaning_workbench_history_mutation();

DROP TRIGGER IF EXISTS cleaning_commit_output_revisions_append_only
ON manual_cleaning.cleaning_commit_output_revisions;
CREATE TRIGGER cleaning_commit_output_revisions_append_only
BEFORE UPDATE OR DELETE ON manual_cleaning.cleaning_commit_output_revisions
FOR EACH ROW EXECUTE FUNCTION manual_cleaning.reject_cleaning_workbench_history_mutation();

SELECT core.apply_project_rls('manual_cleaning.cleaning_workbench_drafts'::regclass);
SELECT core.apply_project_rls('manual_cleaning.cleaning_draft_edl_revisions'::regclass);
SELECT core.apply_project_rls('manual_cleaning.cleaning_draft_previews'::regclass);
SELECT core.apply_project_rls('manual_cleaning.cleaning_workbench_commits'::regclass);
SELECT core.apply_project_rls('manual_cleaning.cleaning_workbench_jobs'::regclass);
SELECT core.apply_project_rls('manual_cleaning.cleaning_commit_output_revisions'::regclass);
