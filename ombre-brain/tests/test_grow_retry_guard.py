import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import tools.grow as grow_entry
from tools.grow.retry_guard import request_fingerprint, reset_for_tests, run_once


class GrowRetryGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.decay_engine = SimpleNamespace(ensure_started=AsyncMock())
        self.runtime_patch = patch.object(
            grow_entry.rt, "decay_engine", self.decay_engine, create=True
        )
        self.runtime_patch.start()

    def tearDown(self):
        self.runtime_patch.stop()
        reset_for_tests()

    async def test_completed_retry_reuses_result_without_running_twice(self):
        calls = 0

        async def operation():
            nonlocal calls
            calls += 1
            return "2条|新2合0"

        first = await run_once("same-request", operation)
        second = await run_once("same-request", operation)

        self.assertEqual(first, "2条|新2合0")
        self.assertIn("未重复写入", second)
        self.assertEqual(calls, 1)

    async def test_original_write_survives_waiter_cancellation(self):
        started = asyncio.Event()
        release = asyncio.Event()
        completed = asyncio.Event()
        calls = 0

        async def operation():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            completed.set()
            return "后台写入完成"

        original_waiter = asyncio.create_task(run_once("slow-request", operation))
        await started.wait()
        duplicate = await asyncio.wait_for(
            run_once("slow-request", operation), timeout=0.1
        )
        self.assertIn("仍在后台处理中", duplicate)

        original_waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await original_waiter

        release.set()
        await asyncio.wait_for(completed.wait(), timeout=1)
        await asyncio.sleep(0)

        retried = await run_once("slow-request", operation)
        self.assertIn("未重复写入", retried)
        self.assertEqual(calls, 1)

    async def test_failed_operation_is_not_cached(self):
        calls = 0

        async def operation():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("temporary provider failure")
            return "retry succeeded"

        with self.assertRaisesRegex(RuntimeError, "temporary provider failure"):
            await run_once("failed-request", operation)

        self.assertEqual(
            await run_once("failed-request", operation), "retry succeeded"
        )
        self.assertEqual(calls, 2)

    async def test_result_can_use_a_shorter_deduplication_window(self):
        calls = 0

        async def operation():
            nonlocal calls
            calls += 1
            return "pending"

        first = await run_once(
            "pending-request",
            operation,
            result_ttl_seconds=lambda _result: 0,
        )
        await asyncio.sleep(0.001)
        second = await run_once(
            "pending-request",
            operation,
            result_ttl_seconds=lambda _result: 0,
        )

        self.assertEqual(first, "pending")
        self.assertEqual(second, "pending")
        self.assertEqual(calls, 2)

    def test_fingerprint_includes_write_admission_context(self):
        manual = request_fingerprint(
            content=" diary\r\nentry ", items=None, auto=False, source=""
        )
        normalized = request_fingerprint(
            content="diary\nentry", items=None, auto=False, source=""
        )
        automatic = request_fingerprint(
            content="diary\nentry", items=None, auto=True, source="mobile"
        )
        items = request_fingerprint(
            content="diary\nentry",
            items=["final memory"],
            auto=False,
            source="",
        )

        self.assertEqual(manual, normalized)
        self.assertNotEqual(manual, automatic)
        self.assertNotEqual(manual, items)

    async def test_dispatch_guards_long_content_retries(self):
        operation = AsyncMock(return_value="长内容写入完成")
        content = "这是一段足够长的测试内容，用来确认长内容入口重复调用时不会重复写入同一批记忆。"
        with (
            patch.object(grow_entry, "check_grow_input_size", return_value=None),
            patch.object(grow_entry, "grow_core", operation),
        ):
            first = await grow_entry.dispatch(
                content, auto=True, source="continuity"
            )
            second = await grow_entry.dispatch(
                content, auto=True, source="continuity"
            )

        self.assertEqual(first, "长内容写入完成")
        self.assertIn("未重复写入", second)
        operation.assert_awaited_once_with(
            content, auto=True, source="continuity"
        )

    async def test_dispatch_guards_short_content_after_write_admission(self):
        operation = AsyncMock(return_value="短内容写入完成")
        admission = SimpleNamespace(allowed=True, reason="")
        with (
            patch.object(grow_entry, "check_grow_input_size", return_value=None),
            patch.object(grow_entry, "decide_write", return_value=admission) as gate,
            patch.object(grow_entry, "grow_shortpath", operation),
        ):
            first = await grow_entry.dispatch("短记忆", auto=False, source="")
            second = await grow_entry.dispatch("短记忆", auto=False, source="")

        self.assertEqual(first, "短内容写入完成")
        self.assertIn("未重复写入", second)
        self.assertEqual(gate.call_count, 1)
        operation.assert_awaited_once_with("短记忆")

    async def test_dispatch_does_not_count_an_immediate_pending_retry_twice(self):
        operation = AsyncMock(return_value="不应写入")
        admission = SimpleNamespace(
            allowed=False,
            reason="automatic_candidate_needs_review_or_repeat",
        )
        with (
            patch.object(grow_entry, "check_grow_input_size", return_value=None),
            patch.object(grow_entry, "default_ledger_path", return_value=None),
            patch.object(grow_entry, "decide_write", return_value=admission) as gate,
            patch.object(grow_entry, "grow_shortpath", operation),
        ):
            first = await grow_entry.dispatch(
                "自动候选", auto=True, source="continuity"
            )
            second = await grow_entry.dispatch(
                "自动候选", auto=True, source="continuity"
            )

        self.assertIn("暂未写入", first)
        self.assertIn("未重复写入", second)
        self.assertEqual(gate.call_count, 1)
        operation.assert_not_awaited()

    async def test_dispatch_guards_pre_split_items(self):
        operation = AsyncMock(return_value="预拆分写入完成")
        items = ["第一条最终正文", "第二条最终正文"]
        with (
            patch.object(grow_entry, "check_grow_items_payload", return_value=None),
            patch.object(grow_entry, "grow_items", operation),
        ):
            first = await grow_entry.dispatch(
                items=items, auto=False, source="manual"
            )
            second = await grow_entry.dispatch(
                items=items, auto=False, source="manual"
            )

        self.assertEqual(first, "预拆分写入完成")
        self.assertIn("未重复写入", second)
        operation.assert_awaited_once_with(items, auto=False, source="manual")


if __name__ == "__main__":
    unittest.main()
