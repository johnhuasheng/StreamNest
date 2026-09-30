import os
import time
from pathlib import Path

import pytest

from streamnest_api.jobs import DownloadJobStore
from streamnest_api.resolver import DownloadProgress
from streamnest_api.tickets import (
    DownloadClaim,
    PreparedFileClaim,
    PreparedFileStore,
    TicketNotFound,
    TicketStore,
    cleanup_orphaned_temp_directories,
)


def test_ticket_store_explicit_prune_removes_expired_claim() -> None:
    store = TicketStore(-1)
    ticket = store.issue(
        DownloadClaim(
            url="https://www.youtube.com/watch?v=test",
            selector="18",
            kind="快速",
            title="test",
            adult_confirmed=False,
        )
    )

    store.prune_expired()

    with pytest.raises(TicketNotFound):
        store.get(ticket)


def test_prepared_file_prune_removes_expired_directory(tmp_path: Path) -> None:
    directory = tmp_path / "prepared"
    directory.mkdir()
    file_path = directory / "video.mp4"
    file_path.write_bytes(b"prepared-media")
    store = PreparedFileStore(-1)
    store.issue(PreparedFileClaim(directory, file_path, "video/mp4"))

    store.prune_expired()

    assert not directory.exists()


def test_download_job_store_explicit_prune_removes_expired_job() -> None:
    store = DownloadJobStore(-1)
    job = store.issue()

    store.prune_expired()

    with pytest.raises(TicketNotFound):
        store.get(job.job_id)


def test_download_job_progress_resets_when_signed_media_source_changes() -> None:
    store = DownloadJobStore(60)
    job = store.issue()
    store.mark_downloading(job.job_id)
    store.update(job.job_id, DownloadProgress("downloading", 100, 200, 10.0, 10, 50.0))
    store.update(job.job_id, DownloadProgress("retrying", 0, None, None, None, 0.0))

    refreshed = store.get(job.job_id)
    assert refreshed.status == "downloading"
    assert refreshed.progress == 0.0
    assert refreshed.downloaded_bytes == 0
    assert refreshed.total_bytes is None


def test_orphan_cleanup_only_removes_old_streamnest_directories(tmp_path: Path) -> None:
    old_streamnest = tmp_path / "streamnest-old-job"
    recent_streamnest = tmp_path / "streamnest-recent-job"
    unrelated = tmp_path / "another-app-job"
    for directory in (old_streamnest, recent_streamnest, unrelated):
        directory.mkdir()
        (directory / "file.part").write_bytes(b"temporary")
    old_time = time.time() - 7200
    os.utime(old_streamnest, (old_time, old_time))
    os.utime(old_streamnest / "file.part", (old_time, old_time))
    os.utime(unrelated, (old_time, old_time))

    removed = cleanup_orphaned_temp_directories(temp_root=tmp_path, older_than_seconds=3600)

    assert removed == 1
    assert not old_streamnest.exists()
    assert recent_streamnest.exists()
    assert unrelated.exists()


def test_orphan_cleanup_preserves_old_directory_with_active_download(tmp_path: Path) -> None:
    active = tmp_path / "streamnest-active-job"
    active.mkdir()
    part = active / "video.part"
    part.write_bytes(b"recent progress")
    old_time = time.time() - 7200
    os.utime(active, (old_time, old_time))

    removed = cleanup_orphaned_temp_directories(temp_root=tmp_path, older_than_seconds=3600)

    assert removed == 0
    assert part.read_bytes() == b"recent progress"
