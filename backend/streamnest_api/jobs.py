from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

from .resolver import DownloadProgress
from .tickets import TicketNotFound


@dataclass(frozen=True)
class DownloadJobSnapshot:
    job_id: str
    status: str
    progress: float
    downloaded_bytes: int
    total_bytes: int | None
    speed: float | None
    eta: int | None
    file_ticket: str | None
    filename: str | None
    error: str | None


@dataclass
class _DownloadJob:
    deadline: float
    status: str = "queued"
    progress: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed: float | None = None
    eta: int | None = None
    file_ticket: str | None = None
    filename: str | None = None
    error: str | None = None


class DownloadJobStore:
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = ttl_seconds
        self._items: dict[str, _DownloadJob] = {}
        self._lock = threading.Lock()

    def issue(self) -> DownloadJobSnapshot:
        job_id = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._items[job_id] = _DownloadJob(deadline=now + self._ttl_seconds)
            return self._snapshot(job_id, self._items[job_id])

    def get(self, job_id: str) -> DownloadJobSnapshot:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            job = self._items.get(job_id)
            if job is None:
                raise TicketNotFound(job_id)
            if job.status in {"queued", "downloading"}:
                job.deadline = now + self._ttl_seconds
            return self._snapshot(job_id, job)

    def mark_downloading(self, job_id: str) -> None:
        with self._lock:
            job = self._require(job_id)
            job.status = "downloading"
            job.deadline = time.monotonic() + self._ttl_seconds

    def update(self, job_id: str, update: DownloadProgress) -> None:
        with self._lock:
            job = self._require(job_id)
            if job.status in {"ready", "failed"}:
                return
            job.status = "downloading"
            job.deadline = time.monotonic() + self._ttl_seconds
            if update.status == "retrying":
                # A refreshed signed URL starts in a new temporary directory;
                # progress from the abandoned source must not be shown as current.
                job.progress = 0.0
                job.downloaded_bytes = 0
                job.total_bytes = None
                job.speed = None
                job.eta = None
                return
            job.downloaded_bytes = max(job.downloaded_bytes, update.downloaded_bytes)
            if update.total_bytes:
                job.total_bytes = max(job.total_bytes or 0, update.total_bytes)
            if update.speed is not None:
                job.speed = update.speed
            if update.eta is not None:
                job.eta = update.eta
            if update.percent is not None:
                job.progress = max(job.progress, min(99.0, update.percent))

    def complete(self, job_id: str, *, file_ticket: str, filename: str) -> None:
        with self._lock:
            job = self._require(job_id)
            job.status = "ready"
            job.deadline = time.monotonic() + self._ttl_seconds
            job.progress = 100.0
            job.speed = 0.0
            job.eta = 0
            job.file_ticket = file_ticket
            job.filename = filename

    def fail(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._require(job_id)
            job.status = "failed"
            job.deadline = time.monotonic() + self._ttl_seconds
            job.speed = 0.0
            job.eta = None
            job.error = message

    def prune_expired(self) -> None:
        with self._lock:
            self._prune(time.monotonic())

    def _require(self, job_id: str) -> _DownloadJob:
        job = self._items.get(job_id)
        if job is None:
            raise TicketNotFound(job_id)
        return job

    def _prune(self, now: float) -> None:
        expired = [job_id for job_id, job in self._items.items() if job.deadline <= now]
        for job_id in expired:
            self._items.pop(job_id, None)

    @staticmethod
    def _snapshot(job_id: str, job: _DownloadJob) -> DownloadJobSnapshot:
        return DownloadJobSnapshot(
            job_id=job_id,
            status=job.status,
            progress=round(job.progress, 1),
            downloaded_bytes=job.downloaded_bytes,
            total_bytes=job.total_bytes,
            speed=job.speed,
            eta=job.eta,
            file_ticket=job.file_ticket,
            filename=job.filename,
            error=job.error,
        )
