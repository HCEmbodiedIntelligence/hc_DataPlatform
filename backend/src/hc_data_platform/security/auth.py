from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

import jwt

from hc_data_platform.core.errors import problem

from .capabilities import (
    ALL_PLATFORM_ADMIN_EFFECTIVE_CAPABILITIES,
    CAPABILITY_PLATFORM_ADMIN,
    PERMISSION_CAPABILITIES,
    capabilities_for_roles,
    capabilities_from_legacy_roles,
    expand_capability_aliases,
    legacy_roles_from_capabilities,
)


class Role(str, Enum):
    UPLOADER = "uploader"
    ANNOTATOR = "annotator"
    REVIEWER = "reviewer"
    PUBLISHER = "publisher"
    ADMIN = "admin"


class Permission(str, Enum):
    READ = "read"
    UPLOAD = "upload"
    ANNOTATE = "annotate"
    REVIEW = "review"
    PUBLISH = "publish"
    ADMINISTER = "administer"


ALLOWED_ROLES = frozenset(role.value for role in Role)
ALL_PERMISSIONS = frozenset(permission.value for permission in Permission)
# PyJWT supports more algorithms than the platform should accept from configuration. This
# list deliberately contains only algorithms that authenticate a signature.
SIGNATURE_ALGORITHMS = frozenset(
    {
        "HS256",
        "HS384",
        "HS512",
        "RS256",
        "RS384",
        "RS512",
        "ES256",
        "ES384",
        "ES512",
        "PS256",
        "PS384",
        "PS512",
        "EdDSA",
    }
)
MAX_JWT_ORGANIZATION_SCOPES = 256
MAX_JWT_SCOPE_CAPABILITIES = 256


def _claim_values(claims: Mapping[str, Any], name: str) -> frozenset[str]:
    raw = claims.get(name, [])
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise problem(
            status=401,
            code="INVALID_ACCESS_TOKEN",
            title="Invalid access token",
            detail=f"The {name} claim must be an array of non-empty strings.",
        )
    if any(not isinstance(value, str) or not value for value in raw):
        raise problem(
            status=401,
            code="INVALID_ACCESS_TOKEN",
            title="Invalid access token",
            detail=f"The {name} claim contains an empty value.",
        )
    return frozenset(raw)


def _organization_scope_claims(
    claims: Mapping[str, Any],
) -> tuple[
    frozenset[str],
    frozenset[str],
    frozenset[str],
    frozenset[tuple[str, str | None]],
    frozenset[tuple[str, str]],
    frozenset[tuple[str, str, str | None]],
    frozenset[tuple[str, str, str]],
]:
    raw = claims.get("organization_scopes", [])
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise problem(
            status=401,
            code="INVALID_ACCESS_TOKEN",
            title="Invalid access token",
            detail="The organization_scopes claim must be an array of scope objects.",
        )
    if len(raw) > MAX_JWT_ORGANIZATION_SCOPES:
        raise problem(
            status=401,
            code="INVALID_ACCESS_TOKEN",
            title="Invalid access token",
            detail="The organization_scopes claim contains too many entries.",
        )

    organization_ids: set[str] = set()
    project_ids: set[str] = set()
    region_codes: set[str] = set()
    scope_pairs: set[tuple[str, str | None]] = set()
    scoped_capabilities: set[tuple[str, str]] = set()
    organization_scope_triples: set[tuple[str, str, str | None]] = set()
    organization_scoped_capabilities: set[tuple[str, str, str]] = set()
    allowed_keys = frozenset({"organization_id", "project_id", "region_code", "capabilities"})
    for value in raw:
        if not isinstance(value, Mapping) or set(value) != allowed_keys:
            raise problem(
                status=401,
                code="INVALID_ACCESS_TOKEN",
                title="Invalid access token",
                detail=(
                    "Each organization_scopes entry must contain exactly organization_id, "
                    "project_id, region_code, and capabilities."
                ),
            )
        organization_id = value["organization_id"]
        project_id = value["project_id"]
        region_code = value["region_code"]
        capabilities = value["capabilities"]
        if (
            not isinstance(organization_id, str)
            or not organization_id
            or not isinstance(project_id, str)
            or not project_id
            or (region_code is not None and (not isinstance(region_code, str) or not region_code))
        ):
            raise problem(
                status=401,
                code="INVALID_ACCESS_TOKEN",
                title="Invalid access token",
                detail="An organization scope contains an empty or invalid identity.",
            )
        if (
            isinstance(capabilities, (str, bytes))
            or not isinstance(capabilities, Sequence)
            or len(capabilities) > MAX_JWT_SCOPE_CAPABILITIES
            or any(not isinstance(capability, str) or not capability for capability in capabilities)
        ):
            raise problem(
                status=401,
                code="INVALID_ACCESS_TOKEN",
                title="Invalid access token",
                detail="An organization scope contains invalid capabilities.",
            )
        organization_ids.add(organization_id)
        project_ids.add(project_id)
        if region_code is not None:
            region_codes.add(region_code)
        scope_pairs.add((project_id, region_code))
        organization_scope_triples.add((organization_id, project_id, region_code))
        for capability in capabilities:
            scoped_capabilities.add((project_id, capability))
            organization_scoped_capabilities.add((organization_id, project_id, capability))

    return (
        frozenset(organization_ids),
        frozenset(project_ids),
        frozenset(region_codes),
        frozenset(scope_pairs),
        frozenset(scoped_capabilities),
        frozenset(organization_scope_triples),
        frozenset(organization_scoped_capabilities),
    )


@dataclass(frozen=True, slots=True)
class AuthContext:
    subject_id: str
    project_ids: frozenset[str]
    region_codes: frozenset[str]
    roles: frozenset[str]
    service_identity: bool = False
    capabilities: frozenset[str] = frozenset()
    capability_revision: int = 0
    scope_pairs: frozenset[tuple[str, str | None]] = frozenset()
    scoped_capabilities: frozenset[tuple[str, str]] = frozenset()
    organization_ids: frozenset[str] = frozenset()
    organization_scope_triples: frozenset[tuple[str, str, str | None]] = frozenset()
    organization_scoped_capabilities: frozenset[tuple[str, str, str]] = frozenset()

    def __post_init__(self) -> None:
        if not self.subject_id:
            raise ValueError("subject_id must not be empty")
        if any(
            not value for value in (*self.organization_ids, *self.project_ids, *self.region_codes)
        ):
            raise ValueError(
                "organization, project, and region scopes must not contain empty values"
            )
        unknown = self.roles - ALLOWED_ROLES
        if unknown:
            raise ValueError(f"unknown roles: {', '.join(sorted(unknown))}")
        if self.capability_revision < 0:
            raise ValueError("capability_revision must not be negative")
        if any(not project_id or region_code == "" for project_id, region_code in self.scope_pairs):
            raise ValueError("scope pairs must contain a project and an optional non-empty region")
        if any(
            not project_id or not capability for project_id, capability in self.scoped_capabilities
        ):
            raise ValueError("scoped capabilities must contain non-empty values")
        if any(
            not organization_id or not project_id or region_code == ""
            for organization_id, project_id, region_code in self.organization_scope_triples
        ):
            raise ValueError(
                "organization scope triples must contain organization, project, and optional region"
            )
        if any(
            not organization_id or not project_id or not capability
            for organization_id, project_id, capability in self.organization_scoped_capabilities
        ):
            raise ValueError("organization scoped capabilities must contain non-empty values")
        pair_projects = {project_id for project_id, _ in self.scope_pairs}
        pair_regions = {
            region_code for _, region_code in self.scope_pairs if region_code is not None
        }
        if not pair_projects.issubset(self.project_ids):
            raise ValueError("scope-pair projects must be present in project_ids")
        if not pair_regions.issubset(self.region_codes):
            raise ValueError("scope-pair regions must be present in region_codes")
        if any(project_id not in self.project_ids for project_id, _ in self.scoped_capabilities):
            raise ValueError("scoped capability projects must be present in project_ids")
        triple_organizations = {
            organization_id for organization_id, _, _ in self.organization_scope_triples
        }
        triple_projects = {project_id for _, project_id, _ in self.organization_scope_triples}
        triple_regions = {
            region_code
            for _, _, region_code in self.organization_scope_triples
            if region_code is not None
        }
        if not triple_organizations.issubset(self.organization_ids):
            raise ValueError("organization scope triples must be present in organization_ids")
        if not triple_projects.issubset(self.project_ids):
            raise ValueError("organization scope triple projects must be present in project_ids")
        if not triple_regions.issubset(self.region_codes):
            raise ValueError("organization scope triple regions must be present in region_codes")
        if any(
            organization_id not in self.organization_ids or project_id not in self.project_ids
            for organization_id, project_id, _ in self.organization_scoped_capabilities
        ):
            raise ValueError(
                "organization scoped capability identities must be present in "
                "organization_ids/project_ids"
            )

    @property
    def permissions(self) -> frozenset[str]:
        if self.is_platform_admin:
            return ALL_PERMISSIONS
        effective = self.effective_capabilities()
        return frozenset(
            permission
            for permission, accepted in PERMISSION_CAPABILITIES.items()
            if effective.intersection(accepted)
        )

    def effective_capabilities(
        self, project_id: str | None = None, organization_id: str | None = None
    ) -> frozenset[str]:
        """Resolve global/project grants and their canonical/legacy compatibility aliases."""

        scoped = (
            frozenset(
                capability
                for project, capability in self.scoped_capabilities
                if project == project_id
            )
            if project_id is not None and organization_id is None
            else frozenset()
        )
        organization_scoped = (
            frozenset(
                capability
                for organization, project, capability in self.organization_scoped_capabilities
                if project == project_id and organization == organization_id
            )
            if project_id is not None and organization_id is not None
            else frozenset()
        )
        if self.is_platform_admin:
            return ALL_PLATFORM_ADMIN_EFFECTIVE_CAPABILITIES
        # ``platform.admin`` is only meaningful as a global platform grant.  A malformed
        # JWT or an accidentally approved project capability must never activate it.
        project_capabilities = (scoped | organization_scoped) - {CAPABILITY_PLATFORM_ADMIN}
        return expand_capability_aliases(
            self.capabilities | project_capabilities | capabilities_from_legacy_roles(self.roles)
        )

    @property
    def is_platform_admin(self) -> bool:
        """Whether the principal holds the global, non-project super-admin marker."""

        return CAPABILITY_PLATFORM_ADMIN in self.capabilities

    def legacy_roles(
        self, project_id: str | None = None, organization_id: str | None = None
    ) -> frozenset[str]:
        return legacy_roles_from_capabilities(
            self.effective_capabilities(project_id, organization_id)
        )

    def require_role(
        self,
        *allowed: str | Role,
        project_id: str | None = None,
        organization_id: str | None = None,
    ) -> None:
        normalized = frozenset(role.value if isinstance(role, Role) else role for role in allowed)
        required_capabilities = capabilities_for_roles(normalized)
        if normalized and self.is_platform_admin:
            return
        if (
            not normalized
            or not required_capabilities
            or not self.effective_capabilities(project_id, organization_id).intersection(
                required_capabilities
            )
        ):
            raise problem(
                status=403,
                code="ROLE_REQUIRED",
                title="Insufficient role",
                detail=f"One of these roles is required: {', '.join(sorted(normalized))}.",
            )

    def require_permission(
        self,
        permission: str | Permission,
        project_id: str | None = None,
        organization_id: str | None = None,
    ) -> None:
        normalized = permission.value if isinstance(permission, Permission) else permission
        accepted = PERMISSION_CAPABILITIES.get(normalized, frozenset())
        if normalized in ALL_PERMISSIONS and self.is_platform_admin:
            return
        if normalized not in ALL_PERMISSIONS or not self.effective_capabilities(
            project_id, organization_id
        ).intersection(accepted):
            raise problem(
                status=403,
                code="PERMISSION_REQUIRED",
                title="Insufficient permission",
                detail=f"The {normalized!r} permission is required.",
            )

    def has_capability(
        self,
        capability: str,
        project_id: str | None = None,
        organization_id: str | None = None,
    ) -> bool:
        return bool(capability) and (
            self.is_platform_admin
            or capability in self.effective_capabilities(project_id, organization_id)
        )

    def require_capability(
        self,
        capability: str,
        project_id: str | None = None,
        organization_id: str | None = None,
    ) -> None:
        if not capability or not self.has_capability(capability, project_id, organization_id):
            raise problem(
                status=403,
                code="CAPABILITY_REQUIRED",
                title="Insufficient capability",
                detail="The current project scope does not grant the required capability.",
            )

    def has_exact_platform_capability(self, capability: str) -> bool:
        """Check a global operational grant without the platform-admin wildcard."""

        return capability.startswith("platform.") and capability in self.capabilities

    def require_exact_platform_capability(self, *capabilities: str) -> None:
        """Require one exact global platform grant for separated-duty operations."""

        required = frozenset(capabilities)
        if (
            not required
            or any(not capability.startswith("platform.") for capability in required)
            or not required.intersection(self.capabilities)
        ):
            raise problem(
                status=403,
                code="PLATFORM_CAPABILITY_REQUIRED",
                title="Platform operation capability required",
                detail="The verified identity is not authorized for this platform operation.",
            )

    @classmethod
    def service(
        cls,
        *,
        subject_id: str,
        roles: Collection[str | Role],
        project_ids: Collection[str] = (),
        region_codes: Collection[str] = (),
    ) -> AuthContext:
        """Build a worker identity whose usable scopes still have to be explicit."""

        return cls(
            subject_id=subject_id,
            project_ids=frozenset(project_ids),
            region_codes=frozenset(region_codes),
            roles=frozenset(role.value if isinstance(role, Role) else role for role in roles),
            service_identity=True,
            scope_pairs=frozenset(
                (project_id, region_code)
                for project_id in project_ids
                for region_code in (tuple(region_codes) or (None,))
            ),
        )


class SigningKeyResolver(Protocol):
    def resolve(self, token: str) -> Any: ...


class StaticSigningKeyResolver:
    def __init__(self, key: Any) -> None:
        self._key = key

    def resolve(self, token: str) -> Any:
        del token
        return self._key


class OidcJwksKeyResolver:
    """Resolve rotating OIDC signing keys from the provider's JWKS endpoint."""

    def __init__(
        self,
        jwks_url: str,
        *,
        cache_keys: bool = True,
        lifespan_seconds: int = 300,
        timeout_seconds: int = 5,
    ) -> None:
        if not jwks_url.lower().startswith("https://"):
            raise ValueError("the OIDC JWKS URL must use HTTPS")
        self._client = jwt.PyJWKClient(
            jwks_url,
            cache_keys=cache_keys,
            lifespan=lifespan_seconds,
            timeout=timeout_seconds,
        )

    def resolve(self, token: str) -> Any:
        return self._client.get_signing_key_from_jwt(token).key


class JwtVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        algorithms: Collection[str],
        key: Any | None = None,
        jwks_url: str | None = None,
        key_resolver: SigningKeyResolver | None = None,
        leeway_seconds: int = 0,
    ) -> None:
        normalized_algorithms = tuple(dict.fromkeys(algorithms))
        invalid_algorithms = set(normalized_algorithms) - SIGNATURE_ALGORITHMS
        if not normalized_algorithms:
            raise ValueError("at least one JWT signature algorithm is required")
        if invalid_algorithms:
            raise ValueError(
                "unsupported or unsigned JWT algorithms: " + ", ".join(sorted(invalid_algorithms))
            )
        if not issuer or not audience:
            raise ValueError("issuer and audience are required")
        if leeway_seconds < 0:
            raise ValueError("JWT leeway must not be negative")
        resolvers = sum(value is not None for value in (key, jwks_url, key_resolver))
        if resolvers != 1:
            raise ValueError("provide exactly one of key, jwks_url, or key_resolver")

        self._issuer = issuer
        self._audience = audience
        self._algorithms = normalized_algorithms
        self._leeway_seconds = leeway_seconds
        if key_resolver is not None:
            self._key_resolver = key_resolver
        elif jwks_url is not None:
            self._key_resolver = OidcJwksKeyResolver(jwks_url)
        else:
            self._key_resolver = StaticSigningKeyResolver(key)

    def verify(self, token: str) -> AuthContext:
        try:
            header = jwt.get_unverified_header(token)
            token_algorithm = header.get("alg")
            if not isinstance(token_algorithm, str) or token_algorithm not in self._algorithms:
                raise jwt.InvalidAlgorithmError("token algorithm is not allowed")
            if token_algorithm.lower() == "none":
                raise jwt.InvalidAlgorithmError("unsigned tokens are forbidden")
            signing_key = self._key_resolver.resolve(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway_seconds,
                options={
                    "require": ["exp", "iat", "sub", "iss", "aud"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise problem(
                status=401,
                code="INVALID_ACCESS_TOKEN",
                title="Invalid access token",
                detail="The access token is expired, malformed, or does not match this service.",
            ) from exc

        subject_id = claims.get("sub")
        if not isinstance(subject_id, str) or not subject_id:
            raise problem(
                status=401,
                code="INVALID_ACCESS_TOKEN",
                title="Invalid access token",
                detail="The subject claim must be a non-empty string.",
            )
        roles = _claim_values(claims, "roles")
        unknown = roles - ALLOWED_ROLES
        if unknown:
            raise problem(
                status=403,
                code="UNKNOWN_ROLE",
                title="Unknown role",
                detail="The token contains roles not recognized by this service.",
                details={"roles": sorted(unknown)},
            )
        service_identity = claims.get("service_identity", False)
        if not isinstance(service_identity, bool):
            raise problem(
                status=401,
                code="INVALID_ACCESS_TOKEN",
                title="Invalid access token",
                detail="The service_identity claim must be a boolean.",
            )
        (
            organization_ids,
            organization_projects,
            organization_regions,
            scope_pairs,
            scoped_capabilities,
            organization_scope_triples,
            organization_scoped_capabilities,
        ) = _organization_scope_claims(claims)
        return AuthContext(
            subject_id=subject_id,
            project_ids=_claim_values(claims, "project_ids") | organization_projects,
            region_codes=_claim_values(claims, "region_codes") | organization_regions,
            roles=roles,
            service_identity=service_identity,
            capabilities=_claim_values(claims, "capabilities"),
            capability_revision=_non_negative_int_claim(claims, "capability_revision"),
            scope_pairs=scope_pairs,
            scoped_capabilities=scoped_capabilities,
            organization_ids=organization_ids,
            organization_scope_triples=organization_scope_triples,
            organization_scoped_capabilities=organization_scoped_capabilities,
        )


def _non_negative_int_claim(claims: Mapping[str, Any], name: str) -> int:
    raw = claims.get(name, 0)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise problem(
            status=401,
            code="INVALID_ACCESS_TOKEN",
            title="Invalid access token",
            detail=f"The {name} claim must be a non-negative integer.",
        )
    return int(raw)
