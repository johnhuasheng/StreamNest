import json
import os
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from yt_dlp.utils import DownloadError

import streamnest_api.resolver as resolver_module
from streamnest_api.resolver import (
    AuthenticationRequiredError,
    ProtectedMediaError,
    ResolvedFormat,
    ResolverError,
    VisitorSessionRequiredError,
    _configure_ascii_ca_bundle,
    _download_direct_public_mp4,
    download_media,
    _extract_info,
    _image_request_candidates,
    _read_allowed_url,
    _safe_image_stem,
    _sniff_image_extension,
    _resolver_error_from_download,
    _resolve_hqporner,
    _resolve_haijiao,
    _haijiao_playlist_duration,
    _resolve_beeg,
    _resolve_sxyprn,
    _resolve_xmoviesforyou,
    _safe_formats,
    _site_options,
    normalize_media_page_url,
    _sxyprn_media_path,
    _ted_media_from_html,
    _trusted_source_url,
    _vimeo_media_from_config,
    build_thumbnail_format,
    build_first_frame_format,
    download_image,
    download_image_collection,
    sanitize_helper_headers,
    validate_helper_media_request,
)
from streamnest_api.tickets import DownloadClaim


def test_youtube_uses_fetchable_embedded_client() -> None:
    options = _site_options("https://www.youtube.com/watch?v=test")

    assert options["source_address"] == "0.0.0.0"
    assert options["extractor_args"]["youtube"]["player_client"] == ["web_embedded"]


def test_other_platforms_keep_default_extractor_options() -> None:
    assert _site_options("https://www.bilibili.com/video/BVtest") == {}


def test_bilibili_member_only_error_is_actionable() -> None:
    error = _resolver_error_from_download(
        "https://www.bilibili.com/video/BVtest",
        DownloadError("This format is for premium member only"),
    )
    assert isinstance(error, AuthenticationRequiredError)
    assert "会员身份" in str(error)
    assert "账号" in str(error)


def test_hjw_route_page_is_rejected_without_network(monkeypatch) -> None:
    monkeypatch.setattr(
        resolver_module,
        "_read_allowed_url",
        lambda *_args, **_kwargs: pytest.fail("线路发布页不应触发网络解析"),
    )

    with pytest.raises(ResolverError, match="线路发布页"):
        normalize_media_page_url("https://hjw2026.com/")

    with pytest.raises(ResolverError, match="线路发布页"):
        normalize_media_page_url("https://hjwang21.com/category/videos/")
    assert normalize_media_page_url("https://www.hjw01.com/archives/194451/") == (
        "https://www.hjw01.com/archives/194451/"
    )


def test_haijiao_resolves_multiple_public_hls_segments(monkeypatch) -> None:
    page = "https://www.hjw01.com/archives/194451/"
    source_a = "https://hls.qldjxf.cn/videos5/a/a.m3u8?auth_key=sample"
    source_b = "https://hls.qldjxf.cn/videos5/b/b.m3u8?auth_key=sample"
    config_a = json.dumps({"video": {"type": "hls", "url": source_a}})
    config_b = json.dumps({"video": {"type": "hls", "url": source_b}})
    html_page = (
        '<html><title>公开测试视频 | 海角网</title>'
        f"<div class='dplayer' data-config='{config_a}'></div>"
        f"<div class='dplayer' data-config='{config_b}'></div></html>"
    ).encode()
    playlist = (
        "#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,"
        'URI="https://mts.hhjd.mobi/videos5/a/crypt.key"\n'
        "#EXTINF:2.5,\nhttps://mts.hhjd.mobi/videos5/a/0.m4s\n"
        "#EXTINF:3.0,\nhttps://mts.hhjd.mobi/videos5/a/1.m4s\n"
    ).encode()

    def fake_read(url, allowed_hosts, **_kwargs):
        if url == page:
            return html_page
        if url == source_a:
            return playlist
        if url == source_b:
            return playlist + b"#EXTINF:10,\nhttps://mts.hhjd.mobi/videos5/a/2.m4s\n"
        if url.endswith("crypt.key"):
            return b"1" * 16
        pytest.fail(f"Unexpected URL: {url}")

    monkeypatch.setattr(resolver_module, "_read_allowed_url", fake_read)
    result = _resolve_haijiao(page)
    assert [item.label for item in result.formats] == ["第 2 段视频", "第 1 段视频"]
    assert result.formats[1].source_url == source_a
    assert "00:06" in result.formats[1].detail


def test_haijiao_rejects_drm_or_untrusted_hls_urls(monkeypatch) -> None:
    source = "https://hls.qldjxf.cn/videos5/a/a.m3u8"
    monkeypatch.setattr(
        resolver_module,
        "_read_allowed_url",
        lambda *_args, **_kwargs: b"#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES\n#EXTINF:2,\nsegment.m4s\n",
    )
    with pytest.raises(ProtectedMediaError, match="受保护"):
        _haijiao_playlist_duration(source, "https://www.hjw01.com/archives/194451/")

    monkeypatch.setattr(
        resolver_module,
        "_read_allowed_url",
        lambda *_args, **_kwargs: b"#EXTM3U\n#EXTINF:2,\nhttp://127.0.0.1/internal\n",
    )
    with pytest.raises(ResolverError, match="安全检查"):
        _haijiao_playlist_duration(source, "https://www.hjw01.com/archives/194451/")


def test_haijiao_rejects_unverified_js_redirect(monkeypatch) -> None:
    monkeypatch.setattr(
        resolver_module,
        "_read_allowed_url",
        lambda *_args, **_kwargs: (
            b'<html><script>window.location.replace("https://unverified.example/archives/194451/")'
            b"</script></html>"
        ),
    )
    with pytest.raises(ResolverError, match="未核验"):
        _resolve_haijiao("https://hjwang21.com/archives/194451/")


def test_haijiao_public_page_retries_one_transient_403(monkeypatch) -> None:
    url = "https://www.hjw01.com/archives/194451/"
    attempts = 0

    class FakeResponse:
        headers = {"Content-Length": "6"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return url

        def read(self, _size):
            return b"public"

    class FakeOpener:
        def open(self, _request, timeout):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise HTTPError(url, 403, "Forbidden", {}, None)
            return FakeResponse()

    monkeypatch.setattr(resolver_module, "build_opener", lambda *_args: FakeOpener())
    monkeypatch.setattr(resolver_module.time, "sleep", lambda *_args: None)
    assert _read_allowed_url(url, ("hjw01.com",), retry_forbidden=True) == b"public"
    assert attempts == 2


def test_haijiao_refresh_does_not_switch_to_another_segment(monkeypatch) -> None:
    monkeypatch.setattr(resolver_module, "_download_direct_public_mp4", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        resolver_module,
        "_haijiao_playlist_duration",
        lambda *_args: (_ for _ in ()).throw(ResolverError("旧地址已失效")),
    )
    monkeypatch.setattr(
        resolver_module,
        "_resolve_haijiao",
        lambda _url: resolver_module.ResolvedMedia(
            title="Public sample",
            author="海角网",
            duration="--:--",
            duration_seconds=None,
            thumbnail=None,
            formats=(ResolvedFormat(
                "haijiao-1", "第 2 段视频", "HLS", "未知大小", "视频", "haijiao-1",
                "https://hls.qldjxf.cn/new.m3u8",
            ),),
        ),
    )
    with pytest.raises(ResolverError, match="视频地址已过期"):
        download_media(DownloadClaim(
            url="https://www.hjw01.com/archives/194451/",
            selector="haijiao-1",
            kind="视频",
            title="Public sample",
            adult_confirmed=True,
            source_url="https://hls.qldjxf.cn/old.m3u8",
            format_id="haijiao-1",
            format_label="第 1 段视频",
        ))
def test_rule34video_uses_browser_compatible_public_request() -> None:
    options = _site_options("https://rule34video.com/video/123/test/")

    assert str(options["impersonate"]) == "chrome:windows"


def test_public_page_reader_stops_at_declared_content_length(monkeypatch) -> None:
    payload = b"public"

    class FakeResponse:
        headers = {"Content-Length": str(len(payload))}

        def __init__(self) -> None:
            self.buffer = BytesIO(payload)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return "https://hqporner.com/public"

        def read(self, size=-1):
            if self.buffer.tell() >= len(payload):
                raise TimeoutError("server kept the connection open")
            return self.buffer.read(size)

    class FakeOpener:
        def open(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(resolver_module, "_validate_allowed_https_url", lambda url, _hosts: url)
    monkeypatch.setattr(resolver_module, "build_opener", lambda *_args: FakeOpener())

    assert _read_allowed_url("https://hqporner.com/public", ("hqporner.com",)) == payload


def test_xmovies_uses_only_page_referer_for_public_hls() -> None:
    url = "https://xmoviesforyou.com/public-video"
    options = _site_options(url)

    assert options["http_headers"] == {
        "Referer": url,
        "Origin": "https://xmoviesforyou.com",
    }
    assert options["concurrent_fragment_downloads"] == 8


def test_xnxx_search_result_normalizes_exact_numeric_id(monkeypatch) -> None:
    page = b'''<div id="video_other" data-id="111"><a href="/video-wrong/not_this_one">wrong</a></div>
    <div id="video_fbz2h36" data-id="25752905" class="thumb-block">
      <div><a href="/video-fbz2h36/sexy_ancient_sex_of_chinese_babe">target</a></div>
    </div>'''
    requested: list[str] = []

    def fake_read(url, allowed_hosts, **_kwargs):
        requested.append(url)
        assert allowed_hosts == ("xnxx.com", "xnxx3.com")
        return page

    monkeypatch.setattr(resolver_module, "_read_allowed_url", fake_read)

    result = normalize_media_page_url("https://www.xnxx.com/search/sex?top&id=25752905")

    assert result == "https://www.xnxx.com/video-fbz2h36/sexy_ancient_sex_of_chinese_babe"
    assert requested == ["https://www.xnxx.com/search/sex?top&id=25752905"]


def test_xnxx_plain_search_page_requires_a_single_result_id() -> None:
    with pytest.raises(ResolverError, match="单个视频页面"):
        normalize_media_page_url("https://www.xnxx.com/search/sex?top")


def test_xnxx_video_page_is_left_unchanged() -> None:
    url = "https://www.xnxx.com/video-fbz2h36/sexy_ancient_sex_of_chinese_babe"
    assert normalize_media_page_url(url) == url


def test_sxyprn_public_player_path_matches_site_script() -> None:
    raw = "/cdn/c10/r43m8tzv2r0e7pzi1r3n6vz11h7o9/token/1787928869/bk6o91cuabbc3j4w6c7me9cr421/file123.vid"

    result = _sxyprn_media_path(raw)

    assert result.startswith("/cdn8/")
    assert "/c10/r43m8tzv2r0e7pzi1r3n6vz11h7o9/token/" in result
    assert result.endswith("/bk6o91cuabbc3j4w6c7me9cr421/file123.vid")


def test_hqporner_public_embed_builds_quality_and_first_frame(monkeypatch) -> None:
    page = b"""<html><head><title>Public sample - HQporner.com</title></head>
    <body><iframe src='//mydaddy.cc/video/abcdef123456/'></iframe></body></html>"""
    embed = b"""<video><source src='//s1.bigcdn.cc/pubs/example/360.mp4'>
    <source src='//s1.bigcdn.cc/pubs/example/720.mp4'></video>"""

    monkeypatch.setattr(
        resolver_module,
        "_read_allowed_url",
        lambda url, *_args, **_kwargs: embed if "mydaddy.cc" in url else page,
    )
    media = _resolve_hqporner("https://hqporner.com/hdporn/123-public.html")

    assert media.title == "Public sample"
    assert [item.label for item in media.formats] == ["720P", "360P", "视频首帧"]
    assert media.formats[-1].selector == "streamnest-frame:best"
    assert media.formats[-1].source_url == media.formats[0].source_url


@pytest.mark.parametrize(
    "url",
    [
        "https://hqporner.com/?ref=porndude",
        "https://hqporner.com/category/teen",
    ],
)
def test_hqporner_home_or_listing_requires_single_video_page(monkeypatch, url: str) -> None:
    monkeypatch.setattr(
        resolver_module,
        "_read_allowed_url",
        lambda *_args, **_kwargs: pytest.fail("首页或列表页不应发起远程媒体解析"),
    )

    with pytest.raises(ResolverError, match="首页或列表页"):
        _resolve_hqporner(url)


def test_beeg_normalizes_public_id_and_builds_h264_formats(monkeypatch) -> None:
    api_payload = {
        "file": {
            "data": {"cd_value": "Public Beeg sample"},
            "fl_duration": 62,
            "hls_resources": {"fl_cdn_multi": "signed/_TPL_/581073045853882.mp4.m3u8"},
        },
        "fc_facts": [{"fc_thumbs": [10, 25]}],
    }
    playlist = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=426x240,CODECS="avc1.640015,mp4a.40.2"
//ip1.video.beeg.com/signed/240/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1200000,RESOLUTION=1280x720,CODECS="avc1.640020,mp4a.40.2"
//ip2.video.beeg.com/signed/720/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=854x480,CODECS="av01.0.04M.08,mp4a.40.2"
//ip3.video.beeg.com/signed/av1/index.m3u8
"""
    requested: list[str] = []

    def fake_read(url: str, *_args, **_kwargs) -> bytes:
        requested.append(url)
        return playlist.encode() if "video.beeg.com" in url else json.dumps(api_payload).encode()

    monkeypatch.setattr(resolver_module, "_read_allowed_url", fake_read)
    media = _resolve_beeg("https://beeg.com/-0581073045853882")

    assert requested[0].endswith("/facts/file/581073045853882")
    assert media.title == "Public Beeg sample"
    assert media.duration == "01:02"
    assert [item.label for item in media.formats] == ["720P", "240P", "封面原图"]
    assert media.formats[1].id == "video-fast"
    assert media.formats[0].source_url == "https://ip2.video.beeg.com/signed/720/index.m3u8"
    assert "推荐" in media.formats[0].detail
    assert media.thumbnail == "https://thumbs.externulls.com/videos/581073045853882/25.webp?size=480x270"


def test_sxyprn_public_page_builds_video_and_cover(monkeypatch) -> None:
    page = b"""<html><head><title>Public SxyPrn sample</title>
    <meta itemprop='duration' content='PT1M2S'><meta itemprop='thumbnailUrl'
    content='https://b2.trafficdeposit.com/poster.jpg'></head><body>
    <video data-postid='abcdef123456' data-mgfs='100'></video>
    <span class='vidsnfo' data-vnfo='{"abcdef123456":"/cdn/c10/token123/token456/1787929000/folder123/file456.vid"}'></span>
    <div>Video Info -&gt; resolution:<b>SD</b>480</div></body></html>"""
    monkeypatch.setattr(resolver_module, "_read_allowed_url", lambda *_args, **_kwargs: page)

    media = _resolve_sxyprn("https://sxyprn.com/post/abcdef123456.html")

    assert media.duration == "01:02"
    assert media.formats[0].label == "480P"
    assert media.formats[0].source_url.startswith("https://sxyprn.com/cdn8/")
    assert media.formats[1].source_url == "https://b2.trafficdeposit.com/poster.jpg"


def test_sxyprn_removed_post_has_actionable_error(monkeypatch) -> None:
    monkeypatch.setattr(
        resolver_module,
        "_read_allowed_url",
        lambda *_args, **_kwargs: b"<html><title>Post Not Found [abcdef123] - SexyPorn</title></html>",
    )

    with pytest.raises(ResolverError, match="已删除或链接已经失效"):
        _resolve_sxyprn("https://sxyprn.com/post/abcdef123.html")


def test_unexpected_download_error_removes_temporary_directory(monkeypatch, tmp_path) -> None:
    directory = tmp_path / "unexpected-download"
    directory.mkdir()
    monkeypatch.setattr(resolver_module.tempfile, "mkdtemp", lambda **_kwargs: str(directory))
    monkeypatch.setattr(resolver_module, "_download_direct_public_mp4", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        resolver_module,
        "_extract_info",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unexpected")),
    )

    with pytest.raises(ResolverError, match="下载过程中出现临时错误"):
        download_media(
            DownloadClaim(
                url="https://beeg.com/-123456",
                selector="best",
                kind="快速",
                title="Public sample",
                adult_confirmed=True,
                source_url="https://video.beeg.com/public/index.m3u8",
            )
        )

    assert not directory.exists()


def test_expired_public_source_refreshes_only_same_quality_and_discards_old_part(monkeypatch, tmp_path) -> None:
    old_url = "https://vod-progressive-ak.vimeocdn.com/video/old.mp4"
    new_url = "https://vod-progressive-ak.vimeocdn.com/video/new.mp4"
    attempts: list[tuple[str, Path]] = []
    progress_events: list[resolver_module.DownloadProgress] = []

    def fake_extract(url, options, *, download):
        assert download is True
        directory = Path(options["outtmpl"]).parent
        attempts.append((url, directory))
        assert options["skip_unavailable_fragments"] is False
        assert options["fragment_retries"] == 5
        if len(attempts) == 1:
            (directory / "video.part").write_bytes(b"partial-old-source")
            raise DownloadError("HTTP Error 403: signed URL expired")
        (directory / "complete.mp4").write_bytes(b"complete-new-source")
        return {}

    monkeypatch.setattr(resolver_module, "_extract_info", fake_extract)
    monkeypatch.setattr(resolver_module, "_download_direct_public_mp4", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        resolver_module,
        "_resolve_vimeo",
        lambda _url: resolver_module.ResolvedMedia(
            title="Public sample",
            author="Vimeo",
            duration="00:10",
            duration_seconds=10,
            thumbnail=None,
            formats=(ResolvedFormat("vimeo-progressive-1080", "1080P", "MP4", "未知大小", "高清", "best", new_url),),
        ),
    )
    claim = DownloadClaim(
        url="https://vimeo.com/123456",
        selector="best",
        kind="高清",
        title="Public sample",
        adult_confirmed=False,
        source_url=old_url,
        format_id="vimeo-progressive-1080",
        format_label="1080P",
    )

    downloaded = download_media(claim, progress_events.append)

    assert [url for url, _directory in attempts] == [old_url, new_url]
    assert attempts[0][1] != attempts[1][1]
    assert not attempts[0][1].exists()
    assert [event.status for event in progress_events] == ["retrying"]
    assert downloaded.path.read_bytes() == b"complete-new-source"


def test_public_source_refresh_never_substitutes_lower_quality(monkeypatch) -> None:
    old_url = "https://vod-progressive-ak.vimeocdn.com/video/old.mp4"
    calls = 0

    def expired(_url, _options, *, download):
        nonlocal calls
        calls += 1
        raise DownloadError("HTTP Error 403: expired")

    monkeypatch.setattr(resolver_module, "_extract_info", expired)
    monkeypatch.setattr(resolver_module, "_download_direct_public_mp4", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        resolver_module,
        "_resolve_vimeo",
        lambda _url: resolver_module.ResolvedMedia(
            title="Public sample",
            author="Vimeo",
            duration="00:10",
            duration_seconds=10,
            thumbnail=None,
            formats=(ResolvedFormat("vimeo-progressive-1080", "720P", "MP4", "未知大小", "高清", "best", old_url),),
        ),
    )

    with pytest.raises(ResolverError, match="所选画质下载失败"):
        download_media(DownloadClaim(
            url="https://vimeo.com/123456",
            selector="best",
            kind="高清",
            title="Public sample",
            adult_confirmed=False,
            source_url=old_url,
            format_id="vimeo-progressive-1080",
            format_label="1080P",
        ))
    assert calls == 1


def test_xmovies_public_api_builds_hls_and_cover(monkeypatch) -> None:
    page = b"""<html><head><meta property='og:title' content='Public XMovies sample'>
    <meta property='og:image' content='https://xmoviescdn.online/poster.webp'></head><body><script>
    const videoId = "123456789"; const target = "dedi_1";
    fetch(`/api/stream/${videoId}?target=${target}`);
    </script></body></html>"""
    payload = b'{"url":"https://node1.eternalslumber.online/hls/vid_123456789/index.m3u8"}'

    monkeypatch.setattr(
        resolver_module,
        "_read_allowed_url",
        lambda url, *_args, **_kwargs: payload if "/api/stream/" in url else page,
    )
    media = _resolve_xmoviesforyou("https://xmoviesforyou.com/public-sample")

    assert media.title == "Public XMovies sample"
    assert media.formats[0].source_url.endswith("/hls/vid_123456789/index.m3u8")
    assert media.formats[1].source_url == "https://xmoviescdn.online/poster.webp"


def test_hqporner_direct_mp4_is_saved_without_generic_extractor(monkeypatch, tmp_path) -> None:
    payload = b"\x00\x00\x00\x18ftypmp42" + b"public-video"
    captured: dict[str, str | None] = {}

    class Headers(dict):
        def get_content_type(self):
            return "video/mp4"

    class FakeResponse:
        def __init__(self):
            self.buffer = BytesIO(payload)
            self.headers = Headers({"Content-Length": str(len(payload))})

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size=-1):
            return self.buffer.read(size)

        def geturl(self):
            return "https://s1.bigcdn.cc/pubs/example/360.mp4"

    class FakeOpener:
        def open(self, request, **_kwargs):
            captured["range"] = request.get_header("Range")
            return FakeResponse()

    directory = tmp_path / "direct"
    directory.mkdir()
    monkeypatch.setattr("streamnest_api.resolver.tempfile.mkdtemp", lambda **_kwargs: str(directory))
    monkeypatch.setattr("streamnest_api.resolver.build_opener", lambda *_args: FakeOpener())

    downloaded = _download_direct_public_mp4(
        DownloadClaim(
            url="https://hqporner.com/hdporn/123-public.html",
            selector="best",
            kind="快速",
            title="Public sample",
            adult_confirmed=True,
            source_url="https://s1.bigcdn.cc/pubs/example/360.mp4",
        )
    )

    assert downloaded is not None
    assert downloaded.media_type == "video/mp4"
    assert downloaded.path.read_bytes() == payload
    assert captured["range"] == "bytes=0-1048575"


def test_direct_media_rejects_more_bytes_than_declared_range(monkeypatch, tmp_path) -> None:
    class Headers(dict):
        def get_content_type(self):
            return "video/mp4"

    class FakeResponse:
        status = 206
        headers = Headers({"Content-Range": "bytes 0-3/4"})

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            if not hasattr(self, "sent"):
                self.sent = True
                return b"12345"
            return b""

        def geturl(self):
            return "https://s1.bigcdn.cc/pubs/example/360.mp4"

    class FakeOpener:
        def open(self, _request, **_kwargs):
            return FakeResponse()

    directory = tmp_path / "overlong-range"
    directory.mkdir()
    monkeypatch.setattr(resolver_module.tempfile, "mkdtemp", lambda **_kwargs: str(directory))
    monkeypatch.setattr(resolver_module, "build_opener", lambda *_args: FakeOpener())

    with pytest.raises(ResolverError, match="分段长度无效"):
        _download_direct_public_mp4(DownloadClaim(
            url="https://hqporner.com/hdporn/123-public.html",
            selector="best",
            kind="高清",
            title="Public sample",
            adult_confirmed=True,
            source_url="https://s1.bigcdn.cc/pubs/example/360.mp4",
        ))
    assert not directory.exists()


def test_hqporner_expired_range_keeps_downloaded_bytes_after_public_refresh(monkeypatch, tmp_path) -> None:
    old_url = "https://s1.bigcdn.cc/pubs/example/1080.mp4"
    new_url = "https://s2.bigcdn.cc/pubs/example/1080.mp4"
    requests: list[tuple[str, str | None]] = []

    class Headers(dict):
        def get_content_type(self):
            return "video/mp4"

    class FakeResponse:
        status = 206

        def __init__(self, url: str, start: int, payload: bytes):
            self.url = url
            self.buffer = BytesIO(payload)
            self.headers = Headers({"Content-Range": f"bytes {start}-{start + len(payload) - 1}/8"})

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size=-1):
            return self.buffer.read(size)

        def geturl(self):
            return self.url

    class FakeOpener:
        def open(self, request, **_kwargs):
            media_url = request.full_url
            byte_range = request.get_header("Range")
            requests.append((media_url, byte_range))
            if media_url == old_url and byte_range == "bytes=0-1048575":
                return FakeResponse(old_url, 0, b"ftyp")
            if media_url == old_url:
                raise HTTPError(media_url, 403, "expired", {}, None)
            return FakeResponse(new_url, 4, b"test")

    directory = tmp_path / "direct-resume"
    directory.mkdir()
    monkeypatch.setattr("streamnest_api.resolver.tempfile.mkdtemp", lambda **_kwargs: str(directory))
    monkeypatch.setattr("streamnest_api.resolver.build_opener", lambda *_args: FakeOpener())
    monkeypatch.setattr(
        resolver_module,
        "_refresh_direct_public_claim",
        lambda claim: resolver_module.replace(claim, source_url=new_url),
    )

    downloaded = _download_direct_public_mp4(
        DownloadClaim(
            url="https://hqporner.com/hdporn/123-public.html",
            selector="best",
            kind="高清",
            title="Public sample",
            adult_confirmed=True,
            source_url=old_url,
        )
    )

    assert downloaded is not None
    assert downloaded.path.read_bytes() == b"ftyptest"
    assert requests == [
        (old_url, "bytes=0-1048575"),
        (old_url, "bytes=4-1048579"),
        (new_url, "bytes=4-1048579"),
    ]


def test_hqporner_expired_direct_url_refreshes_once(monkeypatch) -> None:
    calls: list[str | None] = []
    sentinel = object()

    def fake_direct(claim, _progress=None):
        calls.append(claim.source_url)
        if len(calls) == 1:
            raise ResolverError("平台公开媒体地址已过期，请重新解析后再试")
        return sentinel

    refreshed_format = ResolvedFormat(
        id="hqporner-360",
        label="360P",
        detail="MP4",
        size="未知大小",
        kind="快速",
        selector="best",
        source_url="https://s2.bigcdn.cc/pubs/new/360.mp4",
    )
    monkeypatch.setattr(resolver_module, "_download_direct_public_mp4", fake_direct)
    monkeypatch.setattr(
        resolver_module,
        "_resolve_hqporner",
        lambda _url: resolver_module.ResolvedMedia(
            title="Public sample",
            author="HQPorner",
            duration="--:--",
            duration_seconds=None,
            thumbnail=None,
            formats=(refreshed_format,),
        ),
    )

    result = download_media(
        DownloadClaim(
            url="https://hqporner.com/hdporn/123-public.html",
            selector="best",
            kind="快速",
            title="Public sample",
            adult_confirmed=True,
            source_url="https://s1.bigcdn.cc/pubs/old/360.mp4",
        )
    )

    assert result is sentinel
    assert calls == [
        "https://s1.bigcdn.cc/pubs/old/360.mp4",
        "https://s2.bigcdn.cc/pubs/new/360.mp4",
    ]


def test_sxyprn_public_refresh_retries_one_transient_page_failure(monkeypatch) -> None:
    source = "https://sxyprn.com/cdn8/token/c1/a/b/1234567890/c/d.vid"
    calls = 0

    def fake_resolve(_url):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ResolverError("平台公开页面暂时无法访问")
        return resolver_module.ResolvedMedia(
            title="Public sample",
            author="SxyPrn",
            duration="00:10",
            duration_seconds=10,
            thumbnail=None,
            formats=(ResolvedFormat("sxyprn-public", "720P", "MP4", "未知大小", "快速", "best", source),),
        )

    monkeypatch.setattr(resolver_module, "_resolve_sxyprn", fake_resolve)
    monkeypatch.setattr(resolver_module.time, "sleep", lambda _seconds: None)

    refreshed = resolver_module._refresh_direct_public_claim(
        DownloadClaim(
            url="https://sxyprn.com/post/6ab7d17a66c8d.html",
            selector="best",
            kind="快速",
            title="Public sample",
            adult_confirmed=True,
            source_url=source,
        )
    )

    assert calls == 2
    assert refreshed is not None and refreshed.source_url == source


def test_first_frame_format_routes_to_video_frame(monkeypatch) -> None:
    frame = build_first_frame_format(
        (
            ResolvedFormat(
                id="video-fast",
                label="360P",
                detail="MP4",
                size="未知大小",
                kind="快速",
                selector="best",
                source_url="https://s1.bigcdn.cc/pubs/example/360.mp4",
            ),
        )
    )
    assert frame is not None
    sentinel = object()
    captured: dict[str, str] = {}

    def fake_frame(claim, _progress=None):
        captured["selector"] = claim.selector.removeprefix("streamnest-frame:")
        return sentinel

    monkeypatch.setattr(resolver_module, "download_video_frame", fake_frame)
    monkeypatch.setattr(
        resolver_module,
        "download_image",
        lambda *_args, **_kwargs: pytest.fail("首帧格式不应按封面图片下载"),
    )

    result = download_media(
        DownloadClaim(
            url="https://hqporner.com/hdporn/123-public.html",
            selector=frame.selector,
            kind=frame.kind,
            title="Public sample",
            adult_confirmed=True,
            source_url=frame.source_url,
        )
    )

    assert result is sentinel
    assert captured["selector"] == "best"


def test_eporner_retries_transient_metadata_failures(monkeypatch) -> None:
    calls = 0
    sleeps: list[float] = []

    class FakeYoutubeDL:
        def __init__(self, options) -> None:
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def extract_info(self, url, *, download):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise DownloadError("HTTP Error 502: Bad Gateway")
            return {"id": "public-test", "url": url, "download": download}

    monkeypatch.setattr(resolver_module.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(resolver_module.time, "sleep", sleeps.append)

    result = _extract_info(
        "https://www.eporner.com/video-public-test/",
        {"quiet": True},
        download=False,
    )

    assert result == {
        "id": "public-test",
        "url": "https://www.eporner.com/video-public-test/",
        "download": False,
    }
    assert calls == 3
    assert sleeps == [2.0, 4.0]


def test_other_platform_retries_only_transient_extract_failures(monkeypatch) -> None:
    calls = 0
    sleeps: list[float] = []

    class FakeYoutubeDL:
        def __init__(self, _options) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def extract_info(self, _url, *, download):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise DownloadError("HTTP Error 503: Service Unavailable")
            return {"id": "public-video", "download": download}

    monkeypatch.setattr(resolver_module.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(resolver_module.time, "sleep", sleeps.append)
    assert _extract_info("https://www.youtube.com/watch?v=public", {}, download=False) == {
        "id": "public-video", "download": False,
    }
    assert calls == 2
    assert sleeps == [0.75]

    calls = 0
    def denied(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise DownloadError("HTTP Error 403: Forbidden")

    monkeypatch.setattr(FakeYoutubeDL, "extract_info", denied)
    with pytest.raises(DownloadError, match="403"):
        _extract_info("https://www.youtube.com/watch?v=public", {}, download=False)
    assert calls == 1
    assert sleeps == [0.75]


def test_direct_range_size_grows_only_on_responsive_complete_ranges() -> None:
    one_mib = 1024 * 1024
    choose = resolver_module._next_direct_range_size
    assert choose(one_mib, 2, one_mib) == 2 * one_mib
    assert choose(2 * one_mib, 2, 2 * one_mib) == 4 * one_mib
    assert choose(4 * one_mib, 2, 4 * one_mib) == 4 * one_mib
    assert choose(4 * one_mib, 20, 4 * one_mib) == 2 * one_mib
    assert choose(one_mib, 20, one_mib) == one_mib
    assert choose(one_mib, 1, 10) == one_mib


def test_instagram_login_only_response_has_actionable_error() -> None:
    error = _resolver_error_from_download(
        "https://www.instagram.com/reel/test/",
        DownloadError("Instagram sent an empty media response. use --cookies"),
    )

    assert isinstance(error, AuthenticationRequiredError)
    assert error.code == "authentication_required"
    assert error.status_code == 403
    assert "未登录" in str(error)


def test_douyin_guest_session_response_has_actionable_error() -> None:
    error = _resolver_error_from_download(
        "https://v.douyin.com/test/",
        DownloadError("Fresh cookies (not necessarily logged in) are needed; s_v_web_id missing"),
    )

    assert isinstance(error, VisitorSessionRequiredError)
    assert error.code == "visitor_session_required"
    assert error.status_code == 403
    assert "访客会话" in str(error)


def test_spankbang_cloudflare_response_has_actionable_error() -> None:
    error = _resolver_error_from_download(
        "https://spankbang.com/98t61/video/porn",
        DownloadError("Unable to download webpage: HTTP Error 403: Forbidden (Cloudflare challenge)"),
    )

    assert isinstance(error, VisitorSessionRequiredError)
    assert error.code == "visitor_session_required"
    assert error.status_code == 403
    assert "不是链接格式错误" in str(error)
    assert "不会绕过验证" in str(error)


@pytest.mark.parametrize(
    ("url", "message", "expected_type", "expected_text"),
    [
        (
            "https://vimeo.com/123",
            "The web client only works when logged-in. Use --cookies",
            AuthenticationRequiredError,
            "不会读取账号 Cookie",
        ),
        (
            "https://www.ixigua.com/123",
            "Cookies (not necessarily logged in) are needed",
            VisitorSessionRequiredError,
            "访客会话",
        ),
        (
            "https://www.xiaohongshu.com/explore/123",
            "No video formats found!",
            AuthenticationRequiredError,
            "未登录访问",
        ),
    ],
)
def test_restricted_platforms_have_actionable_errors(
    url: str,
    message: str,
    expected_type: type[ResolverError],
    expected_text: str,
) -> None:
    error = _resolver_error_from_download(url, DownloadError(message))

    assert isinstance(error, expected_type)
    assert expected_text in str(error)


def test_ted_fallback_reads_official_next_data() -> None:
    media = _ted_media_from_html(
        """<html><script id="__NEXT_DATA__" type="application/json">{
          "props": {"pageProps": {"videoData": {
            "title": "Test talk",
            "videoPlayerData": {
              "title": "Test talk",
              "speaker": "Test speaker",
              "duration": 83,
              "thumb": "https://pi.tedcdn.com/test.jpg",
              "resources": {"h264": [{
                "bitrate": 1200,
                "height": 480,
                "file": "https://py.tedcdn.com/test-video.mp4"
              }]}
            }
          }}}
        }</script></html>"""
    )

    assert media.title == "Test talk"
    assert media.author == "Test speaker"
    assert media.duration == "01:23"
    assert media.formats[0].label == "480P"
    assert media.formats[0].source_url == "https://py.tedcdn.com/test-video.mp4"


def test_ted_prefers_highest_public_bitrate_without_inventing_resolution() -> None:
    media = _ted_media_from_html(
        """<script id="__NEXT_DATA__" type="application/json">{
          "props": {"pageProps": {"videoData": {"videoPlayerData": {
            "resources": {"h264": [
              {"bitrate": 1200, "file": "https://py.tedcdn.com/low.mp4"},
              {"bitrate": 4500, "file": "https://py.tedcdn.com/high.mp4"}
            ]}
          }}}}
        }</script>"""
    )
    assert [item.label for item in media.formats] == ["4500 kbps", "1200 kbps"]
    assert media.formats[0].source_url == "https://py.tedcdn.com/high.mp4"


def test_ted_prefers_higher_resolution_before_bitrate() -> None:
    media = _ted_media_from_html(
        """<script id="__NEXT_DATA__" type="application/json">{
          "props": {"pageProps": {"videoData": {"videoPlayerData": {
            "resources": {"h264": [
              {"height": 720, "bitrate": 3000, "file": "https://py.tedcdn.com/720.mp4"},
              {"height": 1080, "bitrate": 2500, "file": "https://py.tedcdn.com/1080.mp4"}
            ]}
          }}}}
        }</script>"""
    )
    assert media.formats[0].label == "1080P"
    assert media.formats[0].source_url == "https://py.tedcdn.com/1080.mp4"


def test_direct_media_sources_are_limited_to_ted_cdn() -> None:
    assert _trusted_source_url("https://py.tedcdn.com/video.mp4")
    assert _trusted_source_url("https://vod-progressive-ak.vimeocdn.com/video/file.mp4")
    assert not _trusted_source_url("https://tedcdn.com.evil.example/video.mp4")
    assert not _trusted_source_url("https://vimeocdn.com.evil.example/video/master.m3u8")
    assert not _trusted_source_url("http://py.tedcdn.com/video.mp4")


def test_vimeo_public_player_builds_progressive_quality_and_image_options() -> None:
    media = _vimeo_media_from_config(
        "https://vimeo.com/76979871",
        {
            "video": {
                "title": "Public Vimeo video",
                "duration": 62,
                "thumbnail_url": "https://i.vimeocdn.com/video/test_1280x720.jpg",
                "owner": {"name": "Vimeo"},
            },
            "request": {
                "files": {
                    "progressive": [
                        {
                            "url": "https://vod-progressive-ak.vimeocdn.com/video/file.mp4",
                            "height": 540,
                            "mime": "video/mp4",
                        }
                    ],
                },
            },
        },
    )

    assert media.title == "Public Vimeo video"
    assert media.duration == "01:02"
    assert media.formats[0].label == "540P"
    assert media.formats[0].selector == "best"
    assert media.formats[1].kind == "图片"


def test_vimeo_public_player_orders_available_quality_highest_first() -> None:
    media = _vimeo_media_from_config(
        "https://vimeo.com/76979871",
        {
            "video": {"title": "Public Vimeo video", "duration": 62},
            "request": {"files": {"progressive": [
                {"url": "https://vod-progressive-ak.vimeocdn.com/video/low.mp4", "height": 360, "mime": "video/mp4"},
                {"url": "https://vod-progressive-ak.vimeocdn.com/video/high.mp4", "height": 1080, "mime": "video/mp4"},
            ]}},
        },
    )
    assert [item.label for item in media.formats[:2]] == ["1080P", "360P"]
    assert "推荐" in media.formats[0].detail


def test_vimeo_uses_higher_bitrate_when_resolution_matches() -> None:
    media = _vimeo_media_from_config(
        "https://vimeo.com/76979871",
        {
            "video": {"title": "Public Vimeo video"},
            "request": {"files": {"progressive": [
                {"url": "https://vod-progressive-ak.vimeocdn.com/video/high.mp4",
                 "height": 1080, "bitrate": 4000, "mime": "video/mp4"},
                {"url": "https://vod-progressive-ak.vimeocdn.com/video/low.mp4",
                 "height": 1080, "bitrate": 1200, "mime": "video/mp4"},
            ]}},
        },
    )
    assert media.formats[0].source_url == "https://vod-progressive-ak.vimeocdn.com/video/high.mp4"


def test_vimeo_rejects_config_without_public_progressive_mp4() -> None:
    with pytest.raises(ProtectedMediaError, match="没有公开 MP4"):
        _vimeo_media_from_config(
            "https://vimeo.com/76979871",
            {
                "video": {"title": "Protected Vimeo video", "duration": 62},
                "request": {"files": {"progressive": []}},
            },
        )


def test_thumbnail_format_only_accepts_platform_image_hosts() -> None:
    image = build_thumbnail_format(
        "https://www.youtube.com/watch?v=test",
        "https://i.ytimg.com/vi/test/maxresdefault.jpg",
    )

    assert image is not None
    assert image.kind == "图片"
    assert image.source_url == "https://i.ytimg.com/vi/test/maxresdefault.jpg"
    upgraded = build_thumbnail_format(
        "https://www.bilibili.com/video/BVtest",
        "http://i1.hdslb.com/bfs/archive/test.jpg",
    )
    assert upgraded is not None
    assert upgraded.source_url.startswith("https://i1.hdslb.com/")
    assert build_thumbnail_format(
        "https://www.youtube.com/watch?v=test",
        "https://ytimg.com.evil.example/cover.jpg",
    ) is None


@pytest.mark.parametrize(
    ("page_url", "image_url"),
    [
        ("https://www.dailymotion.com/video/test", "https://s2.dmcdn.net/v/test/x1080"),
        ("https://www.tiktok.com/@test/video/1", "https://p16-common-sign.tiktokcdn.com/test.image"),
        ("https://www.instagram.com/reel/test/", "https://scontent-nrt.cdninstagram.com/test.jpg"),
        ("https://www.pinterest.com/pin/123/", "https://i.pinimg.com/originals/test.jpg"),
        ("https://clips.twitch.tv/test", "https://static-cdn.jtvnw.net/test.jpg"),
        ("https://soundcloud.com/test/audio", "https://i1.sndcdn.com/artworks-test-original.jpg"),
        ("https://www.eporner.com/video-test/example/", "https://static-sg-cdn.eporner.com/thumbs/test.jpg"),
        ("https://www.xnxx.com/video-test/example", "https://thumb-cdn77.xnxx-cdn.com/test.jpg"),
        ("https://rule34video.com/video/123/example/", "https://rule34video.com/contents/preview.jpg"),
        ("https://sxyprn.com/post/abcdef.html", "https://b2.trafficdeposit.com/poster.jpg"),
        ("https://xmoviesforyou.com/example", "https://xmoviescdn.online/poster.webp"),
        ("https://www.xiaohongshu.com/explore/67f75b07000000000b0154b3", "https://sns-i11.rednotecdn.com/poster.webp"),
        ("https://hongguoduanju.com/player/7684649550316833817", "https://p3-novel.byteimg.com/novel-pic/poster.image"),
    ],
)
def test_more_public_platform_thumbnail_hosts(page_url: str, image_url: str) -> None:
    image = build_thumbnail_format(page_url, image_url)

    assert image is not None
    assert image.source_url == image_url


def test_xiaohongshu_public_gallery_keeps_order_and_rejects_external_images(monkeypatch) -> None:
    note_id = "67f75b07000000000b0154b3"
    page = f"https://www.xiaohongshu.com/discovery/item/{note_id}"
    state = {
        "note": {"noteDetailMap": {note_id: {"note": {
            "type": "normal", "title": "公开图文", "user": {"nickname": "作者"},
            "imageList": [
                {"urlDefault": "https://sns-i11.rednotecdn.com/first.webp"},
                {"urlDefault": "https://evil.example/blocked", "urlPre": "https://sns-webpic-qc.xhscdn.com/second.jpg"},
                {"urlDefault": "https://sns-i11.rednotecdn.com/third.webp"},
            ],
        }}}}
    }
    monkeypatch.setattr(
        resolver_module,
        "_read_allowed_url",
        lambda *_args, **_kwargs: (
            '<script>window.__INITIAL_STATE__=' + json.dumps(state) + '</script>'
        ).encode(),
    )
    monkeypatch.setattr(
        resolver_module,
        "_extract_info",
        lambda *_args, **_kwargs: {"webpage_url": page, "formats": [], "title": "fallback"},
    )

    media = resolver_module.resolve_media(page, adult_confirmed=False)

    assert media.title == "公开图文"
    assert media.author == "作者"
    assert [format.id for format in media.formats] == ["image-all", "image-1", "image-2", "image-3"]
    assert media.formats[0].source_urls == tuple(format.source_url for format in media.formats[1:])
    assert media.formats[2].source_url == "https://sns-webpic-qc.xhscdn.com/second.jpg"


def test_xiaohongshu_unavailable_video_does_not_masquerade_as_gallery(monkeypatch) -> None:
    note_id = "67f75b07000000000b0154b3"
    page = f"https://www.xiaohongshu.com/explore/{note_id}"
    state = {"note": {"noteDetailMap": {note_id: {"note": {
        "type": "video", "imageList": [{"urlDefault": "https://sns-i11.rednotecdn.com/poster.webp"}],
    }}}}}
    monkeypatch.setattr(
        resolver_module,
        "_read_allowed_url",
        lambda *_args, **_kwargs: (
            '<script>window.__INITIAL_STATE__=' + json.dumps(state) + '</script>'
        ).encode(),
    )
    monkeypatch.setattr(
        resolver_module,
        "_extract_info",
        lambda *_args, **_kwargs: {"webpage_url": page, "formats": []},
    )

    with pytest.raises(AuthenticationRequiredError, match="公开播放流"):
        resolver_module.resolve_media(page, adult_confirmed=False)


def test_xiaohongshu_original_video_without_dimensions_remains_selectable() -> None:
    formats = _safe_formats({
        "extractor_key": "XiaoHongShu",
        "formats": [{
            "format_id": "direct", "url": "https://sns-video-bd.xhscdn.com/original",
            "ext": "mp4", "filesize": 1234567,
        }],
    })

    assert formats[0].id == "video-original"
    assert formats[0].selector == "direct"
    assert "分辨率未标注" in formats[0].detail


def test_hongguo_single_episode_accepts_public_mp4_without_resolution() -> None:
    page = "https://hongguoduanju.com/player/7684649550316833817"
    formats = _safe_formats({
        "webpage_url": page,
        "extractor_key": "Generic",
        "formats": [{
            "format_id": "mp4", "url": "https://v3-hgweb.qznovelvod.com/public-stream",
            "ext": "mp4", "protocol": "https", "vcodec": None,
        }],
    })

    assert formats[0].selector == "mp4"
    assert formats[0].label == "站点公开画质"
    assert "分辨率未标注" in formats[0].detail
    with pytest.raises(ProtectedMediaError):
        _safe_formats({"webpage_url": "https://example.com/video", "formats": [{
            "format_id": "mp4", "url": "https://example.com/video.mp4",
            "ext": "mp4", "protocol": "https",
        }]})


def test_hongguo_requires_a_single_episode_link() -> None:
    with pytest.raises(ResolverError, match="具体单集"):
        normalize_media_page_url("https://hongguoduanju.com/")
    with pytest.raises(ResolverError, match="具体单集"):
        normalize_media_page_url("https://hongguoduanju.com/series/123")
    assert normalize_media_page_url("https://hongguoduanju.com/player/7684649550316833817") == (
        "https://hongguoduanju.com/player/7684649550316833817"
    )


def test_hongguo_resolve_does_not_call_shrunk_cover_original(monkeypatch) -> None:
    page = "https://hongguoduanju.com/player/7684649550316833817"
    monkeypatch.setattr(resolver_module, "_extract_info", lambda *_args, **_kwargs: {
        "webpage_url": page, "extractor_key": "Generic", "title": "短剧第1集",
        "duration": 159, "thumbnail": "https://p3-novel.byteimg.com/poster~tplv-shrink.image",
        "formats": [{"format_id": "mp4", "url": "https://v3-hgweb.qznovelvod.com/public-stream",
                     "ext": "mp4", "protocol": "https"}],
    })

    media = resolver_module.resolve_media(page, adult_confirmed=False)

    assert media.formats[0].id == "video-public"
    assert media.formats[1].label == "封面图片"
    assert "当前提供尺寸" in media.formats[1].detail


def test_image_signatures_and_windows_filename_are_safe() -> None:
    assert _sniff_image_extension(b"\xff\xd8\xff\xe0test") == (".jpg", "image/jpeg")
    assert _sniff_image_extension(b"\x89PNG\r\n\x1a\nrest") == (".png", "image/png")
    assert _sniff_image_extension(b"<html>blocked</html>") is None
    assert _safe_image_stem('a<b>:c"d/e\\f|g?h*') == "a_b_c_d_e_f_g_h"


def test_youtube_image_retry_uses_stable_same_host_url() -> None:
    signed = "https://i.ytimg.com/vi/test/hqdefault.jpg?sqp=signed&rs=temporary"

    assert _image_request_candidates(signed, "YouTube") == (
        signed,
        "https://i.ytimg.com/vi/test/hqdefault.jpg",
    )
    assert _image_request_candidates("https://i.ytimg.com/vi/test/hqdefault.jpg", "YouTube") == (
        "https://i.ytimg.com/vi/test/hqdefault.jpg",
        "https://i.ytimg.com/vi/test/hqdefault.jpg",
    )


def test_non_ascii_certificate_path_gets_ascii_runtime_copy(monkeypatch, tmp_path) -> None:
    source_dir = tmp_path / "下载"
    source_dir.mkdir()
    source = source_dir / "cacert.pem"
    source.write_bytes(b"test certificate bundle")
    ascii_dir = tmp_path / "ascii"
    ascii_dir.mkdir()
    monkeypatch.setattr("streamnest_api.resolver.certifi.where", lambda: str(source))
    monkeypatch.setattr("streamnest_api.resolver.tempfile.gettempdir", lambda: str(ascii_dir))
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)

    target = _configure_ascii_ca_bundle()

    assert target == ascii_dir / "streamnest-cacert.pem"
    assert target.read_bytes() == source.read_bytes()
    assert os.environ["CURL_CA_BUNDLE"] == str(target)


def test_first_frame_fallback_reuses_fast_video_selector() -> None:
    frame = build_first_frame_format(
        (
            ResolvedFormat(
                id="video-fast",
                label="480P",
                detail="MP4",
                size="1 MB",
                kind="快速",
                selector="18",
            ),
        )
    )

    assert frame is not None
    assert frame.label == "视频首帧"
    assert frame.selector == "streamnest-frame:18"
    assert frame.source_url is None


def test_download_image_saves_verified_raster_bytes(monkeypatch, tmp_path) -> None:
    payload = b"\x89PNG\r\n\x1a\n" + b"authorized-public-cover"

    class FakeResponse:
        def __init__(self):
            self.buffer = BytesIO(payload)
            self.headers = {"Content-Length": str(len(payload))}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size=-1):
            return self.buffer.read(size)

        def geturl(self):
            return "https://i.ytimg.com/vi/test/maxresdefault.jpg"

    class FakeOpener:
        def open(self, *_args, **_kwargs):
            return FakeResponse()

    directory = tmp_path / "image-success"
    def fake_mkdtemp(**_kwargs):
        directory.mkdir()
        return str(directory)

    monkeypatch.setattr("streamnest_api.resolver.tempfile.mkdtemp", fake_mkdtemp)
    monkeypatch.setattr("streamnest_api.resolver.build_opener", lambda *_args: FakeOpener())
    downloaded = download_image(
        DownloadClaim(
            url="https://www.youtube.com/watch?v=test",
            selector="image-cover",
            kind="图片",
            title="Test: cover",
            adult_confirmed=False,
            source_url="https://i.ytimg.com/vi/test/maxresdefault.jpg",
        )
    )

    assert downloaded.media_type == "image/png"
    assert downloaded.path.suffix == ".png"
    assert downloaded.path.read_bytes() == payload


def test_download_image_cleans_temporary_directory_after_network_error(monkeypatch, tmp_path) -> None:
    directory = tmp_path / "image-job"

    class FailingOpener:
        def open(self, *_args, **_kwargs):
            raise URLError("temporary failure")

    monkeypatch.setattr("streamnest_api.resolver.tempfile.mkdtemp", lambda **_kwargs: str(directory))
    monkeypatch.setattr("streamnest_api.resolver.build_opener", lambda *_args: FailingOpener())

    with pytest.raises(ResolverError, match="封面图片请求"):
        download_image(
            DownloadClaim(
                url="https://www.youtube.com/watch?v=test",
                selector="image-cover",
                kind="图片",
                title="Test cover",
                adult_confirmed=False,
                source_url="https://i.ytimg.com/vi/test/maxresdefault.jpg",
            )
        )

    assert not directory.exists()


def test_download_image_collection_creates_one_zip_with_every_image(monkeypatch, tmp_path) -> None:
    collection_dir = tmp_path / "collection"
    collection_dir.mkdir()
    calls = 0

    def fake_download_image(claim, progress_callback=None):
        nonlocal calls
        calls += 1
        item_dir = tmp_path / f"item-{calls}"
        item_dir.mkdir()
        item_path = item_dir / f"image-{calls}.jpg"
        item_path.write_bytes(b"\xff\xd8\xff" + bytes([calls]))
        return type("Downloaded", (), {"directory": item_dir, "path": item_path, "media_type": "image/jpeg"})()

    monkeypatch.setattr("streamnest_api.resolver.tempfile.mkdtemp", lambda **_kwargs: str(collection_dir))
    monkeypatch.setattr("streamnest_api.resolver.download_image", fake_download_image)
    progress = []
    downloaded = download_image_collection(
        DownloadClaim(
            url="https://www.douyin.com/note/7546907495592168714",
            selector="image-all",
            kind="图片合集",
            title="Dog wallpapers",
            adult_confirmed=False,
            source_urls=(
                "https://p3-pc-sign.douyinpic.com/image-1.jpeg",
                "https://p3-pc-sign.douyinpic.com/image-2.jpeg",
                "https://p3-pc-sign.douyinpic.com/image-3.jpeg",
            ),
        ),
        progress.append,
    )

    assert downloaded.media_type == "application/zip"
    assert downloaded.path.name.endswith("全部3张图片.zip")
    with zipfile.ZipFile(downloaded.path) as archive:
        assert archive.namelist() == ["01.jpg", "02.jpg", "03.jpg"]
    assert calls == 3
    assert progress[-1].percent == 100


def test_helper_media_is_limited_to_selected_platform_cdn() -> None:
    headers = validate_helper_media_request(
        "https://v3.douyinvod.com/video/example.mp4",
        "抖音",
        {"referer": "https://www.douyin.com/video/example"},
    )
    assert headers["referer"].startswith("https://www.douyin.com/")

    with pytest.raises(ResolverError):
        validate_helper_media_request(
            "https://douyinvod.com.evil.example/video.mp4",
            "抖音",
            {},
        )
    with pytest.raises(ResolverError):
        validate_helper_media_request("http://v3.douyinvod.com/video.mp4", "抖音", {})
    with pytest.raises(ResolverError):
        validate_helper_media_request("https://127.0.0.1/video.mp4", "抖音", {})


def test_helper_media_rejects_truncated_content_length(monkeypatch, tmp_path) -> None:
    class Headers(dict):
        def get_content_type(self):
            return "video/mp4"

    class FakeResponse:
        headers = Headers({"Content-Length": "8"})

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            if not hasattr(self, "sent"):
                self.sent = True
                return b"1234"
            return b""

        def geturl(self):
            return "https://v3.douyinvod.com/video/example.mp4"

    class FakeOpener:
        def open(self, _request, **_kwargs):
            return FakeResponse()

    directory = tmp_path / "helper-truncated"
    directory.mkdir()
    monkeypatch.setattr(resolver_module.tempfile, "mkdtemp", lambda **_kwargs: str(directory))
    monkeypatch.setattr(resolver_module, "build_opener", lambda *_args: FakeOpener())

    with pytest.raises(ResolverError, match="下载不完整"):
        resolver_module.download_helper_media(
            "https://v3.douyinvod.com/video/example.mp4",
            "抖音",
            {"Referer": "https://www.douyin.com/video/example"},
        )
    assert not directory.exists()


def test_helper_headers_drop_credentials_and_untrusted_referers() -> None:
    assert sanitize_helper_headers(
        "抖音",
        {
            "User-Agent": "Test browser",
            "Referer": "https://evil.example/steal",
            "Cookie": "secret=1",
            "Authorization": "Bearer secret",
            "Range": "bytes=0-",
        },
    ) == {"user-agent": "Test browser"}


def test_safe_formats_drops_drm_and_builds_video_and_audio_options() -> None:
    result = _safe_formats(
        {
            "formats": [
                {
                    "format_id": "drm",
                    "url": "https://cdn.example/drm",
                    "height": 2160,
                    "vcodec": "av01",
                    "acodec": "none",
                    "has_drm": True,
                },
                {
                    "format_id": "maybe-drm",
                    "url": "https://cdn.example/maybe-drm",
                    "height": 2160,
                    "vcodec": "av01",
                    "acodec": "none",
                    "has_drm": "maybe",
                },
                {
                    "format_id": "137",
                    "url": "https://cdn.example/video",
                    "height": 1080,
                    "vcodec": "avc1.640028",
                    "acodec": "none",
                    "ext": "mp4",
                    "filesize": 25_000_000,
                    "tbr": 2200,
                },
                {
                    "format_id": "140",
                    "url": "https://cdn.example/audio",
                    "vcodec": "none",
                    "acodec": "mp4a.40.2",
                    "ext": "m4a",
                    "filesize": 4_000_000,
                    "abr": 129,
                },
            ]
        }
    )

    assert [item.label for item in result] == ["1080P", "MP3"]
    assert result[0].selector.startswith("137+bestaudio")
    assert result[1].selector == "140"


def test_safe_formats_prefers_combined_video_for_fast_download() -> None:
    result = _safe_formats(
        {
            "formats": [
                {
                    "format_id": "18",
                    "url": "https://cdn.example/fast",
                    "height": 360,
                    "vcodec": "avc1.42001E",
                    "acodec": "mp4a.40.2",
                    "ext": "mp4",
                    "filesize": 12_000_000,
                    "tbr": 420,
                },
                {
                    "format_id": "401",
                    "url": "https://cdn.example/ultra",
                    "height": 1608,
                    "vcodec": "av01.0.12M.08",
                    "acodec": "none",
                    "ext": "mp4",
                    "filesize": 77_000_000,
                    "tbr": 2200,
                },
            ]
        }
    )

    assert result[0].id == "video-fast"
    assert result[0].selector == "18"
    assert result[0].kind == "快速"
    assert "含音频" in result[0].detail
    assert "推荐" in result[0].detail
    assert result[1].label == "1608P"


def test_bilibili_formats_put_real_highest_resolution_first() -> None:
    result = _safe_formats({
        "extractor_key": "BiliBiliBangumi",
        "formats": [
            {"format_id": "80", "url": "https://cdn.example/480", "height": 480,
             "vcodec": "avc1", "acodec": "mp4a", "ext": "mp4"},
            {"format_id": "120", "url": "https://cdn.example/1080", "height": 1080,
             "vcodec": "avc1", "acodec": "none", "ext": "mp4"},
            {"format_id": "112", "url": "https://cdn.example/720", "height": 720,
             "vcodec": "avc1", "acodec": "none", "ext": "mp4"},
        ],
    })
    assert [item.label for item in result] == ["1080P", "720P", "480P"]
    assert "推荐" in result[0].detail
    assert "推荐" not in result[2].detail
    assert result[0].selector.startswith("120+bestaudio")


def test_large_non_drm_4k_is_available_on_bilibili_and_other_sites() -> None:
    fourk = {
        "format_id": "120",
        "url": "https://cdn.example/4k",
        "height": 2160,
        "vcodec": "avc1",
        "acodec": "none",
        "ext": "mp4",
        "filesize": 2 * 1024**3,
    }
    audio = {
        "format_id": "30280",
        "url": "https://cdn.example/audio",
        "vcodec": "none",
        "acodec": "mp4a",
        "ext": "m4a",
    }
    result = _safe_formats({"extractor_key": "BiliBiliBangumi", "formats": [fourk, audio]})
    assert result[0].label == "2160P · 4K"
    assert result[0].selector.startswith("120+bestaudio")
    assert "推荐" in result[0].detail
    other_site = _safe_formats({"extractor_key": "OtherSite", "formats": [fourk, audio]})
    assert other_site[0].label == "2160P · 4K"
    assert other_site[0].selector.startswith("120+bestaudio")


def test_safe_formats_prefers_higher_bitrate_video_at_same_resolution() -> None:
    result = _safe_formats({"formats": [
        {"format_id": "muxed", "url": "https://cdn.example/low.mp4", "width": 1920,
         "height": 1080, "vcodec": "avc1", "acodec": "mp4a", "ext": "mp4",
         "protocol": "https", "tbr": 900},
        {"format_id": "video-only", "url": "https://cdn.example/high.mp4", "width": 1920,
         "height": 1080, "vcodec": "avc1", "acodec": "none", "ext": "mp4",
         "protocol": "https", "tbr": 4000, "format_note": "1080p, AI-upscaled"},
    ]})

    assert result[0].selector == "muxed"
    assert result[1].selector.startswith("video-only+bestaudio")
    assert "1920×1080" in result[1].detail
    assert "AI 放大" in result[1].detail


def test_safe_formats_labels_portrait_resolution_by_short_edge() -> None:
    result = _safe_formats({"formats": [
        {"format_id": "portrait-720", "url": "https://cdn.example/portrait.mp4",
         "width": 720, "height": 1280, "vcodec": "avc1", "acodec": "mp4a", "ext": "mp4"},
    ]})

    assert result[0].label == "720P"
    assert "720×1280" in result[0].detail


def test_safe_formats_recommends_480p_for_hls_stability() -> None:
    result = _safe_formats(
        {
            "formats": [
                {
                    "format_id": "hls-480",
                    "url": "https://cdn.example/480.m3u8",
                    "height": 480,
                    "vcodec": "avc1",
                    "acodec": "mp4a",
                    "ext": "mp4",
                    "protocol": "m3u8_native",
                },
                {
                    "format_id": "hls-720",
                    "url": "https://cdn.example/720.m3u8",
                    "height": 720,
                    "vcodec": "avc1",
                    "acodec": "mp4a",
                    "ext": "mp4",
                    "protocol": "m3u8_native",
                },
            ]
        }
    )

    assert result[0].id == "video-fast"
    assert result[0].selector == "hls-480"
    assert result[1].label == "720P"


def test_safe_formats_uses_tiktok_public_stream_as_stable_default() -> None:
    result = _safe_formats(
        {
            "extractor_key": "TikTok",
            "formats": [
                {
                    "format_id": "download",
                    "url": "https://v16-webapp-prime.tiktok.com/public.mp4",
                    "vcodec": "h264",
                    "acodec": "aac",
                    "ext": "mp4",
                    "protocol": "https",
                    "format_note": "watermarked",
                },
                {
                    "format_id": "h264-1024",
                    "url": "https://v16-webapp-prime.tiktok.com/high.mp4",
                    "height": 1024,
                    "vcodec": "h264",
                    "acodec": "aac",
                    "ext": "mp4",
                    "protocol": "https",
                    "tbr": 800,
                },
            ],
        }
    )

    assert result[0].id == "video-fast"
    assert result[0].label == "公开画质"
    assert result[0].selector == "download"
    assert result[1].label == "1024P"


def test_safe_formats_uses_smallest_combined_height_when_480p_is_unavailable() -> None:
    result = _safe_formats(
        {
            "formats": [
                {
                    "format_id": "568",
                    "url": "https://cdn.example/568.mp4",
                    "height": 568,
                    "vcodec": "h264",
                    "acodec": "aac",
                    "ext": "mp4",
                },
                {
                    "format_id": "1280",
                    "url": "https://cdn.example/1280.mp4",
                    "height": 1280,
                    "vcodec": "h264",
                    "acodec": "aac",
                    "ext": "mp4",
                },
            ]
        }
    )

    assert result[0].id == "video-fast"
    assert result[0].label == "568P"
    assert result[0].selector == "568"


def test_safe_formats_keeps_hls_video_with_unknown_codecs() -> None:
    result = _safe_formats(
        {
            "formats": [
                {
                    "format_id": "scrubber",
                    "url": "https://cdn.example/storyboard.jpg",
                    "height": 180,
                    "vcodec": None,
                    "acodec": None,
                    "ext": "jpg",
                },
                {
                    "format_id": "hls-480",
                    "url": "https://cdn.example/video.m3u8",
                    "height": 480,
                    "vcodec": None,
                    "acodec": None,
                    "ext": "mp4",
                    "filesize_approx": 2_000_000,
                }
            ]
        }
    )

    assert result[0].label == "480P"
    assert result[0].selector == "hls-480"
    assert "含音频" in result[0].detail
    assert all(item.label != "180P" for item in result)


def test_safe_formats_prefers_direct_audio_when_quality_matches() -> None:
    result = _safe_formats(
        {
            "formats": [
                {
                    "format_id": "hls-mp3",
                    "url": "https://cdn.example/audio.m3u8",
                    "protocol": "m3u8_native",
                    "vcodec": "none",
                    "acodec": "mp3",
                    "ext": "mp3",
                    "abr": 128,
                },
                {
                    "format_id": "http-mp3",
                    "url": "https://cdn.example/audio.mp3",
                    "protocol": "https",
                    "vcodec": "none",
                    "acodec": "mp3",
                    "ext": "mp3",
                    "abr": 128,
                },
            ]
        }
    )

    assert result[0].kind == "音频"
    assert result[0].selector == "http-mp3"


def test_safe_formats_rejects_drm_only_media() -> None:
    with pytest.raises(ProtectedMediaError):
        _safe_formats(
            {
                "formats": [
                    {
                        "format_id": "drm",
                        "url": "https://cdn.example/drm",
                        "height": 1080,
                        "vcodec": "avc1",
                        "acodec": "mp4a",
                        "has_drm": True,
                    }
                ]
            }
        )
