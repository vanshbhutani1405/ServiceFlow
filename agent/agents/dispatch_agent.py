"""Dispatch workflow agent for deterministic technician assignment."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from livekit.agents import Agent, RunContext, function_tool
from livekit.agents.llm import ToolError

from agent.state import WorkflowState
from db import tools as db_tools

logger = logging.getLogger(__name__)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Date/time must include a timezone offset")
    return parsed


class DispatchAgent(Agent):
    """Uses database-backed matching and never invents an assignment."""

    def __init__(self, *, customer_id: str | None = None, db_client: Any = None,
                 job_id: str | None = None, scheduled_start: str | None = None,
                 scheduled_end: str | None = None, chat_ctx: Any = None,
                 state: WorkflowState | None = None) -> None:
        self.state = state or WorkflowState()
        self.customer_id = customer_id
        self.db_client = db_client
        self.job_id = job_id
        self.scheduled_start = scheduled_start
        self.scheduled_end = scheduled_end
        self.safety_escalated = False
        self.state.customer_id = customer_id or self.state.customer_id
        self.state.job_id = job_id or self.state.job_id
        self.state.scheduled_start = scheduled_start or self.state.scheduled_start
        self.state.scheduled_end = scheduled_end or self.state.scheduled_end
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
        if context is not None:
            context.disallow_interruptions()
            try:
                if isinstance(context.userdata, WorkflowState):
                    self.state = context.userdata
            except (AttributeError, ValueError):
                pass
        if not hasattr(self, "state"):
            self.state = WorkflowState(customer_id=getattr(self, "customer_id", None), job_id=getattr(self, "job_id", None))
        if getattr(self, "safety_escalated", False):
            result = db_tools.ToolResult("failure", error="Dispatch stopped because safety escalation is active")
            raise ToolError(result.error or "Dispatch stopped because safety escalation is active")
        self.job_id = self.job_id or self.state.job_id
        if not self.job_id:
            result = db_tools.ToolResult("failure", error="Dispatch requires a canonical persisted job ID")
            logger.warning("dispatch_validation status=failure error=%s", result.error)
            raise ToolError(result.error)
        canonical_job_id = self.job_id
        logger.info("dispatch_attempt job_id=%s customer_id=%s", canonical_job_id, self.customer_id)
        try:
            result = await db_tools.dispatch_job(
                canonical_job_id or "", customer_id=self.customer_id,
                scheduled_start=_parse_time(scheduled_start or self.scheduled_start or ""),
                scheduled_end=_parse_time(scheduled_end or self.scheduled_end or ""),
                service_type=service_type or None, service_area=service_area or None,
                client=self.db_client,
            )
        except ValueError as exc:
            result = db_tools.ToolResult("failure", error=str(exc))
        logger.info("dispatch_result status=%s job_id=%s error=%s", result.status, canonical_job_id, result.error)
        self.state.dispatch_status = result.status
        self.state.booking_status = "dispatched" if result.ok else "failed"
        if not result.ok:
            raise ToolError(result.error or "Dispatch could not be completed")
        return json.dumps({"status": result.status, "data": result.data, "error": result.error}, default=str)
