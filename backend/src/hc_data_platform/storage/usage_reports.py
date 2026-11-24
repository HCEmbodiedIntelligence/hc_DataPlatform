"""Publish complete reports atomically and serve the last observation during refresh."""
import hashlib
import json
import logging
import os
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from contextvars import copy_context
from pathlib import Path
from threading import Lock
from time import time

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from .task_usage_models import ProjectTaskStorage, TaskStorageFile, TaskStorageFilePage

log = logging.getLogger(__name__)


class UsageReports:
    def __init__(self, root, namespace, scan):
        self.root = Path(root).resolve()
        self.namespace = namespace
        self.scan = scan
        self.lock = Lock()
        self.pending = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="storage-report")

    def path(self, project):
        ctx = current_request_context()
        identity = (1, self.namespace, ctx.organization_id, project, ctx.region_code)
        return self.root / (hashlib.sha256(json.dumps(identity).encode()).hexdigest() + ".sqlite")

    def refresh(self, project, path):
        with self.lock:
            if path not in self.pending:
                self.pending[path] = self.executor.submit(copy_context().run, self.build, project, path)
            return self.pending[path]

    def build(self, project, path):
        try:
            overview, grouped = self.scan(project)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".sqlite", delete=False) as tmp:
                temp = Path(tmp.name)
            try:
                with closing(sqlite3.connect(temp)) as db, db:
                    db.execute("create table summary(value text not null)")
                    db.execute("insert into summary values (?)", (overview.model_dump_json(),))
                    db.execute("create table files (grp text, category text, size integer, name text, id text, search text, value text)")
                    db.executemany("insert into files values (?,?,?,?,?,?,?)", (
                        (group, f.category, int(f.bytes), f.name, f.file_id,
                         " ".join((f.name, f.relative_path, f.record_name, f.robot_name, f.status)).casefold(),
                         f.model_dump_json())
                        for group, files in grouped.items() for f in files
                    ))
                    db.execute("create index by_group on files(grp,size desc,name,id)")
                os.replace(temp, path)
            finally:
                temp.unlink(missing_ok=True)
            return overview
        except Exception:
            log.exception("Could not refresh task storage report")
            raise
        finally:
            with self.lock:
                self.pending.pop(path, None)

    def overview(self, project, refresh=False):
        path = self.path(project)
        if refresh or not path.is_file():
            return self.refresh(project, path).result()
        if time() - path.stat().st_mtime > 300:
            self.refresh(project, path)
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            return ProjectTaskStorage.model_validate_json(db.execute("select value from summary").fetchone()[0])

    def files(self, project, *, group_id, category, search, offset, limit):
        self.overview(project)
        with closing(sqlite3.connect(self.path(project).as_uri() + "?mode=ro", uri=True)) as db:
            overview = ProjectTaskStorage.model_validate_json(db.execute("select value from summary").fetchone()[0])
            if group_id not in {row.group_id for row in overview.tasks}:
                raise problem(status=404, code="STORAGE_TASK_NOT_FOUND", title="Task not found",
                              detail="The selected task does not exist in this project.")
            where = " where grp=?"
            args = [group_id]
            if category:
                where += " and category=?"
                args.append(category)
            needle = search.strip().casefold()
            if needle:
                where += " and instr(search,?)>0"
                args.append(needle)
            count, size = db.execute("select count(*),coalesce(sum(size),0) from files" + where, args).fetchone()
            rows = db.execute("select value from files" + where + " order by size desc,name,id limit ? offset ?",
                              [*args, limit, offset])
            return TaskStorageFilePage(project_id=project, group_id=group_id, observed_at=overview.observed_at,
                items=tuple(TaskStorageFile.model_validate_json(row[0]) for row in rows),
                total_count=count, total_bytes=str(size), offset=offset, limit=limit)
