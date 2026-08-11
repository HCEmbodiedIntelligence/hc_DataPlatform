from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.core import audit, idempotency, outbox  # noqa: E402,F401
from app.core.db import Base, get_session  # noqa: E402
from app.domains.ingest import models  # noqa: E402,F401
from app.main import create_app  # noqa: E402
from app.platform.ports.fakes import FakeObjectStoragePort  # noqa: E402
from app.platform.storage import get_object_storage  # noqa: E402

TEST_ENGINE = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TEST_SESSIONS = async_sessionmaker(TEST_ENGINE, class_=AsyncSession, expire_on_commit=False)
TEST_TABLES = [
    idempotency.IdempotencyRecord.__table__,
    outbox.OutboxRecord.__table__,
    audit.AuditRecord.__table__,
    models.DataSource.__table__,
    models.CredentialRef.__table__,
    models.ConnectionTest.__table__,
    models.UploadSession.__table__,
    models.UploadObject.__table__,
    models.UploadPartAttempt.__table__,
    models.SourceManifest.__table__,
    models.ManifestNode.__table__,
    models.VerificationRun.__table__,
    models.VerificationStage.__table__,
    models.VerificationFinding.__table__,
    models.Quarantine.__table__,
    models.UploadJob.__table__,
    models.DatasetRegistrationReceipt.__table__,
    models.UploadEvent.__table__,
]


@pytest_asyncio.fixture(autouse=True)
async def clean_database() -> AsyncIterator[None]:
    storage = get_object_storage()
    if isinstance(storage, FakeObjectStoragePort):
        storage.objects.clear()
        storage.calls.clear()
    async with TEST_ENGINE.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=TEST_TABLES))
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=TEST_TABLES))
    yield


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    async with TEST_SESSIONS() as session:
        async with session.begin():
            yield session


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with TEST_SESSIONS() as session:
            try:
                async with session.begin():
                    yield session
            except BaseException:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


@pytest_asyncio.fixture
def headers() -> dict[str, str]:
    return {
        "Authorization": (
            "Bearer test:user_fx_01:ingest_source.read,ingest_source.manage,"
            "upload.read,upload.manage"
        ),
        "X-Organization-Id": "org_fx_01",
        "X-Client-Version": "test-1",
        "Accept-Language": "en-US",
    }
