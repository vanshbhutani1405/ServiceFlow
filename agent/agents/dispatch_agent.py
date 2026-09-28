"""Dispatch workflow agent for deterministic technician assignment."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from livekit.agents import Agent, RunContext, function_tool

from db import tools as db_tools


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Date/time must include a timezone offset")
    return parsed


class DispatchAgent(Agent):
    """Uses database-backed matching and never invents an assignment."""

    def __init__(self, *, customer_id: str | None = None, db_client: Any = None,
                 job_id: str | None = None, scheduled_start: str | None = None,
                 scheduled_end: str | None = None, chat_ctx: Any = None) -> None:
        self.customer_id = customer_id
        self.db_client = db_client
        self.job_id = job_id
        self.scheduled_start = scheduled_start
        self.scheduled_end = scheduled_end
        kwargs: dict[str, Any] = {"instructions": (
            "You are ServiceFlow's dispatch coordinator. Assign technicians only through the dispatch tool. "
            "Never invent availability, assignment, ETA, or database state. Only confirm dispatch after a "
            "successful tool result; explain unavailable, not_found, and failure naturally."
        )}
        if chat_ctx is not None:
            kwargs["chat_ctx"] = chat_ctx
        super().__init__(**kwargs)

    async def on_enter(self) -> None:
        await self.session.generate_reply(instructions=(
            "Use dispatch_job with the supplied job and time window. Ask only for genuinely missing details."
        ))

    @function_tool()
    async def dispatch_job(self, context: RunContext, job_id: str = "",
                           scheduled_start: str = "", scheduled_end: str = "",
                           service_type: str = "", service_area: str = "") -> str:
        """Select and persist the best eligible technician for a scheduled job."""
        try:
            result = await db_tools.dispatch_job(
                job_id or self.job_id or "", customer_id=self.customer_id,
                scheduled_start=_parse_time(scheduled_start or self.scheduled_start or ""),
                scheduled_end=_parse_time(scheduled_end or self.scheduled_end or ""),
                service_type=service_type or None, service_area=service_area or None,
                client=self.db_client,
            )
        except ValueError as exc:
            result = db_tools.ToolResult("failure", error=str(exc))
        return json.dumps({"status": result.status, "data": result.data, "error": result.error}, default=str)
