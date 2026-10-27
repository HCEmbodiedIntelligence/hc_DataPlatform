"""Exact-file cleanup for platform-owned alignment attempts on shared staging.

File URIs are not arbitrary delete capabilities: the path must match the Arrow
writer's rollout/attempt/content identity under the configured staging root.
Directory descriptors prevent symlink traversal during inspection and cleanup.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
from contextlib import contextmanager, suppress
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .deletion_plan import conflict


class TaskLocalArtifacts:
    def __init__(self, alignment_root):
        self.root = Path(alignment_root).absolute()

    def locator(self, table, row):
        if table != "public.aligned_fragment_attempts" or not row.get("staging_uri"):
            return None
        uri = urlsplit(row["staging_uri"])
        if uri.scheme != "file":
            return None
        parts = [
            hashlib.sha256(str(row.get(name, "")).encode()).hexdigest()
            for name in ("rollout_id", "attempt_id")
        ]
        expected = self.root.joinpath(*parts, str(row.get("content_sha256")) + ".arrow")
        path = unquote(uri.path)
        if uri.netloc or uri.query or uri.fragment or path != str(expected):
            raise conflict("TASK_DELETE_INVALID_LOCATOR", "本地中间文件路径与任务归属不匹配。")
        self._parts(path)
        return path

    @staticmethod
    def references(table, row):
        fields = {
            "public.aligned_fragment_attempts": "staging_uri",
            "public.lance_rollout_lineage": "fragment_uri",
            "publishing.export_attempts": "staging_uri",
        }
        uri = urlsplit(row.get(fields.get(table, "")) or "")
        return {os.path.normpath(unquote(uri.path))} if uri.scheme == "file" else set()

    def _parts(self, path):
        try:
            parts = Path(path).relative_to(self.root).parts
        except ValueError:
            parts = ()
        if len(parts) != 3 or not all(
            re.fullmatch(pattern, part)
            for pattern, part in zip((r"[a-f0-9]{64}", r"[a-f0-9]{64}",
                                      r"[a-f0-9]{64}\.arrow"), parts, strict=False)
        ):
            raise conflict("TASK_DELETE_INVALID_LOCATOR", "本地文件不在受管的对齐暂存目录中。")
        return parts

    @contextmanager
    def _parent(self, path):
        parts = self._parts(path)
        handles = []
        try:
            try:
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                handles.append(os.open(self.root, flags))
                for part in parts[:-1]:
                    handles.append(os.open(part, flags, dir_fd=handles[-1]))
            except FileNotFoundError:
                yield None, parts[-1]
                return
            except OSError as error:
                if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise conflict(
                        "TASK_DELETE_INVALID_LOCATOR", "本地中间文件的父目录不是普通目录。"
                    ) from error
                raise
            yield handles[-1], parts[-1]
        finally:
            for handle in reversed(handles):
                os.close(handle)

    @staticmethod
    def _stat(parent, name):
        if parent is None:
            return None
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode):
            raise conflict("TASK_DELETE_INVALID_LOCATOR", "本地中间文件不是普通文件。")
        return info

    def size(self, path):
        with self._parent(path) as (parent, name):
            info = self._stat(parent, name)
            return info.st_size if info is not None else None

    def purge(self, paths):
        for path in paths:
            with self._parent(path) as (parent, name):
                if self._stat(parent, name) is not None:
                    with suppress(FileNotFoundError):
                        os.unlink(name, dir_fd=parent)
                if self._stat(parent, name) is not None:
                    raise OSError("local artifact deletion was not confirmed")
