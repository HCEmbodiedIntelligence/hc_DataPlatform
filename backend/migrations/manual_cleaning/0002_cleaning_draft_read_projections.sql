-- P10 owns only the read projection for the P09 Issue-to-Draft handoff.
-- It deliberately adds no EDL, preview, commit, or successor-draft mutation
-- state: those are P11 responsibilities.

ALTER TABLE manual_cleaning.cleaning_drafts
    ADD COLUMN IF NOT EXISTS projection_version bigint NOT NULL DEFAULT 1;

DO $block$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'cleaning_drafts_projection_version_positive'
           AND conrelid = 'manual_cleaning.cleaning_drafts'::regclass
    ) THEN
        ALTER TABLE manual_cleaning.cleaning_drafts
            ADD CONSTRAINT cleaning_drafts_projection_version_positive
            CHECK (projection_version > 0);
    END IF;
END;
$block$;

-- List projections remain exact organization/project/region reads.  The
-- P09-installed project RLS policy remains the authorization boundary.
CREATE INDEX IF NOT EXISTS cleaning_drafts_projection_page_idx
ON manual_cleaning.cleaning_drafts (
    organization_id, project_id, region_code, updated_at DESC, draft_id DESC
);

CREATE INDEX IF NOT EXISTS cleaning_drafts_projection_status_idx
ON manual_cleaning.cleaning_drafts (
    organization_id, project_id, region_code, status, updated_at DESC, draft_id DESC
);
