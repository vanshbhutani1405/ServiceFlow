"""Scheduling workflow agent backed by the Phase 2 database tools."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from livekit.agents import Agent, RunContext, function_tool
from livekit.agents.llm import ToolError

from agent.agents.triage_agent import TriageAgent
from agent.recovery import run_with_recovery
from agent.routing import SchedulingIntent, is_flexible_availability_request, route_request
from agent.safety.classifier import SAFETY_RESPONSE, SafetyStatus
from agent.state import WorkflowState
from db import tools as db_tools

logger = logging.getLogger(__name__)
_PLACEHOLDER_IDS = {"new_customer", "unknown", "test_customer", "new_job", "new"}
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"
_PHONE_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
    "nine": "9",
}


def _real_id(value: str | None) -> str | None:
    if not value or value.casefold() in _PLACEHOLDER_IDS or value == _ZERO_UUID:
        return None
    return value


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


def _result(result: db_tools.ToolResult) -> dict[str, Any]:
    return {"status": result.status, "data": result.data, "error": result.error}


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _normalize_datetime(value: str) -> str:
    """Canonicalize supported ISO timestamps without inventing a timezone."""
    return _parse_time(value.strip()).isoformat()


def _normalize_phone(value: str) -> str:
    """Normalize digits and common spoken-number words without inference."""
    words = value.casefold().replace("-", " ").split()
    translated = "".join(_PHONE_WORDS.get(word, word) for word in words)
    return re.sub(r"[^\d+]", "", translated)


def _merge_phone(existing: str | None, incoming: str) -> str:
    normalized = _normalize_phone(incoming)
    if not normalized:
        return existing or ""
    existing_normalized = _normalize_phone(existing or "")
    incoming_digits = "".join(char for char in normalized if char.isdigit())
    existing_digits = "".join(char for char in existing_normalized if char.isdigit())
    if len(existing_digits) >= 7 and len(incoming_digits) < 7:
        return existing_normalized
    if existing_digits and len(existing_digits) < 7 and incoming_digits:
        return existing_digits + incoming_digits
    return normalized


def _effective_service_duration(service_type: str, requested: int, description: str | None) -> int:
    """Keep V1 duration deterministic; only an explicit customer duration overrides it."""
    normalized = service_type.casefold().replace("_", " ").replace("-", " ")
    if normalized.strip() not in {"ac repair", "air conditioner repair", "air conditioning repair", "hvac"}:
        return requested
    explicit = bool(re.search(r"\b\d+\s*(?:minutes?|mins?|hours?|hrs?)\b", description or "", re.IGNORECASE))
    if explicit or requested in {60, 90}:
        return requested
    return 60


class SchedulingAgent(Agent):
    """Focused receptionist for booking, rescheduling, and cancellation."""

    def __init__(self, *, customer_id: str | None = None, db_client: Any = None,
                 state: WorkflowState | None = None) -> None:
        self.state = state or WorkflowState(customer_id=_real_id(customer_id))
        self.customer_id = _real_id(customer_id) or _real_id(self.state.customer_id)
        self.db_client = db_client
        self._safety_escalated = False
        self._last_job_id: str | None = None
        self._workflow_state = "INTAKE"
        self._customer_name: str | None = None
        self._customer_phone: str | None = None
        self._sync_state()
        today = datetime.now(timezone.utc).date().isoformat()
        super().__init__(
            instructions=f"""
You are ServiceFlow's friendly scheduling receptionist.
Today is {today}. Keep replies concise, natural, and conversational.

Handle only appointment booking, availability, rescheduling, cancellation,
dispatch,
and returning-customer appointment context. Use the database tools whenever
customer, job, appointment, technician, availability, or confirmation data is
needed. Ask for genuinely missing information instead of guessing.
When asked whether a job or appointment is booked, use the status tool and
report its actual state. Never describe dispatch as asynchronous processing.

Never invent an appointment, technician, slot, ID, or database state. A tool
result is authoritative. Only tell the customer an operation succeeded when
the tool result has status "success". For "unavailable", "not_found", or
"failure", explain the issue naturally and ask for clarification or another
request as appropriate. Never expose internal tool names or statuses.

For a new booking, collect service type, service area, requested date/time,
full street address, full customer name, and phone before handing off a new
request. The handoff resolves or creates the customer, creates the job first,
and captures its returned database ID; never invent or substitute an ID. The
booking workflow will only proceed when the database exposes exactly one
eligible technician; do not rank technicians yourself. Complete dispatch
synchronously and wait for its actual result before reporting assignment. After
flexible availability returns options, let the customer choose one and use the
selection tool; it finalizes the booking. Do not call transfer_to_dispatch
before a flexible slot has been selected.
""".strip(),
        )

    async def on_user_turn_completed(self, turn_ctx: Any, new_message: Any) -> None:
        safety = TriageAgent.classify(new_message.text_content)
        if safety.status == SafetyStatus.EMERGENCY_ESCALATION:
            self._safety_escalated = True
            logger.warning("safety_escalation normal_workflow_stopped reason=%s", safety.reason)
            turn_ctx.add_message(role="assistant", content=SAFETY_RESPONSE)
            return
        intent = route_request(new_message.text_content)
        active = getattr(self.state, "active_workflow", None)
        flexible = is_flexible_availability_request(new_message.text_content)
        if flexible:
            self.state.active_workflow = SchedulingIntent.BOOK.value
            self.state.availability_mode = "FLEXIBLE"
            self.state.workflow_stage = "CHECKING_AVAILABILITY"
            intent = SchedulingIntent.BOOK
        elif active == SchedulingIntent.BOOK.value and intent not in {
            SchedulingIntent.CANCEL, SchedulingIntent.RESCHEDULE,
        }:
            intent = SchedulingIntent.BOOK
        elif active is None and intent in {
            SchedulingIntent.BOOK, SchedulingIntent.RESCHEDULE, SchedulingIntent.CANCEL,
            SchedulingIntent.GENERAL_SCHEDULING,
        }:
            self.state.active_workflow = intent.value
            self.state.workflow_stage = "INTAKE" if intent == SchedulingIntent.BOOK else "ACTIVE"
        logger.info(
            "workflow_state workflow=%s stage=%s transcript=%r",
            self.state.active_workflow or intent.value, self.state.workflow_stage, new_message.text_content,
        )

    @function_tool()
    async def transfer_to_dispatch(
        self, context: RunContext, job_id: str = "", scheduled_start: str = "",
        scheduled_end: str = "", service_type: str = "", service_area: str = "",
        address: str = "", description: str = "", full_name: str = "", phone: str = "",
    ) -> str:
        """Complete persistence and dispatch before returning a user-facing result."""
        guard = self._normal_workflow_guard()
        if guard is not None:
            raise ToolError(guard.error or "Normal workflow is unavailable")
        self._ensure_state()
        if context is not None:
            self._bind_state(context)
            context.disallow_interruptions()
        if (
            self.state.active_workflow == SchedulingIntent.BOOK.value
            and self.state.availability_mode == "FLEXIBLE"
            and not self.state.selected_technician_id
        ):
            raise ToolError("Select an available appointment slot before dispatching")
        self._merge_intake(
            service_type=service_type, service_area=service_area, address=address,
            description=description, full_name=full_name, phone=phone, scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
        )
        validation = await self._validate_dispatch_prerequisites(
            service_type=self.state.service_type or "", service_area=self.state.service_area or "",
            scheduled_start=self.state.scheduled_start or "", scheduled_end=self.state.scheduled_end or "",
            address=self.state.address or "", full_name=self.state.full_name or "", phone=self.state.phone or "",
        )
        if not validation.ok:
            logger.warning("dispatch_validation status=%s error=%s", validation.status, validation.error)
            raise ToolError(validation.error or "Required booking information is missing")
        resolved_job_id, error = await self._resolve_dispatch_job_id(
            job_id, service_type=service_type, service_area=service_area,
            address=address, description=description,
        )
        if error is not None:
            logger.warning("dispatch_handoff_rejected requested_job_id=%r error=%s", job_id, error.error)
            raise ToolError(error.error or "The service request could not be created")
        logger.info("dispatch_validation status=success customer_id=%s job_id=%s", self.customer_id, resolved_job_id)
        self.state.job_id = resolved_job_id
        self.state.dispatch_status = "attempted"
        self.state.workflow_stage = "DISPATCHING"
        self._sync_state()
        try:
            dispatch_result = await db_tools.dispatch_job(
                resolved_job_id,
                technician_id=self.state.selected_technician_id,
                customer_id=self.state.customer_id,
                scheduled_start=_parse_time(self.state.scheduled_start or ""),
                scheduled_end=_parse_time(self.state.scheduled_end or ""),
                service_type=self.state.service_type,
                service_area=self.state.service_area,
                client=self.db_client,
            )
        except ValueError as exc:
            dispatch_result = db_tools.ToolResult("failure", error=str(exc))
        self.state.dispatch_status = dispatch_result.status
        self.state.booking_status = "dispatched" if dispatch_result.ok else "failed"
        self.state.workflow_stage = "COMPLETE" if dispatch_result.ok else "FAILED"
        logger.info(
            "dispatch_result status=%s customer_id=%s job_id=%s error=%s",
            dispatch_result.status, self.state.customer_id, resolved_job_id, dispatch_result.error,
        )
        if not dispatch_result.ok:
            raise ToolError(dispatch_result.error or "The service request could not be dispatched")
        return _json(_result(dispatch_result))

    def _sync_state(self) -> None:
        if self.customer_id:
            self.state.customer_id = self.customer_id
        elif self.state.customer_id:
            self.customer_id = _real_id(self.state.customer_id)
        self.state.full_name = getattr(self, "_customer_name", None) or self.state.full_name
        self.state.phone = getattr(self, "_customer_phone", None) or self.state.phone
        self.state.job_id = getattr(self, "_last_job_id", None) or self.state.job_id

    def _ensure_state(self) -> None:
        if not hasattr(self, "state"):
            self.state = WorkflowState(customer_id=getattr(self, "customer_id", None))

    def _bind_state(self, context: RunContext | None = None) -> None:
        """Use AgentSession.userdata as the sole canonical state when available."""
        try:
            userdata = getattr(context, "userdata", None) if context is not None else None
        except (AttributeError, ValueError):
            userdata = None
        if isinstance(userdata, WorkflowState):
            self.state = userdata
            self.customer_id = _real_id(self.state.customer_id) or self.customer_id
        else:
            self._ensure_state()

    def _merge_intake(self, **values: str) -> None:
        for field, value in values.items():
            if not value or not hasattr(self.state, field):
                continue
            if field == "phone":
                normalized = _merge_phone(self.state.phone, value)
            elif field in {"scheduled_start", "scheduled_end"}:
                try:
                    normalized = _normalize_datetime(value)
                except (TypeError, ValueError):
                    normalized = value.strip()
            else:
                normalized = value.strip()
            if normalized:
                setattr(self.state, field, normalized)
        self._customer_name = self.state.full_name
        self._customer_phone = self.state.phone
        self._last_job_id = self.state.job_id

    @function_tool()
    async def record_intake(
        self, context: RunContext, service_type: str = "", service_area: str = "",
        scheduled_start: str = "", scheduled_end: str = "", address: str = "",
        description: str = "", full_name: str = "", phone: str = "",
    ) -> str:
        """Store supplied intake fields and return exactly the next field to ask for."""
        self._bind_state(context)
        self._merge_intake(
            service_type=service_type, service_area=service_area,
            scheduled_start=scheduled_start, scheduled_end=scheduled_end,
            address=address, description=description, full_name=full_name, phone=phone,
        )
        next_field = self.state.next_missing_field()
        return _json({
            "status": "success", "next_field": next_field,
            "question": f"What is your {next_field}?" if next_field else "All required information is collected.",
            "state": self.state.__dict__,
        })

    @function_tool()
    async def find_next_available_slots(
        self, context: RunContext, service_duration_minutes: int = 60,
        search_start: str = "", search_horizon_days: int = 7,
    ) -> str:
        """Return only database-backed future slots for a flexible request."""
        self._bind_state(context)
        if context is not None:
            context.disallow_interruptions()
        if not self.state.service_type or not self.state.service_area:
            raise ToolError("Please provide the service type and service area before checking availability")
        try:
            requested_duration = int(service_duration_minutes)
        except (TypeError, ValueError) as exc:
            raise ToolError("Service duration must be a valid number of minutes") from exc
        duration = _effective_service_duration(
            self.state.service_type, requested_duration, self.state.description,
        )
        if duration != requested_duration:
            logger.info(
                "availability_duration normalized service_type=%s requested_minutes=%s effective_minutes=%s",
                self.state.service_type, requested_duration, duration,
            )
        try:
            start = _parse_time(search_start) if search_start else datetime.now(timezone.utc)
        except ValueError as exc:
            raise ToolError(f"Invalid availability search start: {exc}") from exc
        self.state.availability_mode = "FLEXIBLE"
        self.state.workflow_stage = "CHECKING_AVAILABILITY"
        result = await db_tools.find_next_available_slots(
            self.state.service_area, self.state.service_type,
            service_duration_minutes=duration,
            search_start=start, search_horizon_days=search_horizon_days,
            client=self.db_client,
        )
        if result.ok:
            self.state.candidate_slots = list(result.data.get("slots", []))
            self.state.workflow_stage = "SELECTING_SLOT"
        if not result.ok:
            raise ToolError(result.error or "No eligible technician slots were found")
        return _json(_result(result))

    async def _finalize_selected_slot(self) -> db_tools.ToolResult:
        """Recheck and persist a selected backend slot without another LLM call."""
        selected_technician = self.state.selected_technician_id
        selected_slot_id = self.state.selected_slot_id
        candidate = next((slot for slot in (self.state.candidate_slots or []) if slot.get("slot_id") == selected_slot_id), None)
        if candidate is None:
            return db_tools.ToolResult("failure", error="Selected slot is not a canonical availability candidate")
        if (
            candidate.get("technician_id") != selected_technician
            or candidate.get("start") != self.state.selected_scheduled_start
            or candidate.get("end") != self.state.selected_scheduled_end
        ):
            return db_tools.ToolResult("failure", error="Selected slot state is inconsistent with its backend candidate")
        self.state.scheduled_start = self.state.selected_scheduled_start
        self.state.scheduled_end = self.state.selected_scheduled_end
        if not selected_technician or not self.state.selected_scheduled_start or not self.state.selected_scheduled_end:
            return db_tools.ToolResult("failure", error="A valid selected appointment slot is required")
        if self.state.booking_status == "dispatched" and self.state.job_id:
            current = await db_tools.get_job_status(
                self.state.job_id, customer_id=self.customer_id, client=self.db_client,
            )
            if current.ok and (current.data.get("job") or {}).get("technician_id") == selected_technician:
                return db_tools.ToolResult("success", {
                    "appointment": current.data.get("appointment"),
                    "job": current.data.get("job"),
                    "technician_id": selected_technician,
                    "reused": True,
                })
        stage_started = time.perf_counter()
        try:
            start = _parse_time(self.state.scheduled_start)
            end = _parse_time(self.state.scheduled_end)
        except ValueError as exc:
            return db_tools.ToolResult("failure", error=str(exc))
        validation = await self._validate_dispatch_prerequisites(
            service_type=self.state.service_type or "", service_area=self.state.service_area or "",
            scheduled_start=self.state.scheduled_start, scheduled_end=self.state.scheduled_end,
            address=self.state.address or "", full_name=self.state.full_name or "", phone=self.state.phone or "",
        )
        logger.info("customer_resolution_ms=%.1f", (time.perf_counter() - stage_started) * 1000)
        if not validation.ok:
            return validation
        job_id, error = await self._resolve_dispatch_job_id(
            self.state.job_id, service_type=self.state.service_type or "",
            service_area=self.state.service_area or "", address=self.state.address or "",
            description=self.state.description or "",
        )
        logger.info("job_resolution_ms=%.1f", (time.perf_counter() - stage_started) * 1000)
        if error is not None:
            return error
        recheck = await db_tools.validate_selected_technician_for_booking(
            selected_technician, self.state.service_type or "", self.state.service_area or "",
            start, end, client=self.db_client,
        )
        logger.info("slot_revalidation_ms=%.1f", (time.perf_counter() - stage_started) * 1000)
        if not recheck.ok:
            self.state.workflow_stage = "CHECKING_AVAILABILITY"
            return recheck
        selected = (recheck.data or {}).get("technician") or {}
        if selected.get("id") != selected_technician:
            return db_tools.ToolResult("failure", error="Selected technician verification failed")
        logger.info(
            "booking_validation_result status=success slot_id=%s technician_id=%s start=%s end=%s",
            selected_slot_id, selected_technician, self.state.scheduled_start, self.state.scheduled_end,
        )
        logger.info("booking_write_start job_id=%s technician_id=%s", job_id, selected_technician)
        appointment_started = time.perf_counter()
        booking = await db_tools.book_appointment(
            self.customer_id or "", job_id or "", selected_technician, start, end,
            notes="Selected from database-backed availability", validated_slot=True, client=self.db_client,
        )
        logger.info("appointment_persistence_ms=%.1f", (time.perf_counter() - appointment_started) * 1000)
        if not booking.ok:
            return booking
        self.state.appointment_id = booking.data.get("id")
        self.state.job_id = job_id
        self.state.workflow_stage = "DISPATCHING"
        dispatch_started = time.perf_counter()
        dispatch = await db_tools.dispatch_job(
            job_id or "", technician_id=selected_technician, customer_id=self.customer_id,
            scheduled_start=start, scheduled_end=end,
            service_type=self.state.service_type, service_area=self.state.service_area,
            validated_selected_technician=True,
            client=self.db_client,
        )
        logger.info("dispatch_persistence_ms=%.1f", (time.perf_counter() - dispatch_started) * 1000)
        if not dispatch.ok:
            return dispatch
        persisted_job = (dispatch.data or {}).get("job") or {}
        logger.info("verification_ms=%.1f", (time.perf_counter() - dispatch_started) * 1000)
        if persisted_job.get("technician_id") != selected_technician:
            return db_tools.ToolResult("failure", error="Technician assignment verification failed")
        self.state.booking_status = "dispatched"
        self.state.dispatch_status = "success"
        self.state.workflow_stage = "COMPLETE"
        logger.info("total_finalization_ms=%.1f", (time.perf_counter() - stage_started) * 1000)
        return db_tools.ToolResult("success", {
            "appointment": booking.data, "dispatch": dispatch.data,
        })

    @function_tool()
    async def select_available_slot(
        self, context: RunContext, slot_id: str = "",
        scheduled_start: str = "", scheduled_end: str = "", technician_id: str = "",
    ) -> str:
        """Select only one of the previously returned backend slots."""
        self._bind_state(context)
        if context is not None:
            context.disallow_interruptions()
        if not slot_id:
            raise ToolError("Select an available slot by its backend slot ID")
        slots = self.state.candidate_slots or []
        selected = next((slot for slot in slots if slot.get("slot_id") == slot_id), None)
        if selected is None:
            raise ToolError("Please choose one of the available appointment options")
        self.state.scheduled_start = selected["start"]
        self.state.scheduled_end = selected["end"]
        self.state.selected_slot_id = selected["slot_id"]
        self.state.selected_technician_id = selected["technician_id"]
        self.state.selected_scheduled_start = selected["start"]
        self.state.selected_scheduled_end = selected["end"]
        self.state.availability_mode = "FLEXIBLE"
        self.state.workflow_stage = "DISPATCHING"
        logger.info(
            "availability_selection status=success technician_id=%s start=%s end=%s",
            selected["technician_id"], selected["start"], selected["end"],
        )
        selection_started = time.perf_counter()
        finalized = await self._finalize_selected_slot()
        logger.info("select_available_slot_duration_ms=%.1f", (time.perf_counter() - selection_started) * 1000)
        if not finalized.ok:
            self.state.booking_status = "failed"
            raise ToolError(finalized.error or "The selected appointment could not be booked")
        logger.info(
            "booking_finalization status=success job_id=%s appointment_id=%s technician_id=%s",
            self.state.job_id, self.state.appointment_id, self.state.selected_technician_id,
        )
        return _json(_result(finalized))

    def _missing_customer(self) -> db_tools.ToolResult:
        return db_tools.ToolResult(
            "not_found",
            error="Customer identity is not available for this session",
        )

    def _normal_workflow_guard(self) -> db_tools.ToolResult | None:
        if getattr(self, "_safety_escalated", False):
            return db_tools.ToolResult(
                "failure", error="Normal workflow stopped because safety escalation is active"
            )
        return None

    async def _get_customer_context(self) -> db_tools.ToolResult:
        guard = self._normal_workflow_guard()
        if guard is not None:
            return guard
        if not self.customer_id:
            return self._missing_customer()
        return await run_with_recovery(
            lambda: db_tools.get_customer_context(self.customer_id, client=self.db_client),
            operation_name="get_customer_context",
            retry_safe=True,
        )

    @staticmethod
    def _valid_address(address: str) -> bool:
        return bool(re.search(r"\b\d+[A-Za-z]?\s+[A-Za-z]", address.strip())) and len(address.split()) >= 3

    async def _validate_dispatch_prerequisites(
        self, *, service_type: str, service_area: str, scheduled_start: str,
        scheduled_end: str, address: str, full_name: str, phone: str,
    ) -> db_tools.ToolResult:
        """Enforce intake and customer-resolution prerequisites before handoff."""
        missing: list[str] = []
        if not service_type.strip():
            missing.append("service type")
        if not service_area.strip():
            missing.append("service area")
        try:
            start = _parse_time(scheduled_start)
            end = _parse_time(scheduled_end)
            if end <= start:
                missing.append("a valid requested time window")
        except (TypeError, ValueError):
            missing.append("requested date and time window")
        if not self._valid_address(address):
            missing.append("full street address")
        if missing:
            logger.info("required_info_validation status=failure missing=%s", ",".join(missing))
            return db_tools.ToolResult("failure", error="Please provide your " + ", ".join(missing) + ".")
        if self.customer_id:
            context = await self._get_customer_context()
            if not context.ok:
                logger.warning("customer_resolution status=%s error=%s", context.status, context.error)
                return context
            customer = context.data.get("customer") or {}
            self._customer_name = getattr(self, "_customer_name", None) or customer.get("full_name")
            self._customer_phone = getattr(self, "_customer_phone", None) or customer.get("phone")
        else:
            self._customer_name = full_name.strip() or None
            self._customer_phone = _normalize_phone(phone) or None
        if not self._customer_name:
            missing.append("customer full name")
        if not self._customer_phone or len("".join(char for char in self._customer_phone if char.isdigit())) < 7:
            missing.append("customer phone number")
        if missing:
            logger.info("required_info_validation status=failure missing=%s", ",".join(missing))
            return db_tools.ToolResult("failure", error="Please provide your " + ", ".join(missing) + ".")

        if not self.customer_id:
            logger.info("customer_resolution status=attempt phone=%s", self._customer_phone)
            resolved = await db_tools.resolve_or_create_customer(
                self._customer_name, self._customer_phone, address=address, client=self.db_client
            )
            if not resolved.ok:
                logger.warning("customer_resolution status=%s error=%s", resolved.status, resolved.error)
                return resolved
            self.customer_id = resolved.data["customer"]["id"]
            self.state.customer_id = self.customer_id
            self._workflow_state = "CUSTOMER_RESOLVED"
            logger.info("customer_resolution status=success customer_id=%s created=%s", self.customer_id, resolved.data.get("created"))
        else:
            self._workflow_state = "CUSTOMER_RESOLVED"
        self._sync_state()
        self.state.workflow_stage = "CUSTOMER_RESOLVED"
        return db_tools.ToolResult("success", {"customer_id": self.customer_id})

    async def _resolve_dispatch_job_id(
        self, requested_job_id: str | None, *, service_type: str = "",
        service_area: str = "", address: str = "", description: str = "",
    ) -> tuple[str | None, db_tools.ToolResult | None]:
        """Resolve dispatch identity from customer context, never from model invention."""
        canonical_job_id = _real_id(getattr(self.state, "job_id", None))
        if canonical_job_id and callable(getattr(self.db_client, "from_", None)):
            existing = await db_tools.get_job(canonical_job_id, client=self.db_client)
            if existing.ok and existing.data.get("customer_id") == self.customer_id:
                self._last_job_id = canonical_job_id
                self.state.job_id = canonical_job_id
                self.state.workflow_stage = "JOB_CREATED"
                logger.info("job_creation status=existing job_id=%s", canonical_job_id)
                return canonical_job_id, None
            if not existing.ok:
                return None, existing

        context_result = await self._get_customer_context()
        if not context_result.ok:
            return None, context_result
        jobs = context_result.data.get("jobs", [])
        known_ids = {job.get("id") for job in jobs if job.get("id")}
        last_job_id = getattr(self, "_last_job_id", None)
        if last_job_id and last_job_id in known_ids:
            self._workflow_state = "JOB_CREATED"
            self.state.job_id = last_job_id
            self.state.workflow_stage = "JOB_CREATED"
            logger.info("canonical_job_id source=session state=valid job_id=%s", last_job_id)
            return last_job_id, None
        if requested_job_id and requested_job_id in known_ids:
            self._last_job_id = requested_job_id
            self.state.job_id = requested_job_id
            self._workflow_state = "JOB_CREATED"
            self.state.workflow_stage = "JOB_CREATED"
            logger.info("canonical_job_id source=customer_context job_id=%s", requested_job_id)
            return requested_job_id, None
        if (not requested_job_id or requested_job_id not in known_ids) and service_type and (address or service_area):
            logger.info("dispatch_handoff_ignoring_unverified_job_id requested_job_id=%r", requested_job_id)
            created = await db_tools.create_job(
                self.customer_id or "", service_type,
                description or f"{service_type} service request",
                address or service_area,
                client=self.db_client,
            )
            if not created.ok:
                logger.warning("job_creation status=%s error=%s", created.status, created.error)
                return None, created
            canonical_id = created.data.get("id")
            if not canonical_id:
                return None, db_tools.ToolResult("failure", error="Created job has no canonical database ID")
            self._last_job_id = canonical_id
            self.state.job_id = canonical_id
            self._workflow_state = "JOB_CREATED"
            self.state.workflow_stage = "JOB_CREATED"
            logger.info("job_creation status=success canonical_job_id=%s", canonical_id)
            return canonical_id, None
        if not known_ids:
            return None, db_tools.ToolResult("not_found", error="No existing customer job is available for dispatch")
        if requested_job_id:
            return None, db_tools.ToolResult("not_found", error="Requested job was not found for customer")
        return None, db_tools.ToolResult("failure", error="Multiple existing jobs require clarification")

    @function_tool()
    async def get_customer_context(self, context: RunContext) -> str:
        """Get the current customer's jobs and appointments."""
        guard = self._normal_workflow_guard()
        if guard is not None:
            raise ToolError(guard.error or "Normal workflow is unavailable")
        result = await self._get_customer_context()
        if not result.ok:
            raise ToolError(result.error or "Customer context could not be retrieved")
        return _json(_result(result))

    @function_tool()
    async def get_job(self, context: RunContext, job_id: str) -> str:
        """Get one existing service job by ID from customer context."""
        guard = self._normal_workflow_guard()
        if guard is not None:
            raise ToolError(guard.error or "Normal workflow is unavailable")
        result = await db_tools.get_job(job_id, client=self.db_client)
        if not result.ok:
            raise ToolError(result.error or "Job could not be found")
        return _json(_result(result))

    @function_tool()
    async def check_booking_status(self, context: RunContext, job_id: str = "") -> str:
        """Report authoritative booking, job, and technician-assignment state."""
        self._ensure_state()
        canonical = getattr(self, "_last_job_id", None)
        if canonical and (not job_id or job_id != canonical):
            job_id = canonical
        if not job_id:
            return _json(_result(db_tools.ToolResult("not_found", error="No canonical job exists yet")))
        result = await db_tools.get_job_status(job_id, customer_id=self.customer_id, client=self.db_client)
        dispatch_status = getattr(self.state, "dispatch_status", "not_started")
        if dispatch_status in {"failure", "unavailable"}:
            return _json(_result(db_tools.ToolResult(
                dispatch_status,
                data={"state": "operation_failed", "backend": result.data},
                error="The dispatch operation did not complete successfully",
            )))
        if not result.ok:
            raise ToolError(result.error or "Booking status could not be retrieved")
        return _json(_result(result))

    @function_tool()
    async def check_technician_availability(
        self,
        context: RunContext,
        technician_id: str,
        scheduled_start: str,
        scheduled_end: str,
    ) -> str:
        """Check a concrete technician and time window before booking."""
        guard = self._normal_workflow_guard()
        if guard is not None:
            raise ToolError(guard.error or "Normal workflow is unavailable")
        try:
            result = await db_tools.check_technician_availability(
                technician_id,
                _parse_time(scheduled_start),
                _parse_time(scheduled_end),
                client=self.db_client,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if not result.ok:
            raise ToolError(result.error or "Technician availability could not be checked")
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
        self._ensure_state()
        guard = self._normal_workflow_guard()
        if guard is not None:
            return guard
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
        self._last_job_id = result.data.get("job_id") or job_id
        self.state.job_id = self._last_job_id
        self.state.booking_status = "confirmed"
        self._sync_state()
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
        if context is not None:
            context.disallow_interruptions()
        result = await self._book_appointment(
            service_type=service_type,
            service_area=service_area,
            job_id=job_id,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
            notes=notes or None,
        )
        if not result.ok:
            raise ToolError(result.error or "The appointment could not be booked")
        return _json(_result(result))

    async def _reschedule_appointment(
        self, appointment_id: str, scheduled_start: str, scheduled_end: str
    ) -> db_tools.ToolResult:
        guard = self._normal_workflow_guard()
        if guard is not None:
            return guard
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
        if context is not None:
            context.disallow_interruptions()
        result = await self._reschedule_appointment(appointment_id, scheduled_start, scheduled_end)
        if not result.ok:
            raise ToolError(result.error or "The appointment could not be rescheduled")
        return _json(_result(result))

    async def _cancel_appointment(self, appointment_id: str) -> db_tools.ToolResult:
        guard = self._normal_workflow_guard()
        if guard is not None:
            return guard
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
        if context is not None:
            context.disallow_interruptions()
        result = await self._cancel_appointment(appointment_id)
        if not result.ok:
            raise ToolError(result.error or "The appointment could not be cancelled")
        return _json(_result(result))

