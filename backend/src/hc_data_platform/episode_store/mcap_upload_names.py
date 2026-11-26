"""Name MCAP exports from the uploaded MCAP bytes and their frozen file names."""

import hashlib
import re
import unicodedata
from collections import Counter
from pathlib import Path

from .selected_cache import original_reference


def upload_stem(provenance, storage, checkpoint, digests, *, copy_to=None):
    original = original_reference(provenance).object
    path = provenance["source_object"]["path"].replace("\\", "/")
    name = Path(path).name
    if not name.lower().endswith(".mcap"):
        raise ValueError("MCAP upload name is missing from its source receipt")

    if original.sha256 not in digests or copy_to is not None:
        md5 = hashlib.md5(usedforsecurity=False)
        sha256 = hashlib.sha256()
        size = 0
        for block in storage.read_chunks(original.key):
            checkpoint()
            size += len(block)
            if size > original.size:
                raise ValueError("MCAP upload exceeds its frozen size")
            sha256.update(block)
            md5.update(block)
            if copy_to is not None:
                copy_to.write(block)
        if size != original.size or sha256.hexdigest() != original.sha256:
            raise ValueError("MCAP upload differs from its frozen receipt")
        digests[original.sha256] = md5.hexdigest()

    stem = unicodedata.normalize("NFC", name[:-5])
    stem = re.sub(r'[<>:"|?*\x00-\x1f\x7f]', "_", stem)
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])\.", stem, re.I):
        stem = "_" + stem
    # Leave space for the MD5 and the longest sidecar suffix on common filesystems.
    stem = stem.encode("utf-8")[:200].decode("utf-8", errors="ignore")
    return f"{stem}_{digests[original.sha256]}"


def name_members(root, members, stems):
    names = [stems[m["data"]["data_version_id"]] for m in members]
    counts = Counter(name.casefold() for name in names)
    for member, name in zip(members, names, strict=True):
        old = root / member["path"]
        directory = root / old.stem if counts[name.casefold()] > 1 else root
        directory.mkdir(exist_ok=True)
        target = directory / (name + ".mcap")
        old.rename(target)
        for suffix in ("-frames.json", "-normalization.json"):
            sidecar = old.with_name(old.stem + suffix)
            if sidecar.exists():
                sidecar.rename(directory / (name + suffix))
        member["path"] = target.relative_to(root).as_posix()
