import pytest

from agent.safety.classifier import SafetyStatus, classify_request


@pytest.mark.parametrize("text", [
    "I smell gas in the house",
    "There is a fire in the kitchen",
    "I see sparks from the panel",
    "Someone is in immediate danger",
])
def test_emergency_requests_escalate(text):
    assert classify_request(text).status == SafetyStatus.EMERGENCY_ESCALATION


@pytest.mark.parametrize("text", [
    "I need to book an appointment",
    "Move my appointment to Friday",
    "Cancel my appointment",
    "What times are available?",
])
def test_normal_scheduling_requests_remain_normal(text):
    assert classify_request(text).status == SafetyStatus.SAFE_NORMAL


def test_empty_request_is_uncertain():
    assert classify_request(" ").status == SafetyStatus.UNCERTAIN
