"""Pure, explainable technician eligibility and selection rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import inf
from typing import Literal


@dataclass(frozen=True)
class JobRequirements:
    service_type: str
    service_area: str
    scheduled_start: datetime
    scheduled_end: datetime


@dataclass(frozen=True)
class TechnicianCandidate:
    id: str
    full_name: str
    status: str
    skills: tuple[str, ...] = ()
    service_areas: tuple[str, ...] = ()
    available: bool = True
    workload: int = 0
    distance_minutes: float | None = None
    eta_minutes: float | None = None


@dataclass(frozen=True)
class MatchDecision:
    status: Literal["success", "unavailable", "failure"]
    technician: TechnicianCandidate | None = None
    eligible_candidates: tuple[TechnicianCandidate, ...] = ()
    explanation: tuple[str, ...] = field(default_factory=tuple)
    error: str | None = None


def _contains(values: tuple[str, ...], expected: str) -> bool:
    expected = expected.casefold().strip()
    return any(value.casefold().strip() == expected for value in values)


def _eligible(technician: TechnicianCandidate, requirements: JobRequirements) -> bool:
    return (
        technician.status.casefold() == "active"
        and _contains(technician.skills, requirements.service_type)
        and _contains(technician.service_areas, requirements.service_area)
        and technician.available
    )


def _sort_key(technician: TechnicianCandidate) -> tuple[float, float, int, str, str]:
    return (
        technician.distance_minutes if technician.distance_minutes is not None else inf,
        technician.eta_minutes if technician.eta_minutes is not None else inf,
        technician.workload,
        technician.full_name.casefold(),
        technician.id,
    )


def match_technician(
    requirements: JobRequirements,
    candidates: tuple[TechnicianCandidate, ...] | list[TechnicianCandidate],
) -> MatchDecision:
    """Apply hard constraints, then choose the stable best candidate."""
    if requirements.scheduled_end <= requirements.scheduled_start:
        return MatchDecision("failure", error="scheduled_end must be after scheduled_start")
    eligible = tuple(sorted((c for c in candidates if _eligible(c, requirements)), key=_sort_key))
    if not eligible:
        return MatchDecision("unavailable", error="No eligible technician is available")
    selected = eligible[0]
    reasons = [
        "active status",
        f"skill match: {requirements.service_type}",
        f"service area match: {requirements.service_area}",
        "available for requested window",
        f"workload: {selected.workload}",
    ]
    if selected.distance_minutes is not None:
        reasons.append(f"distance/drive minutes: {selected.distance_minutes:g}")
    if selected.eta_minutes is not None:
        reasons.append(f"ETA minutes: {selected.eta_minutes:g}")
    return MatchDecision("success", selected, eligible, tuple(reasons))
