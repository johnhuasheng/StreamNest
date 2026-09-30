import http.cookiejar

from fastapi.testclient import TestClient

import streamnest_api.main as api
import streamnest_api.resolver as resolver
from streamnest_api.bilibili_session import BilibiliSession, bilibili_session


def _sample_cookie(domain=".bilibili.com", name="SESSDATA", value="local-test-only"):
    return {
        "domain": domain,
        "name": name,
        "value": value,
        "path": "/",
        "secure": True,
        "httpOnly": True,
    }


def test_session_rejects_other_domains_and_requires_bilibili_login() -> None:
    session = BilibiliSession()
    assert not session.replace([_sample_cookie(domain=".example.com")])
    assert not session.replace([_sample_cookie(name="other")])
    assert session.replace([_sample_cookie(), _sample_cookie(domain=".youtube.com", name="LEAK")])
    assert [(cookie.domain, cookie.name) for cookie in session.get()] == [
        (".bilibili.com", "SESSDATA"),
    ]
    session.clear()
    assert not session.get()


def test_member_cookie_is_injected_only_into_bilibili_extraction(monkeypatch) -> None:
    assert bilibili_session.replace([_sample_cookie()])
    observed = []

    class FakeDownloader:
        def __init__(self, _options):
            self.cookiejar = http.cookiejar.CookieJar()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            observed.append((download, [cookie.name for cookie in self.cookiejar]))
            return {"title": "test"}

    monkeypatch.setattr(resolver.yt_dlp, "YoutubeDL", FakeDownloader)
    try:
        resolver._extract_info("https://www.bilibili.com/bangumi/play/ep6281866", {}, download=False)
        resolver._extract_info("https://www.youtube.com/watch?v=test", {}, download=False)
    finally:
        bilibili_session.clear()
    assert observed == [(False, ["SESSDATA"]), (False, [])]


def test_session_api_is_local_extension_only_and_never_echoes_cookie(monkeypatch) -> None:
    client = TestClient(api.app)
    bilibili_session.clear()
    data = {"cookies": [_sample_cookie()]}
    origin = "chrome-extension://" + "a" * 32
    denied = client.post("/v1/bilibili/session", json=data)
    assert denied.status_code == 403

    monkeypatch.setattr(api, "_client_id", lambda _request: "127.0.0.1")
    headers = {"Origin": origin, "X-StreamNest-Bridge": "bilibili-v1"}
    try:
        preflight = client.options(
            "/v1/bilibili/session",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-streamnest-bridge",
            },
        )
        assert preflight.status_code == 204
        assert preflight.headers["access-control-allow-origin"] == origin
        assert "x-streamnest-bridge" in preflight.headers["access-control-allow-headers"].lower()
        other_endpoint = client.options(
            "/v1/bilibili/session/clear",
            headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
        )
        assert other_endpoint.status_code != 204
        accepted = client.post("/v1/bilibili/session", json=data, headers=headers)
        assert accepted.status_code == 200
        assert accepted.json() == {"active": True}
        assert accepted.headers["access-control-allow-origin"] == origin
        assert "local-test-only" not in accepted.text
        assert client.get("/v1/bilibili/session").json() == {"active": True}
        rejected_clear = client.post("/v1/bilibili/session/clear")
        assert rejected_clear.status_code == 403
        cleared = client.post(
            "/v1/bilibili/session/clear",
            headers={"Origin": "http://localhost:3000"},
        )
        assert cleared.status_code == 200
        assert cleared.json() == {"active": False}
    finally:
        bilibili_session.clear()
