import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import server


class SplitBreathToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_split_tools_are_registered_without_removing_unified_breath(self):
        names = set(server.mcp._tool_manager._tools)
        self.assertTrue({"breath", "breath_search", "breath_advanced"}.issubset(names))

    async def test_breath_search_delegates_to_existing_dispatch(self):
        dispatch = AsyncMock(return_value="found")
        with patch.object(server._t_breath, "dispatch", dispatch):
            result = await server.breath_search("共同经历", domain="关系", max_results=2)
        self.assertEqual(result, "found")
        dispatch.assert_awaited_once_with(query="共同经历", domain="关系", max_results=2)

    async def test_breath_advanced_delegates_all_existing_filters(self):
        dispatch = AsyncMock(return_value="advanced")
        with patch.object(server._t_breath, "dispatch", dispatch):
            result = await server.breath_advanced(
                query="牵挂", max_tokens=1200, domain="关系", valence=0.8,
                arousal=0.4, max_results=4, importance_min=6,
                tags="承诺,长期", catalog=True,
            )
        self.assertEqual(result, "advanced")
        dispatch.assert_awaited_once_with(
            query="牵挂", max_tokens=1200, domain="关系", valence=0.8,
            arousal=0.4, max_results=4, importance_min=6,
            tags="承诺,长期", catalog=True,
        )


if __name__ == "__main__":
    unittest.main()
