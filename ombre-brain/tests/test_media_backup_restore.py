"""Backup/export and restore coverage for persisted media binaries."""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path
import zipfile

import frontmatter
import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from backup_archive import build_export_archive, read_backup_archive
from migrate_engine import MigrateEngine


class _BucketManager:
    def __init__(self, existing: dict[str, dict] | None = None) -> None:
        self.existing = existing or {}

    async def get(self, bucket_id: str):
        return self.existing.get(bucket_id)

    async def delete(self, bucket_id: str) -> bool:
        self.existing.pop(bucket_id, None)
        return True


class _EmbeddingEngine:
    model = ""
    enabled = False
    db_path = ""
    _backend = None



def _engine(
    buckets_dir: Path,
    *,
    media_dir: Path | None = None,
    media_max_bytes: int = 1024 * 1024,
    existing: dict[str, dict] | None = None,
) -> MigrateEngine:
    return MigrateEngine(
        {
            "buckets_dir": str(buckets_dir),
            "media_dir": str(media_dir or (buckets_dir / "_media")),
            "media_max_bytes": media_max_bytes,
        },
        _BucketManager(existing),
        _EmbeddingEngine(),
    )


def _write_bucket(
    buckets_dir: Path,
    *,
    bucket_id: str = "bucket-1",
    media: list[dict] | None = None,
    content: str = "memory body",
) -> Path:
    target = buckets_dir / "dynamic" / "general" / f"memory_{bucket_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(
        content,
        id=bucket_id,
        name="memory",
        type="dynamic",
        domain=["general"],
        created="2026-08-25T00:00:00+08:00",
        media=media or [],
    )
    target.write_text(frontmatter.dumps(post), encoding="utf-8")
    return target


def _seed_media_backup(
    root: Path,
    *,
    bucket_id: str = "bucket-1",
    data: bytes = b"persisted-image",
    suffix: str = ".png",
    external_media_dir: Path | None = None,
) -> tuple[bytes, str, Path]:
    buckets_dir = root / "buckets"
    media_dir = external_media_dir or (buckets_dir / "_media")
    digest = hashlib.sha256(data).hexdigest()
    media_path = media_dir / bucket_id / f"{digest}{suffix}"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(data)
    try:
        stored_path = media_path.relative_to(buckets_dir).as_posix()
    except ValueError:
        stored_path = str(media_path.resolve())
    _write_bucket(
        buckets_dir,
        bucket_id=bucket_id,
        media=[{
            "path": stored_path,
            "sha256": digest,
            "size": len(data),
            "stored": True,
            "type": "image/png",
        }],
    )
    payload, _ = build_export_archive(
        str(buckets_dir),
        str(root / "missing-embeddings.db"),
        {"exported_at": "2026-08-25T00:00:00+08:00", "version": "test"},
        str(media_dir),
    )
    return payload, digest, media_path


def _legacy_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _bucket_markdown(bucket_id: str, media: list[dict]) -> bytes:
    return frontmatter.dumps(frontmatter.Post(
        "legacy body",
        id=bucket_id,
        name="legacy",
        type="dynamic",
        domain=["general"],
        media=media,
    )).encode("utf-8")


def _imported_markdown(buckets_dir: Path, media_dir: Path) -> Path:
    matches = [
        path for path in buckets_dir.rglob("*.md")
        if media_dir.resolve() not in path.resolve().parents
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.asyncio
async def test_export_manifest_contains_media_and_excludes_media_markdown_from_buckets(tmp_path: Path) -> None:
    payload, digest, media_path = _seed_media_backup(
        tmp_path,
        data=b"markdown-looking-media",
        suffix=".md",
    )

    package = read_backup_archive(payload)
    files = package["files"]
    media_member = f"media/bucket-1/{digest}.md"

    assert package["integrity_verified"] is True
    assert files[media_member] == media_path.read_bytes()
    assert "buckets/dynamic/general/memory_bucket-1.md" in files
    assert not any(name.startswith("buckets/_media/") for name in files)
    manifest_paths = {item["path"] for item in package["manifest"]["files"]}
    assert media_member in manifest_paths
    assert package["manifest"]["file_count"] == len(files)


@pytest.mark.asyncio
async def test_default_media_directory_round_trip(tmp_path: Path) -> None:
    payload, digest, _ = _seed_media_backup(tmp_path / "source")
    destination = tmp_path / "destination" / "buckets"
    media_dir = destination / "_media"
    engine = _engine(destination)

    parsed = await engine.parse_zip(payload)
    assert parsed["ok"] is True
    assert parsed["total_media"] == 1
    await engine.apply({})

    restored = media_dir / "bucket-1" / f"{digest}.png"
    assert restored.read_bytes() == b"persisted-image"
    post = frontmatter.load(_imported_markdown(destination, media_dir))
    assert post["media"][0]["path"] == f"_media/bucket-1/{digest}.png"
    assert post["media"][0]["stored"] is True


@pytest.mark.asyncio
async def test_external_media_directory_round_trip_rewrites_absolute_path(tmp_path: Path) -> None:
    source_media = tmp_path / "source-media"
    payload, digest, _ = _seed_media_backup(
        tmp_path / "source",
        external_media_dir=source_media,
    )
    destination = tmp_path / "destination" / "buckets"
    destination_media = tmp_path / "destination-media"
    engine = _engine(destination, media_dir=destination_media)

    assert (await engine.parse_zip(payload))["ok"] is True
    await engine.apply({})

    restored = destination_media / "bucket-1" / f"{digest}.png"
    assert restored.read_bytes() == b"persisted-image"
    post = frontmatter.load(_imported_markdown(destination, destination_media))
    assert Path(post["media"][0]["path"]) == restored.resolve()


@pytest.mark.asyncio
async def test_keep_both_uses_new_bucket_id_for_media_directory(tmp_path: Path) -> None:
    payload, digest, _ = _seed_media_backup(tmp_path / "source")
    destination = tmp_path / "destination" / "buckets"
    media_dir = destination / "_media"
    engine = _engine(
        destination,
        existing={"bucket-1": {"metadata": {"name": "existing"}}},
    )

    parsed = await engine.parse_zip(payload)
    assert parsed["conflicts_count"] == 1
    await engine.apply({"bucket-1": "keep_both"})

    children = [path for path in media_dir.iterdir() if path.is_dir()]
    assert len(children) == 1
    assert children[0].name != "bucket-1"
    restored = children[0] / f"{digest}.png"
    assert restored.read_bytes() == b"persisted-image"
    post = frontmatter.load(_imported_markdown(destination, media_dir))
    assert post["id"] == children[0].name
    assert post["media"][0]["path"] == f"_media/{children[0].name}/{digest}.png"


@pytest.mark.asyncio
async def test_stored_media_without_binary_is_rejected_during_parse(tmp_path: Path) -> None:
    digest = hashlib.sha256(b"missing").hexdigest()
    payload = _legacy_zip({
        "buckets/dynamic/general/missing.md": _bucket_markdown(
            "missing-media",
            [{"path": f"_media/missing-media/{digest}.png", "sha256": digest, "stored": True}],
        ),
    })
    engine = _engine(tmp_path / "destination")

    result = await engine.parse_zip(payload)

    assert result["ok"] is False
    assert "媒体未包含在备份中" in result["error"]


@pytest.mark.asyncio
async def test_hash_named_media_with_mismatched_content_is_rejected(tmp_path: Path) -> None:
    claimed = "0" * 64
    payload = _legacy_zip({
        f"media/bucket-1/{claimed}.png": b"different-content",
        "buckets/dynamic/general/memory.md": _bucket_markdown(
            "bucket-1",
            [{"path": f"_media/bucket-1/{claimed}.png", "sha256": claimed, "stored": True}],
        ),
    })
    engine = _engine(tmp_path / "destination")

    result = await engine.parse_zip(payload)

    assert result["ok"] is False
    assert "文件名哈希与内容不一致" in result["error"]


@pytest.mark.asyncio
async def test_media_member_over_configured_size_limit_is_rejected(tmp_path: Path) -> None:
    data = b"four"
    digest = hashlib.sha256(data).hexdigest()
    payload = _legacy_zip({
        f"media/bucket-1/{digest}.png": data,
        "buckets/dynamic/general/memory.md": _bucket_markdown(
            "bucket-1",
            [{"path": f"_media/bucket-1/{digest}.png", "sha256": digest, "stored": True}],
        ),
    })
    engine = _engine(tmp_path / "destination", media_max_bytes=3)

    result = await engine.parse_zip(payload)

    assert result["ok"] is False
    assert "超过单项上限 3 字节" in result["error"]


@pytest.mark.asyncio
async def test_existing_media_target_with_wrong_content_is_not_overwritten(tmp_path: Path) -> None:
    payload, digest, _ = _seed_media_backup(tmp_path / "source")
    destination = tmp_path / "destination" / "buckets"
    media_dir = destination / "_media"
    conflicting = media_dir / "bucket-1" / f"{digest}.png"
    conflicting.parent.mkdir(parents=True, exist_ok=True)
    conflicting.write_bytes(b"wrong-existing-content")
    engine = _engine(destination)

    assert (await engine.parse_zip(payload))["ok"] is True
    await engine.apply({})
    status = engine.get_status()

    assert conflicting.read_bytes() == b"wrong-existing-content"
    assert status["result"] == {"imported": 0, "skipped": 1}
    assert any("哈希冲突" in item for item in status["apply_errors"])
    assert not list((destination / "dynamic").rglob("*.md"))


@pytest.mark.asyncio
async def test_markdown_failure_removes_media_created_for_that_bucket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload, digest, _ = _seed_media_backup(tmp_path / "source")
    destination = tmp_path / "destination" / "buckets"
    media_dir = destination / "_media"
    engine = _engine(destination)

    assert (await engine.parse_zip(payload))["ok"] is True

    def _fail_dump(*_args, **_kwargs):
        raise RuntimeError("forced markdown failure")

    monkeypatch.setattr(frontmatter, "dumps", _fail_dump)
    await engine.apply({})

    assert not (media_dir / "bucket-1" / f"{digest}.png").exists()
    assert not list((destination / "dynamic").rglob("*.md"))
    assert any("forced markdown failure" in item for item in engine.get_status()["apply_errors"])


@pytest.mark.asyncio
async def test_legacy_path_only_media_reference_remains_importable(tmp_path: Path) -> None:
    legacy_path = r"C:\old-host\picture.png"
    payload = _legacy_zip({
        "buckets/dynamic/general/legacy.md": _bucket_markdown(
            "legacy-bucket",
            [{"path": legacy_path, "type": "image/png"}],
        ),
        "export_meta.json": json.dumps({"version": "legacy"}).encode("utf-8"),
    })
    destination = tmp_path / "destination"
    media_dir = destination / "_media"
    engine = _engine(destination)

    parsed = await engine.parse_zip(payload)
    assert parsed["ok"] is True
    assert parsed["integrity_verified"] is False
    assert parsed["total_media"] == 0
    await engine.apply({})

    post = frontmatter.load(_imported_markdown(destination, media_dir))
    assert post["media"][0]["path"] == legacy_path
    assert post["media"][0].get("stored") is not True
