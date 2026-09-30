import html
import json
import os
import re
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


class UrlValidationError(ValueError):
    """Raised when a URL is outside StreamNest's public platform allowlist."""


class AdultConfirmationRequired(UrlValidationError):
    """Raised when an adult platform is used without an age confirmation."""


@dataclass(frozen=True)
class PlatformRule:
    name: str
    hosts: tuple[str, ...]
    adult: bool = False


def _haijiao_hosts() -> tuple[str, ...]:
    """Keep the user-approved rotating domains in one local, editable file."""
    app_dir = os.getenv("STREAMNEST_APP_DIR")
    path = (Path(app_dir) if app_dir else Path(__file__).resolve().parents[2]) / "haijiao-domains.json"
    hosts = json.loads(path.read_text(encoding="utf-8"))["hosts"]
    if not isinstance(hosts, list) or not hosts or any(
        not isinstance(host, str)
        or not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,}", host)
        for host in hosts
    ):
        raise ValueError("海角网域名清单格式无效")
    return tuple(dict.fromkeys(hosts))


HAIJIAO_HOSTS = _haijiao_hosts()


PLATFORMS = (
    PlatformRule("Bilibili", ("bilibili.com", "b23.tv")),
    PlatformRule("抖音", ("douyin.com", "iesdouyin.com")),
    PlatformRule("小红书", ("xiaohongshu.com", "xhslink.com")),
    PlatformRule("红果短剧", ("hongguoduanju.com",)),
    PlatformRule("快手", ("kuaishou.com", "gifshow.com")),
    PlatformRule("微博", ("weibo.com", "weibo.cn")),
    PlatformRule("西瓜视频", ("ixigua.com",)),
    PlatformRule("AcFun", ("acfun.cn",)),
    PlatformRule("YouTube", ("youtube.com", "youtu.be")),
    PlatformRule("TikTok", ("tiktok.com",)),
    PlatformRule("X", ("x.com", "twitter.com")),
    PlatformRule("Facebook", ("facebook.com", "fb.watch")),
    PlatformRule("Instagram", ("instagram.com",)),
    PlatformRule("TED", ("ted.com",)),
    PlatformRule("Vimeo", ("vimeo.com",)),
    PlatformRule("Reddit", ("reddit.com", "redd.it")),
    PlatformRule("Twitch", ("twitch.tv",)),
    PlatformRule("Dailymotion", ("dailymotion.com", "dai.ly")),
    PlatformRule("Pinterest", ("pinterest.com", "pin.it")),
    PlatformRule("SoundCloud", ("soundcloud.com",)),
    PlatformRule("Pornhub", ("pornhub.com",), adult=True),
    PlatformRule("XVideos", ("xvideos.com",), adult=True),
    PlatformRule("Eporner", ("eporner.com",), adult=True),
    PlatformRule("XNXX", ("xnxx.com", "xnxx3.com"), adult=True),
    PlatformRule("Rule34Video", ("rule34video.com",), adult=True),
    PlatformRule("HQPorner", ("hqporner.com",), adult=True),
    PlatformRule("Beeg", ("beeg.com",), adult=True),
    PlatformRule("SxyPrn", ("sxyprn.com",), adult=True),
    PlatformRule("SpankBang", ("spankbang.com",), adult=True),
    PlatformRule("XMoviesForYou", ("xmoviesforyou.com",), adult=True),
    PlatformRule("海角网", HAIJIAO_HOSTS, adult=True),
)


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith(f".{suffix}")


def extract_video_url(value: str) -> str:
    """Extract one HTTP(S) URL from plain text or a Markdown link."""
    if not isinstance(value, str):
        return ""
    normalized = html.unescape(value.strip()).replace("\\_", "_")
    if not normalized:
        return ""

    markdown_target = re.search(r"\]\(\s*(https?://[^)\s]+)", normalized, flags=re.IGNORECASE)
    loose_url = re.search(r"https?://[^\s<>\"'）】]+", normalized, flags=re.IGNORECASE)
    candidate = (markdown_target or loose_url)
    extracted = candidate.group(1 if markdown_target else 0) if candidate else normalized
    extracted = re.split(r"[，。；！？”’、]", extracted, maxsplit=1)[0]
    extracted = extracted.rstrip(")]}>,.;!?")

    try:
        parsed = urlsplit(extracted)
    except ValueError:
        return extracted
    host = (parsed.hostname or "").lower().removeprefix("www.")
    layer_id = parse_qs(parsed.query).get("layerid", [""])[0]
    if host == "weibo.com" and re.fullmatch(r"\d{8,24}", layer_id):
        return f"https://m.weibo.cn/status/{layer_id}"
    return extracted


def validate_platform_url(url: str, *, adult_confirmed: bool = False) -> PlatformRule:
    if not isinstance(url, str) or not url.strip() or len(url) > 2048:
        raise UrlValidationError("链接为空或过长")

    try:
        parsed = urlsplit(url.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise UrlValidationError("链接格式无效") from exc

    if parsed.scheme not in {"http", "https"} or not host:
        raise UrlValidationError("仅支持完整的 HTTP/HTTPS 视频页面链接")
    if parsed.username or parsed.password:
        raise UrlValidationError("链接中不得包含账号或密码")
    if port not in {None, 80, 443}:
        raise UrlValidationError("不支持非标准端口")

    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        raise UrlValidationError("不允许使用 IP 地址")

    rule = next(
        (item for item in PLATFORMS if any(_host_matches(host, suffix) for suffix in item.hosts)),
        None,
    )
    if rule is None:
        raise UrlValidationError("该域名不在已启用的平台列表中")
    if rule.adult and not adult_confirmed:
        raise AdultConfirmationRequired("请先确认已达当地法定年龄")
    return rule
