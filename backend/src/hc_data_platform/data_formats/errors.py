"""Existing structured error contract retained for codec compatibility.

The core error model has no web framework or runtime dependencies.
"""

from hc_data_platform.core.errors import ProblemException, problem


def format_error(code: str, detail: str) -> ProblemException:
    return problem(status=409, code=code, title="Export source is incomplete", detail=detail)
