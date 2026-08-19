from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

import jwt

from hc_data_platform.core.errors import problem

from .capabilities import (
    PERMISSION_CAPABILITIES,
    capabilities_for_roles,
    capabilities_from_legacy_roles,
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

    def __post_init__(self) -> None:
        if not self.subject_id:
            raise ValueError("subject_id must not be empty")
        if any(not value for value in (*self.project_ids, *self.region_codes)):
            raise ValueError("project and region scopes must not contain empty values")
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

    @property
    def permissions(self) -> frozenset[str]:
        effective = self.effective_capabilities()
        return frozenset(
            permission
            for permission, accepted in PERMISSION_CAPABILITIES.items()
            if effective.intersection(accepted)
        )

    def effective_capabilities(self, project_id: str | None = None) -> frozenset[str]:
        """Resolve approved keys plus the one compatibility projection for legacy JWTs."""

        scoped = (
            frozenset(
                capability
                for project, capability in self.scoped_capabilities
                if project == project_id
            )
            if project_id is not None
            else frozenset()
        )
        return self.capabilities | scoped | capabilities_from_legacy_roles(self.roles)

    def legacy_roles(self, project_id: str | None = None) -> frozenset[str]:
        return legacy_roles_from_capabilities(self.effective_capabilities(project_id))

    def require_role(self, *allowed: str | Role, project_id: str | None = None) -> None:
        normalized = frozenset(role.value if isinstance(role, Role) else role for role in allowed)
        required_capabilities = capabilities_for_roles(normalized)
        if (
            not normalized
            or not required_capabilities
            or not self.effective_capabilities(project_id).intersection(required_capabilities)
        ):
            raise problem(
                status=403,
                code="ROLE_REQUIRED",
                title="Insufficient role",
                detail=f"One of these roles is required: {', '.join(sorted(normalized))}.",
            )

    def require_permission(
        self, permission: str | Permission, project_id: str | None = None
    ) -> None:
        normalized = permission.value if isinstance(permission, Permission) else permission
        accepted = PERMISSION_CAPABILITIES.get(normalized, frozenset())
        if normalized not in ALL_PERMISSIONS or not self.effective_capabilities(
            project_id
        ).intersection(accepted):
            raise problem(
                status=403,
                code="PERMISSION_REQUIRED",
                title="Insufficient permission",
                detail=f"The {normalized!r} permission is required.",
            )

    def has_capability(self, capability: str, project_id: str | None = None) -> bool:
        return capability in self.effective_capabilities(project_id)

    def require_capability(self, capability: str, project_id: str | None = None) -> None:
        if not capability or not self.has_capability(capability, project_id):
            raise problem(
                status=403,
                code="CAPABILITY_REQUIRED",
                title="Insufficient capability",
                detail="The current project scope does not grant the required capability.",
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
        return AuthContext(
            subject_id=subject_id,
            project_ids=_claim_values(claims, "project_ids"),
            region_codes=_claim_values(claims, "region_codes"),
            roles=roles,
            service_identity=service_identity,
            capabilities=_claim_values(claims, "capabilities"),
            capability_revision=_non_negative_int_claim(claims, "capability_revision"),
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
