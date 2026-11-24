"""Organization-owned names selected by published capture schemes."""

from typing import Annotated, Any

from pydantic import StringConstraints

from .common import reject
from .references import Strict

TagName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class LibraryTag(Strict):
    name: TagName


def names(db: Any, org: str) -> list[str]:
    return [
        row[0]
        for row in db.execute(
            "SELECT name FROM ingest.annotation_tag_library WHERE organization_id=%s ORDER BY name",
            (org,),
        ).fetchall()
    ]


def create(db: Any, org: str, tag: LibraryTag, actor: str) -> LibraryTag:
    db.execute(
        """INSERT INTO ingest.annotation_tag_library (organization_id,name,created_by)
           VALUES (%s,%s,%s) ON CONFLICT (organization_id,name) DO NOTHING""",
        (org, tag.name, actor),
    )
    return tag


def require_names(db: Any, org: str, selected: tuple[str, ...]) -> None:
    if not selected:
        return
    rows = db.execute(
        "SELECT name FROM ingest.annotation_tag_library WHERE organization_id=%s AND name=ANY(%s)",
        (org, list(selected)),
    ).fetchall()
    unknown = set(selected) - {row[0] for row in rows}
    if unknown:
        reject("CAPTURE_TAG_UNKNOWN", "所选标签不在当前组织的标签库中", 422, names=sorted(unknown))
