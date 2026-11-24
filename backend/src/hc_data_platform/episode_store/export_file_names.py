"""File names observed at export, linked to the exact versions supplied to the writer."""

from pathlib import PurePosixPath

from .delivery_repository import scope_key


def record_files(repository, pairs):
    rows = sorted({(PurePosixPath(path).name, version) for path, version in pairs if path})
    if not rows:
        return
    names, versions = zip(*rows, strict=True)
    with repository.transaction() as cursor:
        cursor.execute(
            """INSERT INTO episode_store.export_file_names
               (organization_id,project_id,region_code,filename,data_version_id)
               SELECT %s,%s,%s,f.name,f.version FROM unnest(%s::text[],%s::text[]) f(name,version)
               ON CONFLICT DO NOTHING""",
            (*scope_key(), list(names), list(versions)),
        )


def collection_files(manifest, plan):
    versions = [m.data.data_version_id for m in plan.members]
    if len(versions) == 1:
        return [(entry["path"], versions[0]) for entry in manifest["files"]]
    pairs = []
    paths = {item["path"] for item in manifest["files"]}
    for member in manifest.get("members", ()):
        if "data" not in member:
            source = member.get("info", {}).get("source_selection") or {}
            if "data_version_id" in source and "index" in member:
                name = f"data-{member['index']:05d}-of-{len(manifest['members']):05d}.arrow"
                if name in paths:
                    pairs.append((name, source["data_version_id"]))
            continue
        version = member["data"]["data_version_id"]
        files = [member.get("path"), member.get("data_path"), member.get("selection")]
        files.extend(item["path"] for item in member.get("files", ()))
        files.extend(item["path"] for item in member.get("media", {}).values())
        mcap = member.get("path") or ""
        if mcap.endswith(".mcap"):
            files.extend(mcap[:-5] + suffix for suffix in ("-frames.json", "-normalization.json"))
        pairs.extend((path, version) for path in files if isinstance(path, str) and path in paths)
    return pairs
