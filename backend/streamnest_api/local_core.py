from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class LocalCoreError(RuntimeError):
    message: str
    status_code: int = 502
    code: str = "local_core_unavailable"

    def __str__(self) -> str:
        return self.message


def core_origin() -> str:
    configured = os.getenv("STREAMNEST_LOCAL_CORE_ORIGIN", "http://127.0.0.1:4000").rstrip("/")
    parsed = urlparse(configured)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise LocalCoreError("本机国内平台下载核心地址配置无效。", 500, "invalid_core_origin")
    return configured


def call_local_core(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: int = 65,
) -> dict[str, Any]:
    if not path.startswith("/api/") or ".." in path:
        raise LocalCoreError("本机下载核心接口路径无效。", 500, "invalid_core_path")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(
        f"{core_origin()}{path}",
        data=body,
        method=method,
        headers={"Accept": "application/json", **({"Content-Type": "application/json"} if body else {})},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            error = json.loads(exc.read().decode("utf-8"))
            message = str(error.get("error", {}).get("message") or "国内平台下载核心请求失败。")
            code = str(error.get("error", {}).get("code") or "local_core_error").lower()
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            message = "国内平台下载核心请求失败。"
            code = "local_core_error"
        raise LocalCoreError(message, exc.code, code) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise LocalCoreError("国内平台下载核心未启动，请重新打开统一下载管理器。", 503) from exc
    except json.JSONDecodeError as exc:
        raise LocalCoreError("国内平台下载核心返回了无效数据。") from exc
    if not isinstance(result, dict):
        raise LocalCoreError("国内平台下载核心返回了无效数据。")
    return result
