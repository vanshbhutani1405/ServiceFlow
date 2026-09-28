"""Small, conservative recovery wrapper for idempotent internal reads."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from db.tools import ToolResult

logger = logging.getLogger(__name__)


async def run_with_recovery(
    operation: Callable[[], Awaitable[ToolResult]],
    *,
    operation_name: str,
    retry_safe: bool = False,
) -> ToolResult:
    """Run an operation and retry one time only when it is explicitly safe."""
    attempts = 2 if retry_safe else 1
    last = ToolResult("failure", error=f"{operation_name} failed")
    for attempt in range(1, attempts + 1):
        try:
            result = await operation()
        except Exception as exc:  # defensive boundary for unexpected tool exceptions
            result = ToolResult("failure", error=str(exc))
        if result.status != "failure" or attempt == attempts:
            if result.status == "failure":
                logger.error("tool_recovery_exhausted operation=%s attempts=%s error=%s", operation_name, attempt, result.error)
            return result
        last = result
        logger.warning("tool_recovery_retry operation=%s attempt=%s", operation_name, attempt)
    return last
