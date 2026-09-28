"""Small policy-driven classifier that runs before scheduling logic."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class SafetyStatus(str, Enum):
    SAFE_NORMAL = "SAFE_NORMAL"
    EMERGENCY_ESCALATION = "EMERGENCY_ESCALATION"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class SafetyDecision:
    status: SafetyStatus
    reason: str | None = None


_EMERGENCY_PATTERNS = (
    (r"\b(smell|smells|smelling|odor|odour)\b.{0,30}\b(gas|propane|natural gas)\b", "suspected gas leak"),
    (r"\b(gas|propane|natural gas)\b.{0,30}\b(leak|leaking)\b", "suspected gas leak"),
    (r"\b(fire|flames?|smoke|burning)\b", "fire or smoke reported"),
    (r"\b(sparking|sparks|electrical fire|live wire|exposed wire|electric shock)\b", "active electrical danger"),
    (r"\b(in immediate danger|someone is in danger|life[- ]?threatening|person is hurt|people are hurt)\b", "immediate danger to people"),
)


def classify_request(text: str) -> SafetyDecision:
    """Return the policy state; the LLM is never the safety authority."""
    normalized = " ".join(text.casefold().split())
    if not normalized:
        return SafetyDecision(SafetyStatus.UNCERTAIN, "empty request")
    for pattern, reason in _EMERGENCY_PATTERNS:
        if re.search(pattern, normalized):
            return SafetyDecision(SafetyStatus.EMERGENCY_ESCALATION, reason)
    return SafetyDecision(SafetyStatus.SAFE_NORMAL)


SAFETY_RESPONSE = (
    "This may be an emergency. Please move to a safe location and contact local emergency services now. "
    "I’m escalating this to our on-call dispatcher."
)
