"""Deterministic database tools for the ServiceFlow MVP.

These functions are intentionally not wired into the conversational agent yet.
They return explicit statuses so future agents cannot mistake a failed
operation for a successful one.
"""

from __future__ import annotations

import asyncio
import logging
import re
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from supabase import Client

from agent.matching.technician_matcher import (
    JobRequirements,
    TechnicianCandidate,
    match_technician,
)
from db.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)
Status = Literal["success", "failure", "not_found", "unavailable"]


@dataclass(frozen=True)
class ToolResult:
    status: Status
    data: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "success"


def _client(client: Client | None) -> Client:
    return client or get_supabase_client()


async def _execute(query: Any) -> Any:
    return await asyncio.to_thread(query.execute)


async def _one(client: Client, table: str, row_id: str) -> dict[str, Any] | None:
    response = await _execute(client.from_(table).select("*").eq("id", row_id).limit(1))
    rows = response.data or []
    return rows[0] if rows else None


async def _many(query: Any) -> list[dict[str, Any]]:
    response = await _execute(query)
    return list(response.data or [])


async def _execute_one(query: Any) -> dict[str, Any] | None:
    """Execute a returning query and safely take its first returned row.

    The installed PostgREST 2.31 query builder has no ``single()`` method.
    Supabase mutations with ``select`` return rows through ``execute()``.
    """
    rows = await _many(query)
    return rows[0] if rows else None


def _normalize_phone(phone: str) -> str:
    return re.sub(r"[^\d+]", "", phone.strip())


def _failure(tool_name: str, exc: Exception) -> ToolResult:
    logger.exception("database_tool_failed tool=%s", tool_name)
    return ToolResult("failure", error=str(exc))


async def get_customer_context(customer_id: str, *, client: Client | None = None) -> ToolResult:
    try:
        db = _client(client)
        customer = await _one(db, "customers", customer_id)
        if customer is None:
            return ToolResult("not_found", error="Customer not found")
        jobs = await _many(db.from_("jobs").select("*").eq("customer_id", customer_id).order("created_at", desc=True))
        appointments = await _many(db.from_("appointments").select("*").eq("customer_id", customer_id).order("scheduled_start", desc=True))
        return ToolResult("success", {"customer": customer, "jobs": jobs, "appointments": appointments})
    except Exception as exc:
        return _failure("get_customer_context", exc)


async def resolve_or_create_customer(
    full_name: str,
    phone: str,
    *,
    address: str | None = None,
    client: Client | None = None,
) -> ToolResult:
    """Resolve a customer by phone or create one with a database-generated UUID."""
    normalized_phone = _normalize_phone(phone)
    if not full_name.strip() or len("".join(char for char in normalized_phone if char.isdigit())) < 7:
        return ToolResult("failure", error="A full name and valid phone number are required")
    try:
        db = _client(client)
        matches = await _many(db.from_("customers").select("*").eq("phone", normalized_phone).limit(1))
        if matches:
            return ToolResult("success", {"customer": matches[0], "created": False})
        customer = await _execute_one(db.from_("customers").insert({
            "full_name": full_name.strip(), "phone": normalized_phone, "address": address,
        }).select("*"))
        if not customer or not customer.get("id"):
            return ToolResult("failure", error="Customer creation returned no database ID")
        return ToolResult("success", {"customer": customer, "created": True})
    except Exception as exc:
        return _failure("resolve_or_create_customer", exc)


async def get_job(job_id: str, *, client: Client | None = None) -> ToolResult:
    try:
        job = await _one(_client(client), "jobs", job_id)
        return ToolResult("success", job) if job else ToolResult("not_found", error="Job not found")
    except Exception as exc:
        return _failure("get_job", exc)


async def create_job(
    customer_id: str,
    service_type: str,
    description: str,
    address: str,
    *,
    priority: str = "normal",
    client: Client | None = None,
) -> ToolResult:
    """Create a new job and return the database-generated canonical UUID."""
    if not service_type.strip() or not description.strip() or not address.strip():
        return ToolResult("failure", error="service_type, description, and address are required")
    try:
        db = _client(client)
        if not await _one(db, "customers", customer_id):
            return ToolResult("not_found", error="Customer not found")
        job = await _execute_one(db.from_("jobs").insert({
            "customer_id": customer_id,
            "service_type": service_type.strip(),
            "description": description.strip(),
            "address": address.strip(),
            "priority": priority,
            "status": "open",
        }).select("*"))
        if not job or not job.get("id"):
            return ToolResult("failure", error="Job creation returned no database ID")
        return ToolResult("success", job)
    except Exception as exc:
        return _failure("create_job", exc)


async def get_job_status(
    job_id: str,
    *,
    customer_id: str | None = None,
    client: Client | None = None,
) -> ToolResult:
    """Return authoritative job/appointment state for status questions."""
    try:
        db = _client(client)
        job = await _one(db, "jobs", job_id)
        if not job:
            return ToolResult("not_found", error="Job not found")
        if customer_id is not None and job.get("customer_id") != customer_id:
            return ToolResult("not_found", error="Job not found for customer")
        appointments = await _many(db.from_("appointments").select("*").eq("job_id", job_id))
        appointment = appointments[0] if appointments else None
        if appointment and appointment.get("status") in {"confirmed", "rescheduled", "dispatched"}:
            state = "appointment_confirmed" if appointment.get("status") != "dispatched" else "technician_assigned"
        elif job.get("status") == "dispatched":
            state = "job_dispatched"
        else:
            state = "job_created"
        return ToolResult("success", {"state": state, "job": job, "appointment": appointment})
    except Exception as exc:
        return _failure("get_job_status", exc)


async def check_technician_availability(
    technician_id: str,
    scheduled_start: datetime,
    scheduled_end: datetime,
    *,
    client: Client | None = None,
    exclude_appointment_id: str | None = None,
) -> ToolResult:
    if scheduled_end <= scheduled_start:
        return ToolResult("failure", error="scheduled_end must be after scheduled_start")
    try:
        db = _client(client)
        technician = await _one(db, "technicians", technician_id)
        if technician is None:
            return ToolResult("not_found", error="Technician not found")
        windows = await _many(
            db.from_("technician_availability")
            .select("*")
            .eq("technician_id", technician_id)
            .eq("status", "available")
            .lte("available_start", scheduled_start.isoformat())
            .gte("available_end", scheduled_end.isoformat())
        )
        query = (
            db.from_("appointments")
            .select("*")
            .eq("technician_id", technician_id)
            .in_("status", ["requested", "confirmed", "rescheduled", "dispatched"])
            .lt("scheduled_start", scheduled_end.isoformat())
            .gt("scheduled_end", scheduled_start.isoformat())
        )
        existing = await _many(query)
        if exclude_appointment_id:
            existing = [row for row in existing if row.get("id") != exclude_appointment_id]
        available = bool(windows) and not existing
        return ToolResult("success" if available else "unavailable", {
            "technician": technician,
            "availability_windows": windows,
            "conflicting_appointments": existing,
            "available": available,
        }, None if available else "Technician is unavailable for the requested slot")
    except Exception as exc:
        return _failure("check_technician_availability", exc)


async def find_best_technician(
    service_type: str,
    service_area: str,
    scheduled_start: datetime,
    scheduled_end: datetime,
    *,
    client: Client | None = None,
) -> ToolResult:
    """Return eligible candidates only; final ranking belongs to Phase 4."""
    try:
        db = _client(client)
        technicians = await _many(db.from_("technicians").select("*").eq("status", "active"))
        requested_area = _area_key(service_area)
        requested_type = _service_key(service_type)
        candidates = [
            tech for tech in technicians
            if requested_area in {_area_key(str(area)) for area in (tech.get("service_areas") or [])}
            and requested_type in {_service_key(str(skill)) for skill in (tech.get("skills") or [])}
        ]
        available = []
        for technician in candidates:
            result = await check_technician_availability(
                technician["id"], scheduled_start, scheduled_end, client=db
            )
            if result.ok and result.data["available"]:
                available.append(technician)
        return ToolResult("success", {"candidates": available, "matching_deferred": True})
    except Exception as exc:
        return _failure("find_best_technician", exc)


def _parse_db_datetime(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _area_key(value: str) -> str:
    return value.casefold().replace(" ", "_").replace("-", "_")


def _service_key(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.casefold().replace("-", " ").replace("_", " ")).strip()
    return {
        "ac repair": "hvac",
        "air conditioner repair": "hvac",
        "air conditioning repair": "hvac",
        "air conditioning": "hvac",
    }.get(normalized, normalized)


async def find_next_available_slots(
    service_area: str,
    service_type: str,
    *,
    service_duration_minutes: int = 60,
    search_start: datetime | None = None,
    search_horizon_days: int = 7,
    preferred_start_time: str | None = None,
    preferred_end_time: str | None = None,
    preferred_days: list[int] | None = None,
    client: Client | None = None,
) -> ToolResult:
    """Search real future technician availability and return deterministic slots."""
    if service_duration_minutes <= 0 or search_horizon_days <= 0:
        return ToolResult("failure", error="Service duration and search horizon must be positive")
    try:
        db = _client(client)
        start = search_start or datetime.now(timezone.utc)
        start = _parse_db_datetime(start)
        horizon_end = start + timedelta(days=search_horizon_days)
        technicians = await _many(db.from_("technicians").select("*").eq("status", "active"))
        availability = await _many(db.from_("technician_availability").select("*").eq("status", "available"))
        appointments = await _many(
            db.from_("appointments").select("*").in_(
                "status", ["requested", "confirmed", "rescheduled", "dispatched"]
            )
        )
        blocking = [
            row for row in appointments
            if row.get("technician_id") and row.get("scheduled_start") and row.get("scheduled_end")
        ]
        duration = timedelta(minutes=service_duration_minutes)
        area = _area_key(service_area)
        skill = _service_key(service_type)
        counters = {
            "technicians_considered": len(technicians), "availability_windows": 0,
            "candidate_slots_generated": 0, "rejected_past": 0, "rejected_duration": 0,
            "rejected_service_type": 0, "rejected_service_area": 0,
            "rejected_conflict": 0, "valid_slots": 0,
        }
        rejection_logs = 0

        def reject(reason: str, **details: Any) -> None:
            nonlocal rejection_logs
            counters[f"rejected_{reason}"] += 1
            if rejection_logs < 5:
                logger.info("availability_rejection reason=%s details=%s", reason, details)
                rejection_logs += 1
        slots: list[dict[str, Any]] = []

        for technician in technicians:
            tech_areas = {_area_key(str(item)) for item in (technician.get("service_areas") or [])}
            tech_skills = {str(item).casefold() for item in (technician.get("skills") or [])}
            if skill not in tech_skills:
                reject("service_type", technician_id=technician.get("id"), requested=skill)
                continue
            if area not in tech_areas:
                reject("service_area", technician_id=technician.get("id"), requested=area)
                continue
            windows = [
                row for row in availability
                if row.get("technician_id") == technician.get("id")
            ]
            for window in windows:
                counters["availability_windows"] += 1
                window_start = max(_parse_db_datetime(window["available_start"]), start)
                window_end = min(_parse_db_datetime(window["available_end"]), horizon_end)
                if window_end <= start:
                    reject("past", technician_id=technician.get("id"), window=window.get("available_start"))
                    continue
                if window_end - window_start < duration:
                    reject("duration", technician_id=technician.get("id"), duration_minutes=service_duration_minutes)
                    continue
                candidate = window_start.replace(second=0, microsecond=0)
                minute = ((candidate.minute + 29) // 30) * 30
                if minute >= 60:
                    candidate = candidate.replace(minute=0) + timedelta(hours=1)
                else:
                    candidate = candidate.replace(minute=minute)
                while candidate + duration <= window_end:
                    if preferred_days is not None and candidate.weekday() not in preferred_days:
                        candidate += timedelta(minutes=30)
                        continue
                    if preferred_start_time and candidate.strftime("%H:%M") < preferred_start_time:
                        candidate += timedelta(minutes=30)
                        continue
                    candidate_end = candidate + duration
                    if preferred_end_time and candidate_end.strftime("%H:%M") > preferred_end_time:
                        candidate += timedelta(minutes=30)
                        continue
                    conflict = False
                    counters["candidate_slots_generated"] += 1
                    for appointment in blocking:
                        if appointment["technician_id"] != technician.get("id"):
                            continue
                        existing_start = _parse_db_datetime(appointment["scheduled_start"])
                        existing_end = _parse_db_datetime(appointment["scheduled_end"])
                        if existing_start < candidate_end and existing_end > candidate:
                            conflict = True
                            reject("conflict", technician_id=technician.get("id"), start=candidate.isoformat())
                            logger.info(
                                "availability_conflict technician_id=%s start=%s end=%s",
                                technician.get("id"), candidate.isoformat(), candidate_end.isoformat(),
                            )
                            break
                    if not conflict:
                        slot = {
                            "slot_id": f"{technician['id']}:{candidate.isoformat()}",
                            "start": candidate.isoformat(),
                            "end": candidate_end.isoformat(),
                            "technician_id": technician["id"],
                            "normalized_service_type": skill,
                            "service_area": area,
                            "duration_minutes": service_duration_minutes,
                        }
                        slots.append(slot)
                        counters["valid_slots"] += 1
                        logger.info(
                            "availability_candidate technician_id=%s start=%s end=%s",
                            technician["id"], slot["start"], slot["end"],
                        )
                    candidate += timedelta(minutes=30)
        slots.sort(key=lambda slot: (slot["start"], slot["technician_id"]))
        slots = slots[:5]
        status = "success" if slots else "unavailable"
        logger.info(
            "availability_search status=%s mode=FLEXIBLE horizon=%s slots_found=%s counters=%s",
            status, search_horizon_days, len(slots), counters,
        )
        return ToolResult(status, {"slots": slots, "search_horizon_days": search_horizon_days},
                          None if slots else "No eligible technician slots were found")
    except Exception as exc:
        return _failure("find_next_available_slots", exc)


async def book_appointment(
    customer_id: str,
    job_id: str,
    technician_id: str,
    scheduled_start: datetime,
    scheduled_end: datetime,
    *,
    notes: str | None = None,
    validated_slot: bool = False,
    client: Client | None = None,
) -> ToolResult:
    try:
        db = _client(client)
        if not await _one(db, "customers", customer_id):
            return ToolResult("not_found", error="Customer not found")
        job = await _one(db, "jobs", job_id)
        if not job:
            return ToolResult("not_found", error="Job not found")
        if job.get("customer_id") != customer_id:
            return ToolResult("failure", error="Job does not belong to customer")
        existing = await _many(
            db.from_("appointments")
            .select("*")
            .eq("customer_id", customer_id)
            .eq("job_id", job_id)
            .eq("technician_id", technician_id)
            .eq("scheduled_start", scheduled_start.isoformat())
            .eq("scheduled_end", scheduled_end.isoformat())
            .in_("status", ["requested", "confirmed", "rescheduled", "dispatched"])
        )
        if existing:
            logger.info("appointment status=existing appointment_id=%s job_id=%s", existing[0].get("id"), job_id)
            return ToolResult("success", existing[0])
        if not validated_slot:
            availability = await check_technician_availability(technician_id, scheduled_start, scheduled_end, client=db)
            if not availability.ok:
                return availability
        appointment = await _execute_one(db.from_("appointments").insert({
            "customer_id": customer_id, "job_id": job_id, "technician_id": technician_id,
            "scheduled_start": scheduled_start.isoformat(), "scheduled_end": scheduled_end.isoformat(),
            "status": "confirmed", "notes": notes,
        }).select("*"))
        if not appointment or not appointment.get("id"):
            return ToolResult("failure", error="Appointment creation returned no database ID")
        await _execute(db.from_("jobs").update({"status": "scheduled"}).eq("id", job_id))
        await _execute(db.from_("appointment_events").insert({
            "appointment_id": appointment["id"], "event_type": "booked", "metadata": {},
        }))
        return ToolResult("success", appointment)
    except Exception as exc:
        return _failure("book_appointment", exc)


async def reschedule_appointment(
    appointment_id: str,
    scheduled_start: datetime,
    scheduled_end: datetime,
    *,
    customer_id: str | None = None,
    client: Client | None = None,
) -> ToolResult:
    try:
        db = _client(client)
        appointment = await _one(db, "appointments", appointment_id)
        if not appointment:
            return ToolResult("not_found", error="Appointment not found")
        if customer_id is not None and appointment.get("customer_id") != customer_id:
            return ToolResult("not_found", error="Appointment not found for customer")
        if appointment.get("status") in {"cancelled", "completed"}:
            return ToolResult("failure", error=f"Cannot reschedule appointment in {appointment['status']} state")
        availability = await check_technician_availability(
            appointment["technician_id"], scheduled_start, scheduled_end,
            client=db, exclude_appointment_id=appointment_id,
        )
        if not availability.ok:
            return availability
        updated = await _execute_one(db.from_("appointments").update({
            "scheduled_start": scheduled_start.isoformat(),
            "scheduled_end": scheduled_end.isoformat(),
            "status": "rescheduled",
        }).eq("id", appointment_id).select("*"))
        if not updated:
            return ToolResult("failure", error="Reschedule returned no database row")
        await _execute(db.from_("appointment_events").insert({
            "appointment_id": appointment_id, "event_type": "rescheduled", "metadata": {},
        }))
        return ToolResult("success", updated)
    except Exception as exc:
        return _failure("reschedule_appointment", exc)


async def cancel_appointment(
    appointment_id: str,
    *,
    customer_id: str | None = None,
    client: Client | None = None,
) -> ToolResult:
    try:
        db = _client(client)
        appointment = await _one(db, "appointments", appointment_id)
        if not appointment:
            return ToolResult("not_found", error="Appointment not found")
        if customer_id is not None and appointment.get("customer_id") != customer_id:
            return ToolResult("not_found", error="Appointment not found for customer")
        if appointment.get("status") == "cancelled":
            return ToolResult("failure", error="Appointment is already cancelled")
        if appointment.get("status") == "completed":
            return ToolResult("failure", error="Cannot cancel a completed appointment")
        updated = await _execute_one(db.from_("appointments").update({"status": "cancelled"}).eq("id", appointment_id).select("*"))
        if not updated:
            return ToolResult("failure", error="Cancellation returned no database row")
        await _execute(db.from_("appointment_events").insert({
            "appointment_id": appointment_id, "event_type": "cancelled", "metadata": {},
        }))
        return ToolResult("success", updated)
    except Exception as exc:
        return _failure("cancel_appointment", exc)


async def match_technician_for_job(
    job_id: str,
    scheduled_start: datetime,
    scheduled_end: datetime,
    *,
    customer_id: str | None = None,
    service_type: str | None = None,
    service_area: str | None = None,
    client: Client | None = None,
) -> ToolResult:
    """Find and deterministically select an eligible technician for a job."""
    try:
        db = _client(client)
        job = await _one(db, "jobs", job_id)
        if not job:
            return ToolResult("not_found", error="Job not found")
        if customer_id is not None and job.get("customer_id") != customer_id:
            return ToolResult("not_found", error="Job not found for customer")
        customer = await _one(db, "customers", job["customer_id"])
        if not customer:
            return ToolResult("not_found", error="Customer not found")
        requested_type = _service_key(service_type or job.get("service_type") or "")
        requested_area = _area_key(service_area or customer.get("service_area") or "")
        if not requested_type or not requested_area:
            return ToolResult("failure", error="Job service type and customer service area are required")

        technicians = await _many(db.from_("technicians").select("*"))
        candidates: list[TechnicianCandidate] = []
        for row in technicians:
            availability = await check_technician_availability(
                row["id"], scheduled_start, scheduled_end, client=db
            )
            workload_rows = await _many(
                db.from_("appointments")
                .select("id")
                .eq("technician_id", row["id"])
                .in_("status", ["requested", "confirmed", "rescheduled", "dispatched"])
            )
            candidates.append(TechnicianCandidate(
                id=row["id"], full_name=row.get("full_name", ""), status=row.get("status", ""),
                skills=tuple(str(skill).casefold() for skill in (row.get("skills") or ())),
                service_areas=tuple(_area_key(str(area)) for area in (row.get("service_areas") or ())),
                available=availability.ok and bool(availability.data.get("available")),
                workload=len(workload_rows),
                distance_minutes=row.get("distance_minutes"), eta_minutes=row.get("eta_minutes"),
            ))
        decision = match_technician(JobRequirements(requested_type, requested_area, scheduled_start, scheduled_end), candidates)
        logger.info(
            "technician_matching status=%s technician_id=%s",
            decision.status, decision.technician.id if decision.technician else None,
        )
        data = {
            "technician": decision.technician.__dict__ if decision.technician else None,
            "eligible_candidates": [candidate.__dict__ for candidate in decision.eligible_candidates],
            "explanation": list(decision.explanation),
        }
        return ToolResult(decision.status, data=data, error=decision.error)
    except Exception as exc:
        return _failure("match_technician_for_job", exc)


async def validate_selected_technician_for_booking(
    technician_id: str,
    service_type: str,
    service_area: str,
    scheduled_start: datetime,
    scheduled_end: datetime,
    *,
    client: Client | None = None,
) -> ToolResult:
    """Validate one selected technician; never select or substitute another."""
    try:
        db = _client(client)
        technician = await _one(db, "technicians", technician_id)
        if technician is None:
            return ToolResult("not_found", {"reason": "technician_not_found"}, "Technician not found")
        if technician.get("status", "").casefold() != "active":
            return ToolResult("failure", {"reason": "technician_inactive"}, "Selected technician is inactive")
        normalized_type = _service_key(service_type)
        if normalized_type not in {_service_key(str(skill)) for skill in (technician.get("skills") or ())}:
            return ToolResult("failure", {"reason": "service_type_mismatch"}, "Selected technician does not support this service")
        if _area_key(service_area) not in {_area_key(str(area)) for area in (technician.get("service_areas") or ())}:
            return ToolResult("failure", {"reason": "service_area_mismatch"}, "Selected technician does not cover this service area")
        availability = await check_technician_availability(
            technician_id, scheduled_start, scheduled_end, client=db,
        )
        if not availability.ok:
            reason = "appointment_conflict" if availability.data and availability.data.get("conflicting_appointments") else "availability_invalidated"
            return ToolResult(availability.status, {"reason": reason, "technician": technician}, availability.error)
        return ToolResult("success", {"reason": "valid", "technician": technician})
    except Exception as exc:
        return _failure("validate_selected_technician_for_booking", exc)


async def dispatch_job(
    job_id: str,
    *,
    technician_id: str | None = None,
    customer_id: str | None = None,
    scheduled_start: datetime | None = None,
    scheduled_end: datetime | None = None,
    service_type: str | None = None,
    service_area: str | None = None,
    validated_selected_technician: bool = False,
    client: Client | None = None,
) -> ToolResult:
    """Assign a job through deterministic matching and persist dispatch state."""
    try:
        db = _client(client)
        job = await _one(db, "jobs", job_id)
        if not job:
            return ToolResult("not_found", error="Job not found")
        if customer_id is not None and job.get("customer_id") != customer_id:
            return ToolResult("not_found", error="Job not found for customer")
        if job.get("status") == "dispatched" and job.get("technician_id"):
            technician = await _one(db, "technicians", job["technician_id"])
            if technician is None:
                return ToolResult("failure", error="Dispatched job has no persisted technician record")
            existing_appointments = await _many(db.from_("appointments").select("*").eq("job_id", job_id))
            if any(
                appointment.get("technician_id") != job["technician_id"]
                or appointment.get("status") != "dispatched"
                for appointment in existing_appointments
            ):
                return ToolResult("failure", error="Persisted appointment assignment does not match the job")
            logger.info("dispatch status=existing job_id=%s technician_id=%s", job_id, job["technician_id"])
            return ToolResult("success", {
                "job": job, "technician": technician, "explanation": ["Existing dispatch reused"],
            })
        appointments = await _many(db.from_("appointments").select("*").eq("job_id", job_id))
        appointment = appointments[0] if appointments else None
        if scheduled_start is None and appointment:
            scheduled_start = datetime.fromisoformat(str(appointment["scheduled_start"]).replace("Z", "+00:00"))
        if scheduled_end is None and appointment:
            scheduled_end = datetime.fromisoformat(str(appointment["scheduled_end"]).replace("Z", "+00:00"))
        if scheduled_start is None or scheduled_end is None:
            return ToolResult("failure", error="A scheduled appointment window is required for dispatch")
        if technician_id is not None:
            if validated_selected_technician:
                selected = await _one(db, "technicians", technician_id)
                if selected is None:
                    return ToolResult("not_found", error="Selected technician not found")
                explanation = ["Selected technician already validated"]
            else:
                validation = await validate_selected_technician_for_booking(
                    technician_id, service_type or job.get("service_type") or "",
                    service_area or "", scheduled_start, scheduled_end, client=db,
                )
                if not validation.ok:
                    return validation
                selected = validation.data["technician"]
                explanation = ["Selected technician revalidated"]
        else:
            match = await match_technician_for_job(
                job_id, scheduled_start, scheduled_end, customer_id=customer_id,
                service_type=service_type, service_area=service_area, client=db,
            )
            if not match.ok:
                return match
            selected = match.data["technician"]
            explanation = match.data["explanation"]
            technician_id = selected["id"]
        patch = {"status": "dispatched", "technician_id": technician_id}
        updated_job = await _execute_one(db.from_("jobs").update(patch).eq("id", job_id).select("*"))
        if not updated_job or updated_job.get("id") != job_id or updated_job.get("technician_id") != technician_id:
            logger.warning("technician_assignment status=failure job_id=%s technician_id=%s", job_id, technician_id)
            return ToolResult("failure", error="Technician assignment was not persisted")
        logger.info("technician_assignment status=success job_id=%s technician_id=%s", job_id, technician_id)
        if appointment:
            updated_appointment = await _execute_one(db.from_("appointments").update({
                "technician_id": technician_id, "status": "dispatched",
            }).eq("id", appointment["id"]).select("*"))
            if (
                not updated_appointment
                or updated_appointment.get("technician_id") != technician_id
                or updated_appointment.get("status") != "dispatched"
            ):
                return ToolResult("failure", error="Appointment technician assignment was not persisted")
            await _execute(db.from_("appointment_events").insert({
                "appointment_id": appointment["id"], "event_type": "dispatched",
                "metadata": {"technician_id": technician_id, "explanation": explanation},
            }))
        await _execute(db.from_("tool_executions").insert({
            "tool_name": "dispatch_job", "status": "success",
            "input": {"job_id": job_id, "technician_id": technician_id},
            "output": updated_job,
        }))
        return ToolResult("success", {"job": updated_job, "technician": selected, "explanation": explanation})
    except Exception as exc:
        return _failure("dispatch_job", exc)


async def send_confirmation(
    appointment_id: str,
    *,
    channel: str = "recorded",
    message: str | None = None,
    client: Client | None = None,
) -> ToolResult:
    try:
        db = _client(client)
        if not await _one(db, "appointments", appointment_id):
            return ToolResult("not_found", error="Appointment not found")
        event = await _execute_one(db.from_("appointment_events").insert({
            "appointment_id": appointment_id,
            "event_type": "confirmation_recorded",
            "metadata": {"channel": channel, "message": message},
        }).select("*"))
        return ToolResult("success", event) if event else ToolResult("failure", error="Confirmation event was not recorded")
    except Exception as exc:
        return _failure("send_confirmation", exc)

