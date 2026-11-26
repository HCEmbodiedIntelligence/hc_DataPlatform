ALTER TABLE manual_cleaning.manual_issues DROP CONSTRAINT manual_issues_status_check,
    ADD CONSTRAINT manual_issues_status_check CHECK (status IN ('OPEN','IN_PROGRESS','RESOLVED','DISCARDED'));
