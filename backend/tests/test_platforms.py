import pytest

from streamnest_api.platforms import (
    AdultConfirmationRequired,
    UrlValidationError,
    extract_video_url,
    validate_platform_url,
)


def test_accepts_real_platform_subdomains() -> None:
    assert validate_platform_url("https://www.youtube.com/watch?v=test").name == "YouTube"
    assert validate_platform_url("https://v.douyin.com/abc").name == "抖音"
    assert validate_platform_url("https://www.acfun.cn/v/ac123").name == "AcFun"
    assert validate_platform_url("https://hongguoduanju.com/player/7684649550316833817").name == "红果短剧"


def test_extracts_video_url_from_markdown_and_chinese_commentary() -> None:
    value = (
        "【如果GPT-5.6在1986年发布】"
        "[视频](https://www.bilibili.com/video/BV18Sb264EXf?"
        "vd\\_source=cc9a48dfefb577da7e8cff29f7351945，再优化一下)"
    )
    assert extract_video_url(value) == (
        "https://www.bilibili.com/video/BV18Sb264EXf?"
        "vd_source=cc9a48dfefb577da7e8cff29f7351945"
    )


def test_converts_weibo_layer_overlay_to_public_status_url() -> None:
    assert extract_video_url("https://weibo.com/?layerid=5334824403343686") == (
        "https://m.weibo.cn/status/5334824403343686"
    )

@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com.evil.example/watch?v=test",
        "http://127.0.0.1/video",
        "https://localhost/video",
        "ftp://youtube.com/video",
        "https://user:password@youtube.com/video",
        "https://youtube.com:8443/video",
    ],
)
def test_rejects_unsafe_or_unlisted_urls(url: str) -> None:
    with pytest.raises(UrlValidationError):
        validate_platform_url(url)


def test_requires_confirmation_for_adult_platforms() -> None:
    with pytest.raises(AdultConfirmationRequired):
        validate_platform_url("https://www.xvideos.com/video.test")
    assert validate_platform_url(
        "https://www.xvideos.com/video.test",
        adult_confirmed=True,
    ).adult
    with pytest.raises(AdultConfirmationRequired):
        validate_platform_url("https://www.eporner.com/video-test/example/")
    assert validate_platform_url(
        "https://static-sg-cdn.eporner.com/thumbs/test.jpg",
        adult_confirmed=True,
    ).name == "Eporner"


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("XNXX", "https://www.xnxx.com/video-test/example"),
        ("Rule34Video", "https://rule34video.com/video/123/example/"),
        ("HQPorner", "https://hqporner.com/hdporn/123-example.html"),
        ("Beeg", "https://beeg.com/123456"),
        ("SxyPrn", "https://sxyprn.com/post/abcdef123.html"),
        ("SpankBang", "https://spankbang.com/abc/video/example"),
        ("XMoviesForYou", "https://xmoviesforyou.com/example"),
        ("海角网", "https://hjw2026.com/"),
    ],
)
def test_new_adult_platforms_require_confirmation(name: str, url: str) -> None:
    with pytest.raises(AdultConfirmationRequired):
        validate_platform_url(url)
    rule = validate_platform_url(url, adult_confirmed=True)
    assert rule.name == name
    assert rule.adult


@pytest.mark.parametrize("domain", ("hjwang21.com", "hjwang20.com", "hjwang19.com", "hjwang18.com", "hjw01.com", "hjw2026.com"))
def test_haijiao_known_domains_are_adult_only(domain: str) -> None:
    url = f"https://www.{domain}/archives/194451/"
    with pytest.raises(AdultConfirmationRequired):
        validate_platform_url(url)
    assert validate_platform_url(url, adult_confirmed=True).name == "海角网"
    with pytest.raises(UrlValidationError):
        validate_platform_url(f"https://{domain}.example.org/archives/194451/", adult_confirmed=True)
