"""Lightweight deterministic routing for scheduling requests."""

from __future__ import annotations

import re
from enum import Enum


class SchedulingIntent(str, Enum):
    BOOK = "BOOK"
    RESCHEDULE = "RESCHEDULE"
    CANCEL = "CANCEL"
    GENERAL_SCHEDULING = "GENERAL_SCHEDULING"
    UNKNOWN = "UNKNOWN"


def route_request(text: str) -> SchedulingIntent:
    """Classify a turn without adding another LLM call."""
    normalized = text.casefold()
    if re.search(r"\b(cancel|call off|no longer need)\b", normalized):
        return SchedulingIntent.CANCEL
    if re.search(r"\b(reschedule|re[- ]?schedule|move|change|different time|different day)\b", normalized):
        return SchedulingIntent.RESCHEDULE
    if re.search(r"\b(availability|available|opening|openings|appointment time|when can)\b", normalized):
        return SchedulingIntent.GENERAL_SCHEDULING
    if re.search(r"\b(book|schedule|appointment|come out|send someone|need someone)\b", normalized):
        return SchedulingIntent.BOOK
    return SchedulingIntent.UNKNOWN

