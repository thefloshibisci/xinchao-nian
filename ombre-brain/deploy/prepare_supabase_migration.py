#!/usr/bin/env python3
"""Prepare verified Supabase exports for a staged Ombre Brain migration.

The source CSV files are the bounded exports produced by Supabase SQL Editor:
one row per UTC date, with a ``record_count`` column and a JSON ``records``
array.  This tool never connects to Supabase or Ombre Brain.  It only creates:

* size-bounded Markdown cold-archive buckets for ``chat_messages``;
* a deduplicated JSONL review queue for ``memory_summaries``;
* a small, date-balanced trial list for the first online import;
* a manifest containing counts and SHA-256 hashes.

Cold-archive buckets use ``dont_surface: true`` and ``resolved: true``.  They
remain explicitly searchable after import but do not enter spontaneous recall
or dream candidate pools.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_BUCKET_BYTES = 42_000
DEFAULT_TRIAL_SIZE = 20


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_id(prefix: str, *parts: object, size: int = 20) -> str:
    raw = "\x1f".join(str(part or "") for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:size]}"


def parse_timestamp(value: object) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def timestamp_text(value: object) -> str:
    parsed = parse_timestamp(value)
    if parsed == datetime.min.replace(tzinfo=timezone.utc):
        return ""
    return parsed.isoformat().replace("+00:00", "Z")


def raise_csv_field_limit() -> None:
    """Use the largest CSV field limit accepted by this Python build."""
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


def read_aggregated_csv(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # Supabase's date-bucketed export stores a whole day's JSON array in one
    # CSV field.  Real chat days can easily exceed Python's conservative
    # 128 KiB default even though the CSV and JSON are both valid.
    raise_csv_field_limit()
    records: list[dict[str, Any]] = []
    declared = 0
    bucket_rows = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            bucket_rows += 1
            declared += int(row.get("record_count") or 0)
            payload = json.loads(row.get("records") or "[]")
            if not isinstance(payload, list):
                raise ValueError(f"records must be an array in {path}")
            for item in payload:
                if not isinstance(item, dict):
                    raise ValueError(f"record must be an object in {path}")
                records.append(item)

    ids = [str(item.get("id") or "") for item in records]
    if not ids or any(not item for item in ids):
        raise ValueError(f"missing record id in {path}")
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate record ids in {path}")
    if declared != len(records):
        raise ValueError(
            f"declared record count does not match payload in {path}: "
            f"{declared} != {len(records)}"
        )
    records.sort(key=lambda item: (parse_timestamp(item.get("created_at")), str(item["id"])))
    return records, {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "date_buckets": bucket_rows,
        "records": len(records),
        "unique_ids": len(set(ids)),
        "first_at": timestamp_text(records[0].get("created_at")),
        "last_at": timestamp_text(records[-1].get("created_at")),
    }


def normalized_summary(text: object) -> str:
    return re.sub(r"\s+", "", str(text or "")).casefold()


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def prepare_summaries(
    records: list[dict[str, Any]], output_dir: Path, trial_size: int
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = normalized_summary(record.get("content"))
        if not key:
            continue
        groups[key].append(record)

    queue: list[dict[str, Any]] = []
    duplicate_records = 0
    for key, group in groups.items():
        ordered = sorted(
            group,
            key=lambda item: (
                1 if str(item.get("review_status") or "") == "candidate" else 0,
                parse_timestamp(item.get("created_at")),
                str(item.get("id") or ""),
            ),
            reverse=True,
        )
        canonical = ordered[0]
        duplicate_records += len(ordered) - 1
        source_ids = sorted(str(item["id"]) for item in ordered)
        queue.append(
            {
                "migration_id": stable_id("legacy_summary", key),
                "source": "supabase.memory_summaries",
                "source_ids": source_ids,
                "assistant_id": canonical.get("assistant_id"),
                "content": str(canonical.get("content") or "").strip(),
                "created_at": timestamp_text(canonical.get("created_at")),
                "review_status": str(canonical.get("review_status") or "unreviewed"),
                "reviewed_at": timestamp_text(canonical.get("reviewed_at")),
                "exact_duplicate_count": len(ordered),
                "migration_state": "review_pending",
            }
        )

    queue.sort(key=lambda item: (item["created_at"], item["migration_id"]))
    queue_path = output_dir / "summary-review-queue.jsonl"
    write_jsonl(queue_path, queue)

    by_status: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in queue:
        by_status[item["review_status"]].append(item)

    # Trial set: half candidate, half backlog where possible, spread across the
    # full time range instead of selecting a single recent topic cluster.
    preferred_statuses = ["candidate", "backlog"]
    quotas = {status: trial_size // len(preferred_statuses) for status in preferred_statuses}
    quotas[preferred_statuses[0]] += trial_size % len(preferred_statuses)

    def evenly_spaced(items: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
        if count <= 0 or not items:
            return []
        if len(items) <= count:
            return list(items)
        if count == 1:
            return [items[-1]]
        indexes = [round(index * (len(items) - 1) / (count - 1)) for index in range(count)]
        return [items[index] for index in indexes]

    trial: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for status in preferred_statuses:
        for item in evenly_spaced(by_status.get(status, []), quotas[status]):
            if item["migration_id"] not in selected_ids:
                trial.append(item)
                selected_ids.add(item["migration_id"])

    if len(trial) < trial_size:
        remaining = [item for item in queue if item["migration_id"] not in selected_ids]
        for item in evenly_spaced(remaining, trial_size - len(trial)):
            if item["migration_id"] not in selected_ids:
                trial.append(item)
                selected_ids.add(item["migration_id"])

    trial.sort(key=lambda item: (item["created_at"], item["migration_id"]))
    trial_path = output_dir / "trial-summaries.json"
    trial_path.write_text(
        json.dumps(trial, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    def write_summary_buckets(items: list[dict[str, Any]], bucket_dir: Path) -> list[Path]:
        bucket_dir.mkdir(parents=True, exist_ok=True)
        bucket_paths: list[Path] = []
        for item in items:
            bucket_id = item["migration_id"]
            content = str(item["content"] or "").strip()
            metadata = {
                "id": bucket_id,
                "name": f"旧摘要审阅 {item['created_at'][:10]}",
                "type": "dynamic",
                "domain": ["旧记忆摘要"],
                "tags": ["supabase迁移", "待审阅", "旧摘要"],
                "importance": 2 if item["review_status"] == "candidate" else 1,
                "valence": 0.5,
                "arousal": 0.3,
                "resolved": True,
                "dont_surface": True,
                "pinned": False,
                "created": item["created_at"],
                "last_active": item["created_at"],
                "source": item["source"],
                "source_ids": item["source_ids"],
                "source_review_status": item["review_status"],
                "migration_state": "review_pending",
            }
            body = (
                "这是一条从 Supabase 搬回的旧摘要，尚未晋升为日常主动记忆。"
                "它默认只供明确搜索和人工审阅。\n\n"
                f"{content}"
            )
            path = bucket_dir / f"{bucket_id}.md"
            path.write_text(markdown_frontmatter(metadata, body), encoding="utf-8", newline="\n")
            bucket_paths.append(path)
        return bucket_paths

    all_bucket_dir = output_dir / "summary-review-buckets"
    all_bucket_paths = write_summary_buckets(queue, all_bucket_dir)
    trial_bucket_dir = output_dir / "trial-summary-buckets"
    trial_bucket_paths = write_summary_buckets(trial, trial_bucket_dir)

    return {
        "input_records": len(records),
        "review_queue_records": len(queue),
        "empty_records_skipped": len(records) - sum(len(group) for group in groups.values()),
        "exact_duplicates_collapsed": duplicate_records,
        "status_counts": dict(sorted(Counter(item["review_status"] for item in queue).items())),
        "trial_records": len(trial),
        "review_queue": file_info(queue_path),
        "trial_file": file_info(trial_path),
        "review_bucket_directory": str(all_bucket_dir.resolve()),
        "review_bucket_files_sha256": stable_directory_hash(all_bucket_paths),
        "trial_bucket_directory": str(trial_bucket_dir.resolve()),
        "trial_bucket_files_sha256": stable_directory_hash(trial_bucket_paths),
    }


def yaml_scalar(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_message(record: dict[str, Any]) -> str:
    role = str(record.get("role") or "unknown").strip().lower()
    label = {"user": "用户", "assistant": "AI", "system": "系统"}.get(role, role or "未知")
    timestamp = timestamp_text(record.get("created_at"))
    content = str(record.get("content") or "").strip()
    return f"### {timestamp} · {label}\n\n{content}\n"


def markdown_frontmatter(metadata: dict[str, Any], body: str) -> str:
    lines = ["---"]
    for key, value in metadata.items():
        lines.append(f"{key}: {yaml_scalar(value)}")
    lines.extend(["---", "", body.rstrip(), ""])
    return "\n".join(lines)


def split_conversation(
    conversation_id: str,
    messages: list[dict[str, Any]],
    max_bucket_bytes: int,
) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_bytes = 0
    body_budget = max(2_000, max_bucket_bytes - 2_000)
    for message in messages:
        rendered = render_message(message)
        size = len(rendered.encode("utf-8"))
        if current and current_bytes + size > body_budget:
            chunks.append(current)
            current = []
            current_bytes = 0
        if size > body_budget:
            raise ValueError(
                f"single chat message exceeds cold archive bucket budget: "
                f"conversation={conversation_id} id={message.get('id')} bytes={size}"
            )
        current.append(message)
        current_bytes += size
    if current:
        chunks.append(current)
    return chunks


def prepare_chat_archive(
    records: list[dict[str, Any]], output_dir: Path, max_bucket_bytes: int
) -> dict[str, Any]:
    archive_dir = output_dir / "chat-archive-buckets"
    archive_dir.mkdir(parents=True, exist_ok=True)
    by_conversation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        conversation_id = str(record.get("conversation_id") or "unknown")
        by_conversation[conversation_id].append(record)

    index_rows: list[dict[str, Any]] = []
    bucket_files: list[Path] = []
    for conversation_id in sorted(by_conversation):
        messages = sorted(
            by_conversation[conversation_id],
            key=lambda item: (parse_timestamp(item.get("created_at")), str(item["id"])),
        )
        chunks = split_conversation(conversation_id, messages, max_bucket_bytes)
        for part_number, chunk in enumerate(chunks, start=1):
            first = chunk[0]
            last = chunk[-1]
            first_at = timestamp_text(first.get("created_at"))
            last_at = timestamp_text(last.get("created_at"))
            bucket_id = stable_id(
                "legacy_chat",
                conversation_id,
                str(first["id"]),
                str(last["id"]),
            )
            body = (
                "这是一份从 Supabase 搬回的原始对话冷档案。它保留原文，供明确搜索和追溯；"
                "不是日常自动浮现的摘要。\n\n"
                + "\n".join(render_message(item) for item in chunk)
            )
            metadata = {
                "id": bucket_id,
                "name": f"原始对话 {first_at[:10]} {part_number}/{len(chunks)}",
                "type": "dynamic",
                "domain": ["原始对话"],
                "tags": ["supabase迁移", "冷档案", "原对话"],
                "importance": 1,
                "valence": 0.5,
                "arousal": 0.3,
                "resolved": True,
                "dont_surface": True,
                "pinned": False,
                "created": first_at,
                "last_active": last_at,
                "source": "supabase.chat_messages",
                "source_conversation_id": conversation_id,
                "source_record_count": len(chunk),
                "source_first_id": str(first["id"]),
                "source_last_id": str(last["id"]),
            }
            rendered_bucket = markdown_frontmatter(metadata, body)
            rendered_bytes = len(rendered_bucket.encode("utf-8"))
            if rendered_bytes > max_bucket_bytes:
                raise ValueError(
                    f"rendered bucket exceeds limit: {bucket_id} "
                    f"{rendered_bytes} > {max_bucket_bytes}"
                )
            path = archive_dir / f"{bucket_id}.md"
            path.write_text(rendered_bucket, encoding="utf-8", newline="\n")
            bucket_files.append(path)
            for item in chunk:
                index_rows.append(
                    {
                        "source_record_id": str(item["id"]),
                        "conversation_id": conversation_id,
                        "created_at": timestamp_text(item.get("created_at")),
                        "role": str(item.get("role") or ""),
                        "bucket_id": bucket_id,
                    }
                )

    index_rows.sort(key=lambda item: (item["created_at"], item["source_record_id"]))
    index_path = output_dir / "chat-record-index.jsonl"
    write_jsonl(index_path, index_rows)
    sizes = [path.stat().st_size for path in bucket_files]
    return {
        "input_records": len(records),
        "indexed_records": len(index_rows),
        "conversations": len(by_conversation),
        "archive_buckets": len(bucket_files),
        "max_bucket_bytes": max(sizes, default=0),
        "configured_bucket_limit": max_bucket_bytes,
        "archive_directory": str(archive_dir.resolve()),
        "archive_files_sha256": stable_directory_hash(bucket_files),
        "record_index": file_info(index_path),
    }


def stable_directory_hash(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.name):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def file_info(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build_import_archive(
    bucket_dir: Path,
    archive_path: Path,
    *,
    created_at: str,
    label: str,
) -> dict[str, Any]:
    """Build and immediately verify an OB-native import package."""
    src_dir = Path(__file__).resolve().parents[1] / "src"
    src_text = str(src_dir)
    if src_text not in sys.path:
        sys.path.insert(0, src_text)
    from backup_archive import build_export_archive, read_backup_archive

    bucket_count = sum(1 for path in bucket_dir.rglob("*.md") if path.is_file())
    export_meta = {
        "exported_at": created_at,
        "version": "supabase-migration-v1",
        "embedding": {"model": "", "dim": 0, "backend": ""},
        "stats": {"bucket_count": bucket_count},
        "migration": {
            "source": "supabase",
            "label": label,
            "embeddings_copied": False,
            "source_mutated": False,
        },
    }
    payload, package_manifest = build_export_archive(str(bucket_dir), "", export_meta)
    archive_path.write_bytes(payload)
    verified = read_backup_archive(payload)
    if not verified.get("integrity_verified"):
        raise ValueError(f"generated import package failed integrity verification: {archive_path}")
    return {
        **file_info(archive_path),
        "bucket_count": bucket_count,
        "integrity_verified": True,
        "manifest_file_count": package_manifest["file_count"],
        "manifest_total_bytes": package_manifest["total_bytes"],
    }


def prepare(
    chat_csv: Path,
    summaries_csv: Path,
    output_dir: Path,
    *,
    max_bucket_bytes: int = DEFAULT_BUCKET_BYTES,
    trial_size: int = DEFAULT_TRIAL_SIZE,
) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    chat_records, chat_source = read_aggregated_csv(chat_csv)
    summary_records, summary_source = read_aggregated_csv(summaries_csv)
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    chat_archive = prepare_chat_archive(chat_records, output_dir, max_bucket_bytes)
    summaries = prepare_summaries(summary_records, output_dir, trial_size)
    import_packages = {
        "chat_cold_archive": build_import_archive(
            Path(chat_archive["archive_directory"]),
            output_dir / "ombre-import-chat-cold-archive.zip",
            created_at=created_at,
            label="chat-cold-archive",
        ),
        "summary_review_full": build_import_archive(
            Path(summaries["review_bucket_directory"]),
            output_dir / "ombre-import-summary-review-full.zip",
            created_at=created_at,
            label="summary-review-full",
        ),
        "summary_trial": build_import_archive(
            Path(summaries["trial_bucket_directory"]),
            output_dir / "ombre-import-summary-trial.zip",
            created_at=created_at,
            label="summary-trial-review",
        ),
    }
    manifest = {
        "format": "ombre-supabase-staged-migration-v1",
        "created_at": created_at,
        "safety": {
            "source_mutated": False,
            "online_ombre_mutated": False,
            "cold_archive_default": "explicit-search-only",
            "embeddings_copied": False,
        },
        "sources": {
            "chat_messages": chat_source,
            "memory_summaries": summary_source,
        },
        "chat_archive": chat_archive,
        "summaries": summaries,
        "import_packages": import_packages,
    }
    manifest_path = output_dir / "migration-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-csv", type=Path, required=True)
    parser.add_argument("--summaries-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-bucket-bytes", type=int, default=DEFAULT_BUCKET_BYTES)
    parser.add_argument("--trial-size", type=int, default=DEFAULT_TRIAL_SIZE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = prepare(
        args.chat_csv,
        args.summaries_csv,
        args.output_dir,
        max_bucket_bytes=args.max_bucket_bytes,
        trial_size=args.trial_size,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
