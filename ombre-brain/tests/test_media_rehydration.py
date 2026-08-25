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
    apply_args = rm.parse_args(
        ["--manifest", str(insecure), "--buckets-dir", str(tmp_path), "--apply"]
    )
    assert dry_args.apply is False
    assert apply_args.apply is True


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
