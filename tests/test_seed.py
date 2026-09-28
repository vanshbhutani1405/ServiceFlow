from db.seed import APPOINTMENTS, TECHNICIANS


def test_seed_contains_realistic_flexible_availability_fixture_shape():
    assert len(TECHNICIANS) >= 4
    assert len({technician["id"] for technician in TECHNICIANS}) == len(TECHNICIANS)
    assert len(APPOINTMENTS) >= 4
    assert {appointment["status"] for appointment in APPOINTMENTS} >= {
        "requested", "confirmed", "rescheduled", "dispatched",
    }


def test_seed_technicians_cover_multiple_service_areas_and_capabilities():
    areas = {area for technician in TECHNICIANS for area in technician["service_areas"]}
    skills = {skill for technician in TECHNICIANS for skill in technician["skills"]}
    assert {"san_francisco", "oakland", "san_jose"} <= areas
    assert {"hvac", "plumbing", "electrical"} <= skills
