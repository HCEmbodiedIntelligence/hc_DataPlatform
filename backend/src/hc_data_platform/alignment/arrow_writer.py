"""Attempt-isolated streaming Arrow IPC fragment writer."""

from __future__ import annotations

import hashlib
from contextlib import suppress
from importlib import import_module
from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes
from .models import AlignedRowV1


class ArrowFragmentWriter:
    """Write bounded Arrow record batches to an isolated attempt path."""

    def __init__(
        self,
        staging_root: Path,
        *,
        batch_rows: int = 4_096,
        batch_bytes: int = 48 * 1024 * 1024,
    ) -> None:
        if not 2_048 <= batch_rows <= 8_192:
            raise ValueError("batch_rows must be between 2048 and 8192")
        if not 32 * 1024 * 1024 <= batch_bytes <= 64 * 1024 * 1024:
            raise ValueError("batch_bytes must be between 32 and 64 MiB")
        self._staging_root = staging_root.resolve()
        self._batch_rows = batch_rows
        self._batch_bytes = batch_bytes
        self._attempt_dir: Path | None = None
        self._partial_path: Path | None = None
        self._committed_path: Path | None = None
        self._sink: Any = None
        self._ipc_writer: Any = None
        self._schema: Any = None
        self._row_count = 0
        self._buffer: list[AlignedRowV1] = []
        self._buffer_bytes = 0

    def begin(self, *, rollout_id: str, attempt_id: str) -> None:
        if self._attempt_dir is not None:
            raise RuntimeError("another writer attempt is active")
        pa = self._load_module("pyarrow")
        ipc = self._load_module("pyarrow.ipc")
        rollout_key = hashlib.sha256(rollout_id.encode("utf-8")).hexdigest()
        attempt_key = hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()
        rollout_dir = self._staging_root / rollout_key
        attempt_dir = rollout_dir / attempt_key
        rollout_dir.mkdir(parents=True, exist_ok=True)
        committed_path = self._prepare_attempt_dir(attempt_dir)
        if committed_path is not None:
            self._attempt_dir = attempt_dir
            self._committed_path = committed_path
            self._row_count = 0
            return
        partial_path = attempt_dir / "fragment.arrow.part"
        try:
            schema = pa.schema(
                [
                    pa.field("rollout_id", pa.string(), nullable=False),
                    pa.field("step_index", pa.int64(), nullable=False),
                    pa.field("timestamp_ns", pa.int64(), nullable=False),
                    pa.field("modalities_json", pa.binary(), nullable=False),
                    pa.field("sample_valid", pa.bool_(), nullable=False),
                ],
                metadata={b"hc.schema": b"aligned-row/v1"},
            )
            sink = pa.OSFile(str(partial_path), "wb")
            ipc_writer = ipc.new_file(sink, schema)
        except Exception:
            with suppress(OSError):
                partial_path.unlink()
            with suppress(OSError):
                attempt_dir.rmdir()
            raise
        self._attempt_dir = attempt_dir
        self._partial_path = partial_path
        self._sink = sink
        self._ipc_writer = ipc_writer
        self._schema = schema
        self._row_count = 0

    def write_row(self, row: AlignedRowV1) -> None:
        self._require_active()
        if self._committed_path is not None:
            self._row_count += 1
            return
        encoded_size = len(canonical_json_bytes(row.model_dump(mode="python")["modalities"]))
        if self._buffer and self._buffer_bytes + encoded_size > self._batch_bytes:
            self._flush_buffer()
        self._buffer.append(row)
        self._buffer_bytes += encoded_size
        self._row_count += 1
        if len(self._buffer) >= self._batch_rows:
            self._flush_buffer()

    def _flush_buffer(self) -> None:
        if not self._buffer:
            return
        pa = self._load_module("pyarrow")
        rows = self._buffer
        batch = pa.record_batch(
            [
                [row.rollout_id for row in rows],
                [row.step_index for row in rows],
                [row.timestamp_ns for row in rows],
                [canonical_json_bytes(row.model_dump(mode="python")["modalities"]) for row in rows],
                [row.sample_valid for row in rows],
            ],
            schema=self._schema,
        )
        self._ipc_writer.write_batch(batch)
        del batch
        self._buffer = []
        self._buffer_bytes = 0
        pa.default_memory_pool().release_unused()

    def commit(self, *, row_count: int, content_sha256: str, schema_sha256: str) -> str:
        self._require_active()
        if row_count != self._row_count:
            raise ValueError("row count does not match staged rows")
        self._validate_hash(content_sha256, "content_sha256")
        self._validate_hash(schema_sha256, "schema_sha256")
        committed_path = self._committed_path
        if committed_path is not None:
            if committed_path.stem != content_sha256:
                raise ValueError("committed attempt content does not match aligned rows")
            self._clear_active()
            return committed_path.as_uri()
        self._flush_buffer()
        attempt_dir = self._attempt_dir
        partial_path = self._partial_path
        assert attempt_dir is not None
        assert partial_path is not None
        self._ipc_writer.close()
        self._sink.close()
        self._load_module("pyarrow").default_memory_pool().release_unused()
        self._ipc_writer = None
        self._sink = None
        final_path = attempt_dir / f"{content_sha256}.arrow"
        partial_path.replace(final_path)
        self._clear_active()
        return final_path.as_uri()

    def abort(self) -> None:
        if self._attempt_dir is None:
            return
        if self._committed_path is not None:
            self._clear_active()
            return
        if self._ipc_writer is not None:
            with suppress(Exception):
                self._ipc_writer.close()
        if self._sink is not None:
            with suppress(Exception):
                self._sink.close()
        if self._partial_path is not None:
            with suppress(OSError):
                self._partial_path.unlink()
        attempt_dir = self._attempt_dir
        self._clear_active()
        with suppress(OSError):
            attempt_dir.rmdir()

    def _require_active(self) -> None:
        if self._attempt_dir is None or (self._ipc_writer is None and self._committed_path is None):
            raise RuntimeError("writer attempt is not active")

    def _clear_active(self) -> None:
        self._attempt_dir = None
        self._partial_path = None
        self._committed_path = None
        self._sink = None
        self._ipc_writer = None
        self._schema = None
        self._row_count = 0
        self._buffer = []
        self._buffer_bytes = 0

    @staticmethod
    def _prepare_attempt_dir(attempt_dir: Path) -> Path | None:
        """Create a fresh attempt or reuse its one immutable committed fragment."""

        try:
            attempt_dir.mkdir(exist_ok=False)
            return None
        except FileExistsError:
            if not attempt_dir.is_dir():
                raise

        entries = tuple(attempt_dir.iterdir())
        committed = tuple(
            path
            for path in entries
            if path.is_file()
            and path.suffix == ".arrow"
            and len(path.stem) == 64
            and all(character in "0123456789abcdef" for character in path.stem)
        )
        if len(entries) == 1 and len(committed) == 1:
            return committed[0]
        if all(path.is_file() and path.name == "fragment.arrow.part" for path in entries):
            for path in entries:
                path.unlink()
            attempt_dir.rmdir()
            attempt_dir.mkdir(exist_ok=False)
            return None
        raise RuntimeError("alignment attempt directory contains unexpected artifacts")

    @staticmethod
    def _load_module(name: str) -> Any:
        try:
            return import_module(name)
        except ImportError as exc:
            raise RuntimeError(
                "ArrowFragmentWriter requires the backend data dependencies"
            ) from exc

    @staticmethod
    def _validate_hash(value: str, name: str) -> None:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError(f"{name} must be a lowercase SHA-256 digest")
