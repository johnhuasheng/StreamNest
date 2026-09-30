import time
from pathlib import Path

from fastapi.testclient import TestClient

import streamnest_api.main as api
from streamnest_api.local_core import LocalCoreError
from streamnest_api.resolver import DownloadedFile, DownloadProgress, ResolvedFormat, ResolvedMedia


client = TestClient(api.app)


def test_health_endpoint() -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["cache-control"] == "no-store"


def test_core_health_rejects_unrelated_service_on_port_4000(monkeypatch) -> None:
    def fake_call(method, path, payload=None, **_kwargs):
        if path == "/api/health":
            return {"status": "ok"}
        raise LocalCoreError("接口不存在：POST /api/parse", 404, "not_found")

    monkeypatch.setattr(api, "call_local_core", fake_call)
    response = client.get("/v1/core/health")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "local_core_incompatible"


def test_core_health_accepts_parse_validation_response(monkeypatch) -> None:
    def fake_call(method, path, payload=None, **_kwargs):
        if path == "/api/health":
            return {"status": "ok"}
        raise LocalCoreError("请输入链接", 400, "invalid_url")

    monkeypatch.setattr(api, "call_local_core", fake_call)
    response = client.get("/v1/core/health")
    assert response.status_code == 200


def test_resolve_rejects_unknown_platform_before_network() -> None:
    response = client.post("/v1/resolve", json={"url": "https://example.com/video"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_url"


def test_resolve_explains_hjw_route_page_without_media_lookup(monkeypatch) -> None:
    monkeypatch.setattr(
        api,
        "resolve_media",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应解析线路发布页")),
    )
    response = client.post(
        "/v1/resolve",
        json={"url": "https://hjw2026.com/", "adult_confirmed": True},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "resolve_failed"
    assert "线路发布页" in response.json()["error"]["message"]
    assert "具体视频" in response.json()["error"]["message"]


def test_resolve_returns_opaque_download_ticket(monkeypatch) -> None:
    monkeypatch.setattr(
        api,
        "resolve_media",
        lambda url, adult_confirmed: ResolvedMedia(
            title="Test video",
            author="Uploader",
            duration="01:02",
            duration_seconds=62,
            thumbnail="https://cdn.example/thumb.jpg",
            formats=(
                ResolvedFormat(
                    id="video-1",
                    label="1080P",
                    detail="MP4 · H264 · 含音频",
                    size="12.0 MB",
                    kind="高清",
                    selector="best",
                ),
                ResolvedFormat(
                    id="image-cover",
                    label="封面原图",
                    detail="图片 · 平台公开封面 · 原始尺寸",
                    size="未知大小",
                    kind="图片",
                    selector="image-cover",
                    source_url="https://i.ytimg.com/vi/test/maxresdefault.jpg",
                ),
            ),
        ),
    )
    response = client.post(
        "/v1/resolve",
        json={"url": "https://www.youtube.com/watch?v=test"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["platform"] == "YouTube"
    ticket = body["formats"][0]["download_ticket"]
    assert len(ticket) >= 32
    assert "youtube" not in ticket.lower()
    assert body["formats"][1]["kind"] == "图片"
    assert len(body["formats"][1]["download_ticket"]) >= 32


def test_xiaohongshu_gallery_tickets_support_all_and_selected_images(monkeypatch) -> None:
    page = "https://www.xiaohongshu.com/explore/67f75b07000000000b0154b3"
    images = (
        "https://sns-i11.rednotecdn.com/one.webp",
        "https://sns-i11.rednotecdn.com/two.webp",
    )
    monkeypatch.setattr(
        api,
        "resolve_media",
        lambda *_args, **_kwargs: ResolvedMedia(
            title="公开图文", author="作者", duration="图文", duration_seconds=None,
            thumbnail=images[0],
            formats=(
                ResolvedFormat("image-all", "全部 2 张图片", "图片合集 · 2 张平台公开图片 · ZIP", "ZIP 压缩包", "图片合集", "image-all", source_urls=images),
                ResolvedFormat("image-1", "第 1 张图片", "图片 · 图文作品第 1/2 张 · 平台公开尺寸", "未知大小", "图片", "image-1", source_url=images[0]),
                ResolvedFormat("image-2", "第 2 张图片", "图片 · 图文作品第 2/2 张 · 平台公开尺寸", "未知大小", "图片", "image-2", source_url=images[1]),
            ),
        ),
    )

    response = client.post("/v1/resolve", json={"url": page})

    assert response.status_code == 200
    formats = response.json()["formats"]
    assert [item["id"] for item in formats] == ["image-all", "image-1", "image-2"]
    assert api.tickets.get(formats[0]["download_ticket"]).source_urls == images
    selection = client.post(
        "/v1/image-collections",
        json={"tickets": [formats[2]["download_ticket"], formats[1]["download_ticket"]], "title": "公开图文"},
    )
    assert selection.status_code == 201
    assert api.tickets.get(selection.json()["download_ticket"]).source_urls == tuple(reversed(images))


def test_resolve_extracts_url_from_share_text(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_resolve(url, *, adult_confirmed):
        captured["url"] = url
        return ResolvedMedia(
            title="Bilibili test",
            author="Uploader",
            duration="01:00",
            duration_seconds=60,
            thumbnail=None,
            formats=(
                ResolvedFormat(
                    id="video-fast",
                    label="480P",
                    detail="MP4 · 含音频",
                    size="1.9 MB",
                    kind="快速",
                    selector="best",
                ),
            ),
        )

    monkeypatch.setattr(api, "resolve_media", fake_resolve)
    response = client.post(
        "/v1/resolve",
        json={
            "url": (
                "【视频标题】[视频](https://www.bilibili.com/video/BV18Sb264EXf?"
                "vd\\_source=abc123，再优化一下)"
            )
        },
    )

    assert response.status_code == 200
    assert captured["url"] == "https://www.bilibili.com/video/BV18Sb264EXf?vd_source=abc123"


def test_resolve_converts_weibo_layer_url(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_resolve(url, *, adult_confirmed):
        captured["url"] = url
        return ResolvedMedia(
            title="Weibo video",
            author="Uploader",
            duration="00:30",
            duration_seconds=30,
            thumbnail=None,
            formats=(
                ResolvedFormat(
                    id="video-fast",
                    label="720P",
                    detail="MP4 · 含音频",
                    size="5 MB",
                    kind="快速",
                    selector="best",
                ),
            ),
        )

    monkeypatch.setattr(api, "resolve_media", fake_resolve)
    response = client.post(
        "/v1/resolve",
        json={"url": "https://weibo.com/?layerid=5334824403343686"},
    )

    assert response.status_code == 200
    assert captured["url"] == "https://m.weibo.cn/status/5334824403343686"


def test_download_serves_ticket_file_and_cleans_up(monkeypatch, tmp_path: Path) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    media_file = media_dir / "test-video.mp4"
    media_file.write_bytes(b"authorized-test-media")

    monkeypatch.setattr(
        api,
        "download_media",
        lambda claim: DownloadedFile(media_dir, media_file, "video/mp4"),
    )
    ticket = api.tickets.issue(
        api.DownloadClaim(
            url="https://www.youtube.com/watch?v=test",
            selector="best",
            kind="高清",
            title="Test video",
            adult_confirmed=False,
        )
    )
    response = client.get(f"/v1/download/{ticket}")
    assert response.status_code == 200
    assert response.content == b"authorized-test-media"
    assert not media_dir.exists()


def test_prepare_then_serves_ready_file_without_reprocessing(monkeypatch, tmp_path: Path) -> None:
    media_dir = tmp_path / "prepared-media"
    media_dir.mkdir()
    media_file = media_dir / "prepared-video.mp4"
    media_file.write_bytes(b"prepared-authorized-media")
    calls = 0

    def fake_download(claim):
        nonlocal calls
        calls += 1
        return DownloadedFile(media_dir, media_file, "video/mp4")

    monkeypatch.setattr(api, "download_media", fake_download)
    ticket = api.tickets.issue(
        api.DownloadClaim(
            url="https://www.youtube.com/watch?v=test",
            selector="best",
            kind="高清",
            title="Prepared video",
            adult_confirmed=False,
        )
    )

    prepared = client.post(f"/v1/prepare/{ticket}")
    assert prepared.status_code == 200
    assert calls == 1
    file_ticket = prepared.json()["file_ticket"]

    response = client.get(f"/v1/file/{file_ticket}")
    assert response.status_code == 200
    assert response.content == b"prepared-authorized-media"
    assert calls == 1
    assert not media_dir.exists()


def test_internal_post_transfer_saves_to_output_folder(monkeypatch, tmp_path: Path) -> None:
    media_dir = tmp_path / "internal-transfer"
    media_dir.mkdir()
    media_file = media_dir / "selected-images.zip"
    media_file.write_bytes(b"internal-zip-content")
    saved_dir = tmp_path / "下载内容"
    monkeypatch.setattr(api, "output_directory", saved_dir)
    file_ticket = api.prepared_files.issue(
        api.PreparedFileClaim(media_dir, media_file, "application/zip")
    )

    response = client.post(f"/v1/file/{file_ticket}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert "content-disposition" not in response.headers
    assert response.json()["ok"] is True
    assert response.json()["filename"] == "selected-images.zip"
    assert (saved_dir / "selected-images.zip").read_bytes() == b"internal-zip-content"
    assert not media_dir.exists()
    assert client.post(f"/v1/file/{file_ticket}").status_code == 404


def test_output_folder_keeps_existing_same_name_file(monkeypatch, tmp_path: Path) -> None:
    saved_dir = tmp_path / "下载内容"
    saved_dir.mkdir()
    (saved_dir / "wallpaper.zip").write_bytes(b"existing")
    source = tmp_path / "new-wallpaper.zip"
    source.write_bytes(b"new")
    monkeypatch.setattr(api, "output_directory", saved_dir)

    destination = api._save_to_output(source, "wallpaper.zip")

    assert destination.name == "wallpaper (2).zip"
    assert (saved_dir / "wallpaper.zip").read_bytes() == b"existing"
    assert destination.read_bytes() == b"new"


def test_download_job_reports_progress_then_serves_file(monkeypatch, tmp_path: Path) -> None:
    media_dir = tmp_path / "job-media"
    media_dir.mkdir()
    media_file = media_dir / "job-video.mp4"
    media_file.write_bytes(b"job-media-content")

    def fake_download(claim, progress_callback=None):
        if progress_callback:
            progress_callback(
                DownloadProgress(
                    status="downloading",
                    downloaded_bytes=750,
                    total_bytes=1000,
                    speed=8_600_000,
                    eta=83,
                    percent=75.0,
                )
            )
        return DownloadedFile(media_dir, media_file, "video/mp4")

    monkeypatch.setattr(api, "download_media", fake_download)
    ticket = api.tickets.issue(
        api.DownloadClaim(
            url="https://www.youtube.com/watch?v=test",
            selector="18",
            kind="快速",
            title="Job video",
            adult_confirmed=False,
        )
    )

    with TestClient(api.app) as job_client:
        started = job_client.post(f"/v1/jobs/{ticket}")
        assert started.status_code == 202
        job_id = started.json()["job_id"]

        finished = None
        for _ in range(50):
            response = job_client.get(f"/v1/jobs/{job_id}")
            assert response.status_code == 200
            if response.json()["status"] == "ready":
                finished = response.json()
                break
            time.sleep(0.01)

        assert finished is not None
        assert finished["progress"] == 100.0
        assert finished["filename"] == "job-video.mp4"
        file_response = job_client.get(f"/v1/file/{finished['file_ticket']}")
        assert file_response.status_code == 200
        assert file_response.content == b"job-media-content"
        assert not media_dir.exists()


def test_helper_job_downloads_captured_mp4_and_serves_file(monkeypatch, tmp_path: Path) -> None:
    media_dir = tmp_path / "helper-job-media"
    media_dir.mkdir()
    media_file = media_dir / "douyin-video-test.mp4"
    media_file.write_bytes(b"captured-public-media")
    captured: dict[str, object] = {}

    def fake_download(url, platform, headers, progress_callback=None):
        captured.update(url=url, platform=platform, headers=headers)
        if progress_callback:
            progress_callback(
                DownloadProgress(
                    status="downloading",
                    downloaded_bytes=18,
                    total_bytes=21,
                    speed=8_600_000,
                    eta=1,
                    percent=85.7,
                )
            )
        return DownloadedFile(media_dir, media_file, "video/mp4")

    monkeypatch.setattr(api, "download_helper_media", fake_download)
    with TestClient(api.app) as job_client:
        started = job_client.post(
            "/v1/helper/jobs",
            json={
                "url": "https://v3.douyinvod.com/video/example.mp4",
                "platform": "抖音",
                "headers": {
                    "referer": "https://www.douyin.com/video/example",
                    "cookie": "must-not-leave-browser=1",
                },
            },
        )
        assert started.status_code == 202
        job_id = started.json()["job_id"]

        finished = None
        for _ in range(50):
            response = job_client.get(f"/v1/jobs/{job_id}")
            if response.json()["status"] == "ready":
                finished = response.json()
                break
            time.sleep(0.01)

        assert finished is not None
        assert captured["platform"] == "抖音"
        assert captured["headers"] == {"referer": "https://www.douyin.com/video/example"}
        file_response = job_client.get(f"/v1/file/{finished['file_ticket']}")
        assert file_response.content == b"captured-public-media"


def test_helper_job_rejects_non_platform_media_url() -> None:
    response = client.post(
        "/v1/helper/jobs",
        json={"url": "https://example.com/video.mp4", "platform": "抖音", "headers": {}},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_helper_media"


def test_local_core_resolve_reuses_douyin_metadata(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_call(method, path, payload=None, **_kwargs):
        captured.update(method=method, path=path, payload=payload)
        return {
            "ok": True,
            "meta": {
                "platform": "douyin",
                "title": "Public Douyin video",
                "author": "Uploader",
                "thumbnail": "https://p3-pc-sign.douyinpic.com/test-cover.jpeg",
                "duration": 12,
                "formats": [
                    {"id": "embed-original", "quality": "1080p", "ext": "mp4", "size": 1234}
                ],
            },
        }

    monkeypatch.setattr(api, "call_local_core", fake_call)
    response = client.post(
        "/v1/core/resolve",
        json={"url": "https://www.douyin.com/video/7674251323609830000"},
    )

    assert response.status_code == 200
    assert response.json()["meta"]["formats"][0]["quality"] == "1080p"
    assert response.json()["meta"]["formats"][1]["kind"] == "image"
    assert len(response.json()["meta"]["formats"][1]["download_ticket"]) >= 32
    assert captured == {
        "method": "POST",
        "path": "/api/parse",
        "payload": {"url": "https://www.douyin.com/video/7674251323609830000"},
    }


def test_local_core_resolve_exposes_all_douyin_images_and_zip(monkeypatch) -> None:
    image_urls = [
        f"https://p3-pc-sign.douyinpic.com/photo-{index}.jpeg"
        for index in range(1, 4)
    ]

    monkeypatch.setattr(
        api,
        "call_local_core",
        lambda *_args, **_kwargs: {
            "ok": True,
            "meta": {
                "platform": "douyin",
                "title": "Public Douyin gallery",
                "author": "Uploader",
                "thumbnail": image_urls[0],
                "images": image_urls,
                "duration": None,
                "formats": [
                    {"id": "embed-original", "quality": "720p", "ext": "mp4", "size": None}
                ],
            },
        },
    )

    response = client.post(
        "/v1/core/resolve",
        json={"url": "https://www.douyin.com/note/7546907495592168714"},
    )

    assert response.status_code == 200
    formats = response.json()["meta"]["formats"]
    assert [item["id"] for item in formats] == [
        "embed-original",
        "image-all",
        "image-1",
        "image-2",
        "image-3",
    ]
    assert formats[1]["kind"] == "image_collection"
    collection_claim = api.tickets.get(formats[1]["download_ticket"])
    assert collection_claim.kind == "图片合集"
    assert collection_claim.source_urls == tuple(image_urls)


def test_custom_image_collection_preserves_selected_order() -> None:
    page_url = "https://www.douyin.com/note/7546907495592168714"
    selected_urls = (
        "https://p3-pc-sign.douyinpic.com/photo-2.jpeg",
        "https://p3-pc-sign.douyinpic.com/photo-5.jpeg",
    )
    image_tickets = [
        api.tickets.issue(
            api.DownloadClaim(
                url=page_url,
                selector=f"image-{index}",
                kind="图片",
                title="Gallery image",
                adult_confirmed=False,
                source_url=image_url,
            )
        )
        for index, image_url in enumerate(selected_urls, start=1)
    ]

    response = client.post(
        "/v1/image-collections",
        json={"tickets": image_tickets, "title": "Selected dog wallpapers"},
    )

    assert response.status_code == 201
    assert response.json()["image_count"] == 2
    claim = api.tickets.get(response.json()["download_ticket"])
    assert claim.kind == "图片合集"
    assert claim.title == "Selected dog wallpapers"
    assert claim.source_urls == selected_urls


def test_custom_image_collection_rejects_expired_ticket() -> None:
    response = client.post(
        "/v1/image-collections",
        json={"tickets": ["expired-ticket"], "title": "Selected images"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ticket_not_found"


def test_local_core_completed_file_is_served_directly(monkeypatch, tmp_path: Path) -> None:
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    file_path = download_dir / "douyin-test.mp4"
    file_path.write_bytes(b"real-public-video")

    monkeypatch.setattr(
        api,
        "call_local_core",
        lambda *_args, **_kwargs: {
            "ok": True,
            "task": {
                "status": "completed",
                "filePath": str(file_path),
                "downloadDir": str(download_dir),
            },
        },
    )
    response = client.get("/v1/core/file/730")

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.content == b"real-public-video"
    assert file_path.exists()


def test_local_core_post_transfer_avoids_attachment_and_consumes_copy(monkeypatch, tmp_path: Path) -> None:
    download_dir = tmp_path / "post-transfer-downloads"
    download_dir.mkdir()
    file_path = download_dir / "douyin-post-transfer.mp4"
    file_path.write_bytes(b"post-transfer-video")
    saved_dir = tmp_path / "下载内容"
    monkeypatch.setattr(api, "output_directory", saved_dir)
    calls: list[tuple[str, str]] = []

    def fake_call(method: str, path: str, *_args, **_kwargs):
        calls.append((method, path))
        if method == "DELETE":
            file_path.unlink(missing_ok=True)
            return {"ok": True}
        return {
            "ok": True,
            "task": {
                "status": "completed",
                "filePath": str(file_path),
                "downloadDir": str(download_dir),
            },
        }

    monkeypatch.setattr(api, "call_local_core", fake_call)
    response = client.post("/v1/core/file/735")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert "content-disposition" not in response.headers
    assert response.json()["ok"] is True
    assert response.json()["filename"] == "douyin-post-transfer.mp4"
    assert (saved_dir / "douyin-post-transfer.mp4").read_bytes() == b"post-transfer-video"
    assert not file_path.exists()
    assert calls[-1] == ("DELETE", "/api/tasks/735?deleteFile=true")


def test_consumed_local_core_file_removes_internal_copy(monkeypatch, tmp_path: Path) -> None:
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    file_path = download_dir / "douyin-consumed.mp4"
    file_path.write_bytes(b"real-public-video")
    calls: list[tuple[str, str]] = []

    def fake_call(method: str, path: str, *_args, **_kwargs):
        calls.append((method, path))
        if method == "DELETE":
            file_path.unlink(missing_ok=True)
            return {"ok": True}
        return {
            "ok": True,
            "task": {
                "status": "completed",
                "filePath": str(file_path),
                "downloadDir": str(download_dir),
            },
        }

    monkeypatch.setattr(api, "call_local_core", fake_call)
    response = client.get("/v1/core/file/731?consume=true")

    assert response.status_code == 200
    assert response.content == b"real-public-video"
    assert not file_path.exists()
    assert calls[-1] == ("DELETE", "/api/tasks/731?deleteFile=true")


def test_consumed_local_core_file_retries_cleanup(monkeypatch, tmp_path: Path) -> None:
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    file_path = download_dir / "douyin-cleanup-retry.mp4"
    file_path.write_bytes(b"real-public-video")
    delete_attempts = 0

    def fake_call(method: str, _path: str, *_args, **_kwargs):
        nonlocal delete_attempts
        if method == "DELETE":
            delete_attempts += 1
            if delete_attempts == 1:
                raise api.LocalCoreError("temporary failure")
            file_path.unlink(missing_ok=True)
            return {"ok": True}
        return {
            "ok": True,
            "task": {
                "status": "completed",
                "filePath": str(file_path),
                "downloadDir": str(download_dir),
            },
        }

    monkeypatch.setattr(api, "call_local_core", fake_call)
    monkeypatch.setattr(api.time, "sleep", lambda _seconds: None)
    response = client.get("/v1/core/file/734?consume=true")

    assert response.status_code == 200
    assert not file_path.exists()
    assert delete_attempts == 2


def test_local_core_completed_video_can_generate_first_frame(monkeypatch, tmp_path: Path) -> None:
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    file_path = download_dir / "kuaishou-test.mp4"
    file_path.write_bytes(b"public-video-placeholder")

    monkeypatch.setattr(
        api,
        "call_local_core",
        lambda *_args, **_kwargs: {
            "ok": True,
            "task": {
                "status": "completed",
                "filePath": str(file_path),
                "downloadDir": str(download_dir),
            },
        },
    )
    monkeypatch.setattr(api.shutil, "which", lambda _name: "ffmpeg")

    def fake_run(args, **_kwargs):
        Path(args[-1]).write_bytes(b"\xff\xd8\xffgenerated-frame")
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(api.subprocess, "run", fake_run)
    response = client.get("/v1/core/frame/732")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content.startswith(b"\xff\xd8\xff")
    assert file_path.exists()


def test_consumed_first_frame_removes_internal_source(monkeypatch, tmp_path: Path) -> None:
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    file_path = download_dir / "kuaishou-consumed.mp4"
    file_path.write_bytes(b"public-video-placeholder")
    calls: list[tuple[str, str]] = []

    def fake_call(method: str, path: str, *_args, **_kwargs):
        calls.append((method, path))
        if method == "DELETE":
            file_path.unlink(missing_ok=True)
            return {"ok": True}
        return {
            "ok": True,
            "task": {
                "status": "completed",
                "filePath": str(file_path),
                "downloadDir": str(download_dir),
            },
        }

    monkeypatch.setattr(api, "call_local_core", fake_call)
    monkeypatch.setattr(api.shutil, "which", lambda _name: "ffmpeg")

    def fake_run(args, **_kwargs):
        Path(args[-1]).write_bytes(b"\xff\xd8\xffgenerated-frame")
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(api.subprocess, "run", fake_run)
    response = client.get("/v1/core/frame/733?consume=true")

    assert response.status_code == 200
    assert response.content.startswith(b"\xff\xd8\xff")
    assert not file_path.exists()
    assert calls[-1] == ("DELETE", "/api/tasks/733?deleteFile=true")


def test_browser_download_error_returns_readable_page() -> None:
    response = client.get(
        "/v1/download/not-a-valid-ticket",
        headers={"accept": "text/html"},
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "下载未完成" in response.text
    assert "返回下载管理器" in response.text


def test_api_download_error_stays_json() -> None:
    response = client.get(
        "/v1/download/not-a-valid-ticket",
        headers={"accept": "application/json"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ticket_not_found"
