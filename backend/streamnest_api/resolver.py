from __future__ import annotations

import base64
import html
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

import certifi
import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.utils import DownloadError, js_to_json

from .config import settings
from .platforms import HAIJIAO_HOSTS, validate_platform_url
from .tickets import DownloadClaim
from .bilibili_session import bilibili_session


class ResolverError(RuntimeError):
    code = "resolve_failed"
    status_code = 422


class ProtectedMediaError(ResolverError):
    code = "protected_media"


class AuthenticationRequiredError(ResolverError):
    code = "authentication_required"
    status_code = 403


class VisitorSessionRequiredError(ResolverError):
    code = "visitor_session_required"
    status_code = 403


class MediaTooLargeError(ResolverError):
    code = "media_too_large"
    status_code = 413


class MediaTooLongError(ResolverError):
    code = "media_too_long"
    status_code = 413


class DownloadLimitReached(Exception):
    pass


_FRAME_SELECTOR_PREFIX = "streamnest-frame:"


HELPER_MEDIA_HOSTS = {
    "抖音": ("douyin.com", "douyinvod.com", "douyinpic.com", "byteimg.com", "snssdk.com"),
    "快手": ("kuaishou.com", "gifshow.com", "kwaicdn.com", "kwimgs.com", "yximgs.com"),
}
HELPER_PAGE_HOSTS = {
    "抖音": ("douyin.com", "iesdouyin.com"),
    "快手": ("kuaishou.com", "gifshow.com"),
}
HELPER_HEADER_NAMES = {
    "accept",
    "accept-language",
    "origin",
    "referer",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
    "user-agent",
}

IMAGE_MEDIA_HOSTS = {
    "YouTube": ("ytimg.com", "ggpht.com", "googleusercontent.com"),
    "Bilibili": ("hdslb.com", "bilibili.com"),
    "X": ("twimg.com", "x.com", "twitter.com"),
    "TED": ("tedcdn.com",),
    "微博": ("sinaimg.cn", "sinaimg.com", "weibo.com", "weibo.cn"),
    "Facebook": ("fbcdn.net", "fbsbx.com", "facebook.com"),
    "Instagram": ("cdninstagram.com", "fbcdn.net"),
    "Reddit": ("redd.it", "redditmedia.com", "redditstatic.com"),
    "AcFun": ("acfun.cn", "acoimg.com"),
    "TikTok": ("tiktokcdn.com", "tiktokcdn-eu.com", "byteimg.com"),
    "Dailymotion": ("dmcdn.net",),
    "Vimeo": ("vimeocdn.com",),
    "Pinterest": ("pinimg.com",),
    "Twitch": ("jtvnw.net",),
    "SoundCloud": ("sndcdn.com",),
    "XVideos": ("xvideos.com", "xvideos2.com", "xvideos-cdn.com"),
    "Pornhub": ("pornhub.com", "pornhub.org", "phncdn.com"),
    "Eporner": ("eporner.com",),
    "XNXX": ("xnxx.com", "xnxx3.com", "xnxx-cdn.com"),
    "Rule34Video": ("rule34video.com",),
    "HQPorner": ("hqporner.com",),
    "Beeg": ("beeg.com", "externulls.com"),
    "SxyPrn": ("sxyprn.com", "trafficdeposit.com"),
    "SpankBang": ("spankbang.com",),
    "XMoviesForYou": ("xmoviesforyou.com", "xmoviescdn.online"),
    "抖音": ("douyin.com", "iesdouyin.com", "douyinpic.com", "byteimg.com", "snssdk.com"),
    "小红书": ("xhscdn.com", "rednotecdn.com"),
    "红果短剧": ("byteimg.com",),
    "快手": ("kuaishou.com", "gifshow.com", "kwimgs.com", "yximgs.com", "kwaicdn.com", "kwai.net"),
}

HAIJIAO_HLS_HOSTS = ("hls.qldjxf.cn",)
HAIJIAO_MEDIA_HOSTS = HAIJIAO_HLS_HOSTS + (
    "mts.hhjd.mobi",
    "ts.zhixunkeji.xyz",
    "tx.doudou520.online",
)


def _configure_ascii_ca_bundle() -> Path | None:
    """Give curl-cffi an ASCII-only CA path on Windows projects with CJK paths."""
    source = Path(certifi.where())
    try:
        str(source).encode("ascii")
        return source
    except UnicodeEncodeError:
        pass

    target = Path(tempfile.gettempdir()) / "streamnest-cacert.pem"
    try:
        if not target.is_file() or target.stat().st_size != source.stat().st_size:
            shutil.copyfile(source, target)
        os.environ.setdefault("CURL_CA_BUNDLE", str(target))
        os.environ.setdefault("SSL_CERT_FILE", str(target))
        return target
    except OSError:
        return None


_configure_ascii_ca_bundle()

def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith(f".{suffix}")


def _validate_helper_media_url(url: str, platform: str) -> str:
    if platform not in HELPER_MEDIA_HOSTS:
        raise ResolverError("浏览器助手只处理抖音或快手的公开视频。")
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise ResolverError("捕获到的媒体地址无效。") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or not any(_host_matches(host, suffix) for suffix in HELPER_MEDIA_HOSTS[platform])
    ):
        raise ResolverError("捕获到的媒体地址未通过平台安全检查。")
    return url


def sanitize_helper_headers(platform: str, headers: dict[str, str]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for raw_name, raw_value in list(headers.items())[:24]:
        name = str(raw_name).strip().lower()
        value = str(raw_value).strip()
        if name not in HELPER_HEADER_NAMES or not value or len(value) > 1024 or "\r" in value or "\n" in value:
            continue
        if name in {"origin", "referer"}:
            parsed = urlparse(value)
            host = (parsed.hostname or "").lower().rstrip(".")
            if parsed.scheme != "https" or not any(
                _host_matches(host, suffix) for suffix in HELPER_PAGE_HOSTS.get(platform, ())
            ):
                continue
        safe[name] = value
    return safe


def validate_helper_media_request(url: str, platform: str, headers: dict[str, str]) -> dict[str, str]:
    _validate_helper_media_url(url, platform)
    return sanitize_helper_headers(platform, headers)


class _PlatformRedirectHandler(HTTPRedirectHandler):
    def __init__(self, platform: str) -> None:
        super().__init__()
        self.platform = platform

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url = _validate_helper_media_url(urljoin(req.full_url, newurl), self.platform)
        return super().redirect_request(req, fp, code, msg, headers, safe_url)


def _validate_image_url(url: str, platform: str) -> str:
    allowed_hosts = IMAGE_MEDIA_HOSTS.get(platform, ())
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise ResolverError("封面图片地址无效。") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or not any(_host_matches(host, suffix) for suffix in allowed_hosts)
    ):
        raise ResolverError("封面图片地址未通过平台安全检查。")
    return url


class _ImageRedirectHandler(HTTPRedirectHandler):
    def __init__(self, platform: str) -> None:
        super().__init__()
        self.platform = platform

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url = _validate_image_url(urljoin(req.full_url, newurl), self.platform)
        return super().redirect_request(req, fp, code, msg, headers, safe_url)


@dataclass(frozen=True)
class ResolvedFormat:
    id: str
    label: str
    detail: str
    size: str
    kind: str
    selector: str
    source_url: str | None = None
    source_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedMedia:
    title: str
    author: str
    duration: str
    duration_seconds: int | None
    thumbnail: str | None
    formats: tuple[ResolvedFormat, ...]


@dataclass(frozen=True)
class DownloadedFile:
    directory: Path
    path: Path
    media_type: str


@dataclass(frozen=True)
class DownloadProgress:
    status: str
    downloaded_bytes: int
    total_bytes: int | None
    speed: float | None
    eta: int | None
    percent: float | None


def build_thumbnail_format(page_url: str, thumbnail: str | None) -> ResolvedFormat | None:
    if not thumbnail:
        return None
    try:
        platform = validate_platform_url(page_url, adult_confirmed=True).name
        parsed = urlparse(thumbnail)
        if parsed.scheme == "http" and parsed.hostname and parsed.username is None and parsed.password is None:
            thumbnail = parsed._replace(scheme="https", netloc=parsed.hostname).geturl()
        _validate_image_url(thumbnail, platform)
    except (ResolverError, ValueError):
        return None
    return ResolvedFormat(
        id="image-cover",
        label="封面原图",
        detail="图片 · 平台公开封面 · 原始尺寸",
        size="未知大小",
        kind="图片",
        selector="image-cover",
        source_url=thumbnail,
    )


def build_first_frame_format(formats: tuple[ResolvedFormat, ...]) -> ResolvedFormat | None:
    source = next((item for item in formats if item.kind != "音频"), None)
    if not source:
        return None
    return ResolvedFormat(
        id="image-frame",
        label="视频首帧",
        detail="图片 · 从公开视频生成 · JPG",
        size="未知大小",
        kind="图片",
        selector=f"{_FRAME_SELECTOR_PREFIX}{source.selector}",
        source_url=source.source_url,
    )


class QuietLogger:
    def debug(self, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        return None

    def error(self, message: str) -> None:
        return None


def _common_options() -> dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extract_flat": False,
        "socket_timeout": 20,
        "retries": 4,
        "fragment_retries": 5,
        "geo_bypass": False,
        "nocheckcertificate": False,
        "logger": QuietLogger(),
    }


def _transient_extract_error(exc: DownloadError) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in (
        "http error 500", "http error 502", "http error 503", "http error 504",
        "timed out", "connection reset", "temporarily unavailable",
    ))


def _extract_info(url: str, options: dict[str, Any], *, download: bool) -> dict[str, Any] | None:
    host = (urlparse(url).hostname or "").lower()
    is_bilibili = host == "bilibili.com" or host.endswith(".bilibili.com")
    is_eporner = host == "eporner.com" or host.endswith(".eporner.com")
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        attempts = 3
    elif is_eporner:
        # Eporner's public metadata endpoint intermittently returns 502 even
        # though the page and media CDN remain available. Retrying the entire
        # extraction refreshes its short-lived media metadata safely.
        attempts = 4
    elif host == "dailymotion.com" or host.endswith(".dailymotion.com") or host == "dai.ly":
        attempts = 2
    else:
        attempts = 2
    last_error: DownloadError | None = None
    for attempt in range(attempts):
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                if is_bilibili:
                    for cookie in bilibili_session.get():
                        downloader.cookiejar.set_cookie(cookie)
                return downloader.extract_info(url, download=download)
        except DownloadError as exc:
            last_error = exc
            if attempt + 1 < attempts and _transient_extract_error(exc):
                time.sleep((2.0 if is_eporner else 0.75) * (attempt + 1))
            else:
                break
    if last_error is not None:
        raise last_error
    return None


def _site_options(url: str) -> dict[str, Any]:
    """Return extractor options that only expose formats this service can fetch."""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if any(_host_matches(host, suffix) for suffix in HAIJIAO_HOSTS):
        return {"http_headers": {"Referer": url}}
    if host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
        # YouTube's default android_vr URLs can currently resolve successfully but
        # return HTTP 403 when the media bytes are requested. web_embedded provides
        # directly fetchable formats without account cookies for embeddable videos.
        return {
            "source_address": "0.0.0.0",
            "extractor_args": {"youtube": {"player_client": ["web_embedded"]}},
        }
    if host == "rule34video.com" or host.endswith(".rule34video.com"):
        return {"impersonate": ImpersonateTarget(client="chrome", os="windows")}
    if host == "beeg.com" or host.endswith(".beeg.com"):
        return {
            "http_headers": {
                "Referer": url,
                "Origin": "https://beeg.com",
            }
        }
    if host == "xmoviesforyou.com" or host.endswith(".xmoviesforyou.com"):
        return {
            # This site exposes one long HLS rendition. A few parallel fragments
            # materially reduce stalls while keeping the request count bounded.
            "concurrent_fragment_downloads": 8,
            "http_headers": {
                "Referer": url,
                "Origin": "https://xmoviesforyou.com",
            }
        }
    return {}


def _resolver_error_from_download(url: str, exc: DownloadError) -> ResolverError:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    message = str(exc).lower()
    if any(_host_matches(host, suffix) for suffix in HAIJIAO_HOSTS) and any(
        marker in message for marker in ("http error 403", "forbidden", "http error 429")
    ):
        return ResolverError(
            "海角网当前线路拒绝本机解析请求（403/429）。请确认详情页在浏览器里能正常播放，"
            "稍后重新解析；StreamNest 不会绕过站点验证。"
        )
    if (host == "bilibili.com" or host.endswith(".bilibili.com")) and any(
        marker in message
        for marker in ("premium member", "premium_only", "vip only", "vip-only", "会员", "need to login", "login required")
    ):
        return AuthenticationRequiredError(
            "这个 B 站视频需要会员身份或其他播放权限。请在本机网页点击“同步 Edge B 站会话”后重新解析；"
            "若仍失败，可能是账号没有该集权限，或平台只允许在官方播放器中观看。"
        )
    if (host == "douyin.com" or host.endswith(".douyin.com")) and any(
        marker in message
        for marker in ("fresh cookies", "s_v_web_id", "web detail json")
    ):
        return VisitorSessionRequiredError(
            "抖音要求由官方网页生成的临时访客会话，当前无登录、无浏览器 Cookie 模式无法解析；这不是短链接失效。"
        )
    if (host == "instagram.com" or host.endswith(".instagram.com")) and any(
        marker in message
        for marker in ("empty media response", "login required", "not granting access", "use --cookies")
    ):
        return AuthenticationRequiredError(
            "这个 Instagram 视频没有向未登录访问返回媒体文件，可能仅登录可见、受地区限制或已被限制。"
        )
    if (host == "vimeo.com" or host.endswith(".vimeo.com")) and any(
        marker in message for marker in ("only works when logged-in", "use --cookies", "login required")
    ):
        return AuthenticationRequiredError(
            "Vimeo 当前没有向未登录访问返回这个视频的媒体文件；StreamNest 不会读取账号 Cookie 或绕过登录限制。"
        )
    if (host == "ixigua.com" or host.endswith(".ixigua.com")) and "cookies" in message:
        return VisitorSessionRequiredError(
            "西瓜视频要求由官方网页生成临时访客会话；当前无登录、无浏览器 Cookie 模式无法直接解析。"
        )
    if (host == "xiaohongshu.com" or host.endswith(".xiaohongshu.com")) and any(
        marker in message for marker in ("no video formats", "use --cookies", "login required")
    ):
        return AuthenticationRequiredError(
            "这个小红书页面没有向未登录访问返回可下载的视频格式；请使用完整的公开分享链接，登录可见内容无法处理。"
        )
    if (host == "spankbang.com" or host.endswith(".spankbang.com")) and any(
        marker in message for marker in ("cloudflare", "impersonate", "http error 403", "forbidden")
    ):
        return VisitorSessionRequiredError(
            "SpankBang 已收到这个单视频链接，但站点当前返回 Cloudflare 403 人机验证，"
            "因此无法在无人值守模式直接下载。StreamNest 不会绕过验证或读取浏览器 Cookie；"
            "这不是链接格式错误，请稍后重试。"
        )
    if (host == "beeg.com" or host.endswith(".beeg.com")) and any(
        marker in message for marker in ("http error 400", "http error 404", "json metadata")
    ):
        return ResolverError("Beeg 当前公开接口没有返回这个视频，请确认是单个视频链接并稍后重试。")
    return ResolverError("平台未返回可用视频，链接可能已失效或规则已变更")


def _trusted_source_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    trusted_host = any(
        _host_matches(host, suffix)
        for suffix in (
            "tedcdn.com",
            "vimeocdn.com",
            "bigcdn.cc",
            "sxyprn.com",
            "eternalslumber.online",
            "video.beeg.com",
            *HAIJIAO_HLS_HOSTS,
        )
    )
    return (
        parsed.scheme == "https"
        and trusted_host
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
    )


def _ted_media_from_html(page_html: str) -> ResolvedMedia:
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(?P<data>.*?)</script>',
        page_html,
        flags=re.DOTALL,
    )
    if not match:
        raise ResolverError("TED 页面没有返回可用的视频信息")
    try:
        data = json.loads(html.unescape(match.group("data")))
        video_data = data["props"]["pageProps"]["videoData"]
        player = video_data["videoPlayerData"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ResolverError("TED 视频信息格式已变更") from exc

    raw_formats = player.get("resources", {}).get("h264") or []
    formats: list[ResolvedFormat] = []
    def ted_quality(item: dict[str, Any]) -> tuple[int, int]:
        resolution = str(item.get("height") or item.get("resolution") or "")
        match = re.search(r"(?:^|x)(\d{3,4})$", resolution, flags=re.IGNORECASE)
        return int(match.group(1)) if match else 0, int(item.get("bitrate") or 0)

    for index, item in enumerate(sorted(raw_formats, key=ted_quality, reverse=True)):
        source_url = str(item.get("file") or "")
        if not _trusted_source_url(source_url):
            continue
        bitrate = int(item.get("bitrate") or 0)
        resolution = str(item.get("height") or item.get("resolution") or "")
        resolution_match = re.search(r"(?:^|x)(\d{3,4})$", resolution, flags=re.IGNORECASE)
        height = int(resolution_match.group(1)) if resolution_match else None
        formats.append(
            ResolvedFormat(
                id=f"ted-{index + 1}",
                label=f"{height}P" if height else f"{bitrate} kbps" if bitrate else "公开画质",
                detail=f"MP4 · H264 · 含音频{' · 最高公开码率' if not formats else ''}",
                size="未知大小",
                kind="快速" if not formats else "高清",
                selector="best",
                source_url=source_url,
            )
        )
    if not formats:
        hls_url = str(player.get("resources", {}).get("hls", {}).get("stream") or "")
        if _trusted_source_url(hls_url):
            formats.append(
                ResolvedFormat(
                    id="ted-hls",
                    label="自动画质",
                    detail="HLS · 含音频 · 推荐",
                    size="未知大小",
                    kind="快速",
                    selector="best",
                    source_url=hls_url,
                )
            )
    if not formats:
        raise ResolverError("TED 页面没有返回可下载的公开格式")

    duration = player.get("duration") or video_data.get("duration")
    duration_seconds = int(duration) if isinstance(duration, (int, float)) else None
    thumbnail = str(player.get("thumb")) if player.get("thumb") else None
    image_format = build_thumbnail_format("https://www.ted.com/", thumbnail)
    return ResolvedMedia(
        title=str(player.get("title") or player.get("name") or video_data.get("title") or "TED Talk")[:240],
        author=str(player.get("speaker") or video_data.get("presenterDisplayName") or "TED")[:160],
        duration=_duration_text(duration_seconds),
        duration_seconds=duration_seconds,
        thumbnail=thumbnail,
        formats=tuple(formats) + ((image_format,) if image_format else ()),
    )


def _resolve_ted(url: str) -> ResolvedMedia:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 StreamNest/0.2"})
    try:
        with urlopen(request, timeout=20) as response:
            page_html = response.read().decode("utf-8", errors="replace")
    except OSError as exc:
        raise ResolverError("TED 页面暂时无法访问") from exc
    return _ted_media_from_html(page_html)


def _vimeo_media_from_config(page_url: str, data: dict[str, Any]) -> ResolvedMedia:
    try:
        video = data["video"]
        files = data["request"]["files"]
    except (KeyError, TypeError) as exc:
        raise ResolverError("Vimeo 公开播放器没有返回可用的视频信息") from exc
    progressive = files.get("progressive") if isinstance(files, dict) else None

    public_formats = [
        item
        for item in progressive
        if isinstance(item, dict)
        and item.get("url")
        and _trusted_source_url(str(item.get("url")))
        and str(item.get("mime") or "video/mp4").lower() == "video/mp4"
    ] if isinstance(progressive, list) else []
    if not public_formats:
        raise ProtectedMediaError(
            "这个 Vimeo 视频没有公开 MP4 文件；播放器只返回了受保护或不可直接保存的分段媒体。"
        )

    duration = video.get("duration") if isinstance(video, dict) else None
    duration_seconds = int(duration) if isinstance(duration, (int, float)) else None
    if duration_seconds and duration_seconds > settings.max_duration_seconds:
        raise MediaTooLongError("视频时长超过当前服务限额")

    thumbnail = str(video.get("thumbnail_url")) if isinstance(video, dict) and video.get("thumbnail_url") else None
    owner = video.get("owner") if isinstance(video, dict) else None
    author = str(owner.get("name") or "Vimeo") if isinstance(owner, dict) else "Vimeo"
    by_height: dict[int, dict[str, Any]] = {}
    for item in public_formats:
        height = max(1, int(item.get("height") or 0))
        previous = by_height.get(height)
        if previous is None or float(item.get("bitrate") or 0) > float(previous.get("bitrate") or 0):
            by_height[height] = item
    ordered_heights = sorted(by_height, reverse=True)[:5]
    formats = tuple(
        ResolvedFormat(
            id=f"vimeo-progressive-{height}",
            label=f"{height}P" if height > 1 else "公开画质",
            detail=f"MP4 · 含音频{' · 推荐' if index == 0 else ''}",
            size="未知大小",
            kind="快速" if index == 0 else "高清",
            selector="best",
            source_url=str(by_height[height]["url"]),
        )
        for index, height in enumerate(ordered_heights)
    )
    image_format = build_thumbnail_format(page_url, thumbnail)
    return ResolvedMedia(
        title=str(video.get("title") or "Vimeo 视频")[:240] if isinstance(video, dict) else "Vimeo 视频",
        author=author[:160],
        duration=_duration_text(duration_seconds),
        duration_seconds=duration_seconds,
        thumbnail=thumbnail,
        formats=formats + ((image_format,) if image_format else ()),
    )


def _resolve_vimeo(url: str) -> ResolvedMedia:
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    id_index = next((index for index in range(len(path_parts) - 1, -1, -1) if path_parts[index].isdigit()), -1)
    if id_index < 0:
        raise ResolverError("请使用单个 Vimeo 视频的完整链接")
    video_id = path_parts[id_index]
    query_hash = parse_qs(parsed.query).get("h", [""])[0]
    path_hash = path_parts[id_index + 1] if id_index + 1 < len(path_parts) else ""
    unlisted_hash = query_hash or (path_hash if re.fullmatch(r"[A-Za-z0-9]{6,64}", path_hash) else "")
    config_url = f"https://player.vimeo.com/video/{video_id}/config"
    if unlisted_hash:
        config_url = f"{config_url}?h={quote(unlisted_hash)}"
    request = Request(
        config_url,
        headers={"User-Agent": "Mozilla/5.0 StreamNest/0.2", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
    except OSError as exc:
        raise AuthenticationRequiredError(
            "这个 Vimeo 视频没有向公开播放器返回媒体信息，可能需要登录或嵌入权限。"
        ) from exc
    if len(raw) > 2 * 1024 * 1024:
        raise ResolverError("Vimeo 播放器返回的数据超过安全限额")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResolverError("Vimeo 公开播放器返回了无效数据") from exc
    if not isinstance(data, dict):
        raise ResolverError("Vimeo 公开播放器返回了无效数据")
    return _vimeo_media_from_config(url, data)


_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132 Safari/537.36"
)


def _validate_allowed_https_url(url: str, allowed_hosts: tuple[str, ...]) -> str:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise ResolverError("平台返回了无效地址") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or not any(_host_matches(host, suffix) for suffix in allowed_hosts)
    ):
        raise ResolverError("平台返回的地址未通过安全检查")
    return url


class _AllowedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: tuple[str, ...]) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url = _validate_allowed_https_url(urljoin(req.full_url, newurl), self.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, safe_url)


def _read_allowed_url(
    url: str,
    allowed_hosts: tuple[str, ...],
    *,
    referer: str | None = None,
    accept: str = "text/html,application/xhtml+xml",
    limit: int = 4 * 1024 * 1024,
    retry_forbidden: bool = False,
) -> bytes:
    _validate_allowed_https_url(url, allowed_hosts)
    headers = {"User-Agent": _BROWSER_USER_AGENT, "Accept": accept}
    if referer:
        headers["Referer"] = referer
    opener = build_opener(_AllowedRedirectHandler(allowed_hosts))
    raw = b""
    last_error: HTTPError | URLError | OSError | None = None
    for attempt in range(2):
        request = Request(url, headers=headers)
        try:
            with opener.open(request, timeout=8) as response:
                _validate_allowed_https_url(response.geturl(), allowed_hosts)
                length_value = response.headers.get("Content-Length")
                expected = int(length_value) if length_value and length_value.isdigit() else None
                if expected is not None and expected > limit:
                    raise ResolverError("平台返回的数据超过安全限额")
                chunks: list[bytes] = []
                total = 0
                while expected is None or total < expected:
                    read_size = min(16 * 1024, (expected - total) if expected is not None else 16 * 1024)
                    chunk = response.read(read_size)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > limit:
                        raise ResolverError("平台返回的数据超过安全限额")
                raw = b"".join(chunks)
                if expected is not None and len(raw) != expected:
                    raise URLError("incomplete public page")
            break
        except HTTPError as exc:
            last_error = exc
            retryable = {408, 429, 500, 502, 503, 504}
            if retry_forbidden:
                retryable.add(403)
            if exc.code not in retryable or attempt == 1:
                raise ResolverError("平台公开页面暂时无法访问") from exc
        except (URLError, OSError) as exc:
            last_error = exc
            if attempt == 1:
                raise ResolverError("平台公开页面暂时无法访问") from exc
        time.sleep(0.75 * (attempt + 1))
    else:
        raise ResolverError("平台公开页面暂时无法访问") from last_error
    if len(raw) > limit:
        raise ResolverError("平台返回的数据超过安全限额")
    return raw


def normalize_media_page_url(url: str) -> str:
    """Turn supported same-site result links into a single public video page."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if any(_host_matches(host, suffix) for suffix in HAIJIAO_HOSTS) and not re.fullmatch(
        r"/archives/\d{1,12}/?", parsed.path
    ):
        raise ResolverError(
            "这是海角网线路发布页、首页或列表页，不是单个视频页面。"
            "请复制具体视频形如 /archives/194451/ 的完整详情链接；"
            "StreamNest 不会自动跳转到未核验的新域名。"
        )
    if _host_matches(host, "hongguoduanju.com") and not re.fullmatch(r"/player/\d{8,24}/?", parsed.path):
        raise ResolverError("请打开红果短剧的具体单集，复制形如 /player/7684649550316833817 的链接；首页和剧集列表不能确定要下载哪一集。")
    if not (
        any(_host_matches(host, suffix) for suffix in ("xnxx.com", "xnxx3.com"))
        and parsed.path.startswith("/search/")
    ):
        return url

    video_ids = parse_qs(parsed.query, keep_blank_values=True).get("id", [])
    video_id = next((value for value in video_ids if re.fullmatch(r"\d{1,12}", value)), None)
    if not video_id:
        raise ResolverError("请打开 XNXX 的单个视频页面后复制链接；普通搜索结果页无法确定要下载哪一个视频。")

    page_html = _read_allowed_url(url, ("xnxx.com", "xnxx3.com")).decode("utf-8", errors="replace")
    marker = re.search(
        rf"<div\b[^>]*\bdata-id\s*=\s*([\"']?){re.escape(video_id)}\1(?=[\s>])[^>]*>",
        page_html,
        flags=re.IGNORECASE,
    )
    if not marker:
        raise ResolverError(
            f"这个 XNXX 搜索链接没有找到 id={video_id} 对应的公开视频；请打开视频详情页后重新复制链接。"
        )
    card_html = page_html[marker.start() : marker.start() + 4096]
    target = re.search(
        r"<a\b[^>]*\bhref\s*=\s*([\"'])(/video-[A-Za-z0-9]+/[^\"']+)\1",
        card_html,
        flags=re.IGNORECASE,
    )
    if not target:
        raise ResolverError("XNXX 搜索结果中没有找到对应的视频详情地址，请打开视频后重新复制链接。")

    canonical_url = urljoin(url, html.unescape(target.group(2)))
    _validate_allowed_https_url(canonical_url, ("xnxx.com", "xnxx3.com"))
    canonical_path = urlparse(canonical_url).path
    if not re.fullmatch(r"/video-[A-Za-z0-9]+/[^/]+/?", canonical_path):
        raise ResolverError("XNXX 返回的视频详情地址未通过安全检查。")
    return canonical_url


def _meta_content(page_html: str, *keys: str) -> str | None:
    wanted = {key.lower() for key in keys}
    for tag in re.findall(r"<meta\b[^>]*>", page_html, flags=re.IGNORECASE):
        attrs = {
            name.lower(): html.unescape(value)
            for name, _quote, value in re.findall(
                r"([:\w-]+)\s*=\s*([\"'])(.*?)\2",
                tag,
                flags=re.DOTALL,
            )
        }
        identity = str(attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
        content = str(attrs.get("content") or "").strip()
        if identity in wanted and content:
            return content
    return None


def _html_title(page_html: str, fallback: str) -> str:
    title = _meta_content(page_html, "og:title", "twitter:title", "name")
    if not title:
        match = re.search(r"<title[^>]*>(.*?)</title>", page_html, flags=re.IGNORECASE | re.DOTALL)
        title = html.unescape(re.sub(r"\s+", " ", match.group(1))).strip() if match else ""
    return (title or fallback)[:240]


def _iso_duration_seconds(value: str | None) -> int | None:
    if not value:
        return None
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value.strip(), flags=re.IGNORECASE)
    if not match:
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _resolve_hqporner(url: str) -> ResolvedMedia:
    path = urlparse(url).path
    if not re.fullmatch(r"/hdporn/[^/?#]+\.html", path, flags=re.IGNORECASE):
        raise ResolverError(
            "这是 HQPorner 首页或列表页，无法确定要下载哪一个视频。"
            "请先打开单个视频，再复制形如 /hdporn/123-video-name.html 的完整地址。"
        )
    page_html = _read_allowed_url(url, ("hqporner.com",)).decode("utf-8", errors="replace")
    embeds = [
        urljoin(url, src)
        for src in re.findall(r"<iframe[^>]+src=[\"']([^\"']+)", page_html, flags=re.IGNORECASE)
        if "mydaddy.cc/video/" in src
    ]
    embed_url = next(
        (
            item
            for item in embeds
            if re.fullmatch(r"/video/[A-Fa-f0-9]{8,64}/", urlparse(item).path)
            and _host_matches((urlparse(item).hostname or "").lower(), "mydaddy.cc")
        ),
        None,
    )
    if not embed_url:
        raise ResolverError("HQPorner 页面没有返回公开播放器")
    embed_html = _read_allowed_url(embed_url, ("mydaddy.cc",), referer=url).decode("utf-8", errors="replace")
    sources: dict[int, str] = {}
    for raw_url in re.findall(r"(?://|https://)[^\"'<>\s]+\.mp4(?:\?[^\"'<>\s]*)?", embed_html, flags=re.IGNORECASE):
        source_url = f"https:{raw_url}" if raw_url.startswith("//") else raw_url
        parsed = urlparse(source_url)
        height_match = re.search(r"/(\d{3,4})\.mp4$", parsed.path, flags=re.IGNORECASE)
        if not height_match or not _host_matches((parsed.hostname or "").lower(), "bigcdn.cc"):
            continue
        _validate_allowed_https_url(source_url, ("bigcdn.cc",))
        sources[int(height_match.group(1))] = source_url
    if not sources:
        raise ResolverError("HQPorner 公开播放器没有返回 MP4 文件")
    ordered = sorted(sources, reverse=True)
    formats = tuple(
        ResolvedFormat(
            id=f"hqporner-{height}",
            label=f"{height}P",
            detail=f"MP4 · 含音频{' · 推荐' if index == 0 else ''}",
            size="未知大小",
            kind="快速" if index == 0 else "高清",
            selector="best",
            source_url=sources[height],
        )
        for index, height in enumerate(ordered)
    )
    thumbnail = _meta_content(page_html, "og:image", "twitter:image", "thumbnailUrl")
    image_format = build_thumbnail_format(url, thumbnail)
    if not image_format:
        image_format = build_first_frame_format(formats)
    return ResolvedMedia(
        title=_html_title(page_html, "HQPorner 视频").removesuffix(" - HQporner.com"),
        author="HQPorner",
        duration="--:--",
        duration_seconds=None,
        thumbnail=thumbnail,
        formats=formats + ((image_format,) if image_format else ()),
    )


def _haijiao_playlist_duration(source_url: str, page_url: str) -> int:
    """Accept only public, ordinary HLS media playlists on observed CDN hosts."""
    raw = _read_allowed_url(
        source_url,
        HAIJIAO_HLS_HOSTS,
        referer=page_url,
        accept="application/vnd.apple.mpegurl,application/x-mpegURL,text/plain,*/*",
        limit=512 * 1024,
    )
    playlist = raw.decode("utf-8", errors="replace")
    if not playlist.startswith("#EXTM3U") or "#EXT-X-STREAM-INF" in playlist:
        raise ResolverError("海角网没有返回可直接下载的单路公开视频清单。")
    if any(marker in playlist.upper() for marker in ("SAMPLE-AES", "KEYFORMAT=\"COM.APPLE", "SKD://")):
        raise ProtectedMediaError("这个视频使用受保护的播放格式，StreamNest 不会处理。")

    segment_count = 0
    duration = 0.0
    for line in playlist.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            try:
                duration += float(line.split(":", 1)[1].split(",", 1)[0])
            except ValueError as exc:
                raise ResolverError("海角网视频清单时长格式无效。") from exc
        elif line.startswith("#EXT-X-KEY:"):
            method = re.search(r"(?:^|[:,])METHOD=([^,]+)", line)
            if not method or method.group(1) not in {"NONE", "AES-128"}:
                raise ProtectedMediaError("这个视频使用受保护的播放格式，StreamNest 不会处理。")
            if method.group(1) == "AES-128":
                key_format = re.search(r'(?:^|[:,])KEYFORMAT="?([^",]+)', line)
                if key_format and key_format.group(1) != "identity":
                    raise ProtectedMediaError("这个视频使用受保护的播放格式，StreamNest 不会处理。")
                key_uri = re.search(r'URI="([^"]{1,2048})"', line)
                if not key_uri:
                    raise ResolverError("海角网视频清单缺少公开播放密钥地址。")
                key_url = _validate_allowed_https_url(urljoin(source_url, key_uri.group(1)), HAIJIAO_MEDIA_HOSTS)
                key_bytes = _read_allowed_url(
                    key_url, HAIJIAO_MEDIA_HOSTS, referer=source_url, accept="*/*", limit=64
                )
                if len(key_bytes) != 16:
                    raise ProtectedMediaError("海角网没有返回可公开读取的标准 HLS 播放密钥。")
        elif line.startswith("#EXT-X-MAP:"):
            init_uri = re.search(r'URI="([^"]{1,2048})"', line)
            if not init_uri:
                raise ResolverError("海角网视频初始化片段地址无效。")
            _validate_allowed_https_url(urljoin(source_url, init_uri.group(1)), HAIJIAO_MEDIA_HOSTS)
        elif not line.startswith("#"):
            _validate_allowed_https_url(urljoin(source_url, line), HAIJIAO_MEDIA_HOSTS)
            segment_count += 1
            if segment_count > 10000:
                raise ResolverError("海角网视频片段数量超过安全限额。")
    if not segment_count:
        raise ResolverError("海角网视频清单没有可下载的媒体片段。")
    return int(round(duration))


def _resolve_haijiao(url: str) -> ResolvedMedia:
    try:
        page_html = _read_allowed_url(url, HAIJIAO_HOSTS, limit=1024 * 1024, retry_forbidden=True).decode(
            "utf-8", errors="replace"
        )
    except ResolverError as exc:
        if "安全检查" in str(exc):
            raise ResolverError("海角网当前线路跳转到了未核验的域名；请勿自动跟随，换用可信的详情页链接。") from exc
        if isinstance(exc.__cause__, HTTPError) and exc.__cause__.code == 403:
            raise ResolverError("海角网当前线路返回 403，暂时拒绝本机读取详情页；请稍后重试，不会绕过人机验证。") from exc
        raise ResolverError("海角网详情页当前无法由本机服务读取，请稍后重试或更换仍可访问的官方线路。") from exc

    if len(page_html) < 5000 and re.search(r"(?:window\.)?location\.replace\s*\(", page_html):
        redirect = re.search(r"https?://[^\s\"'<>]+", page_html)
        redirect_host = (urlparse(redirect.group(0)).hostname or "").lower() if redirect else ""
        if redirect_host and not any(_host_matches(redirect_host, suffix) for suffix in HAIJIAO_HOSTS):
            raise ResolverError("这条海角网线路正在跳转到未核验的其他域名；已停止解析，请换用可信的详情页地址。")

    formats: list[ResolvedFormat] = []
    durations: dict[str, int] = {}
    seen_sources: set[str] = set()
    for _quote, raw_config in re.findall(r'data-config=(["\'])(.*?)\1', page_html, flags=re.DOTALL):
        try:
            config = json.loads(html.unescape(raw_config))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        video = config.get("video") if isinstance(config, dict) else None
        if not isinstance(video, dict) or video.get("type") != "hls":
            continue
        source_url = video.get("url")
        if not isinstance(source_url, str) or source_url in seen_sources:
            continue
        _validate_allowed_https_url(source_url, HAIJIAO_HLS_HOSTS)
        if not urlparse(source_url).path.endswith(".m3u8"):
            raise ResolverError("海角网播放器没有返回标准 HLS 视频地址。")
        seen_sources.add(source_url)
        if len(formats) >= 12:
            break
        try:
            seconds = _haijiao_playlist_duration(source_url, url)
        except ResolverError as exc:
            if not formats:
                raise
            continue
        index = len(seen_sources)
        format_id = f"haijiao-{index}"
        durations[format_id] = seconds
        formats.append(ResolvedFormat(
            id=format_id,
            label=f"第 {index} 段视频",
            detail=f"HLS · {_duration_text(seconds)} · 公开播放流",
            size="未知大小",
            kind="视频",
            selector=format_id,
            source_url=source_url,
        ))
    if not formats:
        raise ResolverError("海角网详情页没有返回可下载的公开视频；请确认原网页可正常播放。")
    formats.sort(key=lambda item: durations[item.id], reverse=True)
    title = _html_title(page_html, "海角网视频").removesuffix(" | 海角网")[:240]
    return ResolvedMedia(
        title=title,
        author="海角网",
        duration="--:--",
        duration_seconds=None,
        thumbnail=None,
        formats=tuple(formats),
    )


def _beeg_h264_formats(master_url: str, playlist: str) -> tuple[ResolvedFormat, ...]:
    lines = [line.strip() for line in playlist.splitlines()]
    by_height: dict[int, ResolvedFormat] = {}
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:") or "avc1" not in line.lower():
            continue
        resolution = re.search(r"RESOLUTION=\d+x(\d+)", line, flags=re.IGNORECASE)
        if not resolution:
            continue
        source = next((item for item in lines[index + 1 :] if item and not item.startswith("#")), "")
        if not source:
            continue
        height = int(resolution.group(1))
        source_url = urljoin(master_url, source)
        _validate_allowed_https_url(source_url, ("video.beeg.com",))
        by_height[height] = ResolvedFormat(
            id="video-fast" if height == 240 else f"beeg-{height}",
            label=f"{height}P",
            detail="HLS · H.264 · 含音频",
            size="未知大小",
            kind="高清" if height >= 720 else "快速",
            selector="best",
            source_url=source_url,
        )
    ordered = [by_height[height] for height in sorted(by_height, reverse=True)]
    return tuple(
        replace(item, detail=item.detail + (" · 推荐" if index == 0 else ""))
        for index, item in enumerate(ordered)
    )


def _resolve_beeg(url: str) -> ResolvedMedia:
    parsed = urlparse(url)
    match = re.fullmatch(r"/(?:video/)?-?(\d{6,18})/?", parsed.path)
    if not match:
        raise ResolverError("请粘贴 Beeg 单个公开视频的完整链接")
    normalized_id = str(int(match.group(1)))
    api_url = f"https://store.externulls.com/facts/file/{normalized_id}"
    try:
        raw = _read_allowed_url(
            api_url,
            ("store.externulls.com",),
            referer=url,
            accept="application/json",
        )
        payload = json.loads(raw.decode("utf-8"))
    except (ResolverError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ResolverError("Beeg 当前公开接口没有返回这个视频，请确认链接仍然公开并稍后重试。") from exc

    file_data = payload.get("file") if isinstance(payload, dict) else None
    if not isinstance(file_data, dict):
        raise ResolverError("Beeg 当前公开接口没有返回可下载的视频信息")
    hls_resources = file_data.get("hls_resources")
    master_path = hls_resources.get("fl_cdn_multi") if isinstance(hls_resources, dict) else None
    if not isinstance(master_path, str) or not master_path.strip():
        raise ResolverError("Beeg 当前公开视频没有返回 HLS 播放地址")
    master_url = urljoin("https://video.beeg.com/", master_path.strip())
    _validate_allowed_https_url(master_url, ("video.beeg.com",))
    try:
        playlist = _read_allowed_url(
            master_url,
            ("video.beeg.com",),
            referer=url,
            accept="application/vnd.apple.mpegurl,application/x-mpegURL,text/plain",
        ).decode("utf-8", errors="replace")
    except ResolverError as exc:
        raise ResolverError("Beeg 公开播放地址暂时无法访问，请重新解析后再试") from exc
    formats = _beeg_h264_formats(master_url, playlist)
    if not formats:
        raise ResolverError("Beeg 当前公开视频没有返回兼容的 H.264 画质")

    title_data = file_data.get("data")
    if isinstance(title_data, list):
        title_data = next((item for item in title_data if isinstance(item, dict) and item.get("cd_value")), None)
    title = str(title_data.get("cd_value") or "").strip() if isinstance(title_data, dict) else ""
    duration = int(file_data.get("fl_duration") or 0) or None
    facts = payload.get("fc_facts") if isinstance(payload, dict) else None
    first_fact = next((item for item in (facts or []) if isinstance(item, dict)), {})
    offsets = first_fact.get("fc_thumbs") if isinstance(first_fact, dict) else None
    offset = next((int(item) for item in reversed(offsets or []) if str(item).isdigit()), None)
    thumbnail = (
        f"https://thumbs.externulls.com/videos/{normalized_id}/{offset}.webp?size=480x270"
        if offset is not None
        else None
    )
    image_format = build_thumbnail_format(url, thumbnail) or build_first_frame_format(formats)
    return ResolvedMedia(
        title=(title or "Beeg 视频")[:240],
        author="Beeg",
        duration=_duration_text(duration),
        duration_seconds=duration,
        thumbnail=thumbnail,
        formats=formats + ((image_format,) if image_format else ()),
    )


def _sxyprn_media_path(raw_path: str, host: str = "sxyprn.com") -> str:
    if not re.fullmatch(
        r"/cdn/c\d+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/\d{9,12}/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+\.vid",
        raw_path,
    ):
        raise ResolverError("SxyPrn 公开播放地址格式已变更")
    parts = raw_path.split("/")
    digit_sum = lambda value: sum(int(character) for character in value if character.isdigit())
    left_sum = digit_sum(parts[6])
    right_sum = digit_sum(parts[7])
    token = base64.b64encode(f"{left_sum}-{host}-{right_sum}".encode("ascii")).decode("ascii")
    token = token.replace("+", "-").replace("/", "_").replace("=", ".")
    parts[1] = f"cdn8/{token}"
    parts[5] = str(int(parts[5]) - left_sum - right_sum)
    return "/".join(parts)


def _resolve_sxyprn(url: str) -> ResolvedMedia:
    page_html = _read_allowed_url(url, ("sxyprn.com",)).decode("utf-8", errors="replace")
    if re.search(r"<title[^>]*>\s*Post Not Found\b", page_html, flags=re.IGNORECASE):
        raise ResolverError("SxyPrn 这个公开作品已删除或链接已经失效")
    post_match = re.search(r"data-postid=[\"']([A-Fa-f0-9]{8,32})[\"']", page_html)
    data_match = re.search(r"data-vnfo='([^']+)'", page_html) or re.search(r'data-vnfo="([^"]+)"', page_html)
    if not post_match or not data_match:
        raise ResolverError("SxyPrn 页面没有返回公开播放器地址")
    post_id = post_match.group(1)
    try:
        source_map = json.loads(html.unescape(data_match.group(1)))
        raw_path = str(source_map[post_id])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ResolverError("SxyPrn 公开播放器信息格式已变更") from exc
    source_url = urljoin("https://sxyprn.com", _sxyprn_media_path(raw_path))
    _validate_allowed_https_url(source_url, ("sxyprn.com",))
    duration_seconds = _iso_duration_seconds(_meta_content(page_html, "duration"))
    if duration_seconds and duration_seconds > settings.max_duration_seconds:
        raise MediaTooLongError("视频时长超过当前服务限额")
    resolution_match = re.search(r"resolution:<b>[^<]*</b>\s*(\d{3,4})", page_html, flags=re.IGNORECASE)
    height = int(resolution_match.group(1)) if resolution_match else 480
    formats = (
        ResolvedFormat(
            id="sxyprn-public",
            label=f"{height}P",
            detail="MP4 · 平台公开播放流 · 含音频 · 推荐",
            size="未知大小",
            kind="快速",
            selector="best",
            source_url=source_url,
        ),
    )
    thumbnail = _meta_content(page_html, "thumbnailUrl", "og:image", "twitter:image")
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"
    image_format = build_thumbnail_format(url, thumbnail)
    if not image_format:
        image_format = build_first_frame_format(formats)
    return ResolvedMedia(
        title=_html_title(page_html, "SxyPrn 视频"),
        author="SxyPrn",
        duration=_duration_text(duration_seconds),
        duration_seconds=duration_seconds,
        thumbnail=thumbnail,
        formats=formats + ((image_format,) if image_format else ()),
    )


def _resolve_xmoviesforyou(url: str) -> ResolvedMedia:
    page_html = _read_allowed_url(url, ("xmoviesforyou.com",)).decode("utf-8", errors="replace")
    video_match = re.search(r'const\s+videoId\s*=\s*["\'](\d{6,20})["\']', page_html)
    target_match = re.search(r'const\s+target\s*=\s*["\']([A-Za-z0-9_-]{1,32})["\']', page_html)
    fetch_match = re.search(r"fetch\(`/api/stream/\$\{videoId\}\?target=\$\{target\}`\)", page_html)
    if not video_match or not target_match or not fetch_match:
        raise ResolverError("XMoviesForYou 页面没有返回公开播放接口")
    video_id = video_match.group(1)
    api_url = urljoin(url, f"/api/stream/{video_id}?target={target_match.group(1)}")
    raw = _read_allowed_url(
        api_url,
        ("xmoviesforyou.com",),
        referer=url,
        accept="application/json",
        limit=256 * 1024,
    )
    try:
        payload = json.loads(raw.decode("utf-8"))
        source_url = str(payload["url"])
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResolverError("XMoviesForYou 播放接口返回了无效数据") from exc
    parsed_source = urlparse(source_url)
    if not re.fullmatch(rf"/hls/vid_{video_id}/index\.m3u8", parsed_source.path):
        raise ResolverError("XMoviesForYou 播放地址格式已变更")
    _validate_allowed_https_url(source_url, ("eternalslumber.online",))
    formats = (
        ResolvedFormat(
            id="xmovies-hls",
            label="自动画质",
            detail="HLS · 平台公开播放流 · 含音频 · 推荐",
            size="未知大小",
            kind="快速",
            selector="best",
            source_url=source_url,
        ),
    )
    thumbnail = _meta_content(page_html, "og:image", "twitter:image", "thumbnailUrl")
    image_format = build_thumbnail_format(url, thumbnail)
    if not image_format:
        image_format = build_first_frame_format(formats)
    return ResolvedMedia(
        title=_html_title(page_html, "XMoviesForYou 视频"),
        author="XMoviesForYou",
        duration="--:--",
        duration_seconds=None,
        thumbnail=thumbnail,
        formats=formats + ((image_format,) if image_format else ()),
    )


def _human_size(value: int | float | None) -> str:
    if not value:
        return "未知大小"
    amount = float(value)
    units = ("B", "KB", "MB", "GB")
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return "未知大小"


def _duration_text(seconds: int | float | None) -> str:
    if seconds is None:
        return "--:--"
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _codec_name(value: str | None) -> str:
    if not value or value == "none":
        return ""
    return value.split(".", 1)[0].upper()


def _format_rank(item: dict[str, Any]) -> tuple[float, float, int, int, int]:
    """Choose the best picture at one resolution, not merely an already-muxed file."""
    protocol = str(item.get("protocol") or "").lower()
    direct = int(protocol in {"http", "https"})
    combined = int(item.get("acodec") != "none")
    preferred_ext = int(str(item.get("ext") or "").lower() == "mp4")
    return (
        float(item.get("tbr") or item.get("vbr") or 0),
        float(item.get("fps") or 0),
        direct,
        preferred_ext,
        combined,
    )


def _safe_formats(info: dict[str, Any]) -> tuple[ResolvedFormat, ...]:
    is_bilibili = str(info.get("extractor_key") or "").lower().startswith("bilibili")
    size_limit = settings.max_bilibili_file_size_bytes if is_bilibili else settings.max_file_size_bytes
    formats = [
        item
        for item in (info.get("formats") or [])
        if item.get("url")
        and not item.get("has_drm")
        and (item.get("filesize") or item.get("filesize_approx") or 0) <= size_limit
    ]

    by_height: dict[int, list[dict[str, Any]]] = {}
    for item in formats:
        quality_height = item.get("quality") if str(info.get("extractor_key") or "") == "Rule34Video" else 0
        width = int(item.get("width") or 0)
        source_height = int(item.get("height") or quality_height or 0)
        # Quality labels use the short edge for portrait video: 720x1280 is
        # 720P portrait, not a fictitious 1280P rendition.
        height = min(width, source_height) if width > 0 and source_height > width else source_height
        vcodec = item.get("vcodec")
        ext = str(item.get("ext") or "").lower()
        protocol = str(item.get("protocol") or "").lower()
        unknown_codec_video = vcodec is None and (
            ext in {"mp4", "m4v", "mov", "mkv", "webm", "ts"} or "m3u8" in protocol
        )
        if height <= 0 or (vcodec in {None, "none"} and not unknown_codec_video):
            continue
        by_height.setdefault(height, []).append(item)

    output: list[ResolvedFormat] = []

    def build_video_option(
        *,
        option_id: str,
        height: int,
        selected: dict[str, Any],
        recommended: bool = False,
    ) -> ResolvedFormat:
        format_id = str(selected.get("format_id") or "best")
        has_audio = selected.get("acodec") != "none"
        selector = format_id if has_audio else f"{format_id}+bestaudio[ext=m4a]/{format_id}+bestaudio/{format_id}"
        ext = str(selected.get("ext") or "mp4").upper()
        video_codec = _codec_name(selected.get("vcodec"))
        audio_note = "含音频" if has_audio else "自动合并音频"
        width = int(selected.get("width") or 0)
        source_height = int(selected.get("height") or 0)
        dimensions = f" · {width}×{source_height}" if width > 0 and source_height > 0 else ""
        format_note = str(selected.get("format_note") or "").lower()
        ai_note = " · AI 放大" if "ai-upscaled" in format_note or "ai upscaled" in format_note else ""
        return ResolvedFormat(
            id=option_id,
            label=f"{height}P · 4K" if height == 2160 else f"{height}P",
            detail=f"{ext} · {video_codec or '视频'}{dimensions}{ai_note} · {audio_note}{' · 推荐' if recommended else ''}",
            size=_human_size(selected.get("filesize") or selected.get("filesize_approx")),
            kind="快速" if recommended else "超清" if height >= 1440 else "高清" if height >= 1080 else "流畅",
            selector=selector,
        )

    stable_tiktok = next(
        (
            item
            for item in formats
            if str(info.get("extractor_key") or "").lower() == "tiktok"
            and str(item.get("format_id") or "").lower() == "download"
            and item.get("vcodec") not in {None, "none"}
            and item.get("acodec") not in {None, "none"}
            and str(item.get("ext") or "").lower() == "mp4"
        ),
        None,
    )
    fast_height: int | None = None
    if stable_tiktok:
        output.append(
            ResolvedFormat(
                id="video-fast",
                label="公开画质",
                detail="MP4 · H264 · 含音频 · 平台公开播放流 · 推荐",
                size=_human_size(stable_tiktok.get("filesize") or stable_tiktok.get("filesize_approx")),
                kind="快速",
                selector=str(stable_tiktok.get("format_id")),
            )
        )
    elif by_height:
        combined = [
            (height, item)
            for height, items in by_height.items()
            for item in items
            if item.get("acodec") != "none"
        ]
        if combined:
            preferred = [item for item in combined if item[0] <= 480]
            if preferred:
                fast_height, fast_selected = max(preferred, key=lambda pair: (pair[0], _format_rank(pair[1])))
            else:
                fast_height = min(height for height, _item in combined)
                fast_selected = max(
                    (item for height, item in combined if height == fast_height),
                    key=_format_rank,
                )
        else:
            moderate_heights = [height for height in by_height if height <= 480]
            fast_height = max(moderate_heights) if moderate_heights else min(by_height)
            fast_selected = max(by_height[fast_height], key=_format_rank)

        output.append(
            build_video_option(
                option_id="video-fast",
                height=fast_height,
                selected=fast_selected,
                recommended=True,
            )
        )
        best_at_fast_height = max(by_height[fast_height], key=_format_rank)
        if best_at_fast_height is not fast_selected:
            # A convenient already-muxed stream can be much softer than the
            # separate video stream at the same resolution. Keep both choices.
            output.append(
                build_video_option(
                    option_id="video-quality",
                    height=fast_height,
                    selected=best_at_fast_height,
                )
            )

    remaining_heights = [height for height in sorted(by_height, reverse=True) if height != fast_height][:4]
    for index, height in enumerate(remaining_heights, start=1):
        selected = max(by_height[height], key=_format_rank)
        output.append(build_video_option(option_id=f"video-{index}", height=height, selected=selected))

    audio_formats = [
        item for item in formats
        if item.get("acodec") not in {None, "none"} and item.get("vcodec") == "none"
    ]
    if audio_formats:
        selected_audio = max(
            audio_formats,
            key=lambda item: (
                float(item.get("abr") or item.get("tbr") or 0),
                int(str(item.get("protocol") or "").lower() in {"http", "https"}),
                int(str(item.get("ext") or "").lower() == "mp3"),
            ),
        )
        output.append(
            ResolvedFormat(
                id="audio-1",
                label="MP3",
                detail=f"音频 · {int(selected_audio.get('abr') or 192)} kbps",
                size=_human_size(selected_audio.get("filesize") or selected_audio.get("filesize_approx")),
                kind="音频",
                selector=str(selected_audio.get("format_id") or "bestaudio"),
            )
        )

    if str(info.get("extractor_key") or "").lower() == "xiaohongshu":
        # The public original-video URL has no dimensions in yt-dlp metadata.
        # Keep it selectable instead of silently dropping it as height=0.
        original = next((item for item in formats if item.get("format_id") == "direct"), None)
        if original:
            output.insert(0, ResolvedFormat(
                id="video-original",
                label="平台原始视频",
                detail="MP4 · 平台公开原始文件 · 分辨率未标注",
                size=_human_size(original.get("filesize") or original.get("filesize_approx")),
                kind="高清",
                selector="direct",
            ))

    page_host = (urlparse(str(info.get("webpage_url") or "")).hostname or "").lower()
    if not output and _host_matches(page_host, "hongguoduanju.com"):
        # This site's public MP4 currently has no height/codec metadata. Do
        # not discard it or invent a resolution; the download still selects
        # the exact format returned by the public single-episode page.
        direct = next((item for item in formats if (
            str(item.get("ext") or "").lower() == "mp4"
            and str(item.get("protocol") or "").lower() in {"http", "https"}
            and str(item.get("format_id") or "")
        )), None)
        if direct:
            output.append(ResolvedFormat(
                id="video-public",
                label="站点公开画质",
                detail="MP4 · 分辨率未标注 · 音轨待核验",
                size=_human_size(direct.get("filesize") or direct.get("filesize_approx")),
                kind="高清",
                selector=str(direct["format_id"]),
            ))

    if is_bilibili:
        # Put the highest *resolved* rendition first for Bilibili. This also
        # protects older clients that still default to formats[0].
        videos = [item for item in output if re.match(r"^\d{3,4}P(?: · 4K)?$", item.label)]
        if videos:
            videos.sort(key=lambda item: int(item.label.split("P", 1)[0]), reverse=True)
            output = [
                replace(item, detail=item.detail.removesuffix(" · 推荐") + (" · 推荐" if index == 0 else ""))
                for index, item in enumerate(videos)
            ] + [item for item in output if item not in videos]

    if not output:
        raise ProtectedMediaError("没有找到可下载的无 DRM 格式")
    return tuple(output)


def _xiaohongshu_gallery_from_public_page(page_url: str, info: dict[str, Any]) -> ResolvedMedia:
    """Read only the note's public image list, never unrelated page thumbnails."""
    parsed = urlparse(page_url)
    note_match = re.fullmatch(r"/(?:explore|discovery/item)/([0-9a-f]{24})/?", parsed.path)
    if not _host_matches((parsed.hostname or "").lower(), "xiaohongshu.com") or not note_match:
        raise ResolverError("请提供小红书单篇笔记的完整公开链接；首页、搜索页和用户主页无法下载。")

    page_html = _read_allowed_url(page_url, ("xiaohongshu.com",)).decode("utf-8", errors="replace")
    state_match = re.search(r"window\.__INITIAL_STATE__\s*=\s*", page_html)
    if not state_match:
        raise ResolverError("小红书未公开返回这篇笔记的数据；可能需要登录、页面已失效或正在验证。")
    script = page_html[state_match.end():].split("</script", 1)[0]
    try:
        state = json.JSONDecoder().raw_decode(js_to_json(script))[0]
    except (TypeError, ValueError) as exc:
        raise ResolverError("小红书公开页面的数据格式已变化，请稍后重试。") from exc
    note_state = state.get("note") if isinstance(state, dict) else None
    note_map = note_state.get("noteDetailMap") if isinstance(note_state, dict) else None
    note_entry = note_map.get(note_match.group(1)) if isinstance(note_map, dict) else None
    note = note_entry.get("note") if isinstance(note_entry, dict) else None
    if not isinstance(note, dict):
        raise ResolverError("小红书未公开返回这篇笔记的数据；可能需要登录、页面已失效或正在验证。")
    if str(note.get("type") or "").lower() in {"video", "vedio"} or note.get("video"):
        raise AuthenticationRequiredError("这篇小红书视频没有向未登录访问返回可下载的公开播放流。")

    image_list = note.get("imageList")
    if not isinstance(image_list, list) or not image_list:
        raise ResolverError("这篇小红书笔记没有返回可下载的公开图片。")
    if len(image_list) > 50:
        raise ResolverError("这篇笔记的图片超过单次 50 张的安全限额。")
    image_urls: list[str] = []
    for image in image_list:
        if not isinstance(image, dict):
            continue
        candidates = [image.get("urlDefault"), image.get("urlPre"), image.get("url")]
        info_list = image.get("infoList")
        if isinstance(info_list, list):
            candidates.extend(item.get("url") for item in info_list if isinstance(item, dict))
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            validated = build_thumbnail_format(page_url, candidate)
            if validated and validated.source_url:
                if validated.source_url not in image_urls:
                    image_urls.append(validated.source_url)
                break
    if not image_urls:
        raise ResolverError("这篇小红书笔记没有返回可下载的公开图片地址。")

    title = str(note.get("title") or info.get("title") or "小红书图文笔记")[:240]
    user = note.get("user")
    author = str((user.get("nickname") if isinstance(user, dict) else None) or "未知作者")[:160]
    count = len(image_urls)
    formats = [
        ResolvedFormat(
            id="image-all",
            label=f"全部 {count} 张图片",
            detail=f"图片合集 · {count} 张平台公开图片 · ZIP",
            size="ZIP 压缩包",
            kind="图片合集",
            selector="image-all",
            source_urls=tuple(image_urls),
        )
    ] if count > 1 else []
    formats.extend(
        ResolvedFormat(
            id=f"image-{index}",
            label=f"第 {index} 张图片",
            detail=f"图片 · 图文作品第 {index}/{count} 张 · 平台公开尺寸",
            size="未知大小",
            kind="图片",
            selector=f"image-{index}",
            source_url=image_url,
        )
        for index, image_url in enumerate(image_urls, start=1)
    )
    return ResolvedMedia(title, author, "图文", None, image_urls[0], tuple(formats))


def resolve_media(url: str, *, adult_confirmed: bool) -> ResolvedMedia:
    host = (urlparse(url).hostname or "").lower()
    if any(_host_matches(host, suffix) for suffix in HAIJIAO_HOSTS):
        return _resolve_haijiao(url)
    if host == "ted.com" or host.endswith(".ted.com"):
        return _resolve_ted(url)
    if host == "vimeo.com" or host.endswith(".vimeo.com"):
        return _resolve_vimeo(url)
    if host == "hqporner.com" or host.endswith(".hqporner.com"):
        return _resolve_hqporner(url)
    if host == "beeg.com" or host.endswith(".beeg.com"):
        return _resolve_beeg(url)
    if host == "sxyprn.com" or host.endswith(".sxyprn.com"):
        return _resolve_sxyprn(url)
    if host == "xmoviesforyou.com" or host.endswith(".xmoviesforyou.com"):
        return _resolve_xmoviesforyou(url)

    is_xiaohongshu = any(_host_matches(host, suffix) for suffix in ("xiaohongshu.com", "xhslink.com"))
    options = {**_common_options(), **_site_options(url), "skip_download": True}
    if is_xiaohongshu:
        # yt-dlp's extractor also returns image notes, but normally rejects
        # them before callers can inspect their public image list.
        options["ignore_no_formats_error"] = True
    try:
        info = _extract_info(url, options, download=False)
    except DownloadError as exc:
        raise _resolver_error_from_download(url, exc) from exc
    except Exception as exc:
        raise ResolverError("平台解析异常，请稍后重试") from exc

    if not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"} or info.get("entries"):
        raise ResolverError("请提供单个视频页面，暂不处理播放列表")

    availability = str(info.get("availability") or "").lower()
    if availability in {"private", "premium_only", "subscriber_only", "needs_auth"}:
        if str(info.get("extractor_key") or "").lower().startswith("bilibili"):
            if not bilibili_session.get() or availability in {"private", "needs_auth"}:
                raise AuthenticationRequiredError(
                    "这个 B 站视频需要登录或会员权限。请先在本机网页同步 Edge B 站会话后重新解析；"
                    "私密或未获授权的内容仍无法下载。"
                )
            # A subscriber-only label can remain on the metadata even when the
            # authenticated account is entitled. The DRM/formats checks below
            # and the actual media download remain authoritative.
        else:
            raise ProtectedMediaError("该内容需要登录、付费或特定权限")
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
        raise ProtectedMediaError("不处理正在直播或尚未开始的内容")
    if info.get("age_limit") and int(info.get("age_limit") or 0) >= 18 and not adult_confirmed:
        raise ProtectedMediaError("该内容需要先完成成年年龄确认")
    if info.get("is_drm") is True:
        raise ProtectedMediaError("该内容受 DRM 保护，不予处理")

    duration_value = info.get("duration")
    duration_seconds = int(duration_value) if isinstance(duration_value, (int, float)) else None
    if duration_seconds and duration_seconds > settings.max_duration_seconds:
        raise MediaTooLongError("视频时长超过当前服务限额")

    thumbnail = str(info.get("thumbnail")) if info.get("thumbnail") else None
    if is_xiaohongshu and not info.get("formats"):
        return _xiaohongshu_gallery_from_public_page(str(info.get("webpage_url") or url), info)
    media_formats = _safe_formats(info)
    image_format = build_thumbnail_format(url, thumbnail)
    if image_format and _host_matches(host, "hongguoduanju.com"):
        image_format = replace(
            image_format,
            label="封面图片",
            detail="图片 · 站点公开封面 · 当前提供尺寸",
        )
    if not image_format:
        image_format = build_first_frame_format(media_formats)
    return ResolvedMedia(
        title=str(info.get("title") or "未命名视频")[:240],
        author=str(info.get("uploader") or info.get("channel") or "未知作者")[:160],
        duration=_duration_text(duration_seconds),
        duration_seconds=duration_seconds,
        thumbnail=thumbnail,
        formats=media_formats + ((image_format,) if image_format else ()),
    )


def _sniff_image_extension(data: bytes) -> tuple[str, str] | None:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif", "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    if len(data) >= 16 and data[4:8] == b"ftyp" and data[8:12] in {b"avif", b"avis"}:
        return ".avif", "image/avif"
    return None


def _safe_image_stem(title: str) -> str:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", html.unescape(title)).strip(" ._")
    return (stem or f"streamnest-image-{secrets.token_hex(4)}")[:100]


def _image_request_candidates(image_url: str, platform: str) -> tuple[str, str]:
    parsed = urlparse(image_url)
    if (
        platform == "YouTube"
        and parsed.hostname in {"i.ytimg.com", "img.youtube.com"}
        and (parsed.query or parsed.fragment)
    ):
        stable = parsed._replace(query="", fragment="").geturl()
        return image_url, _validate_image_url(stable, platform)
    return image_url, image_url


def download_image(
    claim: DownloadClaim,
    progress_callback: Callable[[DownloadProgress], None] | None = None,
) -> DownloadedFile:
    if not claim.source_url:
        raise ResolverError("这个视频没有返回可下载的公开封面。")
    platform = validate_platform_url(claim.url, adult_confirmed=True).name
    image_url = _validate_image_url(claim.source_url, platform)
    directory = Path(tempfile.mkdtemp(prefix="streamnest-image-"))
    opener = build_opener(_ImageRedirectHandler(platform))
    started = time.monotonic()
    downloaded = 0
    part_path = directory / "cover.part"

    try:
        response = None
        last_network_error: HTTPError | URLError | OSError | None = None
        for candidate in _image_request_candidates(image_url, platform):
            request = Request(
                candidate,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/132 Safari/537.36",
                    "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif;q=0.9,*/*;q=0.2",
                    "Referer": claim.url,
                },
                method="GET",
            )
            try:
                response = opener.open(request, timeout=30)
                break
            except (HTTPError, URLError, OSError) as exc:
                last_network_error = exc
        if response is None:
            assert last_network_error is not None
            raise last_network_error

        with response, part_path.open("wb") as output:
            _validate_image_url(response.geturl(), platform)
            total_value = response.headers.get("Content-Length")
            total = int(total_value) if total_value and total_value.isdigit() else None
            if total and total > settings.max_image_size_bytes:
                raise MediaTooLargeError("图片文件超过当前服务限额。")

            first_chunk = response.read(256 * 1024)
            detected = _sniff_image_extension(first_chunk)
            if not detected:
                raise ResolverError("平台返回的不是受支持的 JPG、PNG、WebP、GIF 或 AVIF 图片。")
            extension, media_type = detected
            output.write(first_chunk)
            downloaded += len(first_chunk)
            if progress_callback:
                elapsed = max(0.001, time.monotonic() - started)
                progress_callback(
                    DownloadProgress(
                        status="downloading",
                        downloaded_bytes=downloaded,
                        total_bytes=total,
                        speed=downloaded / elapsed,
                        eta=0 if total and downloaded >= total else None,
                        percent=min(100.0, downloaded / total * 100) if total else None,
                    )
                )

            while True:
                chunk = response.read(512 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                if downloaded > settings.max_image_size_bytes:
                    raise MediaTooLargeError("图片文件超过当前服务限额。")
                elapsed = max(0.001, time.monotonic() - started)
                speed = downloaded / elapsed
                eta = max(0, int((total - downloaded) / speed)) if total and speed > 0 else None
                if progress_callback:
                    progress_callback(
                        DownloadProgress(
                            status="downloading",
                            downloaded_bytes=downloaded,
                            total_bytes=total,
                            speed=speed,
                            eta=eta,
                            percent=(downloaded / total * 100) if total else None,
                        )
                    )

        if downloaded <= 0:
            raise ResolverError("平台没有返回图片数据，请重试。")
        final_path = directory / f"{_safe_image_stem(claim.title)}-cover{extension}"
        part_path.replace(final_path)
        return DownloadedFile(directory=directory, path=final_path, media_type=media_type)
    except (ResolverError, MediaTooLargeError):
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except (HTTPError, URLError, OSError) as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise ResolverError("平台暂时拒绝了封面图片请求，请重新解析后再试。") from exc
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def download_video_frame(
    claim: DownloadClaim,
    progress_callback: Callable[[DownloadProgress], None] | None = None,
) -> DownloadedFile:
    video_claim = DownloadClaim(
        url=claim.url,
        selector=claim.selector.removeprefix(_FRAME_SELECTOR_PREFIX),
        kind="快速",
        title=claim.title,
        adult_confirmed=claim.adult_confirmed,
        source_url=claim.source_url,
    )
    downloaded = download_media(video_claim, progress_callback)
    frame_path = downloaded.directory / f"{_safe_image_stem(claim.title)}-first-frame.jpg"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        shutil.rmtree(downloaded.directory, ignore_errors=True)
        raise ResolverError("生成视频首帧需要 FFmpeg，请重新打开统一下载管理器后再试。")
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                "0.1",
                "-i",
                str(downloaded.path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-y",
                str(frame_path),
            ],
            capture_output=True,
            timeout=90,
            check=False,
        )
        if result.returncode != 0 or not frame_path.is_file() or frame_path.stat().st_size <= 0:
            raise ResolverError("这个视频暂时无法生成首帧图片。")
        downloaded.path.unlink(missing_ok=True)
        return DownloadedFile(downloaded.directory, frame_path, "image/jpeg")
    except ResolverError:
        shutil.rmtree(downloaded.directory, ignore_errors=True)
        raise
    except (OSError, subprocess.SubprocessError):
        shutil.rmtree(downloaded.directory, ignore_errors=True)
        raise ResolverError("生成视频首帧失败，请稍后重试。")


def download_image_collection(
    claim: DownloadClaim,
    progress_callback: Callable[[DownloadProgress], None] | None = None,
) -> DownloadedFile:
    image_urls = tuple(dict.fromkeys(claim.source_urls))[:50]
    if not image_urls:
        raise ResolverError("这个图文作品没有返回可下载的公开图片。")

    directory = Path(tempfile.mkdtemp(prefix="streamnest-images-"))
    archive_path = directory / f"{_safe_image_stem(claim.title)}-全部{len(image_urls)}张图片.zip"
    started = time.monotonic()
    downloaded_bytes = 0
    saved_count = 0
    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
            for index, image_url in enumerate(image_urls, start=1):
                item = download_image(
                    DownloadClaim(
                        url=claim.url,
                        selector=f"image-{index}",
                        kind="图片",
                        title=f"{claim.title}-{index:02d}",
                        adult_confirmed=claim.adult_confirmed,
                        source_url=image_url,
                    )
                )
                try:
                    size = item.path.stat().st_size
                    downloaded_bytes += size
                    if downloaded_bytes > settings.max_file_size_bytes:
                        raise MediaTooLargeError("图片合集超过当前服务限额。")
                    archive.write(item.path, arcname=f"{index:02d}{item.path.suffix.lower()}")
                    saved_count += 1
                finally:
                    shutil.rmtree(item.directory, ignore_errors=True)

                if progress_callback:
                    elapsed = max(0.001, time.monotonic() - started)
                    remaining = len(image_urls) - index
                    progress_callback(
                        DownloadProgress(
                            status="downloading",
                            downloaded_bytes=downloaded_bytes,
                            total_bytes=None,
                            speed=downloaded_bytes / elapsed,
                            eta=round((elapsed / index) * remaining) if remaining else 0,
                            percent=(index / len(image_urls)) * 100,
                        )
                    )

        if saved_count != len(image_urls) or not archive_path.is_file() or archive_path.stat().st_size <= 0:
            raise ResolverError("图片合集生成不完整，请重新解析后再试。")
        return DownloadedFile(directory, archive_path, "application/zip")
    except (ResolverError, MediaTooLargeError):
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise ResolverError("图片合集生成失败，请重新解析后再试。") from exc


def _next_direct_range_size(current: int, elapsed: float, received: int) -> int:
    """Reduce HTTP round trips on fast CDNs without making slow ranges stall."""
    one_mib = 1024 * 1024
    if received < current:
        return current
    if elapsed <= 3:
        return min(current * 2, 4 * one_mib)
    if elapsed >= 12:
        return max(current // 2, one_mib)
    return current


def _download_direct_public_mp4(
    claim: DownloadClaim,
    progress_callback: Callable[[DownloadProgress], None] | None = None,
) -> DownloadedFile | None:
    if not claim.source_url:
        return None
    platform = validate_platform_url(claim.url, adult_confirmed=True).name
    allowed_by_platform = {
        "HQPorner": (("bigcdn.cc",), ("bigcdn.cc",)),
        "SxyPrn": (("sxyprn.com",), ("sxyprn.com", "trafficdeposit.com")),
    }
    if platform not in allowed_by_platform:
        return None
    source_hosts, redirect_hosts = allowed_by_platform[platform]
    def validate_source(candidate: str) -> str:
        safe_url = _validate_allowed_https_url(candidate, source_hosts)
        source_path = urlparse(safe_url).path
        if platform == "HQPorner" and not re.fullmatch(r"/pubs/[A-Za-z0-9._-]+/\d{3,4}\.mp4", source_path):
            raise ResolverError("HQPorner 媒体地址未通过安全检查")
        if platform == "SxyPrn" and not re.fullmatch(
            r"/cdn8/[A-Za-z0-9._-]+/c\d+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/\d{9,12}/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+\.vid",
            source_path,
        ):
            raise ResolverError("SxyPrn 媒体地址未通过安全检查")
        return safe_url

    source_url = validate_source(claim.source_url)

    directory = Path(tempfile.mkdtemp(prefix="streamnest-direct-"))
    part_path = directory / "video.part"
    final_path = directory / f"{_safe_image_stem(claim.title)}.mp4"
    opener = build_opener(_AllowedRedirectHandler(redirect_hosts))
    base_headers = {
        "User-Agent": _BROWSER_USER_AGENT,
        "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.1",
        "Referer": claim.url,
    }
    started = time.monotonic()
    downloaded = 0
    total: int | None = None
    # These CDNs may buffer a large Range response before sending any bytes.
    # Smaller resumable chunks reach the progress UI sooner and are less likely
    # to hit the per-request timeout on slow nodes.
    range_size = 1024 * 1024
    consecutive_failures = 0
    refreshed_once = False
    try:
        with part_path.open("wb") as output:
            while total is None or downloaded < total:
                start_byte = downloaded
                end_byte = start_byte + range_size - 1
                range_started = time.monotonic()
                request = Request(
                    source_url,
                    headers={**base_headers, "Range": f"bytes={start_byte}-{end_byte}"},
                )
                try:
                    with opener.open(request, timeout=30) as response:
                        _validate_allowed_https_url(response.geturl(), redirect_hosts)
                        content_type = str(response.headers.get_content_type() or "").lower()
                        if content_type not in {"video/mp4", "application/octet-stream"}:
                            raise ResolverError("平台返回的不是 MP4 视频文件")
                        status = int(getattr(response, "status", 200))
                        expected_range_bytes: int | None = None
                        if status == 206:
                            content_range = str(response.headers.get("Content-Range") or "")
                            range_match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                            if not range_match or int(range_match.group(1)) != start_byte:
                                raise ResolverError("平台返回的分段范围无效，请重新解析后再试")
                            range_end = int(range_match.group(2))
                            response_total = int(range_match.group(3))
                            if range_end < start_byte or range_end > end_byte or range_end >= response_total:
                                raise ResolverError("平台返回的分段范围无效，请重新解析后再试")
                            expected_range_bytes = range_end - start_byte + 1
                            if total is not None and total != response_total:
                                raise ResolverError("平台文件大小在下载过程中发生变化，请重新解析")
                            total = response_total
                        elif status == 200 and start_byte == 0:
                            total_value = response.headers.get("Content-Length")
                            total = int(total_value) if total_value and total_value.isdigit() else None
                        else:
                            raise ResolverError("平台不支持安全的断点续传，请重新解析后再试")
                        if total and total > settings.max_file_size_bytes:
                            raise MediaTooLargeError("文件超过当前服务限额")
                        range_bytes = 0
                        while True:
                            chunk = response.read(16 * 1024)
                            if not chunk:
                                break
                            if expected_range_bytes is not None and range_bytes + len(chunk) > expected_range_bytes:
                                raise ResolverError("平台返回的视频分段长度无效，请重新解析后再试")
                            output.write(chunk)
                            downloaded += len(chunk)
                            range_bytes += len(chunk)
                            if downloaded > settings.max_file_size_bytes:
                                raise MediaTooLargeError("文件超过当前服务限额")
                            elapsed = max(0.001, time.monotonic() - started)
                            speed = downloaded / elapsed
                            if progress_callback:
                                progress_callback(
                                    DownloadProgress(
                                        status="downloading",
                                        downloaded_bytes=downloaded,
                                        total_bytes=total,
                                        speed=speed,
                                        eta=(
                                            max(0, int((total - downloaded) / speed))
                                            if total and speed > 0
                                            else None
                                        ),
                                        percent=(downloaded / total * 100) if total else None,
                                    )
                                )
                        if expected_range_bytes is not None and range_bytes != expected_range_bytes:
                            raise URLError("incomplete media range")
                        if downloaded <= start_byte:
                            raise URLError("empty media range")
                        consecutive_failures = 0
                        if status == 206 and expected_range_bytes is not None:
                            range_size = _next_direct_range_size(
                                range_size, time.monotonic() - range_started,
                                expected_range_bytes,
                            )
                        if status == 200:
                            break
                except HTTPError as exc:
                    if exc.code in {403, 404, 410} and not refreshed_once:
                        refreshed = _refresh_direct_public_claim(claim)
                        if refreshed and refreshed.source_url:
                            new_url = validate_source(refreshed.source_url)
                            if new_url != source_url:
                                # Continue from the last complete byte.  The
                                # next 206 response must retain the same total.
                                source_url = new_url
                                refreshed_once = True
                                consecutive_failures = 0
                                continue
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        raise
                    time.sleep(float(consecutive_failures))
                except (URLError, OSError):
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        raise
                    time.sleep(float(consecutive_failures))
        if downloaded <= 0:
            raise ResolverError("平台没有返回视频数据，请重新解析后再试")
        if total is not None and downloaded != total:
            raise ResolverError("平台视频下载不完整，请重新解析后再试")
        part_path.replace(final_path)
        return DownloadedFile(directory=directory, path=final_path, media_type="video/mp4")
    except (ResolverError, MediaTooLargeError):
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except (HTTPError, URLError, OSError) as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise ResolverError("平台公开媒体地址已过期，请重新解析后再试") from exc
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def _refresh_direct_public_claim(claim: DownloadClaim) -> DownloadClaim | None:
    """Refresh a short-lived first-party media URL without broadening platform access."""
    platform = validate_platform_url(claim.url, adult_confirmed=True).name
    if platform == "HQPorner":
        media = _resolve_hqporner(claim.url)
    elif platform == "SxyPrn":
        try:
            media = _resolve_sxyprn(claim.url)
        except ResolverError as exc:
            if str(exc) != "平台公开页面暂时无法访问":
                raise
            # A failed refresh must not discard an otherwise resumable large
            # download on the first transient page/network error.
            time.sleep(1.5)
            media = _resolve_sxyprn(claim.url)
    else:
        return None

    candidates = [item for item in media.formats if item.source_url and item.kind != "图片"]
    if not candidates:
        return None

    selected = candidates[0]
    quality_match = re.search(r"/(\d{3,4})\.mp4$", urlparse(claim.source_url or "").path)
    if quality_match:
        quality_label = f"{quality_match.group(1)}P"
        selected = next((item for item in candidates if item.label == quality_label), None)
        if selected is None:
            return None

    return DownloadClaim(
        url=claim.url,
        selector=selected.selector,
        kind=claim.kind,
        title=claim.title,
        adult_confirmed=claim.adult_confirmed,
        source_url=selected.source_url,
        source_urls=claim.source_urls,
        format_id=claim.format_id,
        format_label=claim.format_label,
    )


def _expired_public_source_error(exc: DownloadError) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in ("http error 403", "http error 404", "http error 410", "url has expired"))


def _refresh_public_source_claim(claim: DownloadClaim) -> DownloadClaim | None:
    """Refresh only the same public rendition selected by the user."""
    if not claim.source_url or not claim.format_id or not claim.format_label:
        return None
    media = resolve_media(claim.url, adult_confirmed=claim.adult_confirmed)
    selected = next(
        (
            item for item in media.formats
            if item.id == claim.format_id and item.label == claim.format_label and item.source_url
        ),
        None,
    )
    if selected is None or not selected.source_url or selected.source_url == claim.source_url:
        return None
    if not _trusted_source_url(selected.source_url):
        raise ResolverError("重新解析得到的媒体地址未通过安全检查")
    return replace(claim, selector=selected.selector, source_url=selected.source_url)


def download_media(
    claim: DownloadClaim,
    progress_callback: Callable[[DownloadProgress], None] | None = None,
) -> DownloadedFile:
    if claim.kind == "图片合集":
        return download_image_collection(claim, progress_callback)
    if claim.kind == "图片":
        if claim.selector.startswith(_FRAME_SELECTOR_PREFIX):
            return download_video_frame(claim, progress_callback)
        if claim.source_url:
            return download_image(claim, progress_callback)
        return download_video_frame(claim, progress_callback)
    try:
        direct_download = _download_direct_public_mp4(claim, progress_callback)
    except ResolverError as exc:
        if "公开媒体地址已过期" not in str(exc):
            raise
        refreshed_claim = _refresh_direct_public_claim(claim)
        if refreshed_claim is None:
            raise
        direct_download = _download_direct_public_mp4(refreshed_claim, progress_callback)
    if direct_download:
        return direct_download
    host = (urlparse(claim.url).hostname or "").lower()
    if claim.source_url and any(_host_matches(host, suffix) for suffix in HAIJIAO_HOSTS):
        try:
            _haijiao_playlist_duration(claim.source_url, claim.url)
        except ResolverError:
            refreshed = _resolve_haijiao(claim.url)
            replacement = next(
                (
                    item for item in refreshed.formats
                    if item.selector == claim.selector
                    and (claim.format_label is None or item.label == claim.format_label)
                ),
                None,
            )
            if replacement is None or not replacement.source_url:
                raise ResolverError("海角网视频地址已过期，请重新解析这个详情页后重试。")
            claim = replace(claim, source_url=replacement.source_url)
    max_file_size_bytes = (
        settings.max_bilibili_file_size_bytes
        if host == "bilibili.com" or host.endswith(".bilibili.com")
        else settings.max_file_size_bytes
    )
    directory = Path(tempfile.mkdtemp(prefix="streamnest-"))
    if claim.source_url and not _trusted_source_url(claim.source_url):
        shutil.rmtree(directory, ignore_errors=True)
        raise ResolverError("媒体地址未通过安全检查")
    download_url = claim.source_url or claim.url

    def progress_hook(status: dict[str, Any]) -> None:
        downloaded = int(status.get("downloaded_bytes") or 0)
        total_value = status.get("total_bytes") or status.get("total_bytes_estimate")
        total = int(total_value) if total_value else None
        speed_value = status.get("speed")
        speed = float(speed_value) if speed_value else None
        eta_value = status.get("eta")
        eta = max(0, int(eta_value)) if eta_value is not None else None
        percent = (downloaded / total * 100) if total else None
        if percent is None:
            fragment_index = status.get("fragment_index")
            fragment_count = status.get("fragment_count")
            if fragment_index and fragment_count:
                percent = float(fragment_index) / float(fragment_count) * 100
        if progress_callback:
            progress_callback(
                DownloadProgress(
                    status=str(status.get("status") or "downloading"),
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                    speed=speed,
                    eta=eta,
                    percent=percent,
                )
            )
        if downloaded > max_file_size_bytes:
            raise DownloadLimitReached

    options: dict[str, Any] = {
        **_common_options(),
        "concurrent_fragment_downloads": 4,
        "skip_unavailable_fragments": False,
        **_site_options(claim.url),
        "format": "best" if claim.source_url else claim.selector,
        "outtmpl": str(directory / "%(title).120B-%(id)s.%(ext)s"),
        "restrictfilenames": True,
        "windowsfilenames": True,
        "overwrites": False,
        "max_filesize": max_file_size_bytes,
        "progress_hooks": [progress_hook],
    }
    if claim.kind == "音频":
        options["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
    else:
        options["merge_output_format"] = "mp4"

    try:
        try:
            _extract_info(download_url, options, download=True)
        except DownloadError as exc:
            if not claim.source_url or not _expired_public_source_error(exc):
                raise
            refreshed_claim = _refresh_public_source_claim(claim)
            if refreshed_claim is None:
                raise
            # A changed signed URL must never reuse bytes left by the prior
            # source. This directory is an isolated StreamNest temp directory.
            shutil.rmtree(directory, ignore_errors=True)
            directory = Path(tempfile.mkdtemp(prefix="streamnest-"))
            options["outtmpl"] = str(directory / "%(title).120B-%(id)s.%(ext)s")
            if progress_callback:
                progress_callback(DownloadProgress("retrying", 0, None, None, None, 0.0))
            _extract_info(refreshed_claim.source_url, options, download=True)
        candidates = [path for path in directory.iterdir() if path.is_file() and path.suffix not in {".part", ".ytdl"}]
        if not candidates:
            raise ResolverError("下载完成后未找到可用文件")
        path = max(candidates, key=lambda item: item.stat().st_size)
        if path.stat().st_size > max_file_size_bytes:
            raise MediaTooLargeError("文件超过当前服务限额")
        if any(_host_matches(host, suffix) for suffix in HAIJIAO_HOSTS) and claim.selector.startswith("haijiao-"):
            named_path = directory / f"{_safe_image_stem(claim.title)}-{claim.selector}{path.suffix}"
            path = path.rename(named_path)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return DownloadedFile(directory=directory, path=path, media_type=media_type)
    except DownloadLimitReached as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise MediaTooLargeError("文件超过当前服务限额") from exc
    except ResolverError:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except DownloadError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise ResolverError(
            "所选画质下载失败，可能是平台临时限制或媒体地址已变更。"
            "请重新解析后重试；若仅此画质失败，可改选其他实际可用的画质。"
        ) from exc
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise ResolverError("平台下载过程中出现临时错误，请重新解析后重试") from exc


def download_helper_media(
    url: str,
    platform: str,
    headers: dict[str, str],
    progress_callback: Callable[[DownloadProgress], None] | None = None,
) -> DownloadedFile:
    safe_headers = validate_helper_media_request(url, platform, headers)
    directory = Path(tempfile.mkdtemp(prefix="streamnest-helper-"))
    prefix = "douyin" if platform == "抖音" else "kuaishou"
    path = directory / f"{prefix}-video-{secrets.token_hex(4)}.mp4"
    request = Request(url, headers=safe_headers, method="GET")
    opener = build_opener(_PlatformRedirectHandler(platform))
    started = time.monotonic()
    downloaded = 0

    try:
        with opener.open(request, timeout=30) as response, path.open("wb") as output:
            _validate_helper_media_url(response.geturl(), platform)
            content_type = response.headers.get_content_type().lower()
            if content_type != "video/mp4":
                raise ResolverError("平台返回的不是 MP4 视频文件，请刷新助手后重试。")
            total_value = response.headers.get("Content-Length")
            total = int(total_value) if total_value and total_value.isdigit() else None
            if total and total > settings.max_file_size_bytes:
                raise MediaTooLargeError("文件超过当前服务限额")

            while True:
                chunk = response.read(512 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                if downloaded > settings.max_file_size_bytes:
                    raise MediaTooLargeError("文件超过当前服务限额")
                elapsed = max(0.001, time.monotonic() - started)
                speed = downloaded / elapsed
                eta = max(0, int((total - downloaded) / speed)) if total and speed > 0 else None
                if progress_callback:
                    progress_callback(
                        DownloadProgress(
                            status="downloading",
                            downloaded_bytes=downloaded,
                            total_bytes=total,
                            speed=speed,
                            eta=eta,
                            percent=(downloaded / total * 100) if total else None,
                        )
                    )
        if downloaded <= 0:
            raise ResolverError("平台没有返回视频数据，请重试。")
        if total is not None and downloaded != total:
            raise ResolverError("平台视频下载不完整，请刷新页面后重试。")
        return DownloadedFile(directory=directory, path=path, media_type="video/mp4")
    except (ResolverError, MediaTooLargeError):
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except (HTTPError, URLError, OSError) as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise ResolverError("平台拒绝了本次媒体请求，请刷新页面后重试。") from exc
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
