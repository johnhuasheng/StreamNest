from __future__ import annotations

import asyncio
import html
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.background import BackgroundTask
from starlette.responses import Response

from . import __version__
from .config import settings
from .bilibili_session import bilibili_session
from .jobs import DownloadJobSnapshot, DownloadJobStore
from .local_core import LocalCoreError, call_local_core
from .models import DownloadJobResponse, FormatOption, HealthResponse, HelperDownloadRequest, ImageCollectionRequest, LocalCoreResolveRequest, LocalCoreTaskRequest, PrepareResponse, ResolveRequest, ResolveResponse
from .platforms import AdultConfirmationRequired, UrlValidationError, extract_video_url, validate_platform_url
from .resolver import ResolverError, build_thumbnail_format, download_helper_media, download_media, normalize_media_page_url, resolve_media, validate_helper_media_request
from .tickets import DownloadClaim, PreparedFileClaim, PreparedFileStore, TicketNotFound, TicketStore, cleanup_orphaned_temp_directories


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client: str, bucket: str, *, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        key = (client, bucket)
        with self._lock:
            events = self._events[key]
            cutoff = now - window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True


app = FastAPI(
    title="StreamNest Resolver",
    version=__version__,
    description="Authorized public-media metadata and download service.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    expose_headers=["Content-Disposition"],
    max_age=600,
)

tickets = TicketStore(settings.ticket_ttl_seconds)
prepared_files = PreparedFileStore(settings.ticket_ttl_seconds)
download_jobs = DownloadJobStore(settings.ticket_ttl_seconds)
limiter = SlidingWindowLimiter()
download_slots = asyncio.Semaphore(settings.max_concurrent_downloads)
output_directory = settings.download_output_directory
output_lock = threading.Lock()


def _save_to_output(source: Path, requested_name: str) -> Path:
    resolved_source = source.resolve()
    if not resolved_source.is_file():
        raise OSError("准备好的文件不存在。")

    root = output_directory.resolve()
    root.mkdir(parents=True, exist_ok=True)
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", Path(requested_name).name).strip(" ._")
    requested = Path(cleaned or "download")
    suffix = requested.suffix[:16]
    stem = requested.stem[:150].strip(" ._") or "download"
    if stem.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        stem = f"_{stem}"

    with output_lock:
        destination = root / f"{stem}{suffix}"
        counter = 2
        while destination.exists():
            destination = root / f"{stem} ({counter}){suffix}"
            counter += 1
        shutil.copy2(resolved_source, destination)
    return destination
background_downloads: set[asyncio.Task[None]] = set()
orphan_cleanup_lock = threading.Lock()
last_orphan_cleanup = 0.0


def _cleanup_orphaned_temp_files() -> None:
    global last_orphan_cleanup
    now = time.monotonic()
    with orphan_cleanup_lock:
        if now - last_orphan_cleanup < 5 * 60:
            return
        last_orphan_cleanup = now
    cleanup_orphaned_temp_directories(older_than_seconds=settings.ticket_ttl_seconds + 60)


def _cleanup_core_delivery(task_id: int, directory: Path | None = None) -> None:
    if directory:
        shutil.rmtree(directory, ignore_errors=True)
    for attempt in range(3):
        try:
            call_local_core("DELETE", f"/api/tasks/{task_id}?deleteFile=true", timeout=10)
            return
        except LocalCoreError:
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
    # The browser already received the requested file. If all retries fail, the
    # core keeps the copy rather than risking a partial browser download.


def _client_id(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _is_local_bilibili_extension_request(request: Request) -> bool:
    return (
        request.url.path == "/v1/bilibili/session"
        and _client_id(request) in {"127.0.0.1", "::1"}
        and re.fullmatch(r"chrome-extension://[a-p]{32}", request.headers.get("origin", "")) is not None
    )


@app.middleware("http")
async def bilibili_extension_cors(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    # Scope extension CORS to one localhost endpoint; do not grant extension
    # origins access to the rest of the resolver API.
    if not _is_local_bilibili_extension_request(request):
        return await call_next(request)
    origin = request.headers["origin"]
    if request.method == "OPTIONS" and request.headers.get("access-control-request-method") == "POST":
        return Response(status_code=204, headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "POST",
            "Access-Control-Allow-Headers": "Content-Type, X-StreamNest-Bridge",
            "Access-Control-Max-Age": "600",
            "Vary": "Origin",
        })
    response = await call_next(request)
    if request.method == "POST":
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers.add_vary_header("Origin")
    return response


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _download_error(request: Request, status: int, code: str, message: str) -> JSONResponse | HTMLResponse:
    if "text/html" not in request.headers.get("accept", ""):
        return _error(status, code, message)

    return_to = next((origin for origin in settings.allowed_origins if origin.startswith("http")), "http://localhost:3000")
    safe_message = html.escape(message)
    safe_code = html.escape(code)
    safe_return_to = html.escape(return_to, quote=True)
    return HTMLResponse(
        status_code=status,
        content=f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>下载未完成 · StreamNest</title><style>
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:#f4f5f7;color:#1b1e24;font-family:system-ui,'Microsoft YaHei',sans-serif}}
main{{width:min(440px,100%);padding:34px;background:#fff;border:1px solid #e4e6e9;border-radius:16px;box-shadow:0 18px 50px rgba(25,31,40,.08);text-align:center}}
.mark{{width:52px;height:52px;display:grid;place-items:center;margin:0 auto 18px;border-radius:14px;background:#fceeed;color:#d74f43;font-size:24px;font-weight:700}}
h1{{margin:0;font-size:22px}}p{{margin:12px 0 0;color:#6f7682;font-size:14px;line-height:1.7}}small{{display:block;margin-top:12px;color:#a2a7af}}a{{display:inline-block;margin-top:24px;padding:11px 18px;border-radius:9px;background:#f45140;color:#fff;text-decoration:none;font-size:14px;font-weight:700}}
</style></head><body><main><div class="mark">!</div><h1>下载未完成</h1><p>{safe_message}</p><small>错误代码：{safe_code}</small><a href="{safe_return_to}">返回下载管理器</a></main></body></html>""",
    )


def _job_response(snapshot: DownloadJobSnapshot) -> DownloadJobResponse:
    return DownloadJobResponse(
        job_id=snapshot.job_id,
        status=snapshot.status,
        progress=snapshot.progress,
        downloaded_bytes=snapshot.downloaded_bytes,
        total_bytes=snapshot.total_bytes,
        speed=snapshot.speed,
        eta=snapshot.eta,
        file_ticket=snapshot.file_ticket,
        filename=snapshot.filename,
        error=snapshot.error,
    )


async def _run_download_job(job_id: str, claim: DownloadClaim) -> None:
    try:
        validate_platform_url(claim.url, adult_confirmed=claim.adult_confirmed)
        async with download_slots:
            download_jobs.mark_downloading(job_id)
            downloaded = await asyncio.to_thread(
                download_media,
                claim,
                lambda update: download_jobs.update(job_id, update),
            )
        file_ticket = prepared_files.issue(
            PreparedFileClaim(downloaded.directory, downloaded.path, downloaded.media_type)
        )
        download_jobs.complete(job_id, file_ticket=file_ticket, filename=downloaded.path.name)
    except (UrlValidationError, ResolverError) as exc:
        download_jobs.fail(job_id, str(exc))
    except Exception:
        download_jobs.fail(job_id, "文件准备失败，请稍后重试。")


async def _run_helper_download_job(job_id: str, body: HelperDownloadRequest) -> None:
    try:
        safe_headers = validate_helper_media_request(body.url, body.platform, body.headers)
        async with download_slots:
            download_jobs.mark_downloading(job_id)
            downloaded = await asyncio.to_thread(
                download_helper_media,
                body.url,
                body.platform,
                safe_headers,
                lambda update: download_jobs.update(job_id, update),
            )
        file_ticket = prepared_files.issue(
            PreparedFileClaim(downloaded.directory, downloaded.path, downloaded.media_type)
        )
        download_jobs.complete(job_id, file_ticket=file_ticket, filename=downloaded.path.name)
    except ResolverError as exc:
        download_jobs.fail(job_id, str(exc))
    except Exception:
        download_jobs.fail(job_id, "文件准备失败，请稍后重试。")


@app.middleware("http")
async def security_headers(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.get("/healthz", response_model=HealthResponse)
async def health(background_tasks: BackgroundTasks) -> HealthResponse:
    tickets.prune_expired()
    prepared_files.prune_expired()
    download_jobs.prune_expired()
    background_tasks.add_task(_cleanup_orphaned_temp_files)
    return HealthResponse(status="ok", service="streamnest-resolver", version=__version__)


@app.get("/v1/bilibili/session")
async def bilibili_session_status() -> JSONResponse:
    return JSONResponse(
        content={"active": bool(bilibili_session.get())},
        headers={"Cache-Control": "no-store"},
    )


@app.post("/v1/bilibili/session")
async def sync_bilibili_session(request: Request) -> JSONResponse:
    # Only the local Edge extension may transfer Bilibili-domain cookies. The
    # management page never receives them and this endpoint never echoes them.
    if (
        not _is_local_bilibili_extension_request(request)
        or request.headers.get("x-streamnest-bridge") != "bilibili-v1"
    ):
        return _error(403, "local_extension_required", "仅允许本机 StreamNest 浏览器助手同步 B 站会话。")
    raw = await request.body()
    if len(raw) > 64 * 1024:
        return _error(413, "session_too_large", "B 站会话数据超过安全限制。")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return _error(400, "invalid_session", "B 站会话数据格式不正确。")
    if not isinstance(body, dict) or not bilibili_session.replace(body.get("cookies")):
        return _error(400, "invalid_session", "没有在当前 Edge 配置中找到有效的 B 站登录会话。")
    return JSONResponse(content={"active": True}, headers={"Cache-Control": "no-store"})


@app.post("/v1/bilibili/session/clear")
async def clear_bilibili_session(request: Request) -> JSONResponse:
    if (
        _client_id(request) not in {"127.0.0.1", "::1"}
        or request.headers.get("origin") not in {"http://localhost:3000", "http://127.0.0.1:3000"}
    ):
        return _error(403, "local_manager_required", "仅允许本机 StreamNest 网页断开 B 站会话。")
    bilibili_session.clear()
    return JSONResponse(content={"active": False}, headers={"Cache-Control": "no-store"})


@app.get("/v1/core/health")
async def local_core_health() -> JSONResponse:
    try:
        payload = await asyncio.to_thread(call_local_core, "GET", "/api/health", timeout=4)
        try:
            # A different local app can also expose /api/health on port 4000.
            # Probe the contract we actually need before telling the UI that
            # Douyin/Kuaishou parsing is available. Empty URLs must be rejected
            # locally by a compatible core without downloading any media.
            await asyncio.to_thread(call_local_core, "POST", "/api/parse", {"url": ""}, timeout=4)
        except LocalCoreError as exc:
            if exc.status_code == 404:
                return _error(503, "local_core_incompatible", "4000 端口不是抖音/快手下载核心；当前网页的其他平台仍可使用。")
            if exc.status_code not in {400, 422}:
                raise
        return JSONResponse(content={"status": "ok", "core": payload})
    except LocalCoreError as exc:
        return _error(exc.status_code, exc.code, str(exc))


@app.post("/v1/core/resolve")
async def local_core_resolve(body: LocalCoreResolveRequest) -> JSONResponse:
    normalized_url = extract_video_url(body.url)
    try:
        platform = validate_platform_url(normalized_url)
        if platform.name not in {"抖音", "快手"}:
            return _error(400, "invalid_url", "国内平台核心只处理抖音和快手链接。")
        payload = await asyncio.to_thread(
            call_local_core,
            "POST",
            "/api/parse",
            {"url": normalized_url},
        )
        meta = payload.get("meta")
        if not isinstance(meta, dict) or meta.get("platform") not in {"douyin", "kuaishou"}:
            return _error(422, "invalid_core_response", "国内平台核心没有返回可用视频信息。")
        raw_formats = meta.get("formats")
        title = str(meta.get("title") or f"{platform.name}视频")
        raw_images = meta.get("images")
        image_sources: list[str] = []
        if isinstance(raw_images, list):
            for raw_image in raw_images[:50]:
                if not isinstance(raw_image, str) or not raw_image:
                    continue
                image_format = build_thumbnail_format(normalized_url, raw_image)
                if image_format and image_format.source_url and image_format.source_url not in image_sources:
                    image_sources.append(image_format.source_url)

        if isinstance(raw_formats, list) and image_sources:
            if len(image_sources) > 1:
                collection_ticket = tickets.issue(
                    DownloadClaim(
                        url=normalized_url,
                        selector="image-all",
                        kind="图片合集",
                        title=title,
                        adult_confirmed=False,
                        source_urls=tuple(image_sources),
                    )
                )
                raw_formats.append(
                    {
                        "id": "image-all",
                        "quality": f"全部 {len(image_sources)} 张图片",
                        "ext": "zip",
                        "size": None,
                        "note": f"图片合集 · {len(image_sources)} 张原尺寸图片 · ZIP",
                        "kind": "image_collection",
                        "image_count": len(image_sources),
                        "download_ticket": collection_ticket,
                    }
                )

            for index, image_source in enumerate(image_sources, start=1):
                image_ticket = tickets.issue(
                    DownloadClaim(
                        url=normalized_url,
                        selector=f"image-{index}",
                        kind="图片",
                        title=f"{title}-{index:02d}",
                        adult_confirmed=False,
                        source_url=image_source,
                    )
                )
                raw_formats.append(
                    {
                        "id": f"image-{index}",
                        "quality": f"第 {index} 张原图",
                        "ext": "image",
                        "size": None,
                        "note": f"图片 · 图文作品第 {index}/{len(image_sources)} 张 · 原始尺寸",
                        "kind": "image",
                        "image_index": index,
                        "image_count": len(image_sources),
                        "download_ticket": image_ticket,
                    }
                )
        else:
            image_format = build_thumbnail_format(
                normalized_url,
                str(meta.get("thumbnail")) if meta.get("thumbnail") else None,
            )
            if image_format and isinstance(raw_formats, list):
                image_ticket = tickets.issue(
                    DownloadClaim(
                        url=normalized_url,
                        selector=image_format.selector,
                        kind=image_format.kind,
                        title=title,
                        adult_confirmed=False,
                        source_url=image_format.source_url,
                    )
                )
                raw_formats.append(
                    {
                        "id": image_format.id,
                        "quality": image_format.label,
                        "ext": "image",
                        "size": None,
                        "note": image_format.detail,
                        "kind": "image",
                        "image_count": 1,
                        "download_ticket": image_ticket,
                    }
                )
            elif isinstance(raw_formats, list):
                first_video = next(
                    (item for item in raw_formats if isinstance(item, dict) and item.get("ext") == "mp4"),
                    None,
                )
                if first_video:
                    raw_formats.append(
                        {
                            "id": "image-frame",
                            "quality": "视频首帧",
                            "ext": "image",
                            "size": None,
                            "note": "图片 · 从公开视频生成 · JPG",
                            "kind": "frame",
                            "source_quality": first_video.get("quality") or "720p",
                        }
                    )
        return JSONResponse(content=payload)
    except UrlValidationError as exc:
        return _error(400, "invalid_url", str(exc))
    except LocalCoreError as exc:
        return _error(exc.status_code, exc.code, str(exc))


@app.post("/v1/core/tasks", status_code=201)
async def local_core_start_task(body: LocalCoreTaskRequest) -> JSONResponse:
    normalized_url = extract_video_url(body.url)
    try:
        platform = validate_platform_url(normalized_url)
        if platform.name not in {"抖音", "快手"}:
            return _error(400, "invalid_url", "国内平台核心只处理抖音和快手链接。")
        payload = await asyncio.to_thread(
            call_local_core,
            "POST",
            "/api/tasks",
            {"url": normalized_url, "quality": body.quality, "format": body.format},
        )
        return JSONResponse(status_code=201, content=payload)
    except UrlValidationError as exc:
        return _error(400, "invalid_url", str(exc))
    except LocalCoreError as exc:
        return _error(exc.status_code, exc.code, str(exc))


@app.post("/v1/image-collections", status_code=201)
async def create_image_collection(body: ImageCollectionRequest) -> JSONResponse:
    claims: list[DownloadClaim] = []
    try:
        for ticket in dict.fromkeys(body.tickets):
            claim = tickets.get(ticket)
            if claim.kind != "图片" or not claim.source_url:
                return _error(400, "invalid_image_selection", "所选项目中包含非图片格式，请重新解析。")
            claims.append(claim)
    except TicketNotFound:
        return _error(404, "ticket_not_found", "部分图片凭证已过期，请重新解析后再选择。")

    if not claims:
        return _error(400, "empty_image_selection", "请至少选择一张图片。")
    source_page = claims[0].url
    if any(claim.url != source_page for claim in claims):
        return _error(400, "mixed_image_selection", "只能打包同一个图文作品中的图片。")

    source_urls = tuple(dict.fromkeys(claim.source_url for claim in claims if claim.source_url))
    collection_ticket = tickets.issue(
        DownloadClaim(
            url=source_page,
            selector="image-selected",
            kind="图片合集",
            title=body.title,
            adult_confirmed=claims[0].adult_confirmed,
            source_urls=source_urls,
        )
    )
    return JSONResponse(
        status_code=201,
        content={"download_ticket": collection_ticket, "image_count": len(source_urls)},
    )


@app.get("/v1/core/tasks/{task_id}")
async def local_core_task(task_id: int) -> JSONResponse:
    if task_id <= 0:
        return _error(404, "task_not_found", "下载任务不存在。")
    try:
        payload = await asyncio.to_thread(call_local_core, "GET", f"/api/tasks/{task_id}")
        return JSONResponse(content=payload)
    except LocalCoreError as exc:
        return _error(exc.status_code, exc.code, str(exc))


@app.post("/v1/core/tasks/{task_id}/{action}")
async def local_core_task_action(task_id: int, action: str) -> JSONResponse:
    if task_id <= 0 or action not in {"pause", "resume", "cancel", "retry"}:
        return _error(400, "invalid_action", "任务操作无效。")
    try:
        payload = await asyncio.to_thread(
            call_local_core,
            "POST",
            f"/api/tasks/{task_id}/{action}",
        )
        return JSONResponse(content=payload)
    except LocalCoreError as exc:
        return _error(exc.status_code, exc.code, str(exc))


@app.post("/v1/core/file/{task_id}", response_model=None)
@app.get("/v1/core/file/{task_id}", response_model=None)
async def local_core_file(request: Request, task_id: int, consume: bool = False) -> FileResponse | JSONResponse | HTMLResponse:
    if task_id <= 0:
        return _download_error(request, 404, "task_not_found", "下载任务不存在。")
    try:
        payload = await asyncio.to_thread(call_local_core, "GET", f"/api/tasks/{task_id}")
        task = payload.get("task")
        if not isinstance(task, dict) or task.get("status") != "completed":
            return _download_error(request, 409, "file_not_ready", "视频文件尚未下载完成。")
        file_path = Path(str(task.get("filePath") or "")).resolve()
        download_dir = Path(str(task.get("downloadDir") or "")).resolve()
        if (
            not file_path.is_relative_to(download_dir)
            or file_path.suffix.lower() != ".mp4"
            or not file_path.is_file()
        ):
            return _download_error(request, 404, "file_not_found", "下载文件已被移动或删除。")
        if request.method == "POST":
            destination = await asyncio.to_thread(_save_to_output, file_path, file_path.name)
            await asyncio.to_thread(_cleanup_core_delivery, task_id)
            return JSONResponse(
                content={
                    "ok": True,
                    "filename": destination.name,
                    "folder": str(destination.parent),
                }
            )
        return FileResponse(
            path=file_path,
            media_type="video/mp4",
            filename=file_path.name,
            background=BackgroundTask(_cleanup_core_delivery, task_id) if consume else None,
        )
    except (LocalCoreError, OSError) as exc:
        if isinstance(exc, LocalCoreError):
            return _download_error(request, exc.status_code, exc.code, str(exc))
        return _download_error(request, 404, "file_not_found", "下载文件已被移动或删除。")


@app.post("/v1/core/frame/{task_id}", response_model=None)
@app.get("/v1/core/frame/{task_id}", response_model=None)
async def local_core_frame(request: Request, task_id: int, consume: bool = False) -> FileResponse | JSONResponse | HTMLResponse:
    if task_id <= 0:
        return _download_error(request, 404, "task_not_found", "下载任务不存在。")
    directory: Path | None = None
    try:
        payload = await asyncio.to_thread(call_local_core, "GET", f"/api/tasks/{task_id}")
        task = payload.get("task")
        if not isinstance(task, dict) or task.get("status") != "completed":
            return _download_error(request, 409, "file_not_ready", "源视频尚未下载完成。")
        file_path = Path(str(task.get("filePath") or "")).resolve()
        download_dir = Path(str(task.get("downloadDir") or "")).resolve()
        if (
            not file_path.is_relative_to(download_dir)
            or file_path.suffix.lower() != ".mp4"
            or not file_path.is_file()
        ):
            return _download_error(request, 404, "file_not_found", "源视频文件已被移动或删除。")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return _download_error(request, 503, "ffmpeg_missing", "生成视频首帧需要 FFmpeg。")
        directory = Path(tempfile.mkdtemp(prefix="streamnest-core-frame-"))
        output = directory / "first-frame.jpg"
        result = await asyncio.to_thread(
            subprocess.run,
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                "0.1",
                "-i",
                str(file_path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-y",
                str(output),
            ],
            capture_output=True,
            timeout=90,
            check=False,
        )
        if result.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
            shutil.rmtree(directory, ignore_errors=True)
            return _download_error(request, 422, "frame_failed", "这个视频暂时无法生成首帧图片。")
        safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", file_path.stem).strip(" ._")[:100]
        filename = f"{safe_stem or 'video'}-first-frame.jpg"
        if request.method == "POST":
            destination = await asyncio.to_thread(_save_to_output, output, filename)
            await asyncio.to_thread(_cleanup_core_delivery, task_id, directory)
            directory = None
            return JSONResponse(
                content={
                    "ok": True,
                    "filename": destination.name,
                    "folder": str(destination.parent),
                }
            )
        return FileResponse(
            path=output,
            media_type="image/jpeg",
            filename=filename,
            background=(
                BackgroundTask(_cleanup_core_delivery, task_id, directory)
                if consume
                else BackgroundTask(shutil.rmtree, directory, ignore_errors=True)
            ),
        )
    except (LocalCoreError, OSError, subprocess.SubprocessError) as exc:
        if directory:
            shutil.rmtree(directory, ignore_errors=True)
        if isinstance(exc, LocalCoreError):
            return _download_error(request, exc.status_code, exc.code, str(exc))
        return _download_error(request, 422, "frame_failed", "生成视频首帧失败，请稍后重试。")


@app.post("/v1/resolve", response_model=ResolveResponse)
async def resolve(request: Request, body: ResolveRequest) -> ResolveResponse | JSONResponse:
    if not limiter.allow(_client_id(request), "resolve", limit=12, window_seconds=60):
        return _error(429, "rate_limited", "解析请求过于频繁，请稍后再试")
    normalized_url = extract_video_url(body.url)
    try:
        platform = validate_platform_url(normalized_url, adult_confirmed=body.adult_confirmed)
    except AdultConfirmationRequired as exc:
        return _error(400, "adult_confirmation_required", str(exc))
    except UrlValidationError as exc:
        return _error(400, "invalid_url", str(exc))

    def resolve_normalized_page():
        resolved_url = normalize_media_page_url(normalized_url)
        resolved_platform = validate_platform_url(resolved_url, adult_confirmed=body.adult_confirmed)
        if resolved_platform.name != platform.name:
            raise ResolverError("平台返回的视频地址未通过安全检查")
        return resolved_url, resolve_media(resolved_url, adult_confirmed=body.adult_confirmed)

    try:
        normalized_url, media = await asyncio.wait_for(
            asyncio.to_thread(resolve_normalized_page),
            timeout=settings.resolve_timeout_seconds,
        )
    except TimeoutError:
        return _error(504, "resolve_timeout", "平台响应超时，请稍后再试")
    except ResolverError as exc:
        return _error(exc.status_code, exc.code, str(exc))

    formats: list[FormatOption] = []
    for item in media.formats:
        ticket = tickets.issue(
            DownloadClaim(
                url=normalized_url,
                selector=item.selector,
                kind=item.kind,
                title=media.title,
                adult_confirmed=body.adult_confirmed,
                source_url=item.source_url,
                source_urls=item.source_urls,
                format_id=item.id,
                format_label=item.label,
            )
        )
        formats.append(
            FormatOption(
                id=item.id,
                label=item.label,
                detail=item.detail,
                size=item.size,
                kind=item.kind,
                download_ticket=ticket,
            )
        )

    return ResolveResponse(
        platform=platform.name,
        title=media.title,
        author=media.author,
        duration=media.duration,
        duration_seconds=media.duration_seconds,
        thumbnail=media.thumbnail,
        formats=formats,
    )


@app.post("/v1/jobs/{ticket}", response_model=DownloadJobResponse, status_code=202)
async def start_download_job(request: Request, ticket: str) -> DownloadJobResponse | JSONResponse:
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,64}", ticket):
        return _error(404, "ticket_not_found", "下载凭证无效或已过期，请重新解析。")
    if not limiter.allow(
        _client_id(request),
        "download",
        limit=settings.download_rate_limit_per_hour,
        window_seconds=3600,
    ):
        return _error(429, "rate_limited", "当前下载次数已达限额，请稍后再试。")
    try:
        claim = tickets.get(ticket)
    except TicketNotFound:
        return _error(404, "ticket_not_found", "下载凭证无效或已过期，请重新解析。")

    snapshot = download_jobs.issue()
    task = asyncio.create_task(_run_download_job(snapshot.job_id, claim))
    background_downloads.add(task)
    task.add_done_callback(background_downloads.discard)
    return _job_response(snapshot)


@app.post("/v1/helper/jobs", response_model=DownloadJobResponse, status_code=202)
async def start_helper_download_job(
    request: Request,
    body: HelperDownloadRequest,
) -> DownloadJobResponse | JSONResponse:
    if not limiter.allow(
        _client_id(request),
        "download",
        limit=settings.download_rate_limit_per_hour,
        window_seconds=3600,
    ):
        return _error(429, "rate_limited", "当前下载次数已达限额，请稍后再试。")
    try:
        validate_helper_media_request(body.url, body.platform, body.headers)
    except ResolverError as exc:
        return _error(400, "invalid_helper_media", str(exc))

    snapshot = download_jobs.issue()
    task = asyncio.create_task(_run_helper_download_job(snapshot.job_id, body))
    background_downloads.add(task)
    task.add_done_callback(background_downloads.discard)
    return _job_response(snapshot)


@app.get("/v1/jobs/{job_id}", response_model=DownloadJobResponse)
async def download_job(job_id: str) -> DownloadJobResponse | JSONResponse:
    if not re.fullmatch(r"[A-Za-z0-9_-]{24,64}", job_id):
        return _error(404, "job_not_found", "下载任务不存在或已过期。")
    try:
        return _job_response(download_jobs.get(job_id))
    except TicketNotFound:
        return _error(404, "job_not_found", "下载任务不存在或已过期。")


@app.post("/v1/prepare/{ticket}", response_model=PrepareResponse)
async def prepare_download(request: Request, ticket: str) -> PrepareResponse | JSONResponse:
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,64}", ticket):
        return _error(404, "ticket_not_found", "下载凭证无效或已过期，请重新解析。")
    if not limiter.allow(
        _client_id(request),
        "download",
        limit=settings.download_rate_limit_per_hour,
        window_seconds=3600,
    ):
        return _error(429, "rate_limited", "当前下载次数已达限额，请稍后再试。")
    try:
        claim = tickets.get(ticket)
    except TicketNotFound:
        return _error(404, "ticket_not_found", "下载凭证无效或已过期，请重新解析。")

    try:
        validate_platform_url(claim.url, adult_confirmed=claim.adult_confirmed)
        async with download_slots:
            downloaded = await asyncio.to_thread(download_media, claim)
    except (UrlValidationError, ResolverError) as exc:
        status = exc.status_code if isinstance(exc, ResolverError) else 400
        code = exc.code if isinstance(exc, ResolverError) else "invalid_url"
        return _error(status, code, str(exc))

    file_ticket = prepared_files.issue(
        PreparedFileClaim(downloaded.directory, downloaded.path, downloaded.media_type)
    )
    return PrepareResponse(file_ticket=file_ticket, filename=downloaded.path.name)


@app.post("/v1/file/{ticket}", response_model=None)
@app.get("/v1/file/{ticket}", response_model=None)
async def prepared_file(request: Request, ticket: str) -> FileResponse | JSONResponse | HTMLResponse:
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,64}", ticket):
        return _download_error(request, 404, "file_not_found", "准备好的文件无效或已过期，请重新下载。")
    try:
        prepared = prepared_files.take(ticket)
    except TicketNotFound:
        return _download_error(request, 404, "file_not_found", "准备好的文件无效或已过期，请重新下载。")

    if request.method == "POST":
        try:
            destination = await asyncio.to_thread(_save_to_output, prepared.path, prepared.path.name)
        except OSError:
            shutil.rmtree(prepared.directory, ignore_errors=True)
            return _download_error(request, 500, "save_failed", "文件无法保存到“下载内容”文件夹，请检查文件夹权限。")
        shutil.rmtree(prepared.directory, ignore_errors=True)
        return JSONResponse(
            content={
                "ok": True,
                "filename": destination.name,
                "folder": str(destination.parent),
            }
        )

    return FileResponse(
        path=prepared.path,
        media_type=prepared.media_type,
        filename=prepared.path.name,
        background=BackgroundTask(shutil.rmtree, prepared.directory, ignore_errors=True),
    )


@app.get("/v1/download/{ticket}", response_model=None)
async def download(request: Request, ticket: str) -> FileResponse | JSONResponse | HTMLResponse:
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,64}", ticket):
        return _download_error(request, 404, "ticket_not_found", "下载凭证无效或已过期，请返回后重新解析。")
    if not limiter.allow(
        _client_id(request),
        "download",
        limit=settings.download_rate_limit_per_hour,
        window_seconds=3600,
    ):
        return _download_error(request, 429, "rate_limited", "当前下载次数已达限额，请稍后再试。")
    try:
        claim = tickets.get(ticket)
    except TicketNotFound:
        return _download_error(request, 404, "ticket_not_found", "下载凭证无效或已过期，请返回后重新解析。")

    try:
        validate_platform_url(claim.url, adult_confirmed=claim.adult_confirmed)
        async with download_slots:
            downloaded = await asyncio.to_thread(download_media, claim)
    except (UrlValidationError, ResolverError) as exc:
        status = exc.status_code if isinstance(exc, ResolverError) else 400
        code = exc.code if isinstance(exc, ResolverError) else "invalid_url"
        return _download_error(request, status, code, str(exc))

    return FileResponse(
        path=downloaded.path,
        media_type=downloaded.media_type,
        filename=downloaded.path.name,
        background=BackgroundTask(shutil.rmtree, downloaded.directory, ignore_errors=True),
    )
