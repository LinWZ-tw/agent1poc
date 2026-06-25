"""Background job abstraction.

Heavy pipeline steps (QC, alignment, mutation calling, clustering, ...) can
take minutes to hours in real mode. Claude must never block the agent loop
on them: it starts a job, gets a job_id back immediately, and polls.

In mock mode the same contract holds (start -> poll -> result) even though
the underlying work finishes almost instantly, so swapping mock for real
mode later doesn't change how the model is expected to call tools.
"""

from __future__ import annotations

import itertools
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="pipeline-job")
_jobs: dict[str, "Job"] = {}
_jobs_lock = threading.Lock()
_id_counter = itertools.count(1)


@dataclass
class Job:
    job_id: str
    step_name: str
    future: Future
    started_at: float = field(default_factory=time.time)


def start_job(
    step_name: str,
    fn: Callable[..., dict[str, Any]],
    kwargs: dict[str, Any],
    on_complete: Callable[[str, dict[str, Any] | None, str | None], None] | None = None,
) -> str:
    """Submit `fn(**kwargs)` to run in the background. Returns a job_id."""
    job_id = f"job_{next(_id_counter):04d}_{uuid.uuid4().hex[:6]}"

    def _runnable() -> dict[str, Any]:
        try:
            result = fn(**kwargs)
            if on_complete:
                on_complete(job_id, result, None)
            return result
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller via job status
            if on_complete:
                on_complete(job_id, None, f"{type(exc).__name__}: {exc}")
            raise

    future = _executor.submit(_runnable)
    with _jobs_lock:
        _jobs[job_id] = Job(job_id=job_id, step_name=step_name, future=future)
    return job_id


def check_job_status(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if job is None:
        return {"job_id": job_id, "status": "unknown", "error": "no such job_id"}
    elapsed = round(time.time() - job.started_at, 2)
    if not job.future.done():
        return {"job_id": job_id, "step": job.step_name, "status": "running", "elapsed_seconds": elapsed}
    if job.future.exception() is not None:
        return {
            "job_id": job_id,
            "step": job.step_name,
            "status": "failed",
            "elapsed_seconds": elapsed,
            "error": str(job.future.exception()),
        }
    return {"job_id": job_id, "step": job.step_name, "status": "done", "elapsed_seconds": elapsed}


def get_job_result(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if job is None:
        return {"status": "error", "error": f"no such job_id: {job_id}"}
    if not job.future.done():
        return {"status": "error", "error": "job is still running; call check_job_status first"}
    exc = job.future.exception()
    if exc is not None:
        return {"status": "error", "error": str(exc)}
    return {"status": "ok", "result": job.future.result()}
