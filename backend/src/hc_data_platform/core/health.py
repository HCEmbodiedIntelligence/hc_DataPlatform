from __future__ import annotations

import asyncio
import importlib
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .config import Settings

REQUIRED_DEPENDENCIES = frozenset({"postgresql", "temporal", "object_storage"})


class DependencyStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "not_ready"]
    latency_ms: float = Field(ge=0)
    detail: str | None = None


class ReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "not_ready"]
    dependencies: dict[str, DependencyStatus]


class ReadinessProbe(Protocol):
    async def check(self) -> DependencyStatus | bool | None: ...


@dataclass(frozen=True, slots=True)
class CallableProbe:
    callback: Callable[[], Awaitable[DependencyStatus | bool | None]]

    async def check(self) -> DependencyStatus | bool | None:
        return await self.callback()


@dataclass(frozen=True, slots=True)
class PostgreSQLProbe:
    settings: Settings

    async def check(self) -> None:
        asyncpg: Any = importlib.import_module("asyncpg")
        dsn = self.settings.postgres_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        connection = await asyncpg.connect(
            dsn=dsn,
            timeout=self.settings.readiness_timeout_seconds,
        )
        try:
            value = await connection.fetchval("SELECT 1")
            if value != 1:
                raise RuntimeError("PostgreSQL validation query returned an unexpected value")
            if self.settings.enforce_schema_migrations:
                from .migrations import expected_migration_checksums

                rows = await connection.fetch(
                    "SELECT version, checksum_sha256 FROM core.schema_migrations"
                )
                applied = {str(row["version"]): str(row["checksum_sha256"]) for row in rows}
                expected = expected_migration_checksums()
                if applied != expected:
                    raise RuntimeError("database schema migrations do not match this release")
        finally:
            await connection.close()


@dataclass(frozen=True, slots=True)
class TemporalProbe:
    settings: Settings

    async def check(self) -> None:
        client_module: Any = importlib.import_module("temporalio.client")
        client = await client_module.Client.connect(self.settings.temporal_target, lazy=True)
        healthy = await client.service_client.check_health()
        if not healthy:
            raise RuntimeError("Temporal health service reported not serving")


@dataclass(frozen=True, slots=True)
class ObjectStorageProbe:
    settings: Settings

    async def check(self) -> None:
        boto3: Any = importlib.import_module("boto3")
        config_module: Any = importlib.import_module("botocore.config")
        config = config_module.Config(
            connect_timeout=self.settings.readiness_timeout_seconds,
            read_timeout=self.settings.readiness_timeout_seconds,
            retries={"max_attempts": 0},
        )
        client = boto3.client(
            "s3",
            endpoint_url=self.settings.object_store_endpoint,
            aws_access_key_id=self.settings.object_store_access_key,
            aws_secret_access_key=self.settings.object_store_secret_key,
            region_name=self.settings.object_store_region,
            config=config,
        )
        await asyncio.to_thread(client.head_bucket, Bucket=self.settings.object_store_bucket)


def default_readiness_probes(settings: Settings) -> dict[str, ReadinessProbe]:
    return {
        "postgresql": PostgreSQLProbe(settings),
        "temporal": TemporalProbe(settings),
        "object_storage": ObjectStorageProbe(settings),
    }


def validate_readiness_probes(probes: Mapping[str, ReadinessProbe]) -> None:
    names = set(probes)
    missing = REQUIRED_DEPENDENCIES - names
    extra = names - REQUIRED_DEPENDENCIES
    if missing or extra:
        parts: list[str] = []
        if missing:
            parts.append(f"missing: {', '.join(sorted(missing))}")
        if extra:
            parts.append(f"unknown: {', '.join(sorted(extra))}")
        raise ValueError(f"readiness probes must match required dependencies ({'; '.join(parts)})")


async def _run_probe(
    probe: ReadinessProbe,
    timeout_seconds: float,
) -> DependencyStatus:
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(probe.check(), timeout=timeout_seconds)
        latency_ms = (time.perf_counter() - started) * 1000
        if isinstance(result, DependencyStatus):
            return result.model_copy(update={"latency_ms": latency_ms})
        if result is False:
            return DependencyStatus(
                status="not_ready",
                latency_ms=latency_ms,
                detail="probe reported not ready",
            )
        return DependencyStatus(status="ready", latency_ms=latency_ms)
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        detail = (
            "probe timed out" if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) else str(exc)
        )
        return DependencyStatus(
            status="not_ready",
            latency_ms=latency_ms,
            detail=detail or exc.__class__.__name__,
        )


async def check_readiness(
    probes: Mapping[str, ReadinessProbe],
    *,
    timeout_seconds: float,
) -> ReadinessReport:
    validate_readiness_probes(probes)
    names = sorted(probes)
    results = await asyncio.gather(*(_run_probe(probes[name], timeout_seconds) for name in names))
    dependencies = dict(zip(names, results, strict=True))
    ready = all(result.status == "ready" for result in results)
    return ReadinessReport(
        status="ready" if ready else "not_ready",
        dependencies=dependencies,
    )
