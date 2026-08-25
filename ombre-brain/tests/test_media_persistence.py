"""vNext media persistence regression tests."""

from __future__ import annotations

import base64
import hashlib
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bucket_manager import BucketManager
from ombrebrain.storage.media_store import MediaPersistenceError, MediaStore
from utils import load_config


def _encoded(data: bytes, filename: str = "image.png") -> dict[str, str]:
    return {
        "data_base64": base64.b64encode(data).decode("ascii"),
        "filename": filename,
        "type": "image/png",
    }


def _manager(tmp_path: Path, *, max_bytes: int = 1024 * 1024) -> BucketManager:
    vault = tmp_path / "vault"
    return BucketManager(
        {
            "buckets_dir": str(vault),
            "media_dir": str(vault / "_media"),
            "media_max_bytes": max_bytes,
            "matching": {},
            "storage": {"external_change_poll_seconds": 0},
        }
    )


@pytest.mark.asyncio
async def test_base64_media_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    store = MediaStore(str(vault), str(vault / "_media"))
    data = b"same-image-bytes"
    item = _encoded(data)

    first = await store.persist("bucket-1", [item])
    second = await store.persist("bucket-1", [item])

    expected_hash = hashlib.sha256(data).hexdigest()
    assert first == second
    assert first[0]["sha256"] == expected_hash
    assert first[0]["stored"] is True
    assert first[0]["path"] == f"_media/bucket-1/{expected_hash}.png"
    assert (vault / first[0]["path"]).read_bytes() == data
    assert len(list((vault / "_media" / "bucket-1").iterdir())) == 1


@pytest.mark.asyncio
async def test_media_item_limit_rejects_before_writing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    store = MediaStore(str(vault), str(vault / "_media"), max_items=2)

    with pytest.raises(MediaPersistenceError, match="最多 2 项"):
        await store.persist("bucket-too-many", [_encoded(b"a"), _encoded(b"b"), _encoded(b"c")])

    assert not (vault / "_media" / "bucket-too-many").exists()


@pytest.mark.asyncio
async def test_invalid_base64_and_size_limit_are_explicit(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    store = MediaStore(str(vault), str(vault / "_media"), max_bytes=4)

    with pytest.raises(MediaPersistenceError, match="有效 Base64"):
        await store.persist("bad-base64", [{"data_base64": "@@@", "filename": "x.png"}])
    with pytest.raises(MediaPersistenceError, match="上限 4 字节"):
        await store.persist("too-large", [_encoded(b"12345")])


@pytest.mark.asyncio
async def test_server_path_must_be_inside_allowed_temp_roots(tmp_path: Path) -> None:
    source = tmp_path / "outside.png"
    source.write_bytes(b"image")
    store = MediaStore(str(tmp_path / "vault"), str(tmp_path / "vault" / "_media"))
    store.allowed_roots = (tmp_path / "different-root",)

    with pytest.raises(MediaPersistenceError, match="不在允许的临时目录"):
        await store.persist("bucket-path", [str(source)])


@pytest.mark.asyncio
async def test_bucket_create_persists_media_before_frontmatter(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    data = b"created-image"

    bucket_id = await manager.create("带图记忆", media=[_encoded(data)])
    bucket = await manager.get(bucket_id)

    assert bucket is not None
    media = bucket["metadata"]["media"]
    assert media[0]["stored"] is True
    assert media[0]["size"] == len(data)
    assert media[0]["sha256"] == hashlib.sha256(data).hexdigest()
    assert (tmp_path / "vault" / media[0]["path"]).read_bytes() == data


@pytest.mark.asyncio
async def test_bucket_update_persists_and_deduplicates_media_append(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    bucket_id = await manager.create("先建桶")
    item = _encoded(b"appended-image")

    assert await manager.update(bucket_id, media_append=[item]) is True
    assert await manager.update(bucket_id, media_append=[item]) is True
    bucket = await manager.get(bucket_id)

    assert bucket is not None
    assert len(bucket["metadata"]["media"]) == 1


@pytest.mark.asyncio
async def test_invalid_create_media_leaves_no_memory_bucket(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    with pytest.raises(MediaPersistenceError, match="有效 Base64"):
        await manager.create(
            "不应落下半个桶",
            media=[{"data_base64": "not-base64", "filename": "x.png"}],
        )

    assert await manager.list_all(include_archive=True) == []


@pytest.mark.asyncio
async def test_invalid_update_media_does_not_modify_existing_bucket(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    bucket_id = await manager.create("原内容")
    before = await manager.get(bucket_id)
    assert before is not None
    file_path = Path(before["path"])
    original = file_path.read_bytes()

    with pytest.raises(MediaPersistenceError, match="有效 Base64"):
        await manager.update(
            bucket_id,
            media_append=[{"data_base64": "not-base64", "filename": "x.png"}],
        )

    assert file_path.read_bytes() == original


def test_media_config_defaults_to_persistent_vault_subdir(monkeypatch, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    monkeypatch.setenv("OMBRE_BUCKETS_DIR", str(vault))
    monkeypatch.delenv("OMBRE_VAULT_DIR", raising=False)
    monkeypatch.delenv("OMBRE_MEDIA_DIR", raising=False)
    monkeypatch.delenv("OMBRE_MEDIA_MAX_BYTES", raising=False)

    config = load_config(str(tmp_path / "missing-config.yaml"))

    assert config["media_dir"] == str(vault / "_media")
    assert config["media_max_bytes"] == 25 * 1024 * 1024
    assert (vault / "_media").is_dir()


def test_media_config_env_override_and_invalid_size_fallback(monkeypatch, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    media = tmp_path / "separate-media"
    monkeypatch.setenv("OMBRE_BUCKETS_DIR", str(vault))
    monkeypatch.delenv("OMBRE_VAULT_DIR", raising=False)
    monkeypatch.setenv("OMBRE_MEDIA_DIR", str(media))
    monkeypatch.setenv("OMBRE_MEDIA_MAX_BYTES", "4096")

    config = load_config(str(tmp_path / "missing-config.yaml"))
    assert config["media_dir"] == str(media)
    assert config["media_max_bytes"] == 4096
    assert media.is_dir()

    monkeypatch.setenv("OMBRE_MEDIA_MAX_BYTES", "invalid")
    config = load_config(str(tmp_path / "missing-config.yaml"))
    assert config["media_max_bytes"] == 25 * 1024 * 1024

@pytest.mark.asyncio
async def test_batch_failure_rolls_back_files_created_by_earlier_items(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    store = MediaStore(str(vault), str(vault / "_media"))

    with pytest.raises(MediaPersistenceError, match="有效 Base64"):
        await store.persist(
            "batch-rollback",
            [
                _encoded(b"first-valid-item"),
                {"data_base64": "not-base64", "filename": "second.png"},
            ],
        )

    bucket_media = vault / "_media" / "batch-rollback"
    assert not bucket_media.exists() or not list(bucket_media.iterdir())


@pytest.mark.asyncio
async def test_existing_content_addressed_target_is_verified(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    store = MediaStore(str(vault), str(vault / "_media"))
    data = b"expected-content"
    digest = hashlib.sha256(data).hexdigest()
    target = vault / "_media" / "verified-target" / f"{digest}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"corrupted-existing-content")

    with pytest.raises(MediaPersistenceError, match="哈希冲突"):
        await store.persist("verified-target", [_encoded(data)])

    assert target.read_bytes() == b"corrupted-existing-content"
