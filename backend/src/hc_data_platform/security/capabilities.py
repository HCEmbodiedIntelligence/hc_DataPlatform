"""Canonical capability catalog used by runtime authorization."""

from __future__ import annotations

from collections.abc import Iterable

CAPABILITY_UPLOAD_READ = "upload.read"
CAPABILITY_UPLOAD_MANAGE = "upload.manage"
CAPABILITY_DATASET_READ = "dataset.read"
CAPABILITY_DATASET_CREATE = "dataset.create"
CAPABILITY_DATASET_VERSION_READ = "dataset_version.read"
CAPABILITY_DATASET_VERSION_REVIEW = "dataset_version.review"
CAPABILITY_DATASET_VERSION_PUBLISH = "dataset_version.publish"
CAPABILITY_EPISODE_READ = "episode.read"
CAPABILITY_ANNOTATION_EDIT = "annotation.edit"
CAPABILITY_ANNOTATION_REVIEW = "annotation.review"
CAPABILITY_DATA_SCHEMA_READ = "data_schema.read"
CAPABILITY_DATA_SCHEMA_PUBLISH = "data_schema.publish"
CAPABILITY_ACCESS_READ = "access.read"
CAPABILITY_ACCESS_MANAGE = "access.manage"
CAPABILITY_DASHBOARD_READ = "dashboard.read"

CAPABILITY_PLATFORM_ACCOUNT_READ = "platform.account.read"
CAPABILITY_PLATFORM_ACCOUNT_MANAGE = "platform.account.manage"
CAPABILITY_PLATFORM_ACCOUNT_SECURITY_MANAGE = "platform.account_security.manage"
CAPABILITY_PLATFORM_ADMIN = "platform.admin"
CAPABILITY_PLATFORM_OPERATIONS_READ = "platform.operations.read"
CAPABILITY_PLATFORM_MAINTENANCE_OPERATE = "platform.maintenance.operate"
CAPABILITY_PLATFORM_RELEASE_OPERATE = "platform.release.operate"
CAPABILITY_PLATFORM_MAINTENANCE_VERIFY = "platform.maintenance.verify"
CAPABILITY_PLATFORM_BREAK_GLASS = "platform.break_glass"

PLATFORM_OPERATION_CAPABILITIES = frozenset(
    {
        CAPABILITY_PLATFORM_OPERATIONS_READ,
        CAPABILITY_PLATFORM_MAINTENANCE_OPERATE,
        CAPABILITY_PLATFORM_RELEASE_OPERATE,
        CAPABILITY_PLATFORM_MAINTENANCE_VERIFY,
        CAPABILITY_PLATFORM_BREAK_GLASS,
    }
)

PLATFORM_ADMIN_CAPABILITIES = frozenset(
    {
        CAPABILITY_PLATFORM_ADMIN,
        CAPABILITY_PLATFORM_ACCOUNT_READ,
        CAPABILITY_PLATFORM_ACCOUNT_MANAGE,
        CAPABILITY_PLATFORM_ACCOUNT_SECURITY_MANAGE,
    }
)

KNOWN_BUSINESS_CAPABILITIES = frozenset(
    {
        "access.manage",
        "access.download_approval.manage",
        "access.read",
        "access.role.customize",
        "annotation.edit",
        "annotation.read",
        "annotation.review",
        "annotation.save",
        "annotation.submit",
        "annotation_draft.edit",
        "annotation_set.read",
        "annotation_task.assign",
        "annotation_task.claim",
        "annotation_task.create",
        "annotation_task.read",
        "annotation_task.rebase",
        "audit.export",
        "audit.read",
        "calibration.create",
        "calibration.availability.manage",
        "calibration.publish",
        "calibration.read",
        "calibration.report.download",
        "calibration.source.download",
        "calibration.validate",
        "cleaning.archive",
        "cleaning.create",
        "cleaning.edit",
        "cleaning.preview",
        "cleaning.read",
        "cleaning.submit",
        "dashboard.read",
        "data_schema.create",
        "data_schema.deprecate",
        "data_schema.import",
        "data_schema.publish",
        "data_schema.read",
        "data_schema.validate",
        "dataset.create",
        "dataset.delete",
        "dataset.read",
        "dataset.update",
        "dataset_version.download_manifest",
        "dataset_version.download_raw",
        "dataset_version.delete",
        "dataset_version.publish",
        "dataset_version.read",
        "dataset_version.review",
        "episode.read",
        "export.create",
        "export.download",
        "export.read",
        "ingest.import",
        "ingest_source.manage",
        "ingest_source.read",
        "manual_issue.assign",
        "manual_issue.create",
        "manual_issue.dismiss",
        "manual_issue.export",
        "manual_issue.read",
        "manual_issue.reopen",
        "manual_issue.resolve",
        "manual_issue.triage",
        "robot.create",
        "robot.disable",
        "robot.manage",
        "robot.read",
        "robot.update",
        "robot_component.change_mount",
        "robot_component.create",
        "robot_component.disable",
        "robot_component.update",
        "robot_model.asset.download",
        "robot_model.create",
        "robot_model.disable",
        "robot_model.manage",
        "robot_model.publish",
        "robot_model.read",
        "robot_model.validate",
        "robot_model.validation_report.download",
        "robot_model_binding.manage",
        "robot_model_binding.read",
        "storage.cost.read",
        "storage.inventory.refresh",
        "storage.lifecycle.approve",
        "storage.lifecycle.execute",
        "storage.lifecycle.manage",
        "storage.lifecycle.read",
        "storage.lifecycle.simulate",
        "storage.multipart.abort",
        "storage.multipart.read",
        "storage.object.read",
        "storage.object.manage",
        "storage.overview.read",
        "storage.restore.read",
        "storage.restore.request",
        "upload.manage",
        "upload.read",
    }
)

ALL_PLATFORM_ADMIN_EFFECTIVE_CAPABILITIES = (
    KNOWN_BUSINESS_CAPABILITIES | PLATFORM_ADMIN_CAPABILITIES
)

# A human upload operator can complete the data workflow in the same project.
# These grants do not include account administration or storage lifecycle operations.
DATA_WORKFLOW_CAPABILITIES = frozenset(
    {
        "annotation.edit",
        "annotation.read",
        "annotation.review",
        "annotation.save",
        "annotation.submit",
        "annotation_draft.edit",
        "annotation_set.read",
        "annotation_task.claim",
        "annotation_task.create",
        "annotation_task.read",
        "cleaning.create",
        "cleaning.edit",
        "cleaning.preview",
        "cleaning.read",
        "cleaning.submit",
        "dashboard.read",
        "data_schema.create",
        "data_schema.import",
        "data_schema.publish",
        "data_schema.read",
        "data_schema.validate",
        "dataset.create",
        "dataset.read",
        "dataset_version.download_manifest",
        "dataset_version.publish",
        "dataset_version.read",
        "dataset_version.review",
        "episode.read",
        "export.create",
        "export.download",
        "export.read",
        "ingest.import",
        "ingest_source.read",
        "manual_issue.create",
        "manual_issue.read",
        "manual_issue.resolve",
        "manual_issue.triage",
        "robot.read",
        "robot_model.asset.download",
        "robot_model.read",
        "robot_model_binding.read",
        "upload.manage",
        "upload.read",
    }
)


def expand_data_workflow_capabilities(capabilities: Iterable[str]) -> frozenset[str]:
    """Expand a human operator's grants after selecting their authorized scope."""

    granted = frozenset(capabilities)
    if CAPABILITY_UPLOAD_MANAGE in granted:
        return granted | DATA_WORKFLOW_CAPABILITIES
    return granted
