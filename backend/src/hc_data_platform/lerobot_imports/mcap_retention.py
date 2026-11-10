"""Reclaim only an unregistered failed preparation, under its processing lock."""

import re


def purge_failed(handler, scope, ident, *, checkpoint=lambda: None):
    if not re.fullmatch(r"[0-9a-f]{64}", ident) or any("/" in part for part in scope):
        raise ValueError("invalid processing cleanup scope")
    prefix = "/".join(("derived-mcap", *scope, ident)) + "/"
    with handler.connections() as db:
        row = db.execute(
            """SELECT status,recording_id FROM ingest.mcap_processing_files
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND processing_id=%s""",
            (*scope, ident),
        ).fetchone()
        if row is None or row[0] != "FAILED" or row[1] is not None:
            return 0
        if db.execute(
            "SELECT 1 FROM ingest.recording_upload_assets WHERE starts_with(object_key,%s) LIMIT 1",
            (prefix,),
        ).fetchone():
            return 0
    keys = list(handler.storage.list_object_keys(prefix))
    # Preview namespaces bind the upload identity. Keep them unless a receipt
    # proves which exact keys were produced by this unregistered attempt.
    receipt_key = prefix + "receipt.json"
    if receipt_key in keys:
        from hc_data_platform.continuous_recordings.asset_models import (
            RecordingAsset,
            RecordingUpload,
        )
        from hc_data_platform.continuous_recordings.previews import preview_key
        from hc_data_platform.episode_store.native_sources import read_json

        receipt = read_json(handler.storage, receipt_key)
        upload = RecordingUpload.model_validate(receipt["upload"])
        assets = [RecordingAsset.model_validate(a) for a in receipt["assets"]]
        if (
            upload.scope.organization_id,
            upload.scope.project_id,
            upload.scope.region_code,
        ) != tuple(scope):
            raise ValueError("cleanup receipt belongs to another scope")
        from uuid import NAMESPACE_URL, uuid5

        if upload.upload_id != uuid5(NAMESPACE_URL, "native-mcap:" + ident):
            raise ValueError("cleanup receipt belongs to another attempt")
        for asset in assets:
            if asset.manifest.role == "RAW_VIDEO":
                for selected in (True, False):
                    key = preview_key(upload, asset, assets, selected=selected)
                    keys.extend((key, key + ".json"))
    reclaimed = 0
    for key in dict.fromkeys(keys):
        checkpoint()
        head = handler.storage.head(key)
        if head is not None:
            handler.storage.delete_object(key)
            if handler.storage.head(key) is not None:
                raise RuntimeError("failed processing object remains after delete")
            reclaimed += head.size
    return reclaimed
