"""Distributed public-auth admission, privacy-preserving lock state, and client IP handling.

The tables behind this module contain only keyed HMAC digests.  Keep raw usernames,
addresses, forwarded-header values, browser identifiers, and challenge responses at the HTTP
boundary; none belongs in persistence, audit payloads, exceptions, or logs.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil
from threading import RLock
from typing import Any, Literal, Protocol
from uuid import uuid4

from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.core.errors import ProblemException, problem

PublicAuthOperation = Literal["LOGIN", "REGISTER", "PASSWORD_RECOVERY"]
RateLimitDimension = Literal["GLOBAL", "SOURCE", "SUBJECT"]

_MAX_RETRY_AFTER_SECONDS = 86_400
_AUTH_BUCKET_RETENTION = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class PublicAuthAttempt:
    """Unpersisted public-request facts supplied by the HTTP boundary."""

    operation: PublicAuthOperation
    source_network: str
    subject_hint: str

    def __post_init__(self) -> None:
        if not self.source_network or not self.subject_hint:
            raise ValueError("public authentication attempt fields must be non-empty")


@dataclass(frozen=True, slots=True)
class RateLimit:
    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("rate-limit count must be positive")
        if self.window_seconds < 1:
            raise ValueError("rate-limit window must be positive")


@dataclass(frozen=True, slots=True)
class AuthAbusePolicy:
    """All numeric admission and lockout thresholds, supplied only from Settings."""

    login_source: RateLimit = RateLimit(limit=30, window_seconds=60)
    login_subject: RateLimit = RateLimit(limit=20, window_seconds=900)
    register_source: RateLimit = RateLimit(limit=5, window_seconds=600)
    register_subject: RateLimit = RateLimit(limit=3, window_seconds=3600)
    global_limit: RateLimit = RateLimit(limit=1000, window_seconds=60)
    failure_window_seconds: int = 900
    challenge_after_failures: int = 3
    delay_after_failures: int = 5
    delay_initial_seconds: int = 30
    delay_max_seconds: int = 600
    lock_after_failures: int = 10
    lock_seconds: int = 3600

    def __post_init__(self) -> None:
        if self.failure_window_seconds < 1:
            raise ValueError("failure window must be positive")
        if not 1 <= self.challenge_after_failures <= self.delay_after_failures:
            raise ValueError("challenge threshold must not exceed delay threshold")
        if self.delay_after_failures > self.lock_after_failures:
            raise ValueError("delay threshold must not exceed lock threshold")
        if self.delay_initial_seconds < 1 or self.delay_max_seconds < self.delay_initial_seconds:
            raise ValueError("progressive delay bounds are invalid")
        if self.lock_seconds < 1:
            raise ValueError("temporary lock duration must be positive")

    def limits_for(
        self, operation: PublicAuthOperation
    ) -> tuple[tuple[RateLimitDimension, RateLimit], ...]:
        if operation == "LOGIN":
            return (
                ("GLOBAL", self.global_limit),
                ("SOURCE", self.login_source),
                ("SUBJECT", self.login_subject),
            )
        return (
            ("GLOBAL", self.global_limit),
            ("SOURCE", self.register_source),
            ("SUBJECT", self.register_subject),
        )


def policy_from_settings(settings: Any) -> AuthAbusePolicy:
    """Project Settings into the dependency-free policy used by memory and PostgreSQL."""

    return AuthAbusePolicy(
        login_source=RateLimit(
            limit=settings.auth_login_source_rate_limit,
            window_seconds=settings.auth_login_source_rate_window_seconds,
        ),
        login_subject=RateLimit(
            limit=settings.auth_login_subject_rate_limit,
            window_seconds=settings.auth_login_subject_rate_window_seconds,
        ),
        register_source=RateLimit(
            limit=settings.auth_registration_source_rate_limit,
            window_seconds=settings.auth_registration_source_rate_window_seconds,
        ),
        register_subject=RateLimit(
            limit=settings.auth_registration_subject_rate_limit,
            window_seconds=settings.auth_registration_subject_rate_window_seconds,
        ),
        global_limit=RateLimit(
            limit=settings.auth_global_rate_limit,
            window_seconds=settings.auth_global_rate_window_seconds,
        ),
        failure_window_seconds=settings.auth_login_failure_window_seconds,
        challenge_after_failures=settings.auth_login_challenge_after_failures,
        delay_after_failures=settings.auth_login_delay_after_failures,
        delay_initial_seconds=settings.auth_login_delay_initial_seconds,
        delay_max_seconds=settings.auth_login_delay_max_seconds,
        lock_after_failures=settings.auth_login_lock_after_failures,
        lock_seconds=settings.auth_login_lock_seconds,
    )


class AbuseProtection(Protocol):
    def check(self, attempt: PublicAuthAttempt, *, request_id: str) -> bool: ...

    def check_authenticated(self, *, operation: str, subject_hint: str | None) -> None: ...

    def record_login_failure(
        self,
        attempt: PublicAuthAttempt,
        *,
        principal_id: str | None,
        request_id: str,
    ) -> None: ...

    def record_login_success(self, attempt: PublicAuthAttempt, *, request_id: str) -> None: ...

    def record_challenge_denied(
        self,
        attempt: PublicAuthAttempt,
        *,
        reason: str,
        request_id: str,
    ) -> None: ...

    def unlock_account(
        self,
        *,
        principal_id: str,
        actor_id: str,
        request_id: str,
    ) -> bool: ...


class UnconfiguredAbuseProtection:
    """Local/test fallback; Settings rejects it in staging and production."""

    def check(self, attempt: PublicAuthAttempt, *, request_id: str) -> bool:
        del attempt, request_id
        return False

    def check_authenticated(self, *, operation: str, subject_hint: str | None) -> None:
        del operation, subject_hint

    def record_login_failure(
        self,
        attempt: PublicAuthAttempt,
        *,
        principal_id: str | None,
        request_id: str,
    ) -> None:
        del attempt, principal_id, request_id

    def record_login_success(self, attempt: PublicAuthAttempt, *, request_id: str) -> None:
        del attempt, request_id

    def record_challenge_denied(
        self,
        attempt: PublicAuthAttempt,
        *,
        reason: str,
        request_id: str,
    ) -> None:
        del attempt, reason, request_id

    def unlock_account(
        self,
        *,
        principal_id: str,
        actor_id: str,
        request_id: str,
    ) -> bool:
        del principal_id, actor_id, request_id
        return False


def client_network_from_request(
    *,
    peer_host: str | None,
    forwarded_for: str | None,
    mode: Literal["peer", "trusted_proxy"],
    trusted_proxy_cidrs: tuple[str, ...],
) -> str:
    """Return an address-prefix string only after an explicit proxy trust decision."""

    peer = _parse_ip(peer_host)
    trusted_networks = tuple(
        ipaddress.ip_network(value, strict=False) for value in trusted_proxy_cidrs
    )
    if mode == "peer":
        return _network_prefix(peer)

    if not trusted_networks or not any(peer in network for network in trusted_networks):
        raise _client_network_invalid()
    if forwarded_for is None or not forwarded_for.strip():
        raise _client_network_invalid()
    raw_chain = tuple(piece.strip() for piece in forwarded_for.split(","))
    if not raw_chain or any(not piece for piece in raw_chain):
        raise _client_network_invalid()
    try:
        chain = tuple(ipaddress.ip_address(piece) for piece in raw_chain) + (peer,)
    except ValueError as exc:
        raise _client_network_invalid() from exc
    for candidate in reversed(chain):
        if not any(candidate in network for network in trusted_networks):
            return _network_prefix(candidate)
    raise _client_network_invalid()


def _parse_ip(value: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        return ipaddress.ip_address(value or "")
    except ValueError as exc:
        raise _client_network_invalid() from exc


def _network_prefix(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    prefix = 24 if address.version == 4 else 56
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))


def _client_network_invalid() -> ProblemException:
    return problem(
        status=400,
        code="CLIENT_NETWORK_INVALID",
        title="Invalid client network",
        detail="The request client network could not be verified.",
    )


def _retry_after(*, until: datetime, now: datetime) -> int:
    return min(max(ceil((until - now).total_seconds()), 1), _MAX_RETRY_AFTER_SECONDS)


def _rate_limited(*, code: str, until: datetime, now: datetime) -> ProblemException:
    return problem(
        status=429,
        code=code,
        title="Authentication temporarily limited",
        detail="Wait before attempting authentication again.",
        retryable=True,
        retry_after_seconds=_retry_after(until=until, now=now),
    )


def _utc_now(clock: Callable[[], datetime]) -> datetime:
    timestamp = clock()
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("auth-abuse clock must return a timezone-aware timestamp")
    return timestamp.astimezone(timezone.utc)


def _key(secret: bytes, kind: str, value: str) -> str:
    return hmac.new(secret, f"{kind}\0{value}".encode(), hashlib.sha256).hexdigest()


@dataclass(slots=True)
class _Bucket:
    window_started_at: datetime
    attempt_count: int
    blocked_until: datetime | None
    challenge_required_at: datetime | None = None


@dataclass(slots=True)
class _LoginState:
    principal_id: str | None
    consecutive_failures: int
    failure_window_started_at: datetime | None
    last_failed_at: datetime | None
    next_allowed_at: datetime | None
    locked_at: datetime | None
    locked_until: datetime | None


class InMemoryAbuseProtection:
    """Deterministic parity adapter used by memory runtime and focused unit tests."""

    def __init__(
        self,
        *,
        hmac_secret: str,
        policy: AuthAbusePolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(hmac_secret) < 32:
            raise ValueError("auth abuse HMAC secret must contain at least 32 characters")
        self._secret = hmac_secret.encode("utf-8")
        self._policy = policy or AuthAbusePolicy()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._buckets: dict[tuple[str, str, str], _Bucket] = {}
        self._states: dict[str, _LoginState] = {}
        self.events: list[tuple[str, str]] = []

    def check(self, attempt: PublicAuthAttempt, *, request_id: str) -> bool:
        del request_id
        with self._lock:
            timestamp = _utc_now(self._clock)
            subject_key = self._subject_key(attempt)
            if attempt.operation == "LOGIN":
                state = self._states.get(subject_key)
                if state is not None:
                    until = state.locked_until or state.next_allowed_at
                    if until is not None and until > timestamp:
                        self.events.append(("auth.login.locked.denied", subject_key))
                        raise _rate_limited(
                            code="AUTH_ACCOUNT_TEMPORARILY_LOCKED",
                            until=until,
                            now=timestamp,
                        )
            for dimension, limit in self._policy.limits_for(attempt.operation):
                key = (attempt.operation, dimension, self._bucket_key(attempt, dimension))
                bucket = self._buckets.get(key)
                window = timedelta(seconds=limit.window_seconds)
                if bucket is None or timestamp >= bucket.window_started_at + window:
                    self._buckets[key] = _Bucket(timestamp, 1, None)
                    continue
                if bucket.blocked_until is not None and bucket.blocked_until > timestamp:
                    self.events.append(("auth.rate_limit.denied", key[2]))
                    raise _rate_limited(
                        code="AUTH_RATE_LIMITED", until=bucket.blocked_until, now=timestamp
                    )
                if bucket.attempt_count >= limit.limit:
                    bucket.blocked_until = bucket.window_started_at + window
                    self.events.append(("auth.rate_limit.denied", key[2]))
                    raise _rate_limited(
                        code="AUTH_RATE_LIMITED", until=bucket.blocked_until, now=timestamp
                    )
                bucket.attempt_count += 1
            if attempt.operation != "LOGIN":
                return False
            subject_bucket = self._buckets.get(
                ("LOGIN", "SUBJECT", self._bucket_key(attempt, "SUBJECT"))
            )
            return subject_bucket is not None and subject_bucket.challenge_required_at is not None

    def check_authenticated(self, *, operation: str, subject_hint: str | None) -> None:
        # The historical extension point covered access-request writes.  Their durable
        # admission policy remains separate; this component intentionally owns only public
        # authentication boundaries so a username/network limiter cannot change approvals.
        del operation, subject_hint

    def record_login_failure(
        self,
        attempt: PublicAuthAttempt,
        *,
        principal_id: str | None,
        request_id: str,
    ) -> None:
        del request_id
        if attempt.operation != "LOGIN":
            raise ValueError("only login failures may update login state")
        with self._lock:
            timestamp = _utc_now(self._clock)
            subject_key = self._subject_key(attempt)
            state = self._states.get(subject_key)
            window = timedelta(seconds=self._policy.failure_window_seconds)
            if (
                state is None
                or state.last_failed_at is None
                or timestamp >= state.last_failed_at + window
            ):
                state = _LoginState(principal_id, 1, timestamp, timestamp, None, None, None)
            else:
                state.consecutive_failures += 1
                state.principal_id = principal_id or state.principal_id
                state.last_failed_at = timestamp
                state.next_allowed_at = None
                state.locked_at = None
                state.locked_until = None
            self._apply_failure_penalty(state, timestamp)
            self._states[subject_key] = state
            if state.locked_until is not None:
                self.events.append(("auth.login.locked", subject_key))
            elif state.next_allowed_at is not None:
                self.events.append(("auth.login.delay.applied", subject_key))
            if state.consecutive_failures >= self._policy.challenge_after_failures:
                self._mark_challenge_required(attempt, timestamp)

    def record_login_success(self, attempt: PublicAuthAttempt, *, request_id: str) -> None:
        del request_id
        if attempt.operation != "LOGIN":
            return
        with self._lock:
            subject_key = self._subject_key(attempt)
            self._states.pop(subject_key, None)
            key = ("LOGIN", "SUBJECT", self._bucket_key(attempt, "SUBJECT"))
            bucket = self._buckets.get(key)
            if bucket is not None:
                bucket.challenge_required_at = None

    def record_challenge_denied(
        self,
        attempt: PublicAuthAttempt,
        *,
        reason: str,
        request_id: str,
    ) -> None:
        del request_id
        with self._lock:
            self.events.append(("auth.challenge.denied", self._subject_key(attempt)))
            if reason not in {"AUTH_CHALLENGE_REQUIRED", "AUTH_CHALLENGE_INVALID"}:
                self.events.append(("auth.challenge.unavailable", self._subject_key(attempt)))

    def unlock_account(
        self,
        *,
        principal_id: str,
        actor_id: str,
        request_id: str,
    ) -> bool:
        del actor_id, request_id
        with self._lock:
            timestamp = _utc_now(self._clock)
            for subject_key, state in tuple(self._states.items()):
                if state.principal_id != principal_id:
                    continue
                if state.locked_until is None or state.locked_until <= timestamp:
                    return False
                del self._states[subject_key]
                self.events.append(("auth.login.unlocked", subject_key))
                return True
            return False

    def _apply_failure_penalty(self, state: _LoginState, timestamp: datetime) -> None:
        failures = state.consecutive_failures
        if failures >= self._policy.lock_after_failures:
            state.locked_at = timestamp
            state.locked_until = timestamp + timedelta(seconds=self._policy.lock_seconds)
            state.next_allowed_at = state.locked_until
            return
        if failures >= self._policy.delay_after_failures:
            exponent = failures - self._policy.delay_after_failures
            delay = min(
                self._policy.delay_initial_seconds * (2**exponent),
                self._policy.delay_max_seconds,
            )
            state.next_allowed_at = timestamp + timedelta(seconds=delay)

    def _mark_challenge_required(self, attempt: PublicAuthAttempt, timestamp: datetime) -> None:
        key = ("LOGIN", "SUBJECT", self._bucket_key(attempt, "SUBJECT"))
        bucket = self._buckets.get(key)
        if bucket is not None and bucket.challenge_required_at is None:
            bucket.challenge_required_at = timestamp

    def _subject_key(self, attempt: PublicAuthAttempt) -> str:
        return _key(self._secret, "subject", attempt.subject_hint)

    def _bucket_key(self, attempt: PublicAuthAttempt, dimension: RateLimitDimension) -> str:
        value = {
            "GLOBAL": "global",
            "SOURCE": attempt.source_network,
            "SUBJECT": attempt.subject_hint,
        }[dimension]
        return _key(self._secret, f"{attempt.operation.lower()}:{dimension.lower()}", value)


class PostgresAbuseProtection:
    """Transactional PostgreSQL adapter shared by every API replica."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        hmac_secret: str,
        policy: AuthAbusePolicy | None = None,
    ) -> None:
        if len(hmac_secret) < 32:
            raise ValueError("auth abuse HMAC secret must contain at least 32 characters")
        self._connection_factory = connection_factory
        self._secret = hmac_secret.encode("utf-8")
        self._policy = policy or AuthAbusePolicy()

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        hmac_secret: str,
        policy: AuthAbusePolicy | None = None,
    ) -> PostgresAbuseProtection:
        normalized = normalize_postgres_dsn(dsn)

        def connect() -> Any:
            import psycopg

            return psycopg.connect(normalized)

        return cls(connect, hmac_secret=hmac_secret, policy=policy)

    def check(self, attempt: PublicAuthAttempt, *, request_id: str) -> bool:
        connection = self._connection_factory()
        cursor = connection.cursor()
        denial: ProblemException | None = None
        challenge_required = False
        try:
            timestamp = self._database_timestamp(cursor)
            self._purge_expired(cursor, timestamp)
            subject_key = self._subject_key(attempt)
            if attempt.operation == "LOGIN":
                cursor.execute(
                    """
                    SELECT next_allowed_at, locked_until
                    FROM access_control.auth_login_states
                    WHERE subject_key_hash = %s
                    FOR UPDATE
                    """,
                    (subject_key,),
                )
                state = cursor.fetchone()
                if state is not None:
                    next_allowed_at, locked_until = state
                    until = locked_until or next_allowed_at
                    if until is not None and until > timestamp:
                        self._audit(
                            cursor,
                            action="auth.login.locked.denied",
                            resource_id=subject_key,
                            request_id=request_id,
                            safe_details={"reason": "TEMPORARY_LOCK"},
                            occurred_at=timestamp,
                        )
                        denial = _rate_limited(
                            code="AUTH_ACCOUNT_TEMPORARILY_LOCKED",
                            until=until,
                            now=timestamp,
                        )
            if denial is None:
                for dimension, limit in self._policy.limits_for(attempt.operation):
                    denial = self._check_bucket(
                        cursor,
                        attempt=attempt,
                        dimension=dimension,
                        limit=limit,
                        timestamp=timestamp,
                        request_id=request_id,
                    )
                    if denial is not None:
                        break
            if denial is None and attempt.operation == "LOGIN":
                cursor.execute(
                    """
                    SELECT challenge_required_at
                    FROM access_control.auth_rate_limit_buckets
                    WHERE operation = 'LOGIN' AND dimension = 'SUBJECT'
                      AND bucket_key_hash = %s
                    FOR UPDATE
                    """,
                    (self._bucket_key(attempt, "SUBJECT"),),
                )
                row = cursor.fetchone()
                challenge_required = row is not None and row[0] is not None
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
        if denial is not None:
            raise denial
        return challenge_required

    def check_authenticated(self, *, operation: str, subject_hint: str | None) -> None:
        del operation, subject_hint

    def record_login_failure(
        self,
        attempt: PublicAuthAttempt,
        *,
        principal_id: str | None,
        request_id: str,
    ) -> None:
        if attempt.operation != "LOGIN":
            raise ValueError("only login failures may update login state")
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            timestamp = self._database_timestamp(cursor)
            self._purge_expired(cursor, timestamp)
            subject_key = self._subject_key(attempt)
            cursor.execute(
                """
                INSERT INTO access_control.auth_login_states (
                    subject_key_hash, principal_id, expires_at
                ) VALUES (%s, %s::uuid, %s)
                ON CONFLICT (subject_key_hash) DO NOTHING
                """,
                (subject_key, principal_id, timestamp + _AUTH_BUCKET_RETENTION),
            )
            cursor.execute(
                """
                SELECT principal_id::text, consecutive_failures, failure_window_started_at,
                       last_failed_at
                FROM access_control.auth_login_states
                WHERE subject_key_hash = %s
                FOR UPDATE
                """,
                (subject_key,),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("auth login state insert was not visible")
            prior_principal_id, failures, started_at, last_failed_at = row
            window = timedelta(seconds=self._policy.failure_window_seconds)
            if last_failed_at is None or timestamp >= last_failed_at + window:
                failures = 1
                started_at = timestamp
            else:
                failures = int(failures) + 1
            next_allowed_at: datetime | None = None
            locked_at: datetime | None = None
            locked_until: datetime | None = None
            action: str | None = None
            safe_details: dict[str, object] | None = None
            if failures >= self._policy.lock_after_failures:
                locked_at = timestamp
                locked_until = timestamp + timedelta(seconds=self._policy.lock_seconds)
                next_allowed_at = locked_until
                action = "auth.login.locked"
                safe_details = {"reason": "TEMPORARY_LOCK", "failure_count": failures}
            elif failures >= self._policy.delay_after_failures:
                exponent = failures - self._policy.delay_after_failures
                delay = min(
                    self._policy.delay_initial_seconds * (2**exponent),
                    self._policy.delay_max_seconds,
                )
                next_allowed_at = timestamp + timedelta(seconds=delay)
                action = "auth.login.delay.applied"
                safe_details = {"reason": "PROGRESSIVE_DELAY", "failure_count": failures}
            expires_at = max(
                timestamp + _AUTH_BUCKET_RETENTION,
                next_allowed_at or timestamp,
                locked_until or timestamp,
            )
            cursor.execute(
                """
                UPDATE access_control.auth_login_states
                SET principal_id = COALESCE(%s::uuid, principal_id),
                    consecutive_failures = %s,
                    failure_window_started_at = %s,
                    last_failed_at = %s,
                    next_allowed_at = %s,
                    locked_at = %s,
                    locked_until = %s,
                    updated_at = %s,
                    expires_at = %s
                WHERE subject_key_hash = %s
                """,
                (
                    principal_id or prior_principal_id,
                    failures,
                    started_at,
                    timestamp,
                    next_allowed_at,
                    locked_at,
                    locked_until,
                    timestamp,
                    expires_at,
                    subject_key,
                ),
            )
            if failures >= self._policy.challenge_after_failures:
                cursor.execute(
                    """
                    UPDATE access_control.auth_rate_limit_buckets
                    SET challenge_required_at = COALESCE(challenge_required_at, %s),
                        updated_at = %s
                    WHERE operation = 'LOGIN' AND dimension = 'SUBJECT'
                      AND bucket_key_hash = %s
                    """,
                    (timestamp, timestamp, self._bucket_key(attempt, "SUBJECT")),
                )
            if action is not None and safe_details is not None:
                self._audit(
                    cursor,
                    action=action,
                    resource_id=subject_key,
                    request_id=request_id,
                    safe_details=safe_details,
                    occurred_at=timestamp,
                    actor_id=principal_id or prior_principal_id,
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def record_login_success(self, attempt: PublicAuthAttempt, *, request_id: str) -> None:
        del request_id
        if attempt.operation != "LOGIN":
            return
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            timestamp = self._database_timestamp(cursor)
            subject_key = self._subject_key(attempt)
            cursor.execute(
                "DELETE FROM access_control.auth_login_states WHERE subject_key_hash = %s",
                (subject_key,),
            )
            cursor.execute(
                """
                UPDATE access_control.auth_rate_limit_buckets
                SET challenge_required_at = NULL, updated_at = %s
                WHERE operation = 'LOGIN' AND dimension = 'SUBJECT'
                  AND bucket_key_hash = %s
                """,
                (timestamp, self._bucket_key(attempt, "SUBJECT")),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def record_challenge_denied(
        self,
        attempt: PublicAuthAttempt,
        *,
        reason: str,
        request_id: str,
    ) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            timestamp = self._database_timestamp(cursor)
            action = (
                "auth.challenge.denied"
                if reason in {"AUTH_CHALLENGE_REQUIRED", "AUTH_CHALLENGE_INVALID"}
                else "auth.challenge.unavailable"
            )
            self._audit(
                cursor,
                action=action,
                resource_id=self._subject_key(attempt),
                request_id=request_id,
                safe_details={"provider": "TURNSTILE", "reason": reason},
                occurred_at=timestamp,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def unlock_account(
        self,
        *,
        principal_id: str,
        actor_id: str,
        request_id: str,
    ) -> bool:
        """Clear one active temporary lock atomically and audit only a real transition."""

        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            timestamp = self._database_timestamp(cursor)
            self._purge_expired(cursor, timestamp)
            cursor.execute(
                """
                SELECT subject_key_hash, locked_until
                FROM access_control.auth_login_states
                WHERE principal_id = %s::uuid
                FOR UPDATE
                """,
                (principal_id,),
            )
            row = cursor.fetchone()
            if row is None or row[1] is None or row[1] <= timestamp:
                connection.commit()
                return False
            subject_key = str(row[0])
            cursor.execute(
                "DELETE FROM access_control.auth_login_states WHERE subject_key_hash = %s",
                (subject_key,),
            )
            self._audit(
                cursor,
                action="auth.login.unlocked",
                resource_type="account",
                resource_id=principal_id,
                request_id=request_id,
                safe_details={"reason": "ADMIN_UNLOCK"},
                occurred_at=timestamp,
                actor_id=actor_id,
                outcome="SUCCEEDED",
            )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def _check_bucket(
        self,
        cursor: Any,
        *,
        attempt: PublicAuthAttempt,
        dimension: RateLimitDimension,
        limit: RateLimit,
        timestamp: datetime,
        request_id: str,
    ) -> ProblemException | None:
        key = self._bucket_key(attempt, dimension)
        cursor.execute(
            """
            INSERT INTO access_control.auth_rate_limit_buckets (
                operation, dimension, bucket_key_hash, window_started_at,
                attempt_count, expires_at
            ) VALUES (%s, %s, %s, %s, 0, %s)
            ON CONFLICT (operation, dimension, bucket_key_hash) DO NOTHING
            """,
            (
                attempt.operation,
                dimension,
                key,
                timestamp,
                timestamp + _AUTH_BUCKET_RETENTION,
            ),
        )
        cursor.execute(
            """
            SELECT window_started_at, attempt_count, blocked_until
            FROM access_control.auth_rate_limit_buckets
            WHERE operation = %s AND dimension = %s AND bucket_key_hash = %s
            FOR UPDATE
            """,
            (attempt.operation, dimension, key),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("auth rate-limit bucket insert was not visible")
        started_at, count, blocked_until = row
        window = timedelta(seconds=limit.window_seconds)
        until = started_at + window
        if blocked_until is not None and blocked_until > timestamp:
            return self._rate_denial(
                cursor, key=key, request_id=request_id, timestamp=timestamp, until=blocked_until
            )
        if timestamp >= until:
            cursor.execute(
                """
                UPDATE access_control.auth_rate_limit_buckets
                SET window_started_at = %s, attempt_count = 1, blocked_until = NULL,
                    updated_at = %s, expires_at = %s
                WHERE operation = %s AND dimension = %s AND bucket_key_hash = %s
                """,
                (
                    timestamp,
                    timestamp,
                    timestamp + _AUTH_BUCKET_RETENTION,
                    attempt.operation,
                    dimension,
                    key,
                ),
            )
            return None
        if int(count) >= limit.limit:
            cursor.execute(
                """
                UPDATE access_control.auth_rate_limit_buckets
                SET blocked_until = %s, updated_at = %s, expires_at = %s
                WHERE operation = %s AND dimension = %s AND bucket_key_hash = %s
                """,
                (
                    until,
                    timestamp,
                    max(timestamp + _AUTH_BUCKET_RETENTION, until),
                    attempt.operation,
                    dimension,
                    key,
                ),
            )
            return self._rate_denial(
                cursor, key=key, request_id=request_id, timestamp=timestamp, until=until
            )
        cursor.execute(
            """
            UPDATE access_control.auth_rate_limit_buckets
            SET attempt_count = attempt_count + 1, updated_at = %s, expires_at = %s
            WHERE operation = %s AND dimension = %s AND bucket_key_hash = %s
            """,
            (timestamp, timestamp + _AUTH_BUCKET_RETENTION, attempt.operation, dimension, key),
        )
        return None

    def _rate_denial(
        self,
        cursor: Any,
        *,
        key: str,
        request_id: str,
        timestamp: datetime,
        until: datetime,
    ) -> ProblemException:
        self._audit(
            cursor,
            action="auth.rate_limit.denied",
            resource_id=key,
            request_id=request_id,
            safe_details={"reason": "RATE_LIMIT"},
            occurred_at=timestamp,
        )
        return _rate_limited(code="AUTH_RATE_LIMITED", until=until, now=timestamp)

    def _purge_expired(self, cursor: Any, timestamp: datetime) -> None:
        # Bounded cleanup prevents random usernames and networks from growing the state tables
        # without bound while avoiding a table-wide delete on any authentication request.
        for relation in (
            "access_control.auth_login_states",
            "access_control.auth_rate_limit_buckets",
        ):
            cursor.execute(
                f"""
                DELETE FROM {relation}
                WHERE ctid IN (
                    SELECT ctid FROM {relation}
                    WHERE expires_at <= %s
                    ORDER BY expires_at
                    LIMIT 128
                )
                """,
                (timestamp,),
            )

    def _database_timestamp(self, cursor: Any) -> datetime:
        cursor.execute("SELECT clock_timestamp()")
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("PostgreSQL did not return a clock timestamp")
        timestamp = row[0]
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            raise RuntimeError("PostgreSQL returned an invalid clock timestamp")
        return timestamp.astimezone(timezone.utc)

    def _subject_key(self, attempt: PublicAuthAttempt) -> str:
        return _key(self._secret, "subject", attempt.subject_hint)

    def _bucket_key(self, attempt: PublicAuthAttempt, dimension: RateLimitDimension) -> str:
        value = {
            "GLOBAL": "global",
            "SOURCE": attempt.source_network,
            "SUBJECT": attempt.subject_hint,
        }[dimension]
        return _key(self._secret, f"{attempt.operation.lower()}:{dimension.lower()}", value)

    @staticmethod
    def _audit(
        cursor: Any,
        *,
        action: str,
        resource_type: str = "authentication",
        resource_id: str,
        request_id: str,
        safe_details: dict[str, object],
        occurred_at: datetime,
        actor_id: str | None = None,
        outcome: str = "DENIED",
    ) -> None:
        cursor.execute(
            """
            INSERT INTO access_control.audit_events (
                event_id, scope_kind, project_id, actor_id, action, resource_type,
                resource_id, request_id, outcome, safe_details, occurred_at
            ) VALUES (
                %s::uuid, 'PLATFORM', NULL, %s, %s, %s,
                %s, %s, %s, %s::jsonb, %s
            )
            """,
            (
                str(uuid4()),
                actor_id,
                action,
                resource_type,
                resource_id,
                request_id,
                outcome,
                json.dumps(safe_details, sort_keys=True),
                occurred_at,
            ),
        )
