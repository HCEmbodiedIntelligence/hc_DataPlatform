"""Versioned public contracts for raw MCAP verification."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VerificationStatus(str, Enum):
    VERIFIED = "RAW_VERIFIED"
    REJECTED = "REJECTED"


class VerificationSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class VerificationCode(str, Enum):
    MISSING_MAGIC = "MCAP_MISSING_MAGIC"
    TRUNCATED = "MCAP_TRUNCATED"
    RECORD_TOO_LARGE = "MCAP_RECORD_TOO_LARGE"
    HEADER_MISSING = "MCAP_HEADER_MISSING"
    DATA_END_MISSING = "MCAP_DATA_END_MISSING"
    FOOTER_MISSING = "MCAP_FOOTER_MISSING"
    TRAILING_MAGIC_MISSING = "MCAP_TRAILING_MAGIC_MISSING"
    RECORD_INVALID = "MCAP_RECORD_INVALID"
    DATA_SECTION_CRC_MISMATCH = "MCAP_DATA_SECTION_CRC_MISMATCH"
    CHUNK_CRC_MISMATCH = "MCAP_CHUNK_CRC_MISMATCH"
    CHUNK_SIZE_MISMATCH = "MCAP_CHUNK_SIZE_MISMATCH"
    CHUNK_TOO_LARGE = "MCAP_CHUNK_TOO_LARGE"
    CHUNK_DECOMPRESSION_FAILED = "MCAP_CHUNK_DECOMPRESSION_FAILED"
    UNSUPPORTED_COMPRESSION = "MCAP_UNSUPPORTED_COMPRESSION"
    SUMMARY_INVALID = "MCAP_SUMMARY_INVALID"
    SUMMARY_CRC_MISMATCH = "MCAP_SUMMARY_CRC_MISMATCH"
    INDEX_INVALID = "MCAP_INDEX_INVALID"
    SCHEMA_MISSING = "MCAP_SCHEMA_MISSING"
    REQUIRED_TOPIC_MISSING = "MCAP_REQUIRED_TOPIC_MISSING"
    TOPIC_EMPTY = "MCAP_TOPIC_EMPTY"
    DECODE_FAILED = "MCAP_DECODE_FAILED"
    UNKNOWN_OPTIONAL_TOPIC = "MCAP_UNKNOWN_OPTIONAL_TOPIC"


class VerificationFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: VerificationCode
    severity: VerificationSeverity
    message: str
    topic: str | None = None
    record_offset: int | None = Field(default=None, ge=0)


class TopicInventoryV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    topic: str
    channel_ids: tuple[int, ...] = ()
    message_encoding: str
    schema_name: str | None = None
    schema_encoding: str | None = None
    message_count: int = Field(ge=0)
    log_time_min_ns: int | None = Field(default=None, ge=0)
    log_time_max_ns: int | None = Field(default=None, ge=0)
    decode_checked: bool = False
    decodable: bool | None = None


class SchemaInventoryV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_id: int = Field(ge=1, le=65535)
    name: str
    encoding: str
    data_size: int = Field(ge=0)
    data_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ChannelInventoryV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel_id: int = Field(ge=0, le=65535)
    schema_id: int = Field(ge=0, le=65535)
    topic: str
    message_encoding: str


class RawVerificationReportV1(BaseModel):
    """Immutable, canonical report. ``content_sha256`` excludes itself."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["raw-verification-report/v1"] = "raw-verification-report/v1"
    rollout_id: str
    object_key: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_size: int = Field(ge=0)
    profile: str = ""
    library: str = ""
    record_count: int = Field(ge=0)
    message_count: int = Field(ge=0)
    schemas: int = Field(ge=0)
    channels: int = Field(ge=0)
    chunks: int = Field(ge=0)
    schema_inventory: tuple[SchemaInventoryV1, ...]
    channel_inventory: tuple[ChannelInventoryV1, ...]
    topics: tuple[TopicInventoryV1, ...]
    findings: tuple[VerificationFinding, ...]
    status: VerificationStatus
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def build(cls, **values: object) -> RawVerificationReportV1:
        payload = {**values, "schema_version": "raw-verification-report/v1"}
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=lambda item: (
                item.value if isinstance(item, Enum) else item.model_dump(mode="json")
            ),
        ).encode("utf-8")
        return cls(**values, content_sha256=hashlib.sha256(encoded).hexdigest())


class RawVerifiedV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_version: Literal["raw-verified/v1"] = "raw-verified/v1"
    rollout_id: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
