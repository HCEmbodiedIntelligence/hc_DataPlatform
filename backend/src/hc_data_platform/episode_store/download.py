"""Complete offline ZIP delivery: bounded memory and unchanged media bytes."""

import shutil
import tempfile
import zipfile
from pathlib import Path

from .models import canonical


def complete_archive(package, directory: Path):
    """Caller owns the returned temporary directory until the response completes."""
    directory.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="episode-download-", dir=directory)
    path = Path(temporary.name) / "episode.zip"
    try:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for entry in package.manifest.files:
                member = zipfile.ZipInfo("episode/" + entry.path, (1980, 1, 1, 0, 0, 0))
                member.external_attr = 0o100644 << 16
                with (
                    package.file(entry.path) as source,
                    source.open("rb") as input_file,
                    archive.open(member, "w", force_zip64=True) as output,
                ):
                    shutil.copyfileobj(input_file, output, length=1024**2)
            for relative, model in (
                ("hc/manifest.json", package.manifest),
                ("hc/commit.json", package.receipt),
            ):
                member = zipfile.ZipInfo("episode/" + relative, (1980, 1, 1, 0, 0, 0))
                member.external_attr = 0o100644 << 16
                archive.writestr(member, canonical(model.model_dump(mode="json")))
        return path, temporary
    except BaseException:
        temporary.cleanup()
        raise
