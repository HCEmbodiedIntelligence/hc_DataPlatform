-- Make the Cleaning Workbench tables the only writable/readable Draft contract.

DROP TRIGGER IF EXISTS cleaning_drafts_seed_workbench
ON manual_cleaning.cleaning_drafts;
DROP FUNCTION IF EXISTS manual_cleaning.seed_issue_workbench_draft();

DO $block$
DECLARE
    dependency record;
BEGIN
    FOR dependency IN
        SELECT constraint_record.conrelid::regclass AS table_name,
               constraint_record.conname
          FROM pg_constraint AS constraint_record
         WHERE constraint_record.contype = 'f'
           AND constraint_record.confrelid =
               'manual_cleaning.cleaning_drafts'::regclass
    LOOP
        EXECUTE format(
            'ALTER TABLE %s DROP CONSTRAINT %I',
            dependency.table_name,
            dependency.conname
        );
    END LOOP;
END;
$block$;

ALTER TABLE manual_cleaning.manual_issue_draft_links
    ADD CONSTRAINT manual_issue_draft_links_current_draft_fk
    FOREIGN KEY (organization_id, project_id, region_code, draft_id)
    REFERENCES manual_cleaning.cleaning_workbench_drafts (
        organization_id, project_id, region_code, draft_id
    );

ALTER TABLE manual_cleaning.cleaning_draft_ancestry
    ADD CONSTRAINT cleaning_draft_ancestry_current_root_fk
    FOREIGN KEY (organization_id, project_id, region_code, root_draft_id)
    REFERENCES manual_cleaning.cleaning_workbench_drafts (
        organization_id, project_id, region_code, draft_id
    ),
    ADD CONSTRAINT cleaning_draft_ancestry_current_descendant_fk
    FOREIGN KEY (organization_id, project_id, region_code, descendant_draft_id)
    REFERENCES manual_cleaning.cleaning_workbench_drafts (
        organization_id, project_id, region_code, draft_id
    );

DROP TABLE manual_cleaning.cleaning_draft_commits;
DROP TABLE manual_cleaning.cleaning_drafts;
