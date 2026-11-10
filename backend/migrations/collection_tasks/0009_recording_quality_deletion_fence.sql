-- Fence late recording-quality writes while a task's files are being purged.
CREATE TRIGGER collection_task_deletion_fence
BEFORE INSERT OR UPDATE ON ingest.recording_quality_reports
FOR EACH ROW EXECUTE FUNCTION collection_tasks.reject_deleted_task_write();
