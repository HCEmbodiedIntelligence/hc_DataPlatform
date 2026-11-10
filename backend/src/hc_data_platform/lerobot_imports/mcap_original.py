"""Recording generations reference one immutable original MCAP."""

from hc_data_platform.robot_ingest.raw_recording import verify

ORIGINAL_PATH = "source/original/source.mcap"


def original_key(original, prefix, reprocessing):
    return original["object_key"]


def replay_asset(storage, asset, original, prefix, reprocessing, checkpoint):
    """Preserve old locators on retries without manufacturing another Raw copy."""
    entry = asset.manifest
    if entry.path == ORIGINAL_PATH:
        if (entry.size, entry.sha256) != (original["size"], original["sha256"]):
            raise ValueError("MCAP derivation original differs from Raw receipt")
        allowed = {original["object_key"]}
        if reprocessing:
            allowed.add(f"{prefix}/{original['sha256']}/{ORIGINAL_PATH}")
        if asset.object_key not in allowed:
            raise ValueError("MCAP derivation original locator mismatch")
    elif asset.object_key != f"{prefix}/{entry.sha256}/{entry.path}":
        raise ValueError("MCAP derivation locator mismatch")
    verify(storage, asset.object_key, entry.size, entry.sha256, checkpoint)
    return asset
