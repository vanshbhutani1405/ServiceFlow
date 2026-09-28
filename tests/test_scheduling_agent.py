from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("livekit.agents")

from agent.agents.scheduling_agent import SchedulingAgent
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

