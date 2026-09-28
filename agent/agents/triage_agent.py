"""Dedicated safety/triage policy surface for the voice workflow."""

from __future__ import annotations

from livekit.agents import Agent

from agent.safety.classifier import SAFETY_RESPONSE, SafetyDecision, classify_request


class TriageAgent(Agent):
    """Deterministic triage facade used before normal scheduling operations."""

    def __init__(self) -> None:
        super().__init__(instructions=(
            "You are ServiceFlow's safety triage agent. Safety classification is deterministic. "
            "Emergency requests must receive the policy response and must not enter scheduling or dispatch."
        ))

    @staticmethod
    def classify(text: str) -> SafetyDecision:
        return classify_request(text)

    @staticmethod
    def emergency_response() -> str:
        return SAFETY_RESPONSE
