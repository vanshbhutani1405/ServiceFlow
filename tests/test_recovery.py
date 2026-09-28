import asyncio

from agent.recovery import run_with_recovery
from db.tools import ToolResult


def run(coro):
    return asyncio.run(coro)


def test_safe_failure_retries_once_and_recovers():
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        return ToolResult("failure") if calls == 1 else ToolResult("success", {"ok": True})

    result = run(run_with_recovery(operation, operation_name="availability", retry_safe=True))
    assert result.ok
    assert calls == 2


def test_side_effect_failure_is_not_retried():
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        return ToolResult("failure", error="timeout")

    result = run(run_with_recovery(operation, operation_name="booking", retry_safe=False))
    assert result.status == "failure"
    assert calls == 1


def test_unavailable_is_returned_without_retry():
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        return ToolResult("unavailable")

    result = run(run_with_recovery(operation, operation_name="availability", retry_safe=True))
    assert result.status == "unavailable"
    assert calls == 1
