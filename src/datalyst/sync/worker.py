import logging
import os
import socket
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta

from datalyst.core.exceptions import NotFoundError
from datalyst.core.models import Job, JobStatus, SourceStatus
from datalyst.metastore import Metastore
from datalyst.sync.handlers import (
    CHECK_SOURCE,
    HANDLERS,
    Handler,
    JobContext,
    check_source_request,
)

logger = logging.getLogger(__name__)


@dataclass
class WorkerSetup:
    context: JobContext
    poll_interval: float = 2.0
    lease: timedelta = timedelta(minutes=15)


class SyncWorker:
    def __init__(
        self,
        setup: WorkerSetup | JobContext,
        handlers: Mapping[str, Handler] = HANDLERS,
        *,
        worker_id: str | None = None,
        reconfigure: Callable[[], WorkerSetup | None] | None = None,
        heartbeat_every: float = 10.0,
        maintenance_every: float = 60.0,
        keep_finished: timedelta = timedelta(days=7),
    ) -> None:
        if isinstance(setup, JobContext):
            setup = WorkerSetup(setup)
        self._context = setup.context
        self._poll_interval = setup.poll_interval
        self._lease = setup.lease
        self._reconfigure = reconfigure
        self._handlers = dict(handlers)
        self.worker_id = (
            worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        )
        self._heartbeat_every = heartbeat_every
        self._maintenance_every = maintenance_every
        self._keep_finished = keep_finished
        self._last_heartbeat = 0.0
        self._last_maintenance = 0.0

    @property
    def metastore(self) -> Metastore:
        return self._context.metastore

    def run(self, stop: threading.Event) -> None:
        logger.info("Sync worker %s started", self.worker_id)
        try:
            while not stop.is_set():
                if not self.tick():
                    stop.wait(self._poll_interval)
        finally:
            self.close()
            logger.info("Sync worker %s stopped", self.worker_id)

    def close(self) -> None:
        self.metastore.jobs.remove_worker(self.worker_id)
        self._context.datasources.close()

    def tick(self) -> bool:
        self._apply_new_settings()
        self._housekeeping()
        job = self.metastore.jobs.claim(self.worker_id)
        if job is None:
            return False
        self.process(job)
        return True

    def drain(self, limit: int = 1000) -> int:
        processed = 0
        while processed < limit and self.tick():
            processed += 1
        return processed

    def schedule_due_sources(self) -> int:
        due = self.metastore.sources.due_for_check()
        return self.metastore.jobs.enqueue_many(check_source_request(s) for s in due)

    def process(self, job: Job) -> None:
        handler = self._handlers.get(job.kind)
        started = time.perf_counter()
        try:
            if handler is None:
                raise NotFoundError(f"No handler for job kind '{job.kind}'")
            followups = handler(job, self._context)
        except Exception as e:
            logger.warning("Job %s %s raised", job.id, job.kind, exc_info=True)
            self._handle_failure(job, e)
            return
        self.metastore.jobs.enqueue_many(followups)
        self.metastore.jobs.complete(job.id)
        logger.info(
            "Job %s %s:%s done in %.0f ms, %d follow-up(s)",
            job.id,
            job.kind,
            job.target or job.source_id,
            (time.perf_counter() - started) * 1000,
            len(followups),
        )

    def _apply_new_settings(self) -> None:
        if self._reconfigure is None:
            return
        try:
            setup = self._reconfigure()
        except Exception:
            logger.exception("Could not apply new settings; keeping the current ones")
            return
        if setup is None:
            return
        old = self._context
        self._context = setup.context
        self._poll_interval = setup.poll_interval
        self._lease = setup.lease
        old.datasources.close()
        logger.info("Worker %s picked up new settings", self.worker_id)

    def _handle_failure(self, job: Job, error: Exception) -> None:
        message = f"{type(error).__name__}: {error}"
        status = self.metastore.jobs.fail(job, message)
        log = logger.error if status == JobStatus.FAILED else logger.warning
        log(
            "Job %s %s failed (attempt %d): %s", job.id, job.kind, job.attempts, message
        )
        if job.kind == CHECK_SOURCE:
            try:
                self.metastore.sources.set_status(
                    job.source_id, SourceStatus.ERROR, message
                )
            except NotFoundError:
                pass

    def _housekeeping(self) -> None:
        now = time.monotonic()
        if now - self._last_heartbeat >= self._heartbeat_every:
            self.metastore.jobs.heartbeat(
                self.worker_id, socket.gethostname(), os.getpid()
            )
            self._last_heartbeat = now
        self.schedule_due_sources()
        if now - self._last_maintenance >= self._maintenance_every:
            if recovered := self.metastore.jobs.recover_stale(self._lease):
                logger.warning("Recovered %d job(s) from stopped workers", recovered)
            self.metastore.jobs.purge(self._keep_finished)
            self._last_maintenance = now
