from __future__ import annotations

from collections.abc import Iterator

import pytest

from hc_data_platform.core.context import clear_request_context

_skipped: list[str] = []
_xfailed: list[str] = []


@pytest.fixture(autouse=True)
def _isolate_ambient_request_scope() -> Iterator[None]:
    """Never let one test lend its tenant identity to another test."""

    clear_request_context()
    try:
        yield
    finally:
        clear_request_context()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("release-gates")
    group.addoption(
        "--fail-on-skipped",
        action="store_true",
        help=(
            "fail when any selected test is skipped instead of treating missing dependencies "
            "as pass"
        ),
    )
    group.addoption(
        "--fail-on-xfailed",
        action="store_true",
        help="fail when any selected test remains an expected failure",
    )


def pytest_configure(config: pytest.Config) -> None:
    del config
    _skipped.clear()
    _xfailed.clear()


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    was_xfail = getattr(report, "wasxfail", None)
    if report.skipped and was_xfail:
        _xfailed.append(report.nodeid)
    elif report.skipped:
        _skipped.append(report.nodeid)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    del exitstatus
    config = session.config
    blocked: list[str] = []
    if config.getoption("--fail-on-skipped") and _skipped:
        blocked.append(f"{len(_skipped)} skipped")
    if config.getoption("--fail-on-xfailed") and _xfailed:
        blocked.append(f"{len(_xfailed)} xfailed")
    if blocked:
        reporter = config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_sep("=", f"release gate blocked: {', '.join(blocked)}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
