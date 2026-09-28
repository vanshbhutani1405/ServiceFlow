"""Lightweight deterministic routing for scheduling requests."""

from __future__ import annotations

import re
from enum import Enum


class SchedulingIntent(str, Enum):
    BOOK = "BOOK"
    RESCHEDULE = "RESCHEDULE"
    CANCEL = "CANCEL"
    GENERAL_SCHEDULING = "GENERAL_SCHEDULING"
    DISPATCH = "DISPATCH"
    UNKNOWN = "UNKNOWN"


def is_flexible_availability_request(text: str) -> bool:
    normalized = text.casefold()
    return bool(re.search(
        r"\b(any day|any date|anytime|whenever|next available|what dates? are available|"
        r"what days? are available|what times? do you have|just find me the earliest|"
        r"i(?:'m| am) flexible|get me someone whenever)\b",
        normalized,
    ))


def route_request(text: str) -> SchedulingIntent:
    """Classify a turn without adding another LLM call."""
    normalized = text.casefold()
    if re.search(r"\b(cancel|call off|no longer need)\b", normalized):
        return SchedulingIntent.CANCEL
    if re.search(r"\b(dispatch|assign|send a technician|send someone out)\b", normalized):
        return SchedulingIntent.DISPATCH
    if re.search(r"\b(reschedule|re[- ]?schedule|move|change|different time|different day)\b", normalized):
        return SchedulingIntent.RESCHEDULE
    if is_flexible_availability_request(text) or re.search(r"\b(availability|available|opening|openings|appointment time|when can)\b", normalized):
        return SchedulingIntent.GENERAL_SCHEDULING
    if re.search(r"\b(book|schedule|appointment|come out|send someone|need someone)\b", normalized):
        return SchedulingIntent.BOOK
    return SchedulingIntent.UNKNOWN

