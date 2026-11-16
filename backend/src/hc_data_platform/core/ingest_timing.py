"""Opt-in wall-clock spans for worker-side Episode ingestion."""

import logging
import os
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from functools import wraps
from hashlib import sha256
from time import perf_counter_ns

_ENABLED = os.getenv("HC_INGEST_TIMING", "").strip().lower() in {"1", "true", "yes", "on"}
_EPISODE = ContextVar("ingest_timing_episode", default=None)
_LOG = logging.getLogger(__name__)


@contextmanager
def _measure(stage, episode_id):
    start = perf_counter_ns()
    error = None
    try:
        yield
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        event = "INGEST.TIMING." + stage
        _LOG.info(event, extra={
            "event_code": event,
            "episode_id": episode_id,
            "duration_ms": (perf_counter_ns() - start) / 1_000_000,
            "error_type": error,
        })


def span(stage):
    episode_id = _EPISODE.get() if _ENABLED else None
    return _measure(stage, episode_id) if episode_id else nullcontext()


def timed(stage):
    def decorate(fn):
        if not _ENABLED:
            return fn

        @wraps(fn)
        def measured(*args, **kwargs):
            with span(stage):
                return fn(*args, **kwargs)
        return measured
    return decorate


def episode_stage(stage, identity):
    """Bind an Episode at a synchronous activity or package-service entry point."""
    def decorate(fn):
        if not _ENABLED:
            return fn

        @wraps(fn)
        def measured(*args, **kwargs):
            token = _EPISODE.set(identity(*args, **kwargs))
            try:
                with span(stage):
                    return fn(*args, **kwargs)
            finally:
                _EPISODE.reset(token)
        return measured
    return decorate


def dataset_episode_id(rollout_id):
    return "episode_" + sha256(rollout_id.encode()).hexdigest()[:32]
