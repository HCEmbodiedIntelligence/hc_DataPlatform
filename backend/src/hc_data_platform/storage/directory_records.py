"""Scoped purge of business records after an administrator confirms retirement."""

from psycopg import sql
from hc_data_platform.collection_tasks.deletion_plan import TABLES as TASK_DATA

# The task-data graph already owns captures, episodes, annotation, publishing
# and dataset rows. These separately owned catalogs also have an explicit owner.
SCOPE_CATALOGS = {
    "annotation": (
        "frame_selection_project_quotas",
        "model_annotation_guides",
        "model_connections",
        "tag_schema_versions",
    ),
    "calibrations": (
        "calibration_command_receipts",
        "calibration_publish_preflights",
        "calibration_sets",
        "calibration_validation_reports",
        "calibration_version_documents",
    ),
    "data_schemas": (
        "schema_command_receipts",
        "schema_publish_preflights",
        "schema_validation_reports",
        "stream_schema_versions",
    ),
    "episode_store": ("export_file_names", "qc_retired_versions"),
    "ingest": (
        "annotation_tag_library",
        "capture_legacy_references",
        "capture_mapping_validations",
        "capture_mapping_versions",
        "capture_scheme_links",
        "capture_scheme_validations",
        "capture_scheme_versions",
        "capture_schemes",
        "data_source_connection_test_jobs",
        "data_source_credentials",
        "data_sources",
        "mcap_batch_retirements",
        "mcap_robot_profiles",
        "quality_rule_templates",
        "recording_cache_retirements",
        "robot_connection_locals",
        "robot_connection_tickets",
        "robot_credential_requests",
        "robot_ingest_credentials",
        "robot_ingest_identities",
        "robot_processing_schemes",
        "robot_processing_templates",
        "robot_processing_versions",
        "teleoperation_endpoints",
        "upload_batch_members",
        "upload_batches",
    ),
    "public": ("alignment_profiles", "quality_profiles"),
    "registry": (
        "organization_robot_model_bindings",
        "robot_model_asset_upload_files",
        "robot_model_asset_uploads",
        "robot_model_assets",
        "robot_model_command_receipts",
        "robot_model_joint_mappings",
        "robot_model_publish_preflights",
        "robot_model_versions",
        "robot_models",
    ),
    "robotics": (
        "project_robot_assignments",
        "robot_asset_command_receipts",
        "robot_asset_component_channels",
        "robot_asset_component_frames",
        "robot_asset_components",
        "robot_assets",
        "robot_camera_calibrations",
        "robot_history_anchors",
        "robot_reference_links",
        "robot_resolution_operations",
    ),
    "storage": ("cache_test_copies", "lifecycle_schedules"),
}
SCOPED_DATA = frozenset(TASK_DATA) | {
    schema + "." + name for schema, names in SCOPE_CATALOGS.items() for name in names
}
LEGACY_ANNOTATIONS = frozenset(
    "annotation." + name
    for name in (
        "annotation_revisions",
        "annotation_operations",
        "annotation_reviews",
        "annotation_mutations",
        "annotation_submissions",
        "annotation_submission_mutations",
    )
)


def delete_records(db, org, project):
    # Establish the immutable-business-record exception only for this transaction
    # and only after locking the exact deleted scope and checking its deadline.
    found = db.execute(
        """UPDATE access_control.retired_scopes SET cleanup_xid=pg_current_xact_id()
        WHERE organization_id=%s AND project_id=%s AND status='DELETED' AND purge_after<=now()
        AND completed_at IS NULL RETURNING 1""",
        (org, project),
    ).fetchone()
    if not found:
        raise RuntimeError("DIRECTORY_NOT_DUE")
    db.execute("SELECT set_config('app.retired_directory_cleanup','true',true)")
    db.execute("SET CONSTRAINTS ALL DEFERRED")
    db.execute("SET LOCAL lock_timeout='5s'")
    db.execute(
        """UPDATE episode_store.bindings SET current_data_version_id=NULL WHERE organization_id=%s
        AND (%s='' OR project_id=%s) AND current_data_version_id IS NOT NULL""",
        (org, project, project),
    )
    columns = {}
    for table, col in db.execute("""SELECT n.nspname||'.'||c.relname,a.attname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid
        WHERE a.attname IN ('organization_id','project_id') AND NOT a.attisdropped"""):
        columns.setdefault(table, set()).add(col)
    filters = {}
    for table in SCOPED_DATA:
        cols = columns.get(table, set())
        if table in LEGACY_ANNOTATIONS and "organization_id" not in cols:
            where = "task_id IN (SELECT task_id FROM annotation.annotation_tasks WHERE organization_id=%s AND (%s='' OR project_id=%s))"
            params = (org, project, project)
        elif "organization_id" in cols:
            if project and "project_id" not in cols:
                continue
            where = "organization_id=%s" + (" AND project_id=%s" if project else "")
            params = (org, project) if project else (org,)
        else:
            raise RuntimeError("DIRECTORY_UNVERIFIED_TABLE_OWNER: " + table)
        target = sql.Identifier(*table.split("."))
        if db.execute(
            sql.SQL("SELECT 1 FROM {} WHERE " + where + " LIMIT 1").format(target), params
        ).fetchone():
            filters[table] = (target, where, params)
    edges = set(
        db.execute("""SELECT nc.nspname||'.'||c.relname,np.nspname||'.'||p.relname FROM pg_constraint k
        JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace nc ON nc.oid=c.relnamespace
        JOIN pg_class p ON p.oid=k.confrelid JOIN pg_namespace np ON np.oid=p.relnamespace
        WHERE k.contype='f' AND NOT k.condeferrable AND k.conrelid<>k.confrelid""").fetchall()
    )
    edges.discard(("episode_store.bindings", "episode_store.versions"))
    edges.update((table, "annotation.annotation_tasks") for table in LEGACY_ANNOTATIONS)
    while filters:
        ready = sorted(
            table
            for table in filters
            if not any(parent == table and child in filters for child, parent in edges)
        )
        if not ready:
            raise RuntimeError("DIRECTORY_REFERENCE_CYCLE")
        for table in ready:
            target, where, params = filters.pop(table)
            db.execute(sql.SQL("DELETE FROM {} WHERE " + where).format(target), params)
