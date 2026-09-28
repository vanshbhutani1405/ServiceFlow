"""Scheduling workflow agent backed by the Phase 2 database tools."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from livekit.agents import Agent, RunContext, function_tool

from agent.routing import SchedulingIntent, route_request
from db import tools as db_tools

logger = logging.getLogger(__name__)


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


def _result(result: db_tools.ToolResult) -> dict[str, Any]:
    return {"status": result.status, "data": result.data, "error": result.error}


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Date/time must include a timezone offset")
    return parsed


class SchedulingAgent(Agent):
    """Focused receptionist for booking, rescheduling, and cancellation."""

    def __init__(self, *, customer_id: str | None = None, db_client: Any = None) -> None:
        self.customer_id = customer_id
        self.db_client = db_client
        today = datetime.now(timezone.utc).date().isoformat()
        super().__init__(
            instructions=f"""
You are ServiceFlow's friendly scheduling receptionist.
Today is {today}. Keep replies concise, natural, and conversational.

Handle only appointment booking, availability, rescheduling, cancellation,
and returning-customer appointment context. Use the database tools whenever
customer, job, appointment, technician, availability, or confirmation data is
needed. Ask for genuinely missing information instead of guessing.

Never invent an appointment, technician, slot, ID, or database state. A tool
result is authoritative. Only tell the customer an operation succeeded when
the tool result has status "success". For "unavailable", "not_found", or
"failure", explain the issue naturally and ask for clarification or another
request as appropriate. Never expose internal tool names or statuses.

For a new booking, collect service type, service area, job/customer context,
and an ISO-8601 date/time with timezone before calling the booking tool. The
booking workflow will only proceed when the database exposes exactly one
eligible technician; do not rank technicians yourself.
""".strip(),
        )

    async def on_user_turn_completed(self, turn_ctx: Any, new_message: Any) -> None:
        intent = route_request(new_message.text_content)
        logger.info("scheduling_route intent=%s transcript=%r", intent.value, new_message.text_content)
        turn_ctx.add_message(
            role="assistant",
            content=(
                f"Deterministic routing classification: {intent.value}. "
                "Follow the scheduling instructions and use tools as needed."
            ),
        )

    def _missing_customer(self) -> db_tools.ToolResult:
        return db_tools.ToolResult(
            "not_found",
            error="Customer identity is not available for this session",
        )

    async def _get_customer_context(self) -> db_tools.ToolResult:
        if not self.customer_id:
            return self._missing_customer()
        return await db_tools.get_customer_context(self.customer_id, client=self.db_client)

    @function_tool()
    async def get_customer_context(self, context: RunContext) -> str:
        """Get the current customer's jobs and appointments."""
        return _json(_result(await self._get_customer_context()))

    @function_tool()
    async def get_job(self, context: RunContext, job_id: str) -> str:
        """Get one existing service job by ID from customer context."""
        return _json(_result(await db_tools.get_job(job_id, client=self.db_client)))

    @function_tool()
    async def check_technician_availability(
        self,
        context: RunContext,
        technician_id: str,
        scheduled_start: str,
        scheduled_end: str,
    ) -> str:
        """Check a concrete technician and time window before booking."""
        try:
            result = await db_tools.check_technician_availability(
                technician_id,
                _parse_time(scheduled_start),
                _parse_time(scheduled_end),
                client=self.db_client,
            )
        except ValueError as exc:
            result = db_tools.ToolResult("failure", error=str(exc))
        return _json(_result(result))

    async def _book_appointment(
        self,
        *,
        service_type: str,
        service_area: str,
        job_id: str,
        scheduled_start: str,
        scheduled_end: str,
        notes: str | None = None,
    ) -> db_tools.ToolResult:
        context_result = await self._get_customer_context()
        if not context_result.ok:
            return context_result
        try:
            start = _parse_time(scheduled_start)
            end = _parse_time(scheduled_end)
        except ValueError as exc:
            return db_tools.ToolResult("failure", error=str(exc))

        candidates = await db_tools.find_best_technician(
            service_type, service_area, start, end, client=self.db_client
        )
        if candidates.status != "success":
            return candidates
        eligible = candidates.data.get("candidates", [])
        if not eligible:
            return db_tools.ToolResult("unavailable", error="No eligible technician is available for that slot")
        if len(eligible) != 1:
            return db_tools.ToolResult(
                "failure",
                data={"candidates": eligible},
                error="More than one eligible technician requires clarification",
            )

        result = await db_tools.book_appointment(
            self.customer_id, job_id, eligible[0]["id"], start, end,
            notes=notes, client=self.db_client,
        )
        if not result.ok:
            return result
        confirmation = await db_tools.send_confirmation(
            result.data["id"], message="ServiceFlow appointment confirmation", client=self.db_client
        )
        if not confirmation.ok:
            return db_tools.ToolResult(
                "failure",
                data={"appointment": result.data, "confirmation": _result(confirmation)},
                error="Appointment was created but confirmation recording failed",
            )
        return result

    @function_tool()
    async def book_appointment(
        self,
        context: RunContext,
        service_type: str,
        service_area: str,
        job_id: str,
        scheduled_start: str,
        scheduled_end: str,
        notes: str = "",
    ) -> str:
        """Book only after customer context, availability, and job are valid."""
        return _json(_result(await self._book_appointment(
            service_type=service_type,
            service_area=service_area,
            job_id=job_id,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
            notes=notes or None,
        )))

    async def _reschedule_appointment(
        self, appointment_id: str, scheduled_start: str, scheduled_end: str
    ) -> db_tools.ToolResult:
        context_result = await self._get_customer_context()
        if not context_result.ok:
            return context_result
        try:
            result = await db_tools.reschedule_appointment(
                appointment_id,
                _parse_time(scheduled_start),
                _parse_time(scheduled_end),
                customer_id=self.customer_id,
                client=self.db_client,
            )
        except ValueError as exc:
            return db_tools.ToolResult("failure", error=str(exc))
        if not result.ok:
            return result
        confirmation = await db_tools.send_confirmation(
            appointment_id, message="ServiceFlow rescheduling confirmation", client=self.db_client
        )
        if not confirmation.ok:
            return db_tools.ToolResult(
                "failure", data={"appointment": result.data, "confirmation": _result(confirmation)},
                error="Appointment was rescheduled but confirmation recording failed",
            )
        return result

    @function_tool()
    async def reschedule_appointment(
        self, context: RunContext, appointment_id: str, scheduled_start: str, scheduled_end: str
    ) -> str:
        """Move an existing appointment after the database validates availability."""
        return _json(_result(await self._reschedule_appointment(appointment_id, scheduled_start, scheduled_end)))

    async def _cancel_appointment(self, appointment_id: str) -> db_tools.ToolResult:
        context_result = await self._get_customer_context()
        if not context_result.ok:
            return context_result
        result = await db_tools.cancel_appointment(
            appointment_id, customer_id=self.customer_id, client=self.db_client
        )
        if not result.ok:
            return result
        confirmation = await db_tools.send_confirmation(
            appointment_id, message="ServiceFlow cancellation confirmation", client=self.db_client
        )
        if not confirmation.ok:
            return db_tools.ToolResult(
                "failure", data={"appointment": result.data, "confirmation": _result(confirmation)},
                error="Appointment was cancelled but confirmation recording failed",
            )
        return result

    @function_tool()
    async def cancel_appointment(self, context: RunContext, appointment_id: str) -> str:
        """Cancel an existing appointment only after the database confirms it exists."""
        return _json(_result(await self._cancel_appointment(appointment_id)))

