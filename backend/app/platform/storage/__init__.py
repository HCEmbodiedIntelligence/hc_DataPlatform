from __future__ import annotations

import os
from functools import lru_cache

from app.platform.ports import ObjectStoragePort
from app.platform.ports.fakes import FakeObjectStoragePort
from app.platform.storage.oss import S3ObjectStorage


@lru_cache(maxsize=1)
def get_object_storage() -> ObjectStoragePort:
    if os.getenv("APP_ENV", "development").lower() == "test":
        return FakeObjectStoragePort()
    return S3ObjectStorage()


__all__ = ["S3ObjectStorage", "get_object_storage"]
