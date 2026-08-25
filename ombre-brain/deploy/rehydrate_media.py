#!/usr/bin/env python3
"""Safely attach explicitly listed remote media to existing Ombre Brain buckets.

The tool never scans bucket content for URLs.  Its input is a user-authored JSON
manifest, and its default mode is a network-free dry run.  ``--apply`` is
required before any DNS lookup, HTTP request, media write, or Markdown update.

Manifest example::

    {
      "format": "ombre-media-rehydration-v1",
      "items": [
        {
          "bucket_id": "a7d2d8f38ab3",
          "source_url": "https://example.invalid/image.png",
          "filename": "image.png",
          "expected_sha256": "<optional 64 lowercase hex chars>",
          "expected_size": 68,
          "type": "image/png",
          "title": "optional title",
          "note": "optional note"
        }
      ]
    }

Full source URLs are neither persisted nor included in reports.  Reports use a
short SHA-256-derived source reference instead.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
import re
import socket
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import unquote, urljoin, urlsplit

import frontmatter
import httpx

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ombrebrain.storage.media_store import MediaPersistenceError, MediaStore

MANIFEST_FORMAT = "ombre-media-rehydration-v1"
DEFAULT_MAX_ITEMS = 100
DEFAULT_MAX_ITEM_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 256 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_SOURCE_URL_CHARS = 8192
MAX_REDIRECTS = 5
MEDIA_PER_BUCKET_LIMIT = 20
_BUCKET_SUBDIRS = ("permanent", "dynamic", "archive", "feel", "plans", "letters")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BUCKET_ID_RE = re.compile(r"^[^\x00-\x1f/\\]{1,128}$")

Resolver = Callable[[str, int], Iterable[str]]


class RehydrationError(ValueError):
    """A safe, user-facing rehydration failure."""


@dataclass(frozen=True)
class ManifestItem:
    bucket_id: str
    source_url: str
    source_ref: str
    filename: str
    expected_sha256: str | None
    expected_size: int | None
    mime_type: str
    title: str
    note: str


@dataclass(frozen=True)
class BucketSnapshot:
    bucket_id: str
    path: Path
    original: bytes
    media: list[dict[str, Any]]


@dataclass(frozen=True)
class DownloadedItem:
    ordinal: int
    item: ManifestItem
    path: Path
    sha256: str
    size: int
    mime_type: str


def _source_ref(url: str) -> str:
    return f"url-sha256:{hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]}"


def _clean_filename(value: str, source_url: str) -> str:
    candidate = value.strip()
    if not candidate:
        candidate = unquote(Path(urlsplit(source_url).path).name)
    candidate = Path(candidate).name.strip()
    if not candidate or candidate in {".", ".."}:
        return "media"
    cleaned = re.sub(r"[^\w.()\- ]", "_", candidate, flags=re.UNICODE).strip(" .")
    return cleaned[:200] or "media"


def _validate_url_syntax(value: object) -> str:
    url = str(value or "").strip()
    if not url:
        raise RehydrationError("manifest item source_url is required")
    if len(url) > MAX_SOURCE_URL_CHARS or any(ord(char) < 32 for char in url):
        raise RehydrationError(f"source URL is malformed ({_source_ref(url)})")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise RehydrationError(f"invalid source URL ({_source_ref(url)})") from exc
    if parsed.scheme.lower() != "https":
        raise RehydrationError(f"source URL must use https ({_source_ref(url)})")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise RehydrationError(f"source URL authority is unsafe ({_source_ref(url)})")
    if parsed.fragment:
        raise RehydrationError(f"source URL fragments are not allowed ({_source_ref(url)})")
    if port is not None and not 1 <= port <= 65535:
        raise RehydrationError(f"source URL port is invalid ({_source_ref(url)})")
    return url


def _bounded_text(value: object, *, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise RehydrationError(f"manifest item {field} exceeds {limit} characters")
    return text


def load_manifest(
    path: Path,
    *,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_item_bytes: int = DEFAULT_MAX_ITEM_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> list[ManifestItem]:
    """Parse and validate an explicit manifest without touching the network."""
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise RehydrationError(
                f"manifest exceeds the {MAX_MANIFEST_BYTES}-byte file limit"
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
    except RehydrationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RehydrationError(f"cannot read JSON manifest: {path}") from exc
    if not isinstance(raw, dict) or raw.get("format") != MANIFEST_FORMAT:
        raise RehydrationError(f"manifest format must be {MANIFEST_FORMAT}")
    rows = raw.get("items")
    if not isinstance(rows, list):
        raise RehydrationError("manifest items must be a JSON array")
    if len(rows) > max_items:
        raise RehydrationError(f"manifest has {len(rows)} items; limit is {max_items}")

    items: list[ManifestItem] = []
    declared_total = 0
    for index, raw_item in enumerate(rows):
        if not isinstance(raw_item, dict):
            raise RehydrationError(f"manifest item {index} must be an object")
        bucket_id = str(raw_item.get("bucket_id") or "").strip()
        if not _BUCKET_ID_RE.fullmatch(bucket_id):
            raise RehydrationError(f"manifest item {index} has an invalid bucket_id")
        source_url = _validate_url_syntax(raw_item.get("source_url"))

        expected_sha256_raw = str(raw_item.get("expected_sha256") or "").strip().lower()
        expected_sha256 = expected_sha256_raw or None
        if expected_sha256 is not None and not _SHA256_RE.fullmatch(expected_sha256):
            raise RehydrationError(
                f"manifest item {index} expected_sha256 must be 64 hexadecimal characters"
            )

        expected_size_raw = raw_item.get("expected_size")
        expected_size: int | None
        if expected_size_raw is None or expected_size_raw == "":
            expected_size = None
        else:
            if isinstance(expected_size_raw, bool):
                raise RehydrationError(f"manifest item {index} expected_size must be an integer")
            if isinstance(expected_size_raw, int):
                expected_size = expected_size_raw
            elif isinstance(expected_size_raw, str) and expected_size_raw.isdecimal():
                expected_size = int(expected_size_raw)
            else:
                raise RehydrationError(
                    f"manifest item {index} expected_size must be an integer"
                )
            if expected_size < 0 or expected_size > max_item_bytes:
                raise RehydrationError(
                    f"manifest item {index} expected_size exceeds the per-item limit"
                )
            declared_total += expected_size
            if declared_total > max_total_bytes:
                raise RehydrationError("manifest declared size exceeds the batch byte limit")

        items.append(
            ManifestItem(
                bucket_id=bucket_id,
                source_url=source_url,
                source_ref=_source_ref(source_url),
                filename=_clean_filename(str(raw_item.get("filename") or ""), source_url),
                expected_sha256=expected_sha256,
                expected_size=expected_size,
                mime_type=_bounded_text(raw_item.get("type"), field="type", limit=128),
                title=_bounded_text(raw_item.get("title"), field="title", limit=200),
                note=_bounded_text(raw_item.get("note"), field="note", limit=500),
            )
        )
    return items


def _read_bucket(path: Path, bucket_id: str) -> BucketSnapshot:
    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise RehydrationError(f"bucket is not a regular file: {bucket_id}")
        original = path.read_bytes()
        post = frontmatter.loads(original.decode("utf-8"))
    except RehydrationError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise RehydrationError(f"cannot parse bucket Markdown: {bucket_id}") from exc
    media = post.get("media") or []
    if not isinstance(media, list):
        raise RehydrationError(f"bucket media frontmatter is not a list: {bucket_id}")
    normalized = [dict(item) for item in media if isinstance(item, dict)]
    return BucketSnapshot(bucket_id=bucket_id, path=path, original=original, media=normalized)


def build_bucket_index(buckets_dir: Path) -> dict[str, Path]:
    """Index explicit bucket files only; never inspect text for URLs."""
    base = buckets_dir.resolve()
    if not base.is_dir():
        raise RehydrationError(f"buckets directory does not exist: {buckets_dir}")
    index: dict[str, Path] = {}
    for subdir in _BUCKET_SUBDIRS:
        root = base / subdir
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            try:
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                    raise RehydrationError(f"bucket is not a regular file: {path.name}")
                post = frontmatter.loads(path.read_text(encoding="utf-8"))
            except RehydrationError:
                raise
            except (OSError, UnicodeError, ValueError) as exc:
                raise RehydrationError(f"cannot parse bucket Markdown: {path.name}") from exc
            bucket_id = str(post.get("id") or "").strip()
            if not bucket_id:
                continue
            previous = index.get(bucket_id)
            if previous is not None and previous != path:
                raise RehydrationError(f"duplicate bucket id in vault: {bucket_id}")
            index[bucket_id] = path
    return index


def _safe_media_path(vault: Path, media_root: Path, raw_path: object) -> Path | None:
    value = str(raw_path or "").strip()
    if not value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = vault / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    media_root = media_root.resolve()
    if resolved == media_root or media_root not in resolved.parents:
        return None
    return resolved


def _verified_existing_hashes(
    snapshot: BucketSnapshot, vault: Path, media_root: Path
) -> set[str]:
    verified: set[str] = set()
    for entry in snapshot.media:
        digest = str(entry.get("sha256") or "").lower()
        if not _SHA256_RE.fullmatch(digest):
            continue
        path = _safe_media_path(vault, media_root, entry.get("path"))
        if path is None:
            continue
        try:
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        if actual == digest:
            verified.add(digest)
    return verified


def _resolve_public_addresses(host: str, port: int) -> Iterable[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RehydrationError("source hostname could not be resolved") from exc
    return [str(info[4][0]) for info in infos]


def _validate_remote_target(url: str, resolver: Resolver) -> None:
    parsed = urlsplit(_validate_url_syntax(url))
    host = parsed.hostname or ""
    port = parsed.port or 443
    try:
        literal = ipaddress.ip_address(host)
        addresses = [str(literal)]
    except ValueError:
        addresses = list(resolver(host, port))
    if not addresses:
        raise RehydrationError(f"source hostname resolved to no addresses ({_source_ref(url)})")
    for value in addresses:
        try:
            address = ipaddress.ip_address(value.split("%")[0])
        except ValueError as exc:
            raise RehydrationError(f"source hostname returned an invalid address ({_source_ref(url)})") from exc
        if not address.is_global:
            raise RehydrationError(f"source hostname resolved to a non-public address ({_source_ref(url)})")


def _validate_response_peer(response: httpx.Response, source_ref: str) -> None:
    """Reject a non-public connected peer when httpcore exposes the socket."""
    stream = response.extensions.get("network_stream")
    if stream is None or not hasattr(stream, "get_extra_info"):
        return
    try:
        peer = stream.get_extra_info("server_addr")
    except Exception as exc:
        raise RehydrationError(f"cannot verify download peer ({source_ref})") from exc
    if not peer:
        return
    try:
        address = ipaddress.ip_address(str(peer[0]).split("%")[0])
    except (ValueError, TypeError, IndexError) as exc:
        raise RehydrationError(f"download peer address is invalid ({source_ref})") from exc
    if not address.is_global:
        raise RehydrationError(f"download connected to a non-public address ({source_ref})")


def _download_one(
    item: ManifestItem,
    target: Path,
    *,
    client: httpx.Client,
    resolver: Resolver,
    max_item_bytes: int,
) -> tuple[str, int, str]:
    current = item.source_url
    for redirect_count in range(MAX_REDIRECTS + 1):
        _validate_remote_target(current, resolver)
        try:
            with client.stream(
                "GET",
                current,
                headers={"Accept": "*/*", "Accept-Encoding": "identity"},
            ) as response:
                _validate_response_peer(response, item.source_ref)
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location or redirect_count >= MAX_REDIRECTS:
                        raise RehydrationError(f"unsafe or excessive redirect ({item.source_ref})")
                    current = urljoin(current, location)
                    continue
                if response.status_code < 200 or response.status_code >= 300:
                    raise RehydrationError(
                        f"download returned HTTP {response.status_code} ({item.source_ref})"
                    )
                content_encoding = response.headers.get("content-encoding", "").strip().lower()
                if content_encoding not in {"", "identity"}:
                    raise RehydrationError(
                        f"download returned encoded content despite identity request ({item.source_ref})"
                    )
                declared = response.headers.get("content-length")
                if declared:
                    try:
                        declared_size = int(declared)
                    except ValueError as exc:
                        raise RehydrationError(
                            f"download returned invalid Content-Length ({item.source_ref})"
                        ) from exc
                    if declared_size < 0 or declared_size > max_item_bytes:
                        raise RehydrationError(
                            f"download exceeds per-item byte limit ({item.source_ref})"
                        )
                    if item.expected_size is not None and declared_size != item.expected_size:
                        raise RehydrationError(
                            f"download size does not match manifest ({item.source_ref})"
                        )
                digest = hashlib.sha256()
                size = 0
                with target.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > max_item_bytes:
                            raise RehydrationError(
                                f"download exceeds per-item byte limit ({item.source_ref})"
                            )
                        handle.write(chunk)
                        digest.update(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
        except RehydrationError:
            try:
                target.unlink()
            except OSError:
                pass
            raise
        except (httpx.HTTPError, OSError) as exc:
            try:
                target.unlink()
            except OSError:
                pass
            raise RehydrationError(f"download failed ({item.source_ref})") from exc

        actual_sha256 = digest.hexdigest()
        if item.expected_size is not None and size != item.expected_size:
            target.unlink(missing_ok=True)
            raise RehydrationError(f"download size does not match manifest ({item.source_ref})")
        if item.expected_sha256 is not None and actual_sha256 != item.expected_sha256:
            target.unlink(missing_ok=True)
            raise RehydrationError(f"download SHA-256 does not match manifest ({item.source_ref})")
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()[:128]
        return actual_sha256, size, item.mime_type or content_type
    raise RehydrationError(f"download redirect limit exceeded ({item.source_ref})")


def _download_all(
    items: list[ManifestItem],
    directory: Path,
    *,
    client: httpx.Client,
    resolver: Resolver,
    max_item_bytes: int,
    max_total_bytes: int,
) -> list[DownloadedItem]:
    downloaded: list[DownloadedItem] = []
    total = 0
    for index, item in enumerate(items):
        suffix = Path(item.filename).suffix[:12]
        target = directory / f"{index:05d}{suffix}"
        digest, size, mime_type = _download_one(
            item,
            target,
            client=client,
            resolver=resolver,
            max_item_bytes=max_item_bytes,
        )
        total += size
        if total > max_total_bytes:
            raise RehydrationError("downloaded media exceeds the batch byte limit")
        downloaded.append(
            DownloadedItem(
                ordinal=index,
                item=item,
                path=target,
                sha256=digest,
                size=size,
                mime_type=mime_type,
            )
        )
    return downloaded


def _report_entry(item: ManifestItem, status: str, **extra: Any) -> dict[str, Any]:
    return {
        "bucket_id": item.bucket_id,
        "source_ref": item.source_ref,
        "status": status,
        **extra,
    }


def _dry_run_report(
    items: list[ManifestItem],
    snapshots: dict[str, BucketSnapshot],
    vault: Path,
    media_root: Path,
) -> dict[str, Any]:
    report_items: list[dict[str, Any]] = []
    pending_by_bucket: dict[str, int] = {}
    seen_expected: set[tuple[str, str]] = set()
    verified_by_bucket = {
        bucket_id: _verified_existing_hashes(snapshot, vault, media_root)
        for bucket_id, snapshot in snapshots.items()
    }
    for item in items:
        verified = verified_by_bucket[item.bucket_id]
        if item.expected_sha256 and item.expected_sha256 in verified:
            report_items.append(_report_entry(item, "already_stored", sha256=item.expected_sha256))
            continue
        if item.expected_sha256:
            key = (item.bucket_id, item.expected_sha256)
            if key in seen_expected:
                report_items.append(_report_entry(item, "duplicate_manifest_item"))
                continue
            seen_expected.add(key)
        pending_by_bucket[item.bucket_id] = pending_by_bucket.get(item.bucket_id, 0) + 1
        report_items.append(_report_entry(item, "pending_download"))

    for bucket_id, pending in pending_by_bucket.items():
        existing_count = len(snapshots[bucket_id].media)
        if existing_count + pending > MEDIA_PER_BUCKET_LIMIT:
            raise RehydrationError(
                f"bucket {bucket_id} would exceed the {MEDIA_PER_BUCKET_LIMIT}-item media limit"
            )
    return {
        "format": MANIFEST_FORMAT,
        "mode": "dry-run",
        "network_accessed": False,
        "items": report_items,
        "counts": {
            "manifest": len(items),
            "pending": sum(item["status"] == "pending_download" for item in report_items),
            "already_stored": sum(item["status"] == "already_stored" for item in report_items),
            "duplicate_manifest_items": sum(
                item["status"] == "duplicate_manifest_item" for item in report_items
            ),
        },
    }


def _media_file_snapshot(media_dir: Path, bucket_ids: Iterable[str]) -> set[Path]:
    files: set[Path] = set()
    for bucket_id in set(bucket_ids):
        safe_bucket = re.sub(r"[^a-zA-Z0-9_.-]", "_", bucket_id)[:128]
        root = media_dir / safe_bucket
        if not root.is_dir():
            continue
        for path in root.iterdir():
            try:
                if path.is_file() and not path.is_symlink():
                    files.add(path.resolve())
            except OSError:
                continue
    return files


def _remove_new_media(
    before: set[Path],
    media_dir: Path,
    bucket_ids: Iterable[str],
    *,
    remove_media_root: bool = False,
) -> None:
    after = _media_file_snapshot(media_dir, bucket_ids)
    for path in sorted(after - before, reverse=True):
        try:
            path.unlink()
        except OSError:
            pass
    for bucket_id in set(bucket_ids):
        safe_bucket = re.sub(r"[^a-zA-Z0-9_.-]", "_", bucket_id)[:128]
        try:
            (media_dir / safe_bucket).rmdir()
        except OSError:
            pass
    if remove_media_root:
        try:
            media_dir.rmdir()
        except OSError:
            pass


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    try:
        original_mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        original_mode = None
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if original_mode is not None:
            os.chmod(temporary, original_mode)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _render_updated_bucket(snapshot: BucketSnapshot, appended: list[dict[str, Any]]) -> bytes:
    try:
        post = frontmatter.loads(snapshot.original.decode("utf-8"))
        existing = [dict(item) for item in (post.get("media") or []) if isinstance(item, dict)]
        seen_paths = {str(item.get("path") or "") for item in existing}
        for item in appended:
            path = str(item.get("path") or "")
            if path and path not in seen_paths:
                existing.append(dict(item))
                seen_paths.add(path)
        if len(existing) > MEDIA_PER_BUCKET_LIMIT:
            raise RehydrationError(
                f"bucket {snapshot.bucket_id} exceeds the {MEDIA_PER_BUCKET_LIMIT}-item media limit"
            )
        if existing:
            post["media"] = existing
        else:
            post.metadata.pop("media", None)
        return frontmatter.dumps(post).encode("utf-8")
    except RehydrationError:
        raise
    except Exception as exc:
        raise RehydrationError(f"cannot serialize bucket Markdown: {snapshot.bucket_id}") from exc


async def rehydrate_media(
    manifest_path: Path,
    buckets_dir: Path,
    *,
    media_dir: Path | None = None,
    apply: bool = False,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_item_bytes: int = DEFAULT_MAX_ITEM_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
    resolver: Resolver = _resolve_public_addresses,
) -> dict[str, Any]:
    """Validate or apply one explicit media manifest.

    Dry-run performs no DNS or HTTP work.  Apply downloads every item before the
    first persistent write.  Any download/persistence/serialization failure
    leaves Markdown unchanged and removes media files created by this call.
    """
    items = load_manifest(
        manifest_path,
        max_items=max_items,
        max_item_bytes=max_item_bytes,
        max_total_bytes=max_total_bytes,
    )
    vault = buckets_dir.resolve()
    index = build_bucket_index(vault)
    requested_ids = {item.bucket_id for item in items}
    missing = sorted(requested_ids - set(index))
    if missing:
        raise RehydrationError(f"manifest references missing bucket: {missing[0]}")
    snapshots = {bucket_id: _read_bucket(index[bucket_id], bucket_id) for bucket_id in requested_ids}
    effective_media_dir = (media_dir or (vault / "_media")).resolve()
    dry_report = _dry_run_report(items, snapshots, vault, effective_media_dir)
    if not apply:
        return dry_report
    if not items:
        return {**dry_report, "mode": "apply", "network_accessed": False}

    own_client = client is None
    http_client = client or httpx.Client(
        timeout=timeout_seconds,
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": "ombre-media-rehydration/1"},
    )
    try:
        with tempfile.TemporaryDirectory(prefix="ombre-media-rehydrate-") as temporary:
            downloaded = await asyncio.to_thread(
                _download_all,
                items,
                Path(temporary),
                client=http_client,
                resolver=resolver,
                max_item_bytes=max_item_bytes,
                max_total_bytes=max_total_bytes,
            )

            selected: dict[str, list[DownloadedItem]] = {}
            status_by_ordinal: dict[int, tuple[str, str]] = {}
            verified_by_bucket = {
                bucket_id: _verified_existing_hashes(
                    snapshot, vault, effective_media_dir
                )
                for bucket_id, snapshot in snapshots.items()
            }
            for entry in downloaded:
                verified = verified_by_bucket[entry.item.bucket_id]
                if entry.sha256 in verified:
                    status_by_ordinal[entry.ordinal] = (
                        "already_stored",
                        entry.sha256,
                    )
                    continue
                if any(existing.sha256 == entry.sha256 for existing in selected.get(entry.item.bucket_id, [])):
                    status_by_ordinal[entry.ordinal] = (
                        "duplicate_manifest_item",
                        entry.sha256,
                    )
                    continue
                selected.setdefault(entry.item.bucket_id, []).append(entry)

            for bucket_id, entries in selected.items():
                if len(snapshots[bucket_id].media) + len(entries) > MEDIA_PER_BUCKET_LIMIT:
                    raise RehydrationError(
                        f"bucket {bucket_id} would exceed the {MEDIA_PER_BUCKET_LIMIT}-item media limit"
                    )

            touched_ids = list(selected)
            before_media = _media_file_snapshot(effective_media_dir, touched_ids)
            media_root_existed = effective_media_dir.exists()
            persisted: dict[str, list[dict[str, Any]]] = {}
            try:
                if selected:
                    # The persistent media directory is created only after every
                    # remote object has downloaded and passed validation.
                    store = MediaStore(
                        str(vault),
                        str(effective_media_dir),
                        max_bytes=max_item_bytes,
                        max_items=MEDIA_PER_BUCKET_LIMIT,
                    )
                for bucket_id, entries in selected.items():
                    payload: list[dict[str, Any]] = []
                    for entry in entries:
                        item: dict[str, Any] = {
                            "path": str(entry.path),
                            "filename": entry.item.filename,
                        }
                        if entry.mime_type:
                            item["type"] = entry.mime_type
                        if entry.item.title:
                            item["title"] = entry.item.title
                        if entry.item.note:
                            item["note"] = entry.item.note
                        payload.append(item)
                    persisted[bucket_id] = await store.persist(bucket_id, payload)
            except Exception:
                _remove_new_media(
                    before_media,
                    effective_media_dir,
                    touched_ids,
                    remove_media_root=not media_root_existed,
                )
                raise

            try:
                rendered = {
                    bucket_id: _render_updated_bucket(snapshots[bucket_id], media)
                    for bucket_id, media in persisted.items()
                }
            except Exception:
                _remove_new_media(
                    before_media,
                    effective_media_dir,
                    touched_ids,
                    remove_media_root=not media_root_existed,
                )
                raise

            written: list[str] = []
            try:
                for bucket_id, data in rendered.items():
                    _atomic_write_bytes(snapshots[bucket_id].path, data)
                    written.append(bucket_id)
            except Exception as exc:
                restore_errors: list[str] = []
                for bucket_id in reversed(written):
                    try:
                        _atomic_write_bytes(
                            snapshots[bucket_id].path,
                            snapshots[bucket_id].original,
                        )
                    except Exception:
                        restore_errors.append(bucket_id)
                _remove_new_media(
                    before_media,
                    effective_media_dir,
                    touched_ids,
                    remove_media_root=not media_root_existed,
                )
                if restore_errors:
                    raise RehydrationError(
                        f"Markdown write failed and rollback failed for bucket: {restore_errors[0]}"
                    ) from exc
                raise RehydrationError("Markdown write failed; all prior writes were rolled back") from exc

            for bucket_id, entries in selected.items():
                for entry in entries:
                    status_by_ordinal[entry.ordinal] = ("rehydrated", entry.sha256)

            report_items: list[dict[str, Any]] = []
            for entry in downloaded:
                status, digest = status_by_ordinal[entry.ordinal]
                report_items.append(
                    _report_entry(
                        entry.item,
                        status,
                        sha256=digest,
                        size=entry.size,
                    )
                )
            return {
                "format": MANIFEST_FORMAT,
                "mode": "apply",
                "network_accessed": True,
                "items": report_items,
                "counts": {
                    "manifest": len(items),
                    "rehydrated": sum(item["status"] == "rehydrated" for item in report_items),
                    "already_stored": sum(
                        item["status"] == "already_stored" for item in report_items
                    ),
                    "duplicate_manifest_items": sum(
                        item["status"] == "duplicate_manifest_item" for item in report_items
                    ),
                    "downloaded_bytes": sum(item.size for item in downloaded),
                },
            }
    except MediaPersistenceError as exc:
        raise RehydrationError(str(exc)) from exc
    finally:
        if own_client:
            http_client.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--buckets-dir", type=Path, required=True)
    parser.add_argument("--media-dir", type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform DNS/HTTP and writes; omitted means network-free dry-run",
    )
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--max-item-bytes", type=int, default=DEFAULT_MAX_ITEM_BYTES)
    parser.add_argument("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    if args.max_items < 1 or args.max_item_bytes < 1 or args.max_total_bytes < 1:
        parser.error("all limits must be positive")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = asyncio.run(
            rehydrate_media(
                args.manifest,
                args.buckets_dir,
                media_dir=args.media_dir,
                apply=args.apply,
                max_items=args.max_items,
                max_item_bytes=args.max_item_bytes,
                max_total_bytes=args.max_total_bytes,
                timeout_seconds=args.timeout_seconds,
            )
        )
    except RehydrationError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, **report}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
