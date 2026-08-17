from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

import jwt

from hc_data_platform.core.errors import problem


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
ROLE_PERMISSIONS: Mapping[str, frozenset[str]] = {
    Role.UPLOADER.value: frozenset({Permission.READ.value, Permission.UPLOAD.value}),
    Role.ANNOTATOR.value: frozenset({Permission.READ.value, Permission.ANNOTATE.value}),
    Role.REVIEWER.value: frozenset({Permission.READ.value, Permission.REVIEW.value}),
    Role.PUBLISHER.value: frozenset({Permission.READ.value, Permission.PUBLISH.value}),
    Role.ADMIN.value: ALL_PERMISSIONS,
}

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

    def __post_init__(self) -> None:
        if not self.subject_id:
            raise ValueError("subject_id must not be empty")
        if any(not value for value in (*self.project_ids, *self.region_codes)):
            raise ValueError("project and region scopes must not contain empty values")
        unknown = self.roles - ALLOWED_ROLES
        if unknown:
            raise ValueError(f"unknown roles: {', '.join(sorted(unknown))}")

    @property
    def permissions(self) -> frozenset[str]:
        granted: set[str] = set()
        for role in self.roles:
            granted.update(ROLE_PERMISSIONS.get(role, ()))
        return frozenset(granted)

    def require_role(self, *allowed: str | Role) -> None:
        normalized = frozenset(role.value if isinstance(role, Role) else role for role in allowed)
        if not normalized or not self.roles.intersection(normalized):
            raise problem(
                status=403,
                code="ROLE_REQUIRED",
                title="Insufficient role",
                detail=f"One of these roles is required: {', '.join(sorted(normalized))}.",
            )

    def require_permission(self, permission: str | Permission) -> None:
        normalized = permission.value if isinstance(permission, Permission) else permission
        if normalized not in ALL_PERMISSIONS or normalized not in self.permissions:
            raise problem(
                status=403,
                code="PERMISSION_REQUIRED",
                title="Insufficient permission",
                detail=f"The {normalized!r} permission is required.",
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
        )
