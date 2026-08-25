"""Explicit-manifest media rehydration safety tests."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import frontmatter
import httpx
import pytest

DEPLOY_DIR = Path(__file__).resolve().parents[1] / "deploy"
if str(DEPLOY_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOY_DIR))

import rehydrate_media as rm

PUBLIC_RESOLVER = lambda _host, _port: ["93.184.216.34"]


def _seed_bucket(
    vault: Path,
    bucket_id: str,
    *,
    content: str = "fixture memory",
    media: list[dict] | None = None,
) -> Path:
    target = vault / "dynamic" / "测试" / f"fixture_{bucket_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "id": bucket_id,
        "type": "dynamic",
        "domain": ["测试"],
        "tags": ["fixture"],
        "importance": 3,
    }
    if media is not None:
        metadata["media"] = media
    target.write_text(frontmatter.dumps(frontmatter.Post(content, **metadata)), encoding="utf-8")
    return target


def _write_manifest(path: Path, items: list[dict], *, format_name: str = rm.MANIFEST_FORMAT) -> Path:
    path.write_text(
        json.dumps({"format": format_name, "items": items}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _item(bucket_id: str, url: str, data: bytes | None = None, **extra: object) -> dict:
    item: dict[str, object] = {
        "bucket_id": bucket_id,
        "source_url": url,
        "filename": extra.pop("filename", "fixture.png"),
        **extra,
    }
    if data is not None:
        item["expected_sha256"] = hashlib.sha256(data).hexdigest()
        item["expected_size"] = len(data)
    return item


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


@pytest.mark.asyncio
async def test_dry_run_is_network_free_and_does_not_modify_bucket(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    bucket = _seed_bucket(vault, "bucket-dry-run")
    before = bucket.read_bytes()
    manifest = _write_manifest(
        tmp_path / "manifest.json",
        [_item("bucket-dry-run", "https://media.example.test/image.png?signature=secret")],
    )
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("dry-run must not create an HTTP request")

    client = _client(handler)
    try:
        report = await rm.rehydrate_media(manifest, vault, client=client)
    finally:
        client.close()

    assert calls == 0
    assert report["mode"] == "dry-run"
    assert report["network_accessed"] is False
    assert report["counts"]["pending"] == 1
    assert bucket.read_bytes() == before
    assert not (vault / "_media").exists()
    serialized = json.dumps(report)
    assert "signature=secret" not in serialized
    assert "https://" not in serialized


def test_manifest_schema_and_apply_flag_are_explicit(tmp_path: Path) -> None:
    bad_format = _write_manifest(tmp_path / "bad-format.json", [], format_name="wrong")
    with pytest.raises(rm.RehydrationError, match="manifest format"):
        rm.load_manifest(bad_format)

    insecure = _write_manifest(
        tmp_path / "insecure.json",
        [_item("bucket", "http://example.test/image.png")],
    )
    with pytest.raises(rm.RehydrationError, match="must use https"):
        rm.load_manifest(insecure)

    dry_args = rm.parse_args(["--manifest", str(insecure), "--buckets-dir", str(tmp_path)])
    verify_args = rm.parse_args(
        ["--manifest", str(insecure), "--buckets-dir", str(tmp_path), "--verify-downloads"]
    )
    apply_args = rm.parse_args(
        ["--manifest", str(insecure), "--buckets-dir", str(tmp_path), "--apply"]
    )
    assert dry_args.apply is False
    assert dry_args.verify_downloads is False
    assert verify_args.apply is False
    assert verify_args.verify_downloads is True
    assert apply_args.apply is True
    assert apply_args.verify_downloads is False
    with pytest.raises(SystemExit):
        rm.parse_args(
            [
                "--manifest",
                str(insecure),
                "--buckets-dir",
                str(tmp_path),
                "--verify-downloads",
                "--apply",
            ]
        )


@pytest.mark.asyncio
async def test_apply_and_verify_downloads_are_mutually_exclusive_at_api_boundary(
    tmp_path: Path,
) -> None:
    with pytest.raises(rm.RehydrationError, match="mutually exclusive"):
        await rm.rehydrate_media(
            tmp_path / "not-read.json",
            tmp_path / "not-read-vault",
            apply=True,
            verify_downloads=True,
        )


@pytest.mark.asyncio
async def test_verify_downloads_is_ephemeral_deduplicated_and_never_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    first_bucket = _seed_bucket(vault, "bucket-verify-a")
    second_bucket = _seed_bucket(vault, "bucket-verify-b")
    originals = {first_bucket: first_bucket.read_bytes(), second_bucket: second_bucket.read_bytes()}
    data = b"verified-once"
    source_url = "https://media.example.test/shared.png?token=do-not-log"
    manifest = _write_manifest(
        tmp_path / "manifest.json",
        [
            _item("bucket-verify-a", source_url, data, type="image/png"),
            _item("bucket-verify-b", source_url, data, type="image/png"),
        ],
    )
    requests = 0
    temporary_directories: list[Path] = []
    original_verify_all = rm._verify_downloads_all

    def capture_download_directory(items, directory, **kwargs):
        temporary_directories.append(directory)
        assert directory.exists()
        return original_verify_all(items, directory, **kwargs)

    class ForbiddenMediaStore:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError("verify-downloads must not instantiate MediaStore")

    monkeypatch.setattr(rm, "_verify_downloads_all", capture_download_directory)
    monkeypatch.setattr(rm, "MediaStore", ForbiddenMediaStore)

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            200,
            content=data,
            headers={"content-type": "image/png", "content-length": str(len(data))},
        )

    client = _client(handler)
    try:
        report = await rm.rehydrate_media(
            manifest,
            vault,
            verify_downloads=True,
            client=client,
            resolver=PUBLIC_RESOLVER,
        )
    finally:
        client.close()

    assert requests == 1
    assert report["mode"] == "verify-downloads"
    assert report["network_accessed"] is True
    assert report["counts"] == {
        "manifest": 2,
        "unique_sources": 1,
        "verified_sources": 1,
        "failed_sources": 0,
        "verified": 2,
        "failed": 0,
        "downloaded_bytes": len(data),
    }
    assert [source["status"] for source in report["sources"]] == ["verified"]
    assert [item["status"] for item in report["items"]] == ["verified", "verified"]
    assert all(item["sha256"] == hashlib.sha256(data).hexdigest() for item in report["items"])
    assert all(item["mime_type"] == "image/png" for item in report["items"])
    assert all(path.read_bytes() == original for path, original in originals.items())
    assert not (vault / "_media").exists()
    assert len(temporary_directories) == 1
    assert not temporary_directories[0].exists()
    serialized = json.dumps(report)
    assert source_url not in serialized
    assert "token=do-not-log" not in serialized
    assert "https://" not in serialized


@pytest.mark.asyncio
async def test_verify_download_failure_is_reported_after_other_sources_are_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    bucket = _seed_bucket(vault, "bucket-verify-failure")
    before = bucket.read_bytes()
    good_data = b"still-verified"
    manifest = _write_manifest(
        tmp_path / "manifest.json",
        [
            _item("bucket-verify-failure", "https://media.example.test/missing.png"),
            _item(
                "bucket-verify-failure",
                "https://media.example.test/good.png",
                good_data,
            ),
        ],
    )
    temporary_directories: list[Path] = []
    original_verify_all = rm._verify_downloads_all

    def capture_download_directory(items, directory, **kwargs):
        temporary_directories.append(directory)
        assert directory.exists()
        return original_verify_all(items, directory, **kwargs)

    monkeypatch.setattr(rm, "_verify_downloads_all", capture_download_directory)
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/missing.png":
            return httpx.Response(404, content=b"missing")
        return httpx.Response(200, content=good_data)

    client = _client(handler)
    try:
        report = await rm.rehydrate_media(
            manifest,
            vault,
            verify_downloads=True,
            client=client,
            resolver=PUBLIC_RESOLVER,
        )
    finally:
        client.close()

    assert requested_paths == ["/missing.png", "/good.png"]
    assert report["counts"] == {
        "manifest": 2,
        "unique_sources": 2,
        "verified_sources": 1,
        "failed_sources": 1,
        "verified": 1,
        "failed": 1,
        "downloaded_bytes": len(good_data),
    }
    assert [source["status"] for source in report["sources"]] == ["failed", "verified"]
    assert [item["status"] for item in report["items"]] == ["failed", "verified"]
    assert "HTTP 404" in report["sources"][0]["error"]
    assert bucket.read_bytes() == before
    assert not (vault / "_media").exists()
    assert len(temporary_directories) == 1
    assert not temporary_directories[0].exists()
    serialized = json.dumps(report)
    assert "https://" not in serialized
    assert "media.example.test" not in serialized


@pytest.mark.asyncio
async def test_verify_transport_failure_reports_only_exception_class_and_source_ref(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    bucket = _seed_bucket(vault, "bucket-verify-timeout")
    source_url = "https://private.example.test/image.png?token=must-not-leak"
    manifest = _write_manifest(
        tmp_path / "manifest.json",
        [_item("bucket-verify-timeout", source_url)],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"secret transport detail: {request.url}", request=request)

    client = _client(handler)
    try:
        report = await rm.rehydrate_media(
            manifest,
            vault,
            verify_downloads=True,
            client=client,
            resolver=PUBLIC_RESOLVER,
        )
    finally:
        client.close()

    error = report["sources"][0]["error"]
    assert "ReadTimeout" in error
    assert report["sources"][0]["source_ref"] in error
    assert source_url not in error
    assert "private.example.test" not in error
    assert "token=must-not-leak" not in error
    assert bucket.exists()
    assert not (vault / "_media").exists()


def test_main_returns_nonzero_with_complete_report_for_verification_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_rehydrate(*_args, **_kwargs):
        return {
            "format": rm.MANIFEST_FORMAT,
            "mode": "verify-downloads",
            "network_accessed": True,
            "sources": [{"source_ref": "url-sha256:fixture", "status": "failed"}],
            "items": [],
            "counts": {"failed_sources": 1},
        }

    monkeypatch.setattr(rm, "rehydrate_media", fake_rehydrate)
    exit_code = rm.main(
        [
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--buckets-dir",
            str(tmp_path / "vault"),
            "--verify-downloads",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["ok"] is False
    assert payload["counts"]["failed_sources"] == 1


@pytest.mark.asyncio
async def test_missing_bucket_fails_before_network(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "dynamic").mkdir(parents=True)
    manifest = _write_manifest(
        tmp_path / "manifest.json",
        [_item("missing-bucket", "https://media.example.test/missing.png")],
    )
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"unexpected")

    client = _client(handler)
    try:
        with pytest.raises(rm.RehydrationError, match="missing bucket"):
            await rm.rehydrate_media(
                manifest,
                vault,
                apply=True,
                client=client,
                resolver=PUBLIC_RESOLVER,
            )
    finally:
        client.close()
    assert calls == 0


@pytest.mark.asyncio
async def test_apply_round_trip_is_idempotent_and_never_persists_source_url(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    bucket = _seed_bucket(vault, "bucket-idempotent")
    data = b"synthetic-png-fixture"
    source_url = "https://media.example.test/private.png?token=do-not-log"
    manifest = _write_manifest(
        tmp_path / "manifest.json",
        [_item("bucket-idempotent", source_url, data, type="image/png", title="fixture")],
    )
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert request.url.path == "/private.png"
        return httpx.Response(
            200,
            content=data,
            headers={"content-type": "image/png", "content-length": str(len(data))},
        )

    client = _client(handler)
    try:
        first = await rm.rehydrate_media(
            manifest,
            vault,
            apply=True,
            client=client,
            resolver=PUBLIC_RESOLVER,
        )
        second = await rm.rehydrate_media(
            manifest,
            vault,
            apply=True,
            client=client,
            resolver=PUBLIC_RESOLVER,
        )
    finally:
        client.close()

    digest = hashlib.sha256(data).hexdigest()
    post = frontmatter.load(bucket)
    media = post["media"]
    assert requests == 2
    assert first["items"][0]["status"] == "rehydrated"
    assert second["items"][0]["status"] == "already_stored"
    assert len(media) == 1
    assert media[0]["sha256"] == digest
    assert media[0]["stored"] is True
    assert (vault / media[0]["path"]).read_bytes() == data
    assert source_url not in bucket.read_text(encoding="utf-8")
    assert source_url not in json.dumps(first)
    assert "token=do-not-log" not in json.dumps(first)


@pytest.mark.asyncio
async def test_download_failure_leaves_all_markdown_and_media_untouched(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    first_bucket = _seed_bucket(vault, "bucket-download-a")
    second_bucket = _seed_bucket(vault, "bucket-download-b")
    originals = {first_bucket: first_bucket.read_bytes(), second_bucket: second_bucket.read_bytes()}
    first_data = b"first-download"
    manifest = _write_manifest(
        tmp_path / "manifest.json",
        [
            _item("bucket-download-a", "https://media.example.test/a.png", first_data),
            _item("bucket-download-b", "https://media.example.test/b.png"),
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a.png":
            return httpx.Response(200, content=first_data)
        return httpx.Response(503, content=b"unavailable")

    client = _client(handler)
    try:
        with pytest.raises(rm.RehydrationError, match="HTTP 503"):
            await rm.rehydrate_media(
                manifest,
                vault,
                apply=True,
                client=client,
                resolver=PUBLIC_RESOLVER,
            )
    finally:
        client.close()

    assert all(path.read_bytes() == data for path, data in originals.items())
    assert not (vault / "_media").exists()


@pytest.mark.asyncio
async def test_persistence_failure_rolls_back_media_before_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    first_bucket = _seed_bucket(vault, "bucket-persist-a")
    second_bucket = _seed_bucket(vault, "bucket-persist-b")
    originals = {first_bucket: first_bucket.read_bytes(), second_bucket: second_bucket.read_bytes()}
    data_a = b"persist-a"
    data_b = b"persist-b"
    manifest = _write_manifest(
        tmp_path / "manifest.json",
        [
            _item("bucket-persist-a", "https://media.example.test/a.png", data_a),
            _item("bucket-persist-b", "https://media.example.test/b.png", data_b),
        ],
    )
    original_persist = rm.MediaStore.persist
    calls = 0

    async def fail_second(self, bucket_id, media):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise rm.MediaPersistenceError("synthetic persistence failure")
        return await original_persist(self, bucket_id, media)

    monkeypatch.setattr(rm.MediaStore, "persist", fail_second)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=data_a if request.url.path == "/a.png" else data_b)

    client = _client(handler)
    try:
        with pytest.raises(rm.RehydrationError, match="synthetic persistence failure"):
            await rm.rehydrate_media(
                manifest,
                vault,
                apply=True,
                client=client,
                resolver=PUBLIC_RESOLVER,
            )
    finally:
        client.close()

    assert all(path.read_bytes() == data for path, data in originals.items())
    media_root = vault / "_media"
    assert not media_root.exists() or not list(media_root.rglob("*.*"))


@pytest.mark.asyncio
async def test_markdown_write_failure_restores_prior_buckets_and_new_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    first_bucket = _seed_bucket(vault, "bucket-write-a")
    second_bucket = _seed_bucket(vault, "bucket-write-b")
    originals = {first_bucket: first_bucket.read_bytes(), second_bucket: second_bucket.read_bytes()}
    data_a = b"write-a"
    data_b = b"write-b"
    manifest = _write_manifest(
        tmp_path / "manifest.json",
        [
            _item("bucket-write-a", "https://media.example.test/a.png", data_a),
            _item("bucket-write-b", "https://media.example.test/b.png", data_b),
        ],
    )
    original_atomic = rm._atomic_write_bytes
    failed = False

    def fail_once(path: Path, data: bytes) -> None:
        nonlocal failed
        if path == second_bucket and not failed:
            failed = True
            raise OSError("synthetic Markdown failure")
        original_atomic(path, data)

    monkeypatch.setattr(rm, "_atomic_write_bytes", fail_once)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=data_a if request.url.path == "/a.png" else data_b)

    client = _client(handler)
    try:
        with pytest.raises(rm.RehydrationError, match="all prior writes were rolled back"):
            await rm.rehydrate_media(
                manifest,
                vault,
                apply=True,
                client=client,
                resolver=PUBLIC_RESOLVER,
            )
    finally:
        client.close()

    assert all(path.read_bytes() == data for path, data in originals.items())
    media_root = vault / "_media"
    assert not media_root.exists() or not list(media_root.rglob("*.*"))


def test_manifest_and_bucket_limits_fail_before_apply(tmp_path: Path) -> None:
    oversized = _write_manifest(
        tmp_path / "too-many.json",
        [
            _item("bucket", f"https://media.example.test/{index}.png")
            for index in range(3)
        ],
    )
    with pytest.raises(rm.RehydrationError, match="limit is 2"):
        rm.load_manifest(oversized, max_items=2)

    too_large = _write_manifest(
        tmp_path / "too-large.json",
        [
            _item(
                "bucket",
                "https://media.example.test/large.png",
                expected_size=11,
            )
        ],
    )
    with pytest.raises(rm.RehydrationError, match="per-item limit"):
        rm.load_manifest(too_large, max_item_bytes=10)


@pytest.mark.asyncio
async def test_per_bucket_limit_and_private_dns_are_rejected_without_writes(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    existing = [{"path": f"_media/existing/{index}.png"} for index in range(20)]
    limited_bucket = _seed_bucket(vault, "bucket-full", media=existing)
    before = limited_bucket.read_bytes()
    manifest = _write_manifest(
        tmp_path / "full.json",
        [_item("bucket-full", "https://media.example.test/new.png")],
    )
    with pytest.raises(rm.RehydrationError, match="20-item media limit"):
        await rm.rehydrate_media(manifest, vault)
    assert limited_bucket.read_bytes() == before

    private_bucket = _seed_bucket(vault, "bucket-private")
    private_before = private_bucket.read_bytes()
    private_manifest = _write_manifest(
        tmp_path / "private.json",
        [_item("bucket-private", "https://internal.example.test/image.png")],
    )
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"must-not-download")

    client = _client(handler)
    try:
        with pytest.raises(rm.RehydrationError, match="non-public address"):
            await rm.rehydrate_media(
                private_manifest,
                vault,
                apply=True,
                client=client,
                resolver=lambda _host, _port: ["127.0.0.1"],
            )
    finally:
        client.close()
    assert calls == 0
    assert private_bucket.read_bytes() == private_before
    assert not (vault / "_media").exists()
