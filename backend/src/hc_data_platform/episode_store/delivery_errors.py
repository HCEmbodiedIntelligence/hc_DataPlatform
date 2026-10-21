"""Delivery problem construction without repository or export adapter dependencies."""

from hc_data_platform.core.errors import ProblemException, problem


def failure(code: str, detail: str, status: int = 409) -> ProblemException:
    return problem(
        status=status,
        code="EPISODE_DELIVERY_" + code,
        title="Episode delivery unavailable",
        detail=detail,
    )
