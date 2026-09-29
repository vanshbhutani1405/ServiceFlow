"""Canonical per-call workflow state stored in AgentSession.userdata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WorkflowState:
    customer_id: str | None = None
    full_name: str | None = None
    phone: str | None = None
    service_type: str | None = None
    service_area: str | None = None
    address: str | None = None
    description: str | None = None
    scheduled_start: str | None = None
    scheduled_end: str | None = None
    job_id: str | None = None
    appointment_id: str | None = None
    booking_status: str = "not_started"
    dispatch_status: str = "not_started"
    active_workflow: str | None = None
    workflow_stage: str = "INTAKE"
    availability_mode: str = "EXACT"
    candidate_slots: list[dict] | None = None
    selected_slot_id: str | None = None
    selected_technician_id: str | None = None
    selected_scheduled_start: str | None = None
    selected_scheduled_end: str | None = None

    def next_missing_field(self) -> str | None:
        ordered = (
            ("service type", self.service_type),
            ("service area", self.service_area),
            ("requested date", self.scheduled_start),
            ("requested time window", self.scheduled_end),
            ("full service address", self.address),
            ("full name", self.full_name),
            ("phone number", self.phone),
        )
        return next((name for name, value in ordered if not value), None)
