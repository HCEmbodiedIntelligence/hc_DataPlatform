"""Dependency-free stable errors shared by storage Workers and activities."""


class LifecycleExecutionBlocked(RuntimeError):
    code = "LIFECYCLE_PROTECTION_BLOCKED"
