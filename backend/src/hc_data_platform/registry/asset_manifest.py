"""Canonical asset timestamps for new manifests, with exact verification of legacy digests."""

import hashlib
import json
from datetime import timezone, tzinfo
from zoneinfo import ZoneInfo

from .models import RobotModelAsset


def _in_zone(assets: tuple[RobotModelAsset, ...], zone: tzinfo) -> tuple[RobotModelAsset, ...]:
    if any(asset.created_at.utcoffset() is None for asset in assets):
        raise ValueError("model asset creation timestamp must include a time zone")
    return tuple(
        asset.model_copy(update={"created_at": asset.created_at.astimezone(zone)})
        for asset in assets
    )


def _digest(assets: tuple[RobotModelAsset, ...]) -> str:
    payload = [asset.model_dump(mode="json") for asset in assets]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def asset_manifest(
    assets: tuple[RobotModelAsset, ...], *, recorded_hash: str | None = None
) -> tuple[tuple[RobotModelAsset, ...], str]:
    canonical = _in_zone(assets, timezone.utc)
    checksum = _digest(canonical)
    if recorded_hash is None or checksum == recorded_hash:
        return canonical, checksum

    # Older sites hashed the database session's timestamp rendering. Preserve
    # those immutable digests only when the entire serialized manifest matches.
    if _digest(assets) == recorded_hash:
        return assets, recorded_hash
    shanghai = _in_zone(assets, ZoneInfo("Asia/Shanghai"))
    if _digest(shanghai) == recorded_hash:
        return shanghai, recorded_hash
    return canonical, checksum
