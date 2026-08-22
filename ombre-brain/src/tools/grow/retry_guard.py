"""Process-local idempotency for public ``grow`` retries.

Dehydration and bucket creation may outlive an MCP client's response timeout.
Keep the original operation alive and recognize an exact retry instead of
starting a second write.  The cache is deliberately bounded and short-lived;
real failures are never cached.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


RETRY_WINDOW_SECONDS = 30 * 60
PENDING_RETRY_WINDOW_SECONDS = 5 * 60

_IN_PROGRESS_MESSAGE = (
    "⏳ 相同的 grow 仍在后台处理中；无需重复提交，完成后会自动入库。"
)
_REUSED_RESULT_PREFIX = "✅ 已识别为刚才 grow 的重试；未重复写入。\n"


@dataclass
class _LoopState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    inflight: dict[str, asyncio.Task[str]] = field(default_factory=dict)
    completed: dict[str, tuple[float, float, str]] = field(default_factory=dict)


_states: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopState] = (
    weakref.WeakKeyDictionary()
)


def request_fingerprint(
    *,
    content: str,
    items: list | None,
    auto: bool,
    source: str,
) -> str:
    """Return a privacy-preserving fingerprint for the exact public request."""

    payload = {
        "content": (content or "").replace("\r\n", "\n").strip(),
        "items": items,
        "auto": bool(auto),
        "source": str(source or "").strip(),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _state_for_running_loop() -> _LoopState:
    loop = asyncio.get_running_loop()
    state = _states.get(loop)
    if state is None:
        state = _LoopState()
        _states[loop] = state
    return state


def _consume_background_exception(task: asyncio.Task[str]) -> None:
    """Avoid an unobserved-task warning if the original client disconnected."""

    if not task.cancelled():
        task.exception()


async def run_once(
    fingerprint: str,
    operation: Callable[[], Awaitable[str]],
    *,
    retry_window_seconds: float = RETRY_WINDOW_SECONDS,
    result_ttl_seconds: Callable[[str], float] | None = None,
) -> str:
    """Run one grow request and safely recognize exact retries."""

    state = _state_for_running_loop()
    now = time.monotonic()
    async with state.lock:
        expired = [
            key
            for key, (finished_at, ttl_seconds, _result) in state.completed.items()
            if now - finished_at >= ttl_seconds
        ]
        for key in expired:
            state.completed.pop(key, None)

        completed = state.completed.get(fingerprint)
        if completed is not None:
            return _REUSED_RESULT_PREFIX + completed[2]

        if fingerprint in state.inflight:
            return _IN_PROGRESS_MESSAGE

        async def execute() -> str:
            try:
                result = await operation()
            except BaseException:
                async with state.lock:
                    state.inflight.pop(fingerprint, None)
                raise
            async with state.lock:
                state.inflight.pop(fingerprint, None)
                ttl_seconds = (
                    result_ttl_seconds(result)
                    if result_ttl_seconds is not None
                    else retry_window_seconds
                )
                state.completed[fingerprint] = (
                    time.monotonic(),
                    max(0.0, float(ttl_seconds)),
                    result,
                )
            return result

        task = asyncio.create_task(execute(), name=f"grow:{fingerprint[:12]}")
        task.add_done_callback(_consume_background_exception)
        state.inflight[fingerprint] = task

    # A cancelled HTTP/MCP waiter must not cancel a write already in progress.
    return await asyncio.shield(task)


def reset_for_tests() -> None:
    """Clear process-local state.  Tests only; production never calls this."""

    _states.clear()
