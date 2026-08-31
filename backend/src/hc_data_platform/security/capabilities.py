"""Canonical capability and legacy-role compatibility mappings.

Access grants are the authority for platform sessions.  Legacy roles are translated
through this module; domains must not maintain their own role-to-capability tables.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

CAPABILITY_DATASETS_READ = "datasets.read"
CAPABILITY_DATASETS_WRITE = "datasets.write"
CAPABILITY_COLLECTION_UPLOAD = "collection.upload"
CAPABILITY_INGEST_UPLOAD = "ingest.upload"
CAPABILITY_UPLOAD_READ = "upload.read"
CAPABILITY_UPLOAD_MANAGE = "upload.manage"
CAPABILITY_ANNOTATION_WRITE = "annotation.write"
CAPABILITY_ANNOTATION_REVIEW = "annotation.review"
CAPABILITY_TAG_SCHEMA_WRITE = "tag_schema.write"
CAPABILITY_DATASETS_PUBLISH = "datasets.publish"
CAPABILITY_PROJECT_ACCESS_MANAGE = "project.access.manage"
CAPABILITY_ACCESS_READ = "access.read"
CAPABILITY_ACCESS_MANAGE = "access.manage"
CAPABILITY_DASHBOARD_READ = "dashboard.read"
# Platform account administration is deliberately separate from every project-scoped
# role.  A project administrator may approve access inside their project, but must not
# gain visibility of, or mutation authority over, every direct account.
CAPABILITY_PLATFORM_ACCOUNT_READ = "platform.account.read"
CAPABILITY_PLATFORM_ACCOUNT_MANAGE = "platform.account.manage"
# This is deliberately not implied by a project administrator role or a project-scoped
# capability.  It controls a platform-wide security operation on a direct account.
CAPABILITY_PLATFORM_ACCOUNT_SECURITY_MANAGE = "platform.account_security.manage"
CAPABILITY_PLATFORM_ADMIN = "platform.admin"
# Platform operations deliberately use exact global grants. The legacy
# ``platform.admin`` wildcard remains valid for tenant business operations, but it must
# not silently grant backup, release, verification, or break-glass authority.
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

# Known business keys are projected into ``effective_capabilities`` for callers which need
# to render or inspect a complete snapshot.  Authorization itself does not depend on this
# list: ``AuthContext.has_capability`` treats the global ``platform.admin`` marker as a
# wildcard, so newly introduced business capabilities are covered centrally as well.
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
        "annotation.write",
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
        "collection.upload",
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
        "datasets.publish",
        "datasets.read",
        "datasets.write",
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
        "ingest.upload",
        "ingest_source.manage",
        "ingest_source.read",
        "manual_issue.create",
        "manual_issue.assign",
        "manual_issue.dismiss",
        "manual_issue.export",
        "manual_issue.read",
        "manual_issue.reopen",
        "manual_issue.resolve",
        "manual_issue.triage",
        "project.access.manage",
        "robot.create",
        "robot.disable",
        "robot.manage",
        "robot.read",
        "robot.update",
        "robot_component.change_mount",
        "robot_component.create",
        "robot_component.disable",
        "robot_component.update",
        "robot_model.create",
        "robot_model.disable",
        "robot_model.asset.download",
        "robot_model.manage",
        "robot_model.publish",
        "robot_model.read",
        "robot_model.validate",
        "robot_model.validation_report.download",
        "robot_model_binding.manage",
        "robot_model_binding.read",
        "storage.cost.read",
        "storage.inventory.refresh",
        "storage.lifecycle.execute",
        "storage.lifecycle.manage",
        "storage.lifecycle.read",
        "storage.lifecycle.simulate",
        "storage.multipart.abort",
        "storage.multipart.read",
        "storage.object.read",
        "storage.overview.read",
        "storage.restore.read",
        "storage.restore.request",
        "tag_schema.write",
        "upload.manage",
        "upload.read",
    }
)

ALL_PLATFORM_ADMIN_EFFECTIVE_CAPABILITIES = (
    KNOWN_BUSINESS_CAPABILITIES | PLATFORM_ADMIN_CAPABILITIES
)

# The database and current frontend use the right-hand names.  Older routers and JWTs use
# the left-hand names.  Expansion is deliberately asymmetric for read-only upload access:
# upload.read never activates either legacy write capability.
CAPABILITY_IMPLICATIONS: Mapping[str, frozenset[str]] = {
    CAPABILITY_COLLECTION_UPLOAD: frozenset({CAPABILITY_UPLOAD_READ, CAPABILITY_UPLOAD_MANAGE}),
    CAPABILITY_INGEST_UPLOAD: frozenset({CAPABILITY_UPLOAD_READ, CAPABILITY_UPLOAD_MANAGE}),
    CAPABILITY_UPLOAD_MANAGE: frozenset(
        {CAPABILITY_UPLOAD_READ, CAPABILITY_COLLECTION_UPLOAD, CAPABILITY_INGEST_UPLOAD}
    ),
    CAPABILITY_PROJECT_ACCESS_MANAGE: frozenset({CAPABILITY_ACCESS_READ, CAPABILITY_ACCESS_MANAGE}),
    CAPABILITY_ACCESS_MANAGE: frozenset({CAPABILITY_ACCESS_READ, CAPABILITY_PROJECT_ACCESS_MANAGE}),
}


ROLE_CAPABILITIES: Mapping[str, frozenset[str]] = {
    "uploader": frozenset(
        {
            CAPABILITY_DATASETS_READ,
            CAPABILITY_COLLECTION_UPLOAD,
            CAPABILITY_INGEST_UPLOAD,
        }
    ),
    "annotator": frozenset({CAPABILITY_DATASETS_READ, CAPABILITY_ANNOTATION_WRITE}),
    "reviewer": frozenset({CAPABILITY_DATASETS_READ, CAPABILITY_ANNOTATION_REVIEW}),
    "publisher": frozenset(
        {
            CAPABILITY_DATASETS_READ,
            CAPABILITY_DATASETS_WRITE,
            CAPABILITY_DATASETS_PUBLISH,
            CAPABILITY_TAG_SCHEMA_WRITE,
        }
    ),
    "admin": frozenset({CAPABILITY_PROJECT_ACCESS_MANAGE}),
}

ROLE_ACTIVATION_CAPABILITIES: Mapping[str, frozenset[str]] = {
    "uploader": frozenset({CAPABILITY_COLLECTION_UPLOAD, CAPABILITY_INGEST_UPLOAD}),
    "annotator": frozenset({CAPABILITY_ANNOTATION_WRITE}),
    "reviewer": frozenset({CAPABILITY_ANNOTATION_REVIEW}),
    "publisher": frozenset(
        {CAPABILITY_DATASETS_WRITE, CAPABILITY_DATASETS_PUBLISH, CAPABILITY_TAG_SCHEMA_WRITE}
    ),
    "admin": frozenset({CAPABILITY_PROJECT_ACCESS_MANAGE}),
}

PERMISSION_CAPABILITIES: Mapping[str, frozenset[str]] = {
    "read": frozenset(
        {
            CAPABILITY_DATASETS_READ,
            CAPABILITY_DASHBOARD_READ,
            CAPABILITY_COLLECTION_UPLOAD,
            CAPABILITY_INGEST_UPLOAD,
            CAPABILITY_ANNOTATION_WRITE,
            CAPABILITY_ANNOTATION_REVIEW,
            CAPABILITY_TAG_SCHEMA_WRITE,
            CAPABILITY_DATASETS_WRITE,
            CAPABILITY_DATASETS_PUBLISH,
            CAPABILITY_PROJECT_ACCESS_MANAGE,
        }
    ),
    "upload": frozenset(
        {
            CAPABILITY_COLLECTION_UPLOAD,
            CAPABILITY_INGEST_UPLOAD,
            CAPABILITY_PROJECT_ACCESS_MANAGE,
        }
    ),
    "annotate": frozenset({CAPABILITY_ANNOTATION_WRITE, CAPABILITY_PROJECT_ACCESS_MANAGE}),
    "review": frozenset({CAPABILITY_ANNOTATION_REVIEW, CAPABILITY_PROJECT_ACCESS_MANAGE}),
    "publish": frozenset(
        {
            CAPABILITY_DATASETS_PUBLISH,
            CAPABILITY_TAG_SCHEMA_WRITE,
            CAPABILITY_PROJECT_ACCESS_MANAGE,
        }
    ),
    "administer": frozenset({CAPABILITY_PROJECT_ACCESS_MANAGE}),
}


def capabilities_from_legacy_roles(roles: Collection[str]) -> frozenset[str]:
    """Project legacy roles to capability keys without changing tenant scope."""

    return frozenset(
        capability for role in roles for capability in ROLE_CAPABILITIES.get(role, frozenset())
    )


def expand_capability_aliases(capabilities: Collection[str]) -> frozenset[str]:
    """Return the transitive canonical/legacy compatibility closure."""

    expanded = set(capabilities)
    pending = list(expanded)
    while pending:
        capability = pending.pop()
        for implied in CAPABILITY_IMPLICATIONS.get(capability, frozenset()):
            if implied not in expanded:
                expanded.add(implied)
                pending.append(implied)
    return frozenset(expanded)


def legacy_roles_from_capabilities(capabilities: Collection[str]) -> frozenset[str]:
    """Return only roles fully justified by approved capability grants."""

    granted = expand_capability_aliases(capabilities)
    return frozenset(
        role
        for role, required in ROLE_ACTIVATION_CAPABILITIES.items()
        if required.intersection(granted)
    )


def capabilities_for_roles(roles: Collection[str]) -> frozenset[str]:
    return frozenset(
        capability
        for role in roles
        for capability in ROLE_ACTIVATION_CAPABILITIES.get(role, frozenset())
    )
