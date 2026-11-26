"""Run complete Episode preparations in process-private readers on the API host."""

from concurrent.futures import ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context
from threading import Lock
from time import monotonic

from hc_data_platform.core.context import bind_request_context, current_request_context, reset_request_context
from hc_data_platform.core.errors import ProblemDetails, ProblemException

from .collection_export import CollectionIncompatible
from .delivery_models import PreparedEpisodePart
from .episode_bundle import build_part, cached_part, freeze
from .files import reuse_export_reads
from .models import digest


def _open_worker(settings, workers):
    from hc_data_platform.core.dbapi import psycopg_connection_factory
    from hc_data_platform.runtime import _episode_packages, _object_store_clients

    global _service
    _, _, storage = _object_store_clients(settings)
    # The persistent readers share the configured cache budget across the pool.
    worker_settings = settings.model_copy(update={
        "episode_package_cache_bytes": max(1, settings.episode_package_cache_bytes // workers),
    })
    packages = _episode_packages(psycopg_connection_factory(settings.postgres_dsn),
                                 storage, worker_settings, None, None)
    _service = packages.deliveries


def prepare_now(service, body, auth):
    with reuse_export_reads(body.data.package_prefix, service.packages.files.cache):
        plan = freeze(service, body, auth)
        part_id = digest(plan.model_dump(mode="json"))
        if ready := cached_part(service, plan, part_id):
            return ready
        reused = build_part(service, plan, part_id)
        return PreparedEpisodePart(part_id=part_id, status="ready", reused=reused)


def _prepare(context, body, auth):
    token = bind_request_context(context)
    try:
        return prepare_now(_service, body, auth)
    except ProblemException as exc:
        return exc.problem
    except CollectionIncompatible as exc:
        # Its constructor takes the field name, not the rendered error message.
        return ("incompatible", exc.field)
    finally:
        reset_request_context(token)


def result_of(future):
    result = future.result()
    if isinstance(result, ProblemDetails):
        raise ProblemException(result)
    if isinstance(result, tuple):
        raise CollectionIncompatible(result[1])
    return result


class EpisodePartProcesses:
    def __init__(self, settings):
        self.settings = settings
        self.limit = settings.episode_export_processes
        self.lock = Lock()
        self.active = {}
        self.pool = None
        self.finished = {}

    def close(self):
        if self.pool is not None:
            self.pool.shutdown()

    def _submit(self, context, body, auth):
        if self.pool is None:
            self.pool = ProcessPoolExecutor(self.limit, mp_context=get_context("spawn"),
                initializer=_open_worker, initargs=(self.settings, self.limit))
        try:
            return self.pool.submit(_prepare, context, body, auth)
        except BrokenProcessPool:
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.pool = None
            raise

    def submit(self, context, body, auth):
        with self.lock:
            return self._submit(context, body, auth)

    def prepare(self, service, body, auth):
        context = current_request_context()
        key = (context.organization_id, context.project_id, context.region_code, auth,
               digest(body.model_dump(mode="json")))
        with self.lock:
            future = self.active.get(key)
            submitted = False
            if future is None:
                # Keep a late failure long enough for its browser to collect it.
                now = monotonic()
                running = 0
                for previous, task in list(self.active.items()):
                    if not task.done():
                        running += 1
                    elif now - self.finished.setdefault(previous, now) >= 60:
                        del self.active[previous]
                        del self.finished[previous]
                if running < self.limit:
                    future = self._submit(context, body, auth)
                    self.active[key] = future
                    submitted = True
        if submitted and wait([future], timeout=20).done:
            with self.lock:
                if self.active.get(key) is future:
                    del self.active[key]
                    self.finished.pop(key, None)
            return result_of(future)

        # Polls (and callers above capacity) still check the current QC selection.
        # They never queue another conversion or wait for a busy process.
        plan = freeze(service, body, auth)
        part_id = digest(plan.model_dump(mode="json"))
        if future is not None and future.done():
            with self.lock:
                if self.active.get(key) is future:
                    del self.active[key]
                    self.finished.pop(key, None)
            result = result_of(future)
            ready = cached_part(service, plan, part_id)
            if ready and ready.part_id == result.part_id:
                return result
        else:
            ready = cached_part(service, plan, part_id)
        return ready or PreparedEpisodePart(part_id=part_id, status="preparing", reused=False)
