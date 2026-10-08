"""Versioned registration without loading domains, plugins or runtime services.

Composition selects defaults explicitly. A frozen reference always resolves to
that exact version and never falls forward when the implementation is missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")
KEY_PATTERN = r"^[a-z][a-z0-9_.-]{0,127}$"
VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,127}$"
_KEY = re.compile(KEY_PATTERN)
_VERSION = re.compile(VERSION_PATTERN)


@dataclass(frozen=True)
class ImplementationRef:
    key: str
    version: str

    def __post_init__(self) -> None:
        if not _KEY.fullmatch(self.key) or not _VERSION.fullmatch(self.version):
            raise ValueError("implementation key/version must be bounded identifiers")


@dataclass(frozen=True)
class CapabilityDescriptor:
    ref: ImplementationRef
    required_inputs: frozenset[str] = frozenset()
    outputs: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for values in (self.required_inputs, self.outputs):
            if not isinstance(values, frozenset) or any(not _VERSION.fullmatch(v) for v in values):
                raise ValueError("capability identifiers must be an immutable set of bounded names")


@dataclass(frozen=True)
class Registration(Generic[T]):
    descriptor: CapabilityDescriptor
    implementation: T


class ImplementationUnavailable(LookupError):
    """The requested key or exact version is not registered."""


class UnsupportedInputs(ValueError):
    def __init__(self, missing: frozenset[str]) -> None:
        self.missing = missing
        super().__init__("missing input capabilities: " + ", ".join(sorted(missing)))


class VersionedRegistry(Generic[T]):
    def __init__(self) -> None:
        self._entries: dict[ImplementationRef, Registration[T]] = {}
        self._defaults: dict[str, ImplementationRef] = {}

    def register(
        self, descriptor: CapabilityDescriptor, implementation: T, *, default: bool = False
    ) -> None:
        ref = descriptor.ref
        if ref in self._entries:
            raise ValueError(f"duplicate implementation: {ref.key}@{ref.version}")
        if default and ref.key in self._defaults:
            raise ValueError(f"duplicate default implementation: {ref.key}")
        self._entries[ref] = Registration(descriptor, implementation)
        if default:
            self._defaults[ref.key] = ref

    def select(
        self,
        key: str,
        *,
        version: str | None = None,
        available_inputs: frozenset[str],
    ) -> Registration[T]:
        ref = self._defaults.get(key) if version is None else ImplementationRef(key, version)
        selected = self._entries.get(ref) if ref is not None else None
        if selected is None:
            raise ImplementationUnavailable(
                f"implementation unavailable: {key}@{version or 'default'}"
            )
        missing = selected.descriptor.required_inputs - available_inputs
        if missing:
            raise UnsupportedInputs(missing)
        return selected

    def descriptors(self) -> tuple[CapabilityDescriptor, ...]:
        return tuple(
            entry.descriptor
            for _, entry in sorted(
                self._entries.items(), key=lambda item: (item[0].key, item[0].version)
            )
        )
