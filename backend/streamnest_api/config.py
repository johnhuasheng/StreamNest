import os
from dataclasses import dataclass
from pathlib import Path


def _origins() -> tuple[str, ...]:
    configured = os.getenv("STREAMNEST_ALLOWED_ORIGINS", "")
    values = [item.strip().rstrip("/") for item in configured.split(",") if item.strip()]
    if values:
        return tuple(values)
    return (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    )


def _download_output_directory() -> Path:
    configured = os.getenv("STREAMNEST_OUTPUT_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    app_dir = os.getenv("STREAMNEST_APP_DIR", "").strip()
    if app_dir:
        return (Path(app_dir).expanduser().resolve() / "下载内容").resolve()
    return (Path(__file__).resolve().parents[2] / "下载内容").resolve()


@dataclass(frozen=True)
class Settings:
    allowed_origins: tuple[str, ...] = _origins()
    download_output_directory: Path = _download_output_directory()
    max_duration_seconds: int = int(os.getenv("STREAMNEST_MAX_DURATION_SECONDS", "14400"))
    max_file_size_bytes: int = int(os.getenv("STREAMNEST_MAX_FILE_SIZE_BYTES", str(8 * 1024**3)))
    max_bilibili_file_size_bytes: int = int(os.getenv("STREAMNEST_MAX_BILIBILI_FILE_SIZE_BYTES", str(8 * 1024**3)))
    max_image_size_bytes: int = int(os.getenv("STREAMNEST_MAX_IMAGE_SIZE_BYTES", str(50 * 1024**2)))
    ticket_ttl_seconds: int = int(os.getenv("STREAMNEST_TICKET_TTL_SECONDS", "900"))
    resolve_timeout_seconds: int = int(os.getenv("STREAMNEST_RESOLVE_TIMEOUT_SECONDS", "45"))
    max_concurrent_downloads: int = int(os.getenv("STREAMNEST_MAX_CONCURRENT_DOWNLOADS", "2"))
    download_rate_limit_per_hour: int = int(os.getenv("STREAMNEST_DOWNLOAD_RATE_LIMIT_PER_HOUR", "120"))


settings = Settings()
