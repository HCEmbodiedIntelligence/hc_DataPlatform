from __future__ import annotations

import secrets
import time

ID_PREFIXES: dict[str, str] = {
    "data_source": "source_",
    "source": "source_",
    "credential_ref": "cred_ref_",
    "connection_test": "connection_test_",
    "upload_session": "upload_",
    "upload": "upload_",
    "upload_object": "object_",
    "object": "object_",
    "upload_part": "part_",
    "part": "part_",
    "source_manifest": "manifest_",
    "manifest": "manifest_",
    "manifest_node": "node_",
    "verification_run": "verification_run_",
    "verification_stage": "verification_stage_",
    "verification_finding": "finding_",
    "finding": "finding_",
    "quarantine": "quarantine_",
    "job": "job_",
    "registration_receipt": "receipt_",
    "receipt": "receipt_",
    "event": "event_",
    "request": "req_",
    "outbox": "outbox_",
    "audit": "audit_",
    "idempotency": "idem_record_",
    "dataset": "ds_",
    "dataset_version": "dsv_",
    "manual_issue": "mi_",
    "episode": "ep_",
    "robot": "rb_",
    "calibration_set": "cal_",
    "schema_version": "schv_",
}


def new_id(resource_type: str) -> str:
    prefix = ID_PREFIXES.get(resource_type)
    if prefix is None:
        raise ValueError(f"Unknown resource type: {resource_type}")
    timestamp = time.time_ns().to_bytes(8, "big").hex()
    return f"{prefix}{timestamp}{secrets.token_hex(6)}"


generate_id = new_id
