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
CAPABILITY_ANNOTATION_WRITE = "annotation.write"
CAPABILITY_ANNOTATION_REVIEW = "annotation.review"
CAPABILITY_TAG_SCHEMA_WRITE = "tag_schema.write"
CAPABILITY_DATASETS_PUBLISH = "datasets.publish"
CAPABILITY_PROJECT_ACCESS_MANAGE = "project.access.manage"
CAPABILITY_DASHBOARD_READ = "dashboard.read"


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


def legacy_roles_from_capabilities(capabilities: Collection[str]) -> frozenset[str]:
    """Return only roles fully justified by approved capability grants."""

    granted = frozenset(capabilities)
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
