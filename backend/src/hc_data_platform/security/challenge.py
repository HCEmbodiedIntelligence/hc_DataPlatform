"""Server-side public-auth challenge verification.

Turnstile response tokens are bearer-like, single-use credentials.  They must stay at the
HTTP/service boundary: never persist, audit, log, include in a problem detail, or reuse them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol
from uuid import uuid4

import httpx

from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException, problem

from .access_models import (
    PublicAuthChallengeConfiguration,
    PublicAuthChallengeProvider,
)

ChallengeOperation = Literal["LOGIN", "REGISTER", "PASSWORD_RECOVERY"]
_TURNSTILE_SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_MAX_RESPONSE_TOKEN_CHARS = 2_048


class PublicAuthChallengeVerifier(Protocol):
    """Provider-neutral seam composed only from validated runtime settings."""

    @property
    def configuration(self) -> PublicAuthChallengeConfiguration | None: ...

    def verify(
        self,
        *,
        response_token: str | None,
        operation: ChallengeOperation,
    ) -> None: ...


class DisabledPublicAuthChallengeVerifier:
    """Safe local/test default. Production Settings never permit this verifier when enabled."""

    @property
    def configuration(self) -> PublicAuthChallengeConfiguration | None:
        return None

    def verify(
        self,
        *,
        response_token: str | None,
        operation: ChallengeOperation,
    ) -> None:
        del response_token, operation
        raise problem(
            status=503,
            code="AUTH_CHALLENGE_UNAVAILABLE",
            title="Authentication challenge unavailable",
            detail="Authentication challenge verification is temporarily unavailable.",
            retryable=True,
        )


class TurnstileChallengeVerifier:
    """Strict Cloudflare Siteverify adapter with one idempotent transport retry."""

    def __init__(
        self,
        *,
        site_key: str,
        secret: str,
        expected_hostnames: tuple[str, ...],
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        post: Callable[[dict[str, str]], httpx.Response] | None = None,
    ) -> None:
        if not site_key or not secret or not expected_hostnames:
            raise ValueError("Turnstile verifier requires site key, secret, and expected hostnames")
        self._configuration = PublicAuthChallengeConfiguration(
            provider=PublicAuthChallengeProvider.TURNSTILE,
            site_key=site_key,
        )
        self._secret = secret
        self._expected_hostnames = frozenset(expected_hostnames)
        self._timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=read_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        self._post = post or self._post_siteverify

    @property
    def configuration(self) -> PublicAuthChallengeConfiguration:
        return self._configuration

    def verify(
        self,
        *,
        response_token: str | None,
        operation: ChallengeOperation,
    ) -> None:
        if response_token is None or not response_token.strip():
            raise _challenge_required()
        if len(response_token) > _MAX_RESPONSE_TOKEN_CHARS:
            raise _challenge_invalid()

        payload = {
            "secret": self._secret,
            "response": response_token,
            "idempotency_key": str(uuid4()),
        }
        response: httpx.Response | None = None
        for attempt in range(2):
            try:
                response = self._post(payload)
                break
            except httpx.TransportError:
                if attempt == 1:
                    raise _challenge_unavailable() from None
        if response is None or response.status_code < 200 or response.status_code >= 300:
            raise _challenge_unavailable()
        try:
            result = response.json()
        except ValueError:
            raise _challenge_unavailable() from None
        if not isinstance(result, dict) or result.get("success") is not True:
            raise _challenge_invalid()
        if result.get("action") != operation.lower():
            raise _challenge_invalid()
        hostname = result.get("hostname")
        if (
            not isinstance(hostname, str)
            or hostname.rstrip(".").lower() not in self._expected_hostnames
        ):
            raise _challenge_invalid()

    def _post_siteverify(self, payload: dict[str, str]) -> httpx.Response:
        return httpx.post(
            _TURNSTILE_SITEVERIFY_URL,
            data=payload,
            timeout=self._timeout,
            follow_redirects=False,
        )


def challenge_verifier_from_settings(settings: Settings) -> PublicAuthChallengeVerifier:
    """Construct the only production provider from the validated Settings projection."""

    provider = settings.auth_challenge_provider
    if provider == "disabled":
        return DisabledPublicAuthChallengeVerifier()
    if provider != "turnstile":
        raise ValueError("unknown public authentication challenge provider")
    secret_value = settings.auth_turnstile_secret
    secret = None if secret_value is None else secret_value.get_secret_value()
    site_key = settings.auth_turnstile_site_key
    hostnames = settings.auth_turnstile_expected_hostnames
    if secret is None or site_key is None:
        raise ValueError("Turnstile challenge provider settings are incomplete")
    return TurnstileChallengeVerifier(
        site_key=site_key,
        secret=secret,
        expected_hostnames=hostnames,
        connect_timeout_seconds=settings.auth_turnstile_connect_timeout_seconds,
        read_timeout_seconds=settings.auth_turnstile_read_timeout_seconds,
    )


def _challenge_required() -> ProblemException:
    return problem(
        status=403,
        code="AUTH_CHALLENGE_REQUIRED",
        title="Authentication challenge required",
        detail="Complete the authentication challenge before trying again.",
    )


def _challenge_invalid() -> ProblemException:
    return problem(
        status=403,
        code="AUTH_CHALLENGE_INVALID",
        title="Authentication challenge rejected",
        detail="Complete a new authentication challenge before trying again.",
    )


def _challenge_unavailable() -> ProblemException:
    return problem(
        status=503,
        code="AUTH_CHALLENGE_UNAVAILABLE",
        title="Authentication challenge unavailable",
        detail="Authentication challenge verification is temporarily unavailable.",
        retryable=True,
    )
