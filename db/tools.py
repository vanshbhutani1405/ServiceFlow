"""Deterministic database tools for the ServiceFlow MVP.

These functions are intentionally not wired into the conversational agent yet.
They return explicit statuses so future agents cannot mistake a failed
operation for a successful one.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from supabase import Client

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


async def get_job(job_id: str, *, client: Client | None = None) -> ToolResult:
    try:
        job = await _one(_client(client), "jobs", job_id)
        return ToolResult("success", job) if job else ToolResult("not_found", error="Job not found")
    except Exception as exc:
        return _failure("get_job", exc)


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
        candidates = [
            tech for tech in technicians
            if service_area in (tech.get("service_areas") or [])
            and service_type in (tech.get("skills") or [])
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


async def book_appointment(
    customer_id: str,
    job_id: str,
    technician_id: str,
    scheduled_start: datetime,
    scheduled_end: datetime,
    *,
    notes: str | None = None,
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
        availability = await check_technician_availability(technician_id, scheduled_start, scheduled_end, client=db)
        if not availability.ok:
            return availability
        response = await _execute(db.from_("appointments").insert({
            "customer_id": customer_id, "job_id": job_id, "technician_id": technician_id,
            "scheduled_start": scheduled_start.isoformat(), "scheduled_end": scheduled_end.isoformat(),
            "status": "confirmed", "notes": notes,
        }).select("*").single())
        appointment = response.data
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
    client: Client | None = None,
) -> ToolResult:
    try:
        db = _client(client)
        appointment = await _one(db, "appointments", appointment_id)
        if not appointment:
            return ToolResult("not_found", error="Appointment not found")
        if appointment.get("status") in {"cancelled", "completed"}:
            return ToolResult("failure", error=f"Cannot reschedule appointment in {appointment['status']} state")
        availability = await check_technician_availability(
            appointment["technician_id"], scheduled_start, scheduled_end,
            client=db, exclude_appointment_id=appointment_id,
        )
        if not availability.ok:
            return availability
        response = await _execute(db.from_("appointments").update({
            "scheduled_start": scheduled_start.isoformat(),
            "scheduled_end": scheduled_end.isoformat(),
            "status": "rescheduled",
        }).eq("id", appointment_id).select("*").single())
        updated = response.data
        await _execute(db.from_("appointment_events").insert({
            "appointment_id": appointment_id, "event_type": "rescheduled", "metadata": {},
        }))
        return ToolResult("success", updated)
    except Exception as exc:
        return _failure("reschedule_appointment", exc)


async def cancel_appointment(appointment_id: str, *, client: Client | None = None) -> ToolResult:
    try:
        db = _client(client)
        appointment = await _one(db, "appointments", appointment_id)
        if not appointment:
            return ToolResult("not_found", error="Appointment not found")
        if appointment.get("status") == "cancelled":
            return ToolResult("failure", error="Appointment is already cancelled")
        if appointment.get("status") == "completed":
            return ToolResult("failure", error="Cannot cancel a completed appointment")
        response = await _execute(db.from_("appointments").update({"status": "cancelled"}).eq("id", appointment_id).select("*").single())
        updated = response.data
        await _execute(db.from_("appointment_events").insert({
            "appointment_id": appointment_id, "event_type": "cancelled", "metadata": {},
        }))
        return ToolResult("success", updated)
    except Exception as exc:
        return _failure("cancel_appointment", exc)


async def dispatch_job(job_id: str, *, technician_id: str | None = None, client: Client | None = None) -> ToolResult:
    try:
        db = _client(client)
        job = await _one(db, "jobs", job_id)
        if not job:
            return ToolResult("not_found", error="Job not found")
        if technician_id and not await _one(db, "technicians", technician_id):
            return ToolResult("not_found", error="Technician not found")
        patch = {"status": "dispatched"}
        response = await _execute(db.from_("jobs").update(patch).eq("id", job_id).select("*").single())
        return ToolResult("success", response.data)
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
        response = await _execute(db.from_("appointment_events").insert({
            "appointment_id": appointment_id,
            "event_type": "confirmation_recorded",
            "metadata": {"channel": channel, "message": message},
        }).select("*").single())
        return ToolResult("success", response.data)
    except Exception as exc:
        return _failure("send_confirmation", exc)

