-- P09 ManualIssue facts and the bounded Issue-to-Draft handoff.  A
-- ManualIssue is not a P07 ReviewFinding: it has its own identity, lifecycle,
-- capabilities, audit actions, and source-bound persistence.

CREATE SCHEMA IF NOT EXISTS manual_cleaning;

CREATE TABLE IF NOT EXISTS manual_cleaning.manual_issues (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    manual_issue_id text NOT NULL,
    dataset_id text NOT NULL,
    origin_dataset_version_id text NOT NULL,
    episode_id text NOT NULL,
    episode_revision_id text NOT NULL,
    episode_stream_id text NOT NULL,
    schema_snapshot_id text NOT NULL,
    robot_model_version_id text,
    calibration_set_id text,
    start_ns numeric(30, 0) NOT NULL,
    end_ns numeric(30, 0) NOT NULL,
    issue_type text NOT NULL,
    severity text NOT NULL,
    status text NOT NULL,
    note text NOT NULL,
    assignee_id text,
    assignee_display_name text,
    version bigint NOT NULL,
    resolution_version_id text,
    resolution_note text,
    resolved_at timestamptz,
    resolved_by_id text,
    resolved_by_display_name text,
    issue_document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, manual_issue_id),
    FOREIGN KEY (organization_id, project_id)
        REFERENCES registry.organization_projects (organization_id, project_id),
    FOREIGN KEY (
        organization_id, project_id, region_code, dataset_id, origin_dataset_version_id
    ) REFERENCES dataset_registry.dataset_versions (
        organization_id, project_id, region_code, dataset_id, version_id
    ),
    FOREIGN KEY (
        organization_id, project_id, region_code, dataset_id, origin_dataset_version_id,
        episode_revision_id
    ) REFERENCES dataset_registry.dataset_version_episode_revisions (
        organization_id, project_id, region_code, dataset_id, version_id, revision_id
    ),
    CHECK (manual_issue_id ~ '^issue_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (episode_id ~ '^episode_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (episode_revision_id ~ '^revision_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (episode_stream_id <> ''),
    CHECK (schema_snapshot_id <> ''),
    CHECK (start_ns >= 0),
    CHECK (end_ns > start_ns),
    CHECK (issue_type IN (
        'POSE_JITTER', 'TIMESTAMP_DRIFT', 'MISSING_FRAME', 'STREAM_GAP',
        'CALIBRATION_MISMATCH', 'INVALID_MASK', 'OTHER'
    )),
    CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    CHECK (status IN ('OPEN', 'IN_PROGRESS', 'RESOLVED')),
    CHECK (length(note) <= 8192),
    CHECK (assignee_id IS NULL OR assignee_display_name IS NOT NULL),
    CHECK (status <> 'IN_PROGRESS' OR assignee_id IS NOT NULL),
    CHECK (version > 0),
    CHECK (
        (status = 'RESOLVED') = (
            resolution_version_id IS NOT NULL
            AND resolution_note IS NOT NULL
            AND resolved_at IS NOT NULL
            AND resolved_by_id IS NOT NULL
            AND resolved_by_display_name IS NOT NULL
        )
    ),
    CHECK (updated_at >= created_at),
    CHECK (jsonb_typeof(issue_document) = 'object'),
    CHECK (issue_document ->> 'id' = manual_issue_id),
    CHECK (issue_document ->> 'dataset_id' = dataset_id),
    CHECK (issue_document ->> 'origin_dataset_version_id' = origin_dataset_version_id),
    CHECK (issue_document ->> 'episode_id' = episode_id),
    CHECK (issue_document ->> 'episode_revision_id' = episode_revision_id),
    CHECK (issue_document ->> 'episode_stream_id' = episode_stream_id),
    CHECK (issue_document ->> 'status' = status),
    CHECK (issue_document ->> 'severity' = severity),
    CHECK (issue_document -> 'scope' ->> 'organization_id' = organization_id),
    CHECK (issue_document -> 'scope' ->> 'project_id' = project_id),
    CHECK (issue_document -> 'scope' ->> 'region_code' = region_code),
    CHECK (NOT (issue_document ? 'credential')),
    CHECK (NOT (issue_document ? 'secret')),
    CHECK (NOT (issue_document ? 'token')),
    CHECK (NOT (issue_document ? 'object_locator')),
    CHECK (NOT (issue_document ? 'object_path'))
);

CREATE INDEX IF NOT EXISTS manual_issues_page_updated_idx
ON manual_cleaning.manual_issues (
    organization_id, project_id, region_code, updated_at DESC, manual_issue_id DESC
);

CREATE INDEX IF NOT EXISTS manual_issues_page_severity_idx
ON manual_cleaning.manual_issues (
    organization_id, project_id, region_code, severity DESC, updated_at DESC, manual_issue_id DESC
);

CREATE INDEX IF NOT EXISTS manual_issues_source_idx
ON manual_cleaning.manual_issues (
    organization_id, project_id, region_code, dataset_id, origin_dataset_version_id, episode_id
);

CREATE INDEX IF NOT EXISTS manual_issues_assignee_idx
ON manual_cleaning.manual_issues (
    organization_id, project_id, region_code, assignee_id, status
);

-- This is a P09-owned creation handoff only.  P10/P11 may add projections and
-- editing fields later, but they must preserve this immutable source context.
CREATE TABLE IF NOT EXISTS manual_cleaning.cleaning_drafts (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    draft_id text NOT NULL,
    source_issue_id text NOT NULL,
    dataset_id text NOT NULL,
    base_version_id text NOT NULL,
    episode_id text NOT NULL,
    base_revision_id text NOT NULL,
    selected_stream_id text NOT NULL,
    selected_channel_path text,
    start_ns numeric(30, 0) NOT NULL,
    end_ns numeric(30, 0) NOT NULL,
    status text NOT NULL,
    draft_document jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, draft_id),
    FOREIGN KEY (organization_id, project_id, region_code, source_issue_id)
        REFERENCES manual_cleaning.manual_issues (
            organization_id, project_id, region_code, manual_issue_id
        ),
    FOREIGN KEY (
        organization_id, project_id, region_code, dataset_id, base_version_id
    ) REFERENCES dataset_registry.dataset_versions (
        organization_id, project_id, region_code, dataset_id, version_id
    ),
    CHECK (draft_id ~ '^draft_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (selected_stream_id <> ''),
    CHECK (selected_channel_path IS NULL OR length(selected_channel_path) <= 512),
    CHECK (start_ns >= 0),
    CHECK (end_ns > start_ns),
    CHECK (status IN ('EDITING', 'COMMITTED')),
    CHECK (updated_at >= created_at),
    CHECK (jsonb_typeof(draft_document) = 'object'),
    CHECK (draft_document ->> 'draft_id' = draft_id),
    CHECK (draft_document ->> 'source_issue_id' = source_issue_id),
    CHECK (draft_document ->> 'dataset_id' = dataset_id),
    CHECK (draft_document ->> 'base_version_id' = base_version_id),
    CHECK (draft_document ->> 'base_revision_id' = base_revision_id),
    CHECK (draft_document ->> 'status' = status),
    CHECK (draft_document -> 'scope' ->> 'organization_id' = organization_id),
    CHECK (draft_document -> 'scope' ->> 'project_id' = project_id),
    CHECK (draft_document -> 'scope' ->> 'region_code' = region_code),
    CHECK (NOT (draft_document ? 'credential')),
    CHECK (NOT (draft_document ? 'secret')),
    CHECK (NOT (draft_document ? 'token')),
    CHECK (NOT (draft_document ? 'object_locator')),
    CHECK (NOT (draft_document ? 'object_path'))
);

CREATE INDEX IF NOT EXISTS cleaning_drafts_issue_idx
ON manual_cleaning.cleaning_drafts (
    organization_id, project_id, region_code, source_issue_id, updated_at DESC, draft_id DESC
);

CREATE TABLE IF NOT EXISTS manual_cleaning.manual_issue_draft_links (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    manual_issue_id text NOT NULL,
    draft_id text NOT NULL,
    linked_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, manual_issue_id, draft_id),
    FOREIGN KEY (organization_id, project_id, region_code, manual_issue_id)
        REFERENCES manual_cleaning.manual_issues (
            organization_id, project_id, region_code, manual_issue_id
        ),
    FOREIGN KEY (organization_id, project_id, region_code, draft_id)
        REFERENCES manual_cleaning.cleaning_drafts (
            organization_id, project_id, region_code, draft_id
        )
);

-- Ancestry is appended by the future commit workflow, never inferred from a
-- browser request.  P09 resolution only reads the recorded closure.
CREATE TABLE IF NOT EXISTS manual_cleaning.cleaning_draft_ancestry (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    root_draft_id text NOT NULL,
    descendant_draft_id text NOT NULL,
    lineage_depth bigint NOT NULL,
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (
        organization_id, project_id, region_code, root_draft_id, descendant_draft_id
    ),
    FOREIGN KEY (organization_id, project_id, region_code, root_draft_id)
        REFERENCES manual_cleaning.cleaning_drafts (
            organization_id, project_id, region_code, draft_id
        ),
    FOREIGN KEY (organization_id, project_id, region_code, descendant_draft_id)
        REFERENCES manual_cleaning.cleaning_drafts (
            organization_id, project_id, region_code, draft_id
        ),
    CHECK (lineage_depth >= 0),
    CHECK ((lineage_depth = 0) = (root_draft_id = descendant_draft_id))
);

CREATE INDEX IF NOT EXISTS cleaning_draft_ancestry_descendant_idx
ON manual_cleaning.cleaning_draft_ancestry (
    organization_id, project_id, region_code, descendant_draft_id, lineage_depth
);

-- P11 will write successful commit facts.  P09 deliberately cannot insert
-- them, which prevents a resolve command from manufacturing a READY output.
CREATE TABLE IF NOT EXISTS manual_cleaning.cleaning_draft_commits (
    organization_id text NOT NULL,
    project_id text NOT NULL,
    region_code text NOT NULL,
    commit_id text NOT NULL,
    draft_id text NOT NULL,
    dataset_id text NOT NULL,
    output_version_id text,
    status text NOT NULL,
    committed_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, project_id, region_code, commit_id),
    FOREIGN KEY (organization_id, project_id, region_code, draft_id)
        REFERENCES manual_cleaning.cleaning_drafts (
            organization_id, project_id, region_code, draft_id
        ),
    FOREIGN KEY (
        organization_id, project_id, region_code, dataset_id, output_version_id
    ) REFERENCES dataset_registry.dataset_versions (
        organization_id, project_id, region_code, dataset_id, version_id
    ),
    CHECK (commit_id ~ '^commit_[A-Za-z0-9][A-Za-z0-9_-]{1,95}$'),
    CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')),
    CHECK ((status = 'SUCCEEDED') = (output_version_id IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS cleaning_draft_commits_output_version_uq
ON manual_cleaning.cleaning_draft_commits (
    organization_id, project_id, region_code, dataset_id, output_version_id
)
WHERE output_version_id IS NOT NULL;

-- Source provenance is fixed when an operator creates an issue.  Lifecycle,
-- assignment, severity, resolution, and their versioned documents may change;
-- the source Version/Revision/Stream/range must never be retargeted in place.
CREATE OR REPLACE FUNCTION manual_cleaning.protect_manual_issue_source()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF ROW(
        NEW.organization_id, NEW.project_id, NEW.region_code, NEW.manual_issue_id,
        NEW.dataset_id, NEW.origin_dataset_version_id, NEW.episode_id,
        NEW.episode_revision_id, NEW.episode_stream_id, NEW.schema_snapshot_id,
        NEW.robot_model_version_id, NEW.calibration_set_id, NEW.start_ns, NEW.end_ns,
        NEW.issue_type, NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.organization_id, OLD.project_id, OLD.region_code, OLD.manual_issue_id,
        OLD.dataset_id, OLD.origin_dataset_version_id, OLD.episode_id,
        OLD.episode_revision_id, OLD.episode_stream_id, OLD.schema_snapshot_id,
        OLD.robot_model_version_id, OLD.calibration_set_id, OLD.start_ns, OLD.end_ns,
        OLD.issue_type, OLD.created_at
    ) THEN
        RAISE EXCEPTION 'ManualIssue source facts are immutable' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS manual_issue_source_immutable ON manual_cleaning.manual_issues;
CREATE TRIGGER manual_issue_source_immutable
BEFORE UPDATE ON manual_cleaning.manual_issues
FOR EACH ROW EXECUTE FUNCTION manual_cleaning.protect_manual_issue_source();

-- P09 creates only the initial handoff.  P10/P11 may advance a Draft, but
-- cannot retarget the source identity/range that P09 made visible to users.
CREATE OR REPLACE FUNCTION manual_cleaning.protect_cleaning_draft_source()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF ROW(
        NEW.organization_id, NEW.project_id, NEW.region_code, NEW.draft_id,
        NEW.source_issue_id, NEW.dataset_id, NEW.base_version_id, NEW.episode_id,
        NEW.base_revision_id, NEW.selected_stream_id, NEW.selected_channel_path,
        NEW.start_ns, NEW.end_ns, NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.organization_id, OLD.project_id, OLD.region_code, OLD.draft_id,
        OLD.source_issue_id, OLD.dataset_id, OLD.base_version_id, OLD.episode_id,
        OLD.base_revision_id, OLD.selected_stream_id, OLD.selected_channel_path,
        OLD.start_ns, OLD.end_ns, OLD.created_at
    ) THEN
        RAISE EXCEPTION 'Cleaning Draft source facts are immutable' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS cleaning_draft_source_immutable ON manual_cleaning.cleaning_drafts;
CREATE TRIGGER cleaning_draft_source_immutable
BEFORE UPDATE ON manual_cleaning.cleaning_drafts
FOR EACH ROW EXECUTE FUNCTION manual_cleaning.protect_cleaning_draft_source();

CREATE OR REPLACE FUNCTION manual_cleaning.reject_manual_issue_draft_link_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'ManualIssue Draft links are append-only' USING ERRCODE = '55000';
END;
$function$;

DROP TRIGGER IF EXISTS manual_issue_draft_links_append_only
ON manual_cleaning.manual_issue_draft_links;
CREATE TRIGGER manual_issue_draft_links_append_only
BEFORE UPDATE OR DELETE ON manual_cleaning.manual_issue_draft_links
FOR EACH ROW EXECUTE FUNCTION manual_cleaning.reject_manual_issue_draft_link_mutation();

CREATE OR REPLACE FUNCTION manual_cleaning.reject_cleaning_draft_ancestry_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'Cleaning Draft ancestry is immutable' USING ERRCODE = '55000';
END;
$function$;

DROP TRIGGER IF EXISTS cleaning_draft_ancestry_append_only
ON manual_cleaning.cleaning_draft_ancestry;
CREATE TRIGGER cleaning_draft_ancestry_append_only
BEFORE UPDATE OR DELETE ON manual_cleaning.cleaning_draft_ancestry
FOR EACH ROW EXECUTE FUNCTION manual_cleaning.reject_cleaning_draft_ancestry_mutation();

SELECT core.apply_project_rls('manual_cleaning.manual_issues'::regclass);
SELECT core.apply_project_rls('manual_cleaning.cleaning_drafts'::regclass);
SELECT core.apply_project_rls('manual_cleaning.manual_issue_draft_links'::regclass);
SELECT core.apply_project_rls('manual_cleaning.cleaning_draft_ancestry'::regclass);
SELECT core.apply_project_rls('manual_cleaning.cleaning_draft_commits'::regclass);
