import asyncio
import json

import pytest

pytest.importorskip("livekit.agents")

from agent.agents.dispatch_agent import DispatchAgent
from agent.agents.scheduling_agent import SchedulingAgent
from db import tools as db_tools
from livekit.agents.llm import ChatContext
from livekit.agents.llm import ToolError
from agent.state import WorkflowState


JOB_ID = "00000000-0000-0000-0000-000000000123"


def run(coro):
    return asyncio.run(coro)


def make_agent():
    agent = SchedulingAgent.__new__(SchedulingAgent)
    agent.customer_id = "customer-1"
    agent.db_client = object()
    agent._chat_ctx = ChatContext.empty()
    agent.state = WorkflowState(customer_id="customer-1")
    agent._safety_escalated = False
    agent._last_job_id = None
    return agent


class ToolContext:
    def __init__(self, state):
        self.userdata = state

    def disallow_interruptions(self):
        pass


def context_with_job():
    async def context(_self):
        return db_tools.ToolResult("success", {
            "customer": {"full_name": "Maya Chen", "phone": "4155550100"},
            "jobs": [{"id": JOB_ID}], "appointments": [],
        })
    return context


def test_invalid_job_id_is_rejected_before_dispatch_handoff(monkeypatch):
    agent = make_agent()
    monkeypatch.setattr(SchedulingAgent, "_get_customer_context", context_with_job())
    with pytest.raises(ToolError):
        run(agent.transfer_to_dispatch(
            None, "job_alan_1234567", "2026-10-01T10:00:00+00:00", "2026-10-01T11:00:00+00:00"
        ))


def test_missing_job_id_is_rejected_without_a_canonical_job(monkeypatch):
    agent = make_agent()
    monkeypatch.setattr(SchedulingAgent, "_get_customer_context", context_with_job())
    with pytest.raises(ToolError):
        run(agent.transfer_to_dispatch(
            None, "", "2026-10-01T10:00:00+00:00", "2026-10-01T11:00:00+00:00"
        ))


def test_record_intake_asks_one_next_question_and_skips_supplied_fields():
    agent = make_agent()
    result = json.loads(run(agent.record_intake(
        None, service_type="hvac", service_area="San Francisco",
        scheduled_start="2026-10-01T13:00:00+00:00",
    )))
    assert result["next_field"] == "requested time window"
    assert "time window" in result["question"]


def test_record_intake_full_name_updates_canonical_userdata():
    state = WorkflowState()
    agent = make_agent()
    result = json.loads(run(agent.record_intake(ToolContext(state), full_name="Vansh")))
    assert result["status"] == "success"
    assert state.full_name == "Vansh"
    assert agent.state is state


def test_record_intake_phone_updates_canonical_userdata():
    state = WorkflowState()
    agent = make_agent()
    result = json.loads(run(agent.record_intake(ToolContext(state), phone="+91 (7888) 451-236")))
    assert result["status"] == "success"
    assert state.phone == "+917888451236"


@pytest.mark.parametrize("full_name, phone", [("", "4155550100"), ("Maya Chen", ""), ("", "")])
def test_new_request_missing_customer_identity_cannot_dispatch(monkeypatch, full_name, phone):
    agent = make_agent()
    agent.customer_id = None
    monkeypatch.setattr(SchedulingAgent, "_get_customer_context", _empty_context())
    resolve_called = False

    async def unexpected_resolution(*_args, **_kwargs):
        nonlocal resolve_called
        resolve_called = True
        return db_tools.ToolResult("success", {"customer": {"id": "customer-real"}})

    monkeypatch.setattr(db_tools, "resolve_or_create_customer", unexpected_resolution)
    with pytest.raises(ToolError):
        run(agent.transfer_to_dispatch(
            None, "new_job", "2026-10-01T13:00:00+00:00", "2026-10-01T16:00:00+00:00",
            service_type="hvac", service_area="San Francisco", address="1 Main St",
            description="AC repair", full_name=full_name, phone=phone,
        ))
    assert not resolve_called


def test_successful_scheduling_dispatch_handoff_uses_database_job_id(monkeypatch):
    agent = make_agent()
    monkeypatch.setattr(SchedulingAgent, "_get_customer_context", context_with_job())
    async def available(*_args, **_kwargs):
        return db_tools.ToolResult("success", {"candidates": [{"id": "tech-1"}]})

    async def booked(*_args, **_kwargs):
        return db_tools.ToolResult("success", {"id": "appointment-1", "job_id": JOB_ID})

    async def confirmed(*_args, **_kwargs):
        return db_tools.ToolResult("success", {"id": "event-1"})

    monkeypatch.setattr(db_tools, "find_best_technician", available)
    monkeypatch.setattr(db_tools, "book_appointment", booked)
    monkeypatch.setattr(db_tools, "send_confirmation", confirmed)
    booking = run(agent._book_appointment(
        service_type="hvac", service_area="north", job_id=JOB_ID,
        scheduled_start="2026-10-01T10:00:00+00:00",
        scheduled_end="2026-10-01T11:00:00+00:00",
    ))
    assert booking.ok
    captured = {}
    async def dispatched(job_id, **kwargs):
        captured["job_id"] = job_id
        captured.update(kwargs)
        return db_tools.ToolResult("success", {"job": {"id": JOB_ID}, "technician": {"id": "tech-1"}})

    monkeypatch.setattr(db_tools, "dispatch_job", dispatched)
    result = run(agent.transfer_to_dispatch(
        None, "00000000-0000-0000-0000-000000000000",
        "2026-10-01T10:00:00+00:00", "2026-10-01T11:00:00+00:00",
        service_type="hvac", service_area="north", address="1 Main St",
    ))
    assert json.loads(result)["status"] == "success"
    assert captured["customer_id"] == "customer-1"
    assert captured["job_id"] == JOB_ID


def test_new_request_creates_job_before_dispatch(monkeypatch):
    agent = make_agent()
    agent.customer_id = None
    monkeypatch.setattr(SchedulingAgent, "_get_customer_context", _empty_context())
    calls = []

    async def create_job(*_args, **_kwargs):
        calls.append("create_job")
        return db_tools.ToolResult("success", {"id": JOB_ID})

    async def resolve_customer(*_args, **_kwargs):
        calls.append("resolve_customer")
        return db_tools.ToolResult("success", {"customer": {"id": "customer-real"}, "created": True})

    async def dispatched(*_args, **_kwargs):
        calls.append("dispatch_job")
        return db_tools.ToolResult("success", {"job": {"id": JOB_ID}})

    monkeypatch.setattr(db_tools, "create_job", create_job)
    monkeypatch.setattr(db_tools, "resolve_or_create_customer", resolve_customer)
    monkeypatch.setattr(db_tools, "dispatch_job", dispatched)
    result = run(agent.transfer_to_dispatch(
        None, "", "2026-10-01T13:00:00+00:00", "2026-10-01T16:00:00+00:00",
        service_type="hvac", service_area="San Francisco", address="1 Main St",
        description="AC repair", full_name="Maya Chen", phone="4155550100",
    ))
    assert json.loads(result)["status"] == "success"
    assert calls == ["resolve_customer", "create_job", "dispatch_job"]
    assert agent.customer_id == "customer-real"
    assert agent._last_job_id == JOB_ID


def test_transfer_syncs_tool_name_and_completes_canonical_workflow(monkeypatch):
    agent = make_agent()
    agent.customer_id = None
    state = WorkflowState()
    agent.state = state
    monkeypatch.setattr(SchedulingAgent, "_get_customer_context", _empty_context())
    calls = []

    async def resolve_customer(*_args, **_kwargs):
        calls.append("customer")
        return db_tools.ToolResult("success", {"customer": {"id": "customer-real"}, "created": True})

    async def create_job(*_args, **_kwargs):
        calls.append("job")
        return db_tools.ToolResult("success", {"id": JOB_ID, "customer_id": "customer-real"})

    async def dispatch(job_id, **kwargs):
        calls.append(("dispatch", job_id))
        assert job_id == JOB_ID
        assert kwargs["customer_id"] == "customer-real"
        return db_tools.ToolResult("success", {"job": {"id": JOB_ID}, "technician": {"id": "tech-1"}})

    monkeypatch.setattr(db_tools, "resolve_or_create_customer", resolve_customer)
    monkeypatch.setattr(db_tools, "create_job", create_job)
    monkeypatch.setattr(db_tools, "dispatch_job", dispatch)
    result = json.loads(run(agent.transfer_to_dispatch(
        ToolContext(state), "new_job", "2026-10-01T13:00:00+00:00", "2026-10-01T16:00:00+00:00",
        service_type="hvac", service_area="San Francisco", address="1 Main St",
        description="AC repair", full_name="Vansh", phone="7888459861235689",
    )))
    assert result["status"] == "success"
    assert state.full_name == "Vansh"
    assert state.customer_id == "customer-real"
    assert state.job_id == JOB_ID
    assert state.dispatch_status == "success"
    assert calls == ["customer", "job", ("dispatch", JOB_ID)]


def test_select_available_slot_accepts_only_backend_candidate(monkeypatch):
    agent = make_agent()
    state = WorkflowState(
        customer_id="customer-1", full_name="Maya Chen", phone="4155550100",
        service_type="hvac", service_area="sf", address="1 Main Street",
        description="AC repair",
        job_id=JOB_ID,
        candidate_slots=[{
            "slot_id": "tech-1:2026-10-01T10:00:00+00:00",
            "start": "2026-10-01T10:00:00+00:00",
            "end": "2026-10-01T11:00:00+00:00",
            "technician_id": "tech-1",
        }],
    )
    agent.state = state
    async def context(_self):
        return db_tools.ToolResult("success", {
            "customer": {"id": "customer-1", "full_name": "Maya Chen", "phone": "4155550100"},
            "jobs": [{"id": JOB_ID, "customer_id": "customer-1"}], "appointments": [],
        })
    async def get_job(_job_id, **_kwargs):
        return db_tools.ToolResult("success", {"id": JOB_ID, "customer_id": "customer-1"})
    async def available(*_args, **_kwargs):
        return db_tools.ToolResult("success", {"available": True})
    async def matching(*_args, **_kwargs):
        return db_tools.ToolResult("success", {"technician": {"id": "tech-1"}})
    async def booked(*_args, **_kwargs):
        return db_tools.ToolResult("success", {"id": "appointment-1", "job_id": JOB_ID})
    async def dispatched(*_args, **_kwargs):
        return db_tools.ToolResult("success", {"job": {"id": JOB_ID, "technician_id": "tech-1"}})
    monkeypatch.setattr(SchedulingAgent, "_get_customer_context", context)
    monkeypatch.setattr(db_tools, "get_job", get_job)
    monkeypatch.setattr(db_tools, "check_technician_availability", available)
    monkeypatch.setattr(db_tools, "match_technician_for_job", matching)
    monkeypatch.setattr(db_tools, "book_appointment", booked)
    monkeypatch.setattr(db_tools, "dispatch_job", dispatched)
    selected = json.loads(run(agent.select_available_slot(
        ToolContext(state), slot_id="tech-1:2026-10-01T10:00:00+00:00", technician_id="stale-tech"
    )))
    assert selected["status"] == "success"
    assert state.selected_technician_id == "tech-1"
    assert state.scheduled_start.endswith("10:00:00+00:00")
    with pytest.raises(ToolError):
        run(agent.select_available_slot(ToolContext(state), slot_id="invented"))


def test_selected_flexible_slot_flows_into_dispatch(monkeypatch):
    agent = make_agent()
    state = WorkflowState(
        service_type="hvac", service_area="sf", address="1 Main Street",
        full_name="Maya Chen", phone="4155550100",
        scheduled_start="2026-10-01T10:00:00+00:00",
        scheduled_end="2026-10-01T11:00:00+00:00",
        selected_technician_id="tech-1",
        job_id=JOB_ID,
    )
    agent.state = state
    captured = {}

    async def context(_self):
        return db_tools.ToolResult("success", {
            "customer": {"id": "customer-1", "full_name": "Maya Chen", "phone": "4155550100"},
            "jobs": [{"id": JOB_ID, "customer_id": "customer-1"}], "appointments": [],
        })

    async def get_job(_job_id, **_kwargs):
        return db_tools.ToolResult("success", {"id": JOB_ID, "customer_id": "customer-1"})

    async def dispatch(job_id, **kwargs):
        captured.update(job_id=job_id, **kwargs)
        return db_tools.ToolResult("success", {"job": {"id": JOB_ID, "technician_id": "tech-1"}})

    monkeypatch.setattr(SchedulingAgent, "_get_customer_context", context)
    monkeypatch.setattr(db_tools, "get_job", get_job)
    monkeypatch.setattr(db_tools, "dispatch_job", dispatch)
    async def matching(*_args, **_kwargs):
        return db_tools.ToolResult("success", {"technician": {"id": "tech-1"}})
    monkeypatch.setattr(db_tools, "match_technician_for_job", matching)
    result = json.loads(run(agent.transfer_to_dispatch(ToolContext(state), JOB_ID)))
    assert result["status"] == "success"
    assert captured["job_id"] == JOB_ID
    assert captured["technician_id"] == "tech-1"


def test_selected_slot_recheck_rejects_slot_that_becomes_unavailable(monkeypatch):
    agent = make_agent()
    state = WorkflowState(
        customer_id="customer-1", full_name="Maya Chen", phone="4155550100",
        service_type="hvac", service_area="sf", address="1 Main Street",
        description="AC repair", job_id=JOB_ID,
        candidate_slots=[{
            "slot_id": "tech-1:2026-10-01T10:00:00+00:00",
            "start": "2026-10-01T10:00:00+00:00", "end": "2026-10-01T11:00:00+00:00",
            "technician_id": "tech-1",
        }],
    )
    agent.state = state
    async def context(_self):
        return db_tools.ToolResult("success", {
            "customer": {"full_name": "Maya Chen", "phone": "4155550100"},
            "jobs": [{"id": JOB_ID}], "appointments": [],
        })
    async def get_job(_job_id, **_kwargs):
        return db_tools.ToolResult("success", {"id": JOB_ID, "customer_id": "customer-1"})
    async def unavailable(*_args, **_kwargs):
        return db_tools.ToolResult("unavailable", error="Slot is no longer available")
    monkeypatch.setattr(SchedulingAgent, "_get_customer_context", context)
    monkeypatch.setattr(db_tools, "get_job", get_job)
    monkeypatch.setattr(db_tools, "check_technician_availability", unavailable)
    with pytest.raises(ToolError, match="Slot is no longer available"):
        run(agent.select_available_slot(ToolContext(state), slot_id=state.candidate_slots[0]["slot_id"]))
    assert state.booking_status == "failed"


def test_job_creation_failure_prevents_dispatch(monkeypatch):
    agent = make_agent()
    agent.customer_id = None
    monkeypatch.setattr(SchedulingAgent, "_get_customer_context", _empty_context())
    dispatch_called = False

    async def create_job(*_args, **_kwargs):
        return db_tools.ToolResult("failure", error="database unavailable")

    async def resolve_customer(*_args, **_kwargs):
        return db_tools.ToolResult("success", {"customer": {"id": "customer-real"}, "created": True})

    async def dispatch(*_args, **_kwargs):
        nonlocal dispatch_called
        dispatch_called = True
        return db_tools.ToolResult("success")

    monkeypatch.setattr(db_tools, "create_job", create_job)
    monkeypatch.setattr(db_tools, "resolve_or_create_customer", resolve_customer)
    monkeypatch.setattr(db_tools, "dispatch_job", dispatch)
    with pytest.raises(ToolError):
        run(agent.transfer_to_dispatch(
            None, "", "2026-10-01T13:00:00+00:00", "2026-10-01T16:00:00+00:00",
            service_type="hvac", service_area="San Francisco", address="1 Main St", description="AC repair",
            full_name="Maya Chen", phone="4155550100",
        ))
    assert not dispatch_called


def test_customer_resolution_failure_prevents_job_creation(monkeypatch):
    agent = make_agent()
    agent.customer_id = None
    monkeypatch.setattr(SchedulingAgent, "_get_customer_context", _empty_context())
    create_called = False

    async def resolve_customer(*_args, **_kwargs):
        return db_tools.ToolResult("failure", error="customer database unavailable")

    async def create_job(*_args, **_kwargs):
        nonlocal create_called
        create_called = True
        return db_tools.ToolResult("success", {"id": "should-not-exist"})

    monkeypatch.setattr(db_tools, "resolve_or_create_customer", resolve_customer)
    monkeypatch.setattr(db_tools, "create_job", create_job)
    with pytest.raises(ToolError, match="customer database unavailable"):
        run(agent.transfer_to_dispatch(
            None, "", "2026-10-01T13:00:00+00:00", "2026-10-01T16:00:00+00:00",
            service_type="hvac", service_area="San Francisco", address="1 Main St",
            full_name="Maya Chen", phone="4155550100",
        ))
    assert not create_called


def test_no_technician_available_returns_truthful_failure(monkeypatch):
    agent = DispatchAgent.__new__(DispatchAgent)
    agent.job_id = JOB_ID
    agent.customer_id = "customer-1"
    agent.db_client = object()
    agent.scheduled_start = "2026-10-01T13:00:00+00:00"
    agent.scheduled_end = "2026-10-01T16:00:00+00:00"
    agent.safety_escalated = False

    async def unavailable(*_args, **_kwargs):
        return db_tools.ToolResult("unavailable", error="No technician currently available")

    monkeypatch.setattr(db_tools, "dispatch_job", unavailable)
    with pytest.raises(ToolError, match="No technician currently available"):
        run(agent.dispatch_job(None))


def _empty_context():
    async def context(_self):
        return db_tools.ToolResult("success", {"customer": {}, "jobs": [], "appointments": []})
    return context


