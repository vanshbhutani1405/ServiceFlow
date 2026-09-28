from datetime import datetime, timezone

from agent.matching.technician_matcher import JobRequirements, TechnicianCandidate, match_technician


def requirements() -> JobRequirements:
    start = datetime(2026, 10, 1, 14, tzinfo=timezone.utc)
    return JobRequirements("hvac", "north", start, start.replace(hour=16))


def technician(identifier: str, **kwargs) -> TechnicianCandidate:
    return TechnicianCandidate(identifier, identifier, "active", ("hvac",), ("north",), **kwargs)


def test_hard_constraints_exclude_ineligible_technicians():
    decision = match_technician(requirements(), [
        technician("wrong-skill", skills=("plumbing",)),
        technician("wrong-area", service_areas=("south",)),
        technician("inactive", status="inactive"),
        technician("available"),
    ])
    assert decision.status == "success"
    assert decision.technician.id == "available"


def test_multiple_matches_are_deterministic_and_explainable():
    decision = match_technician(requirements(), [
        technician("b", workload=1),
        technician("a", workload=1),
    ])
    assert decision.technician.id == "a"
    assert "skill match: hvac" in decision.explanation


def test_distance_then_eta_then_workload_are_tiebreakers():
    decision = match_technician(requirements(), [
        technician("far", distance_minutes=20, eta_minutes=10),
        technician("near", distance_minutes=5, eta_minutes=30, workload=4),
    ])
    assert decision.technician.id == "near"


def test_no_eligible_technician_is_unavailable():
    decision = match_technician(requirements(), [technician("busy", available=False)])
    assert decision.status == "unavailable"
    assert decision.technician is None
