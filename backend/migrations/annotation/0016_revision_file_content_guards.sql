-- Preserve the first pinned bytes across explicit retries and operator mistakes.
ALTER TABLE annotation.revision_files
    ADD CONSTRAINT revision_file_digest_size_pair
        CHECK ((content_sha256 IS NULL) = (size_bytes IS NULL)),
    ADD CONSTRAINT revision_file_supported_format
        CHECK (format_version = 'hc-annotation/1');

CREATE TRIGGER revision_file_no_delete
    BEFORE DELETE ON annotation.revision_files
    FOR EACH ROW EXECUTE FUNCTION annotation.reject_immutable_change();

CREATE FUNCTION annotation.protect_revision_file_generation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.generation IS DISTINCT FROM OLD.generation AND (
        NEW.generation <> OLD.generation + 1 OR NEW.status <> 'PENDING'
        OR OLD.status NOT IN ('READY', 'FAILED')
    ) THEN
        RAISE EXCEPTION 'annotation file retry must advance one finished generation'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER revision_file_generation
    BEFORE UPDATE ON annotation.revision_files
    FOR EACH ROW EXECUTE FUNCTION annotation.protect_revision_file_generation();
