from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("prepare_supabase_migration.py")
SPEC = importlib.util.spec_from_file_location("prepare_supabase_migration", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def write_aggregate(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["export_date", "record_count", "records"])
        writer.writeheader()
        writer.writerow(
            {
                "export_date": "2026-08-01",
                "record_count": len(records),
                "records": json.dumps(records, ensure_ascii=False),
            }
        )


class PrepareSupabaseMigrationTest(unittest.TestCase):
    def test_prepares_searchable_cold_archive_and_deduplicates_summaries(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            chat_csv = root / "chat.csv"
            summary_csv = root / "summary.csv"
            output = root / "output"
            write_aggregate(
                chat_csv,
                [
                    {
                        "id": "m1",
                        "assistant_id": "a1",
                        "conversation_id": "c1",
                        "role": "user",
                        "content": "记得杭州的雨",
                        "created_at": "2026-08-01T00:00:00+00:00",
                    },
                    {
                        "id": "m2",
                        "assistant_id": "a1",
                        "conversation_id": "c1",
                        "role": "assistant",
                        "content": "我记得。",
                        "created_at": "2026-08-01T00:01:00+00:00",
                    },
                ],
            )
            write_aggregate(
                summary_csv,
                [
                    {
                        "id": "s1",
                        "assistant_id": "a1",
                        "content": "一起看杭州的雨。",
                        "created_at": "2026-08-01T00:02:00+00:00",
                        "review_status": "backlog",
                        "reviewed_at": None,
                    },
                    {
                        "id": "s2",
                        "assistant_id": "a1",
                        "content": "一起看杭州的雨。",
                        "created_at": "2026-08-01T00:03:00+00:00",
                        "review_status": "candidate",
                        "reviewed_at": None,
                    },
                ],
            )

            manifest = MODULE.prepare(chat_csv, summary_csv, output, trial_size=1)

            self.assertEqual(manifest["chat_archive"]["indexed_records"], 2)
            self.assertEqual(manifest["summaries"]["review_queue_records"], 1)
            self.assertEqual(manifest["summaries"]["exact_duplicates_collapsed"], 1)
            bucket = next((output / "chat-archive-buckets").glob("*.md"))
            text = bucket.read_text(encoding="utf-8")
            self.assertIn("dont_surface: true", text)
            self.assertIn("resolved: true", text)
            self.assertIn("记得杭州的雨", text)
            queue = json.loads((output / "summary-review-queue.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(queue["review_status"], "candidate")
            self.assertEqual(queue["source_ids"], ["s1", "s2"])

    def test_rejects_duplicate_source_ids(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "duplicate.csv"
            duplicate = {
                "id": "same",
                "content": "x",
                "created_at": "2026-08-01T00:00:00+00:00",
            }
            write_aggregate(path, [duplicate, dict(duplicate)])
            with self.assertRaisesRegex(ValueError, "duplicate record ids"):
                MODULE.read_aggregated_csv(path)

    def test_reads_supabase_json_field_larger_than_python_default(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "large-field.csv"
            record = {
                "id": "large",
                "content": "记" * 150_000,
                "created_at": "2026-08-01T00:00:00+00:00",
            }
            write_aggregate(path, [record])

            records, source = MODULE.read_aggregated_csv(path)

            self.assertEqual(source["records"], 1)
            self.assertEqual(records[0]["id"], "large")
            self.assertEqual(len(records[0]["content"]), 150_000)


if __name__ == "__main__":
    unittest.main()
