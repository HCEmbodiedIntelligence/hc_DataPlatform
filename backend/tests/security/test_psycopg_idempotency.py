from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from pydantic import BaseModel

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.psycopg import PsycopgIdempotencyStore


class Result(BaseModel):
    value: str


@dataclass
class _State:
    row: tuple[str, object | None, datetime] | None = None


class _Cursor:
    def __init__(self, state: _State) -> None:
        self._state = state

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        normalized = " ".join(query.split())
        if normalized.startswith("INSERT INTO core.idempotency_records"):
            if self._state.row is None:
                self._state.row = (str(params[4]), None, params[6])  # type: ignore[arg-type]
            return
        if normalized.startswith("SELECT request_fingerprint"):
            return
        if "SET request_fingerprint" in normalized:
            self._state.row = (str(params[0]), None, params[2])  # type: ignore[arg-type]
            return
        if "SET response_json" in normalized:
            assert self._state.row is not None
            self._state.row = (
                self._state.row[0],
                json.loads(str(params[0])),
                params[1],  # type: ignore[arg-type]
            )
            return
        raise AssertionError(normalized)

    def fetchone(self) -> tuple[str, object | None, datetime] | None:
        return self._state.row


class _Connection:
    def __init__(self, state: _State) -> None:
        self._state = state
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _Cursor:
        return _Cursor(self._state)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        return None


def test_psycopg_idempotency_persists_typed_replay_and_rejects_body_reuse() -> None:
    state = _State()
    connections: list[_Connection] = []

    def connect() -> _Connection:
        connection = _Connection(state)
        connections.append(connection)
        return connection

    store = PsycopgIdempotencyStore(connect, response_decoder=Result.model_validate)
    token = bind_request_context(RequestContext(project_id="project-a", region_code="cn-east"))
    calls = 0

    def action() -> Result:
        nonlocal calls
        calls += 1
        return Result(value="created")

    try:
        first = store.execute(scope="project-a", key="request-1", payload={"x": 1}, action=action)
        replay = store.execute(scope="project-a", key="request-1", payload={"x": 1}, action=action)
        with pytest.raises(ProblemException) as conflict:
            store.execute(scope="project-a", key="request-1", payload={"x": 2}, action=action)
    finally:
        reset_request_context(token)

    assert first.value == replay.value == Result(value="created")
    assert first.replayed is False
    assert replay.replayed is True
    assert calls == 1
    assert conflict.value.problem.code == "IDEMPOTENCY_KEY_REUSED"
    assert connections[-1].rollbacks == 1


def test_psycopg_idempotency_requires_selected_project_to_match_scope() -> None:
    store = PsycopgIdempotencyStore(lambda: Any)  # type: ignore[arg-type]
    token = bind_request_context(RequestContext(project_id="project-a"))
    try:
        with pytest.raises(ProblemException) as denied:
            store.execute(
                scope="project-b",
                key="request-1",
                payload={},
                action=lambda: Result(value="never"),
            )
    finally:
        reset_request_context(token)

    assert denied.value.problem.code == "IDEMPOTENCY_SCOPE_DENIED"
