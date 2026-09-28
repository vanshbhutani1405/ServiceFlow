from agent.routing import SchedulingIntent, route_request
from agent.routing import is_flexible_availability_request
from agent.agents.scheduling_agent import SchedulingAgent
from agent.state import WorkflowState
from types import SimpleNamespace
import asyncio


def test_routes_scheduling_intents_without_an_llm():
    assert route_request("I need someone tomorrow at 2") == SchedulingIntent.BOOK
    assert route_request("move my appointment to Friday") == SchedulingIntent.RESCHEDULE
    assert route_request("please cancel my appointment") == SchedulingIntent.CANCEL
    assert route_request("what appointment times are available?") == SchedulingIntent.GENERAL_SCHEDULING
    assert route_request("the technician was very helpful") == SchedulingIntent.UNKNOWN


def test_unknown_intake_answer_does_not_reset_active_booking_workflow():
    agent = SchedulingAgent.__new__(SchedulingAgent)
    agent.state = WorkflowState(active_workflow=SchedulingIntent.BOOK.value)
    agent._safety_escalated = False

    asyncio.run(agent.on_user_turn_completed(None, SimpleNamespace(text_content="San Francisco")))

    assert agent.state.active_workflow == SchedulingIntent.BOOK.value


def test_flexible_availability_phrases_are_detected_deterministically():
    assert is_flexible_availability_request("Any day is fine")
    assert is_flexible_availability_request("What's the next available?")
    assert route_request("What dates are available?") == SchedulingIntent.GENERAL_SCHEDULING

