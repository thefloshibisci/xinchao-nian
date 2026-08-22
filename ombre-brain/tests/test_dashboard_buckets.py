import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from web import buckets as buckets_web


class _RouteRegistry:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(handler):
            self.routes[(path, tuple(methods))] = handler
            return handler

        return decorator


class _BucketManager:
    def __init__(self):
        self.include_archive_calls = []
        self.fail_on_archive_scan = False

    async def list_all(self, include_archive=False):
        self.include_archive_calls.append(include_archive)
        if include_archive and self.fail_on_archive_scan:
            raise AssertionError("the default dashboard path scanned archive")
        return [
            {
                "id": "active-1",
                "metadata": {"name": "Visible memory", "importance": 5},
                "content": "A visible imported memory",
            }
        ]


class _DecayEngine:
    @staticmethod
    def calculate_score(_metadata):
        return 1.0


class DashboardBucketListTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = _RouteRegistry()
        self.bucket_manager = _BucketManager()
        self.old_bucket_manager = buckets_web.sh.bucket_mgr
        self.old_decay_engine = buckets_web.sh.decay_engine
        self.old_require_auth = buckets_web.sh._require_auth
        buckets_web.sh.bucket_mgr = self.bucket_manager
        buckets_web.sh.decay_engine = _DecayEngine()
        buckets_web.sh._require_auth = lambda _request: None
        buckets_web.register(self.registry)
        self.handler = self.registry.routes[("/api/buckets", ("GET",))]

    def tearDown(self):
        buckets_web.sh.bucket_mgr = self.old_bucket_manager
        buckets_web.sh.decay_engine = self.old_decay_engine
        buckets_web.sh._require_auth = self.old_require_auth

    async def test_default_list_skips_archive_scan(self):
        self.bucket_manager.fail_on_archive_scan = True
        response = await self.handler(_request(""))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.bucket_manager.include_archive_calls, [False])

    async def test_archive_scan_requires_explicit_opt_in(self):
        response = await self.handler(_request("include_archive=1"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.bucket_manager.include_archive_calls, [True])


def _request(query_string):
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/buckets",
            "query_string": query_string.encode("ascii"),
            "headers": [],
        }
    )


if __name__ == "__main__":
    unittest.main()
