import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from web import buckets as buckets_web
from web import _shared as shared_web


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


class ServiceTokenAuthTests(unittest.TestCase):
    def setUp(self):
        import os

        self.old_token = os.environ.get("OMBRE_MCP_SERVICE_TOKEN")
        os.environ["OMBRE_MCP_SERVICE_TOKEN"] = "s" * 40

    def tearDown(self):
        import os

        if self.old_token is None:
            os.environ.pop("OMBRE_MCP_SERVICE_TOKEN", None)
        else:
            os.environ["OMBRE_MCP_SERVICE_TOKEN"] = self.old_token

    def test_service_token_is_limited_to_strong_bearer_values(self):
        authorized = _request_with_headers(
            {"authorization": f"Bearer {'s' * 40}"}
        )
        self.assertTrue(shared_web._is_service_token_authenticated(authorized))
        self.assertIsNone(
            shared_web._require_service_or_dashboard_auth(authorized)
        )
        self.assertFalse(shared_web._is_service_token_authenticated(
            _request_with_headers({"authorization": "Bearer wrong"})))
        self.assertFalse(shared_web._is_service_token_authenticated(
            _request_with_headers({"authorization": f"Basic {'s' * 40}"})))


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


def _request_with_headers(headers):
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/bucket/example",
            "query_string": b"",
            "headers": [(key.lower().encode("ascii"), value.encode("ascii")) for key, value in headers.items()],
        }
    )


if __name__ == "__main__":
    unittest.main()
