"""Background job queue.

Everything slow -- API calls, ffmpeg renders, agent runs -- goes through here so the UI
thread never blocks. Jobs are plain callables that receive a `JobContext` for progress
and cancellation; they know nothing about Qt.
"""

from __future__ import annotations

import itertools
import threading
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class JobContext:
    """Handed to the worker function. `cancelled` is checked cooperatively."""
    job_id: int
    _cancel: threading.Event = field(default_factory=threading.Event)
    _report: Callable[[int, float, str], None] = lambda job_id, fraction, message: None

    def progress(self, fraction: float, message: str = "") -> None:
        self._report(self.job_id, max(0.0, min(1.0, fraction)), message)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise JobCancelled()


class JobCancelled(RuntimeError):
    pass


@dataclass
class Job:
    id: int
    title: str
    state: JobState = JobState.QUEUED
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str = ""
    traceback: str = ""
    context: JobContext | None = None
    future: Future | None = None
    category: str = "general"


class JobQueue:
    """Thread pool with observable job state.

    Listeners are called from worker threads. The Qt layer bridges them onto the UI
    thread with a queued signal -- see ui.bridge.JobBridge.
    """

    def __init__(self, max_workers: int = 2) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers,
                                            thread_name_prefix="mediaforge")
        self._ids = itertools.count(1)
        self._jobs: dict[int, Job] = {}
        self._lock = threading.Lock()
        self._listeners: list[Callable[[Job], None]] = []

    def add_listener(self, listener: Callable[[Job], None]) -> None:
        self._listeners.append(listener)

    def _notify(self, job: Job) -> None:
        for listener in list(self._listeners):
            try:
                listener(job)
            except Exception:  # a broken listener must not kill the worker
                traceback.print_exc()

    def _report(self, job_id: int, fraction: float, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.progress, job.message = fraction, message
        self._notify(job)

    def submit(
        self, title: str, work: Callable[[JobContext], Any], category: str = "general"
    ) -> Job:
        job_id = next(self._ids)
        context = JobContext(job_id=job_id, _report=self._report)
        job = Job(id=job_id, title=title, context=context, category=category)
        with self._lock:
            self._jobs[job_id] = job
        self._notify(job)

        def runner() -> Any:
            job.state = JobState.RUNNING
            job.message = "Started"
            self._notify(job)
            try:
                value = work(context)
            except JobCancelled:
                job.state, job.message = JobState.CANCELLED, "Cancelled"
                self._notify(job)
                return None
            except Exception as error:
                job.state = JobState.CANCELLED if context.cancelled else JobState.FAILED
                job.error = str(error) or error.__class__.__name__
                job.traceback = traceback.format_exc()
                job.message = job.error.splitlines()[0][:200] if job.error else "Failed"
                self._notify(job)
                return None
            job.result = value
            job.state = JobState.DONE
            job.progress, job.message = 1.0, "Finished"
            self._notify(job)
            return value

        job.future = self._executor.submit(runner)
        return job

    def cancel(self, job_id: int) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job or job.state in (JobState.DONE, JobState.FAILED, JobState.CANCELLED):
            return False
        if job.context:
            job.context._cancel.set()
        if job.future and job.future.cancel():
            job.state, job.message = JobState.CANCELLED, "Cancelled before start"
            self._notify(job)
        else:
            job.message = "Cancelling..."
            self._notify(job)
        return True

    def jobs(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda job: job.id, reverse=True)

    def active_count(self) -> int:
        with self._lock:
            return sum(
                1 for job in self._jobs.values()
                if job.state in (JobState.QUEUED, JobState.RUNNING)
            )

    def clear_finished(self) -> None:
        with self._lock:
            for job_id in [
                job.id for job in self._jobs.values()
                if job.state in (JobState.DONE, JobState.FAILED, JobState.CANCELLED)
            ]:
                del self._jobs[job_id]

    def shutdown(self) -> None:
        for job in self.jobs():
            if job.context:
                job.context._cancel.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
