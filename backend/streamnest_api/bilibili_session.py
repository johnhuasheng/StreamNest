"""Short-lived, in-memory Bilibili cookies supplied by the local Edge helper.

No browser profile is opened here and no cookie is written to disk or returned to
the web UI.  The browser extension requests only bilibili.com cookies after an
explicit user click; yt-dlp receives them only for Bilibili extraction.
"""

from __future__ import annotations

import http.cookiejar
import re
import threading
import time
from typing import Any


_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")
_MAX_COOKIES = 128
_MAX_VALUE_LENGTH = 4096
_SESSION_SECONDS = 8 * 60 * 60


def _bilibili_domain(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    domain = raw.strip().lower()
    host = domain.lstrip(".")
    if host != "bilibili.com" and not host.endswith(".bilibili.com"):
        return None
    return domain


def _cookie_from_browser(item: Any) -> http.cookiejar.Cookie | None:
    if not isinstance(item, dict):
        return None
    domain = _bilibili_domain(item.get("domain"))
    name, value = item.get("name"), item.get("value")
    path = item.get("path", "/")
    if (
        domain is None
        or not isinstance(name, str)
        or not _NAME.fullmatch(name)
        or not isinstance(value, str)
        or len(value) > _MAX_VALUE_LENGTH
        or any(char in value for char in "\r\n\x00")
        or not isinstance(path, str)
        or not path.startswith("/")
        or len(path) > 256
    ):
        return None
    expiry = item.get("expirationDate")
    expires = int(expiry) if isinstance(expiry, (int, float)) and expiry > 0 else None
    if expires is not None and expires <= time.time():
        return None
    return http.cookiejar.Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path=path,
        path_specified=True,
        secure=item.get("secure") is True,
        expires=expires,
        discard=expires is None,
        comment=None,
        comment_url=None,
        rest={"HttpOnly": item.get("httpOnly") is True},
    )


class BilibiliSession:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cookies: tuple[http.cookiejar.Cookie, ...] = ()
        self._deadline = 0.0

    def replace(self, raw_cookies: Any) -> bool:
        if not isinstance(raw_cookies, list) or not 1 <= len(raw_cookies) <= _MAX_COOKIES:
            return False
        cookies = tuple(filter(None, (_cookie_from_browser(item) for item in raw_cookies)))
        if not any(cookie.name == "SESSDATA" and cookie.value for cookie in cookies):
            return False
        with self._lock:
            self._cookies = cookies
            self._deadline = time.monotonic() + _SESSION_SECONDS
        return True

    def get(self) -> tuple[http.cookiejar.Cookie, ...]:
        with self._lock:
            if time.monotonic() >= self._deadline:
                self._cookies = ()
            return self._cookies

    def clear(self) -> None:
        with self._lock:
            self._cookies = ()
            self._deadline = 0.0


bilibili_session = BilibiliSession()
