"""Human-readable task storage accounting, independent of annotation progress."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import ByteString

UsageCategory = Literal["RAW", "EPISODE", "PROCESSING", "ANNOTATION", "EXPORT", "OTHER"]
USAGE_CATEGORIES: tuple[UsageCategory, ...] = (
    "RAW",
    "EPISODE",
    "PROCESSING",
    "ANNOTATION",
    "EXPORT",
    "OTHER",
)


class UsageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskStorageCategory(UsageModel):
    category: UsageCategory
    bytes: ByteString
    file_count: int = Field(ge=0)


class TaskStorageSummary(UsageModel):
    group_id: str
    task_code: str | None = None
    task_name: str
    total_bytes: ByteString
    file_count: int = Field(ge=0)
    categories: tuple[TaskStorageCategory, ...]
    raw_count: int = Field(ge=0)
    episode_count: int = Field(ge=0)
    annotation_complete_count: int = Field(ge=0)
    export_count: int = Field(ge=0)
    shared_bytes: ByteString = "0"


class StorageDiskUsage(UsageModel):
    total_bytes: ByteString
    used_bytes: ByteString
    available_bytes: ByteString
    reserved_bytes: ByteString


class StoragePurposeUsage(UsageModel):
    category: str
    label: str
    bytes: ByteString | None
    retention: Literal["KEEP", "CONDITIONAL", "REVIEW"]
    description: str


class StorageLocationUsage(UsageModel):
    location: Literal["LOCAL", "OSS"]
    label: str
    scope: str
    total_bytes: ByteString
    complete: bool
    categories: tuple[StoragePurposeUsage, ...]
    note: str


class ProjectTaskStorage(UsageModel):
    project_id: str
    observed_at: datetime
    total_bytes: ByteString
    file_count: int = Field(ge=0)
    categories: tuple[TaskStorageCategory, ...]
    tasks: tuple[TaskStorageSummary, ...]
    disk: StorageDiskUsage | None = None
    database_bytes: ByteString | None = None
    object_store_bytes: ByteString | None = None
    object_store_file_count: int | None = Field(default=None, ge=0)
    storage_locations: tuple[StorageLocationUsage, ...] = ()
    warnings: tuple[str, ...] = ()


class TaskStorageFile(UsageModel):
    file_id: str
    category: UsageCategory
    name: str
    relative_path: str
    record_name: str
    robot_name: str
    status: str
    bytes: ByteString
    updated_at: datetime
    shared: bool = False
    related_task_names: tuple[str, ...] = ()


class TaskStorageFilePage(UsageModel):
    project_id: str
    group_id: str
    observed_at: datetime
    items: tuple[TaskStorageFile, ...]
    total_count: int = Field(ge=0)
    total_bytes: ByteString
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
