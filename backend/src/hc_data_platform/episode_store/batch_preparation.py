"""Stream completed parts in completion order, retaining the caller's selection order by index."""

import asyncio
import json
import logging
from contextvars import copy_context

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import ProblemException

from .delivery_models import PrepareEpisodePart
from .episode_bundle import PART_WORKERS, check_filter
from .part_processes import result_of, prepare_now


def batch_progress(service, body, auth):
    service.authorize(auth, command=True)
    check_filter(body.export_filter)
    context = current_request_context()
    if any(ref.identity.scope != (context.organization_id, context.project_id, context.region_code)
           for ref in body.data):
        raise PermissionError("Episode selection differs from the authorized scope")
    if len({ref.identity.key for ref in body.data}) != len(body.data):
        raise ValueError("Duplicate Episode in export selection")
    processes = getattr(service, "part_processes", None)
    caller = copy_context()

    async def stream():
        from .episode_bundle import _pool
        pending = {}
        items = iter(enumerate(body.data))
        limit = processes.limit if processes else PART_WORKERS
        try:
            yield b"\n"
            while True:
                while len(pending) < limit:
                    item = next(items, None)
                    if item is None:
                        break
                    index, ref = item
                    part = PrepareEpisodePart(data=ref, mode=body.mode, export_filter=body.export_filter)
                    if processes:
                        future = processes.submit(context, part, auth)
                    else:
                        future = _pool.submit(caller.copy().run, prepare_now, service, part, auth)
                    pending[asyncio.wrap_future(future)] = index
                if not pending:
                    break
                done, _ = await asyncio.wait(pending, timeout=10, return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    yield b"\n"
                for job in done:
                    index = pending.pop(job)
                    result = result_of(job)
                    yield (json.dumps({"index": index, "part_id": result.part_id, "reused": result.reused}) + "\n").encode()
        except (ProblemException, ValueError, PermissionError, KeyError) as exc:
            detail = exc.problem.detail if isinstance(exc, ProblemException) else str(exc)
            yield (json.dumps({"error": detail}) + "\n").encode()
        except Exception:
            logging.getLogger(__name__).exception("Episode batch preparation failed")
            yield (json.dumps({"error": "准备 Episode 失败，请重新导出。"}) + "\n").encode()
        finally:
            for job in pending:
                if job.done() and not job.cancelled():
                    job.exception()
                else:
                    job.cancel()

    return stream()
