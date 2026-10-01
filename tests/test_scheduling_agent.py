from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("livekit.agents")

from agent.agents.scheduling_agent import SchedulingAgent
from agent.agents.scheduling_agent import _effective_service_duration, _normalize_datetime, _requested_window_from_text
from agent.state import WorkflowState
from db import tools as db_tools


def run(coro):
    return asyncio.run(coro)


def make_agent() -> SchedulingAgent:
    agent = SchedulingAgent.__new__(SchedulingAgent)
    agent.customer_id = "customer-1"
    agent.db_client = object()
    return agent


async def context_success(self):
    return db_tools.ToolResult("success", {"customer": {}, "jobs": [], "appointments": []})


def test_booking_does_not_claim_success_for_unavailable_slot(monkeypatch):
    agent = make_agent()
    monkeypatch.setattr(SchedulingAgent, "_get_customer_context", context_success)
    monkeypatch.setattr(
        db_tools,
        "find_best_technician",
        lambda *args, **kwargs: _async_result(db_tools.ToolResult("unavailable", error="No slot")),
    )
    result = run(agent._book_appointment(
        service_type="hvac", service_area="sf", job_id="job-1",
        scheduled_start="2026-10-01T10:00:00+00:00",
        scheduled_end="2026-10-01T11:00:00+00:00",
    ))
    assert result.status == "unavailable"
    assert not result.ok


def test_booking_requires_clarification_when_multiple_technicians(monkeypatch):
    agent = make_agent()
    monkeypatch.setattr(SchedulingAgent, "_get_customer_context", context_success)
    monkeypatch.setattr(
        db_tools,
        "find_best_technician",
        lambda *args, **kwargs: _async_result(db_tools.ToolResult(
            "success", {"candidates": [{"id": "tech-1"}, {"id": "tech-2"}]}
        )),
    )
    result = run(agent._book_appointment(
        service_type="hvac", service_area="sf", job_id="job-1",
        scheduled_start="2026-10-01T10:00:00+00:00",
        scheduled_end="2026-10-01T11:00:00+00:00",
    ))
    assert result.status == "failure"
    assert "clarification" in result.error


def test_failed_booking_is_not_confirmed(monkeypatch):
    agent = make_agent()
    monkeypatch.setattr(SchedulingAgent, "_get_customer_context", context_success)
    monkeypatch.setattr(
        db_tools,
        "find_best_technician",
        lambda *args, **kwargs: _async_result(db_tools.ToolResult("success", {"candidates": [{"id": "tech-1"}]})),
    )
    monkeypatch.setattr(
        db_tools,
        "book_appointment",
        lambda *args, **kwargs: _async_result(db_tools.ToolResult("failure", error="database down")),
    )
    confirmation_called = False

    async def unexpected_confirmation(*args, **kwargs):
        nonlocal confirmation_called
        confirmation_called = True
        return db_tools.ToolResult("success")

    monkeypatch.setattr(db_tools, "send_confirmation", unexpected_confirmation)
    result = run(agent._book_appointment(
        service_type="hvac", service_area="sf", job_id="job-1",
        scheduled_start="2026-10-01T10:00:00+00:00",
        scheduled_end="2026-10-01T11:00:00+00:00",
    ))
    assert result.status == "failure"
    assert not confirmation_called


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-10-01T10:00:00Z", "2026-10-01T11:00:00"),
        ("2026-10-01T10:00:00+00:00", "2026-10-01T11:00:00+00:00"),
    ],
)
def test_datetime_normalization_accepts_supported_iso_values(start, end):
    assert _normalize_datetime(start) < _normalize_datetime(end)


def test_datetime_normalization_rejects_invalid_value():
    with pytest.raises(ValueError):
        _normalize_datetime("tomorrow afternoon")


def test_ac_repair_duration_is_deterministic_when_llm_requests_240_minutes():
    assert _effective_service_duration("AC Repair", 240, "AC is not cooling") == 60
    assert _effective_service_duration("AC Repair", 90, "AC is not cooling") == 90
    assert _effective_service_duration("AC Repair", 240, "Please allow 4 hours") == 240


def test_record_intake_merges_phone_fragments_and_preserves_valid_value():
    agent = make_agent()
    agent.state = WorkflowState()
    agent._merge_intake(phone="seven eight eight eight")
    agent._merge_intake(phone="45123689")
    assert agent.state.phone == "788845123689"
    agent._merge_intake(phone="")
    assert agent.state.phone == "788845123689"


def test_record_intake_merges_datetime_before_validation():
    agent = make_agent()
    agent.state = WorkflowState()
    agent._merge_intake(
        service_type="hvac", service_area="San Francisco",
        scheduled_start="2026-10-01T10:00:00Z",
        scheduled_end="2026-10-01T11:00:00Z",
        address="1 Main Street", full_name="Maya Chen", phone="4155550100",
    )
    assert agent.state.scheduled_start == "2026-10-01T10:00:00+00:00"
    assert agent.state.scheduled_end == "2026-10-01T11:00:00+00:00"


@pytest.mark.parametrize("phrase,window", [
    ("tomorrow morning", ("08:00", "12:00")),
    ("tomorrow afternoon", ("12:00", "17:00")),
    ("tomorrow evening", ("17:00", "21:00")),
    ("tomorrow", None),
])
def test_relative_date_preserves_time_window_constraint(phrase, window):
    parsed = _requested_window_from_text(phrase)
    assert parsed is not None
    requested_date, time_window = parsed
    assert requested_date
    assert time_window == (None if window is None else phrase.split()[-1])


def test_customer_intake_progresses_name_then_phone_then_address():
    agent = make_agent()
    agent.state = WorkflowState(
        service_type="hvac", service_area="sf", requested_date="2026-09-30",
        requested_time_window="morning",
    )
    name = run(agent.record_intake(None, full_name="John Smith"))
    assert '"next_field": "phone number"' in name
    phone = run(agent.record_intake(None, phone="9876543210"))
    assert '"next_field": "full service address"' in phone
    address = run(agent.record_intake(None, address="123 Main Street"))
    assert '"next_field": null' in address


def test_spoken_number_address_survives_intake_and_final_validation(monkeypatch):
    agent = make_agent()
    agent.customer_id = None
    agent.state = WorkflowState()
    address = "One five nine Market Street, San Francisco"

    result = run(agent.record_intake(None, address=address))

    assert '"status": "success"' in result
    assert agent.state.address == address

    async def resolve_customer(*_args, **_kwargs):
        return db_tools.ToolResult("success", {"customer": {"id": "customer-real"}})

    monkeypatch.setattr(db_tools, "resolve_or_create_customer", resolve_customer)
    validation = run(agent._validate_dispatch_prerequisites(
        service_type="hvac", service_area="San Francisco",
        scheduled_start="2026-10-01T13:00:00+00:00",
        scheduled_end="2026-10-01T14:00:00+00:00",
        address=agent.state.address, full_name="Vansh", phone="4155550100",
    ))

    assert validation.ok
    assert agent.state.address == address


def test_missing_address_returns_one_deterministic_validation_failure():
    agent = make_agent()
    result = run(agent._validate_dispatch_prerequisites(
        service_type="hvac", service_area="San Francisco",
        scheduled_start="2026-10-01T13:00:00+00:00",
        scheduled_end="2026-10-01T14:00:00+00:00",
        address="", full_name="Vansh", phone="4155550100",
    ))

    assert result.status == "failure"
    assert result.error == "Please provide your full street address."


def test_flexible_search_passes_canonical_requested_date_and_window(monkeypatch):
    agent = make_agent()
    agent.state = WorkflowState(
        service_type="hvac", service_area="sf", requested_date="2026-09-30",
        requested_time_window="morning", requested_window_start="08:00",
        requested_window_end="12:00",
    )
    captured = {}

    async def search(service_area, service_type, **kwargs):
        captured.update(service_area=service_area, service_type=service_type, **kwargs)
        return db_tools.ToolResult("success", {"slots": [{"slot_id": "morning-slot"}]})

    monkeypatch.setattr(db_tools, "find_next_available_slots", search)
    result = run(agent.find_next_available_slots(None))
    assert '"status": "success"' in result
    assert captured["search_start"].isoformat() == "2026-09-30T00:00:00+00:00"
    assert captured["search_horizon_days"] == 1
    assert captured["preferred_start_time"] == "08:00"
    assert captured["preferred_end_time"] == "12:00"


def test_unavailable_requested_window_returns_same_day_alternatives(monkeypatch):
    agent = make_agent()
    agent.state = WorkflowState(
        service_type="hvac", service_area="sf", requested_date="2026-09-30",
        requested_time_window="morning", requested_window_start="08:00",
        requested_window_end="12:00",
    )
    calls = []

    async def search(_area, _service, **kwargs):
        calls.append(kwargs)
        if kwargs.get("preferred_start_time"):
            return db_tools.ToolResult("unavailable", error="No morning slots")
        return db_tools.ToolResult("success", {"slots": [{
            "slot_id": "afternoon-slot", "start": "2026-09-30T13:00:00+00:00",
            "end": "2026-09-30T14:00:00+00:00", "technician_id": "tech-1",
        }]})

    monkeypatch.setattr(db_tools, "find_next_available_slots", search)
    result = run(agent.find_next_available_slots(None))
    assert '"status": "unavailable"' in result
    assert agent.state.candidate_slots[0]["slot_id"] == "afternoon-slot"
    assert len(calls) == 2


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("", "2026-10-01T11:00:00Z", "requested date and time window"),
        ("2026-10-01T10:00:00Z", "", "requested date and time window"),
        ("2026-10-01T11:00:00Z", "2026-10-01T10:00:00Z", "valid requested time window"),
    ],
)
def test_dispatch_validation_rejects_incomplete_or_invalid_time_window(start, end, expected):
    agent = make_agent()
    agent.state = WorkflowState()
    result = run(agent._validate_dispatch_prerequisites(
        service_type="hvac", service_area="San Francisco", scheduled_start=start,
        scheduled_end=end, address="1 Main Street", full_name="Maya Chen", phone="4155550100",
    ))
    assert result.status == "failure"
    assert expected in (result.error or "")


def test_reschedule_not_found_is_not_confirmed(monkeypatch):
    agent = make_agent()
    monkeypatch.setattr(SchedulingAgent, "_get_customer_context", context_success)
    monkeypatch.setattr(
        db_tools,
        "reschedule_appointment",
        lambda *args, **kwargs: _async_result(db_tools.ToolResult("not_found", error="missing")),
    )
    result = run(agent._reschedule_appointment(
        "missing", "2026-10-01T10:00:00+00:00", "2026-10-01T11:00:00+00:00"
    ))
    assert result.status == "not_found"


def _async_result(result):
    async def inner():
        return result
    return inner()

