"""Import queue: the operator submits a file and follows its progress instead of waiting.

Before this, importing was synchronous. Whoever sent the PDF had the browser stuck until OCR
finished (up to 80 seconds on a 41-page scanned statement), and anyone who sent a file at the
same time was refused.

Neither worked for the team. Staring at a frozen screen for 80 seconds makes people reload and
send the file again; being refused makes them retry blind, with no idea when the server will be
free. Worse, behind a proxy the long request hits the timeout before the reading is done.

## The design

One worker and one queue. A single worker is not a simplification, it is the real limit. Each
reading runs three tesseract processes; two readings at once would be six on a four-core
machine, and both would finish later than if one had waited its turn.

    POST .../statements          -> 202 and a ticket, immediately
    GET  /api/imports/{ticket}   -> where the job stands, and the result once it's ready

## What this queue is NOT

It doesn't survive a service restart: the state lives in process memory. A job in progress when
the workbench goes down is lost, and the person sends the file again. That is safe, because
re-importing the same statement duplicates nothing (`identity` is UNIQUE, see ADR 4).

A queue that survives a crash would need to keep the PDF on disk and reprocess it at boot. Not
worth it: it's about 15 files a day, and resending costs one click.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("workbench")

# How long a result stays available after it's ready. The screen polls every few seconds; the
# slack is for someone who closed the tab and came back.
KEEP_RESULT_SECONDS = 30 * 60


@dataclass
class Job:
    ticket: str
    caption: str
    status: str = "queued"          # queued -> processing -> done | error
    results: list = field(default_factory=list)
    err: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def as_dict(self, position: int | None = None) -> dict:
        body: dict[str, Any] = {
            "ticket": self.ticket,
            "caption": self.caption,
            "status": self.status,
        }
        if self.status == "queued" and position is not None:
            body["position"] = position
        if self.status == "done":
            body["results"] = self.results
        if self.status == "error":
            body["error"] = self.err
        return body


class ImportQueue:
    """One per app, like the write lock and the login gate."""

    def __init__(self) -> None:
        self._pending: queue.Queue[tuple[str, Callable[[], list]]] = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    # -------------------------------------------------------------- intake

    def accept(self, caption: str, task: Callable[[], list]) -> Job:
        """Registers the job and returns its ticket right away."""
        ticket = uuid.uuid4().hex[:12]
        job = Job(ticket=ticket, caption=caption)
        with self._lock:
            self._jobs[ticket] = job
            self._order.append(ticket)
            self._purge_expired()
        self._pending.put((ticket, task))
        self._ensure_worker()
        return job

    def lookup(self, ticket: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(ticket)
            if job is None:
                return None
            position = None
            if job.status == "queued":
                ahead = [
                    p
                    for p in self._order
                    if self._jobs[p].status in ("queued", "processing")
                ]
                position = ahead.index(ticket) if ticket in ahead else None
            return job.as_dict(position)

    def in_progress(self) -> list[dict]:
        """What the screen shows in its status bar: everything not finished yet."""
        with self._lock:
            return [
                self._jobs[p].as_dict()
                for p in self._order
                if self._jobs[p].status in ("queued", "processing")
            ]

    # -------------------------------------------------------------- engine

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(
                target=self._work, name="workbench-queue", daemon=True
            )
            self._worker.start()

    def _work(self) -> None:
        while True:
            try:
                ticket, task = self._pending.get(timeout=60)
            except queue.Empty:
                return  # idle: the thread exits and is started again on the next upload
            job = self._jobs.get(ticket)
            if job is None:  # pragma: no cover - only if it expired before running
                continue
            job.status = "processing"
            try:
                job.results = task()
                job.status = "done"
            except Exception as err:  # reading a statement can fail in many ways
                log.exception("import %s failed", ticket)
                job.err = str(err)
                job.status = "error"
            finally:
                job.finished_at = time.time()
                self._pending.task_done()

    def _purge_expired(self) -> None:
        """Must be called with the lock held."""
        limit = time.time() - KEEP_RESULT_SECONDS
        expired = [
            p
            for p in self._order
            if (t := self._jobs[p]).finished_at is not None
            and t.finished_at < limit
        ]
        for p in expired:
            del self._jobs[p]
            self._order.remove(p)
