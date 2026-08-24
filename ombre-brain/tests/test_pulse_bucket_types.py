import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from tools.anchor import core as anchor_core


class _BucketManager:
    async def get_stats(self):
        return {
            "permanent_count": 1,
            "dynamic_count": 2,
            "archive_count": 0,
            "feel_count": 1,
            "plan_count": 1,
            "letter_count": 1,
            "total_size_kb": 1.0,
        }

    async def list_all(self, include_archive=False):
        self.include_archive = include_archive
        return [
            _bucket("permanent-1", "permanent"),
            _bucket("dynamic-1", "dynamic"),
            _bucket("feel-1", "feel"),
            _bucket("plan-1", "plan"),
            _bucket("letter-1", "letter"),
            _bucket("self-1", "i"),
        ]


class _DecayEngine:
    is_running = True

    async def ensure_started(self):
        return None

    @staticmethod
    def calculate_score(_metadata):
        return 1.0


class PulseBucketTypeTests(unittest.IsolatedAsyncioTestCase):
    async def test_pulse_keeps_every_active_bucket_type_distinct(self):
        old_bucket_manager = anchor_core.rt.bucket_mgr
        old_decay_engine = anchor_core.rt.decay_engine
        old_embedding_engine = anchor_core.rt.embedding_engine
        try:
            anchor_core.rt.bucket_mgr = _BucketManager()
            anchor_core.rt.decay_engine = _DecayEngine()
            anchor_core.rt.embedding_engine = None

            result = await anchor_core.pulse()

            self.assertIn("📦 [permanent-1]", result)
            self.assertIn("💭 [dynamic-1]", result)
            self.assertIn("🫧 [feel-1]", result)
            self.assertIn("📋 [plan-1]", result)
            self.assertIn("💌 [letter-1]", result)
            self.assertIn("🪞 [self-1]", result)
            self.assertIn("=== I（1 条）===", result)
        finally:
            anchor_core.rt.bucket_mgr = old_bucket_manager
            anchor_core.rt.decay_engine = old_decay_engine
            anchor_core.rt.embedding_engine = old_embedding_engine


def _bucket(bucket_id, bucket_type):
    return {
        "id": bucket_id,
        "content": f"{bucket_type} content",
        "metadata": {
            "type": bucket_type,
            "name": bucket_type,
            "domain": [bucket_type],
            "tags": [bucket_type],
            "valence": 0.5,
            "arousal": 0.3,
            "importance": 5,
        },
    }


if __name__ == "__main__":
    unittest.main()
