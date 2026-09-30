import secrets
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DownloadClaim:
    url: str
    selector: str
    kind: str
    title: str
    adult_confirmed: bool
    source_url: str | None = None
    source_urls: tuple[str, ...] = ()
    format_id: str | None = None
    format_label: str | None = None


@dataclass(frozen=True)
class PreparedFileClaim:
    directory: Path
    path: Path
    media_type: str


class TicketNotFound(KeyError):
    pass


def cleanup_orphaned_temp_directories(
    *,
    temp_root: Path | None = None,
    older_than_seconds: int = 3600,
) -> int:
    root = (temp_root or Path(tempfile.gettempdir())).resolve()
    cutoff = time.time() - max(0, older_than_seconds)
    removed = 0
    try:
        candidates = list(root.iterdir())
    except OSError:
        return 0
    for candidate in candidates:
        if not candidate.name.startswith("streamnest-") or candidate.is_symlink():
            continue
        try:
            resolved = candidate.resolve()
            if resolved.parent != root or not resolved.is_dir():
                continue
            # A long download keeps writing video.part without changing the
            # containing directory's timestamp.  Treat a recently modified
            # direct child as active, even if the directory itself is old.
            latest_activity = resolved.stat().st_mtime
            for child in resolved.iterdir():
                if not child.is_symlink():
                    latest_activity = max(latest_activity, child.stat().st_mtime)
            if latest_activity > cutoff:
                continue
            shutil.rmtree(resolved)
            removed += 1
        except OSError:
            continue
    return removed


class TicketStore:
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = ttl_seconds
        self._items: dict[str, tuple[float, DownloadClaim]] = {}
        self._lock = threading.Lock()

    def issue(self, claim: DownloadClaim) -> str:
        ticket = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._items[ticket] = (now + self._ttl_seconds, claim)
        return ticket

    def get(self, ticket: str) -> DownloadClaim:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            item = self._items.get(ticket)
            if item is None:
                raise TicketNotFound(ticket)
            return item[1]

    def prune_expired(self) -> None:
        with self._lock:
            self._prune(time.monotonic())

    def _prune(self, now: float) -> None:
        expired = [key for key, (deadline, _) in self._items.items() if deadline <= now]
        for key in expired:
            self._items.pop(key, None)


class PreparedFileStore:
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = ttl_seconds
        self._items: dict[str, tuple[float, PreparedFileClaim]] = {}
        self._lock = threading.Lock()

    def issue(self, claim: PreparedFileClaim) -> str:
        ticket = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._items[ticket] = (now + self._ttl_seconds, claim)
        return ticket

    def take(self, ticket: str) -> PreparedFileClaim:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            item = self._items.pop(ticket, None)
            if item is None:
                raise TicketNotFound(ticket)
            return item[1]

    def prune_expired(self) -> None:
        with self._lock:
            self._prune(time.monotonic())

    def _prune(self, now: float) -> None:
        expired = [key for key, (deadline, _) in self._items.items() if deadline <= now]
        for key in expired:
            _, claim = self._items.pop(key)
            shutil.rmtree(claim.directory, ignore_errors=True)
