"""Immutable process identity shared by API and worker release surfaces."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

import hc_data_platform

Sha256Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
ReleaseDigest = Sha256Digest | Literal["unreleased"]
GitCommit = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")] | Literal["unknown"]
ReleaseId = Annotated[
    str,
    StringConstraints(pattern=r"^(?:unreleased|platform-v[A-Za-z0-9][A-Za-z0-9._-]{0,119})$"),
]
SemanticVersion = Annotated[
    str,
    StringConstraints(pattern=r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"),
]
ComponentRole = Literal["api", "worker", "media-worker", "migration", "frontend"]


class ReleaseIdentitySettings(Protocol):
    release_id: str
    platform_version: str
    git_commit: str
    chart_version: str
    release_manifest_digest: str
    migration_manifest_digest: str
    component_role: ComponentRole
    component_image_digest: str


class PlatformReleaseIdentityV1(BaseModel):
    """Public immutable identity for one running platform process."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal["hc-platform-release-identity/v1"] = "hc-platform-release-identity/v1"
    release_id: ReleaseId
    semantic_version: SemanticVersion
    git_commit: GitCommit
    chart_version: SemanticVersion
    release_manifest_digest: ReleaseDigest
    migration_manifest_digest: ReleaseDigest
    component: ComponentRole
    component_image_digest: ReleaseDigest

    @model_validator(mode="after")
    def validate_runtime_package_version(self) -> PlatformReleaseIdentityV1:
        if self.semantic_version != hc_data_platform.__version__:
            raise ValueError(
                "release semantic_version must equal the installed backend package version"
            )
        return self


def release_identity_from_settings(
    settings: ReleaseIdentitySettings,
) -> PlatformReleaseIdentityV1:
    return PlatformReleaseIdentityV1(
        release_id=settings.release_id,
        semantic_version=settings.platform_version,
        git_commit=settings.git_commit,
        chart_version=settings.chart_version,
        release_manifest_digest=settings.release_manifest_digest,
        migration_manifest_digest=settings.migration_manifest_digest,
        component=settings.component_role,
        component_image_digest=settings.component_image_digest,
    )


__all__ = [
    "ComponentRole",
    "PlatformReleaseIdentityV1",
    "release_identity_from_settings",
]
