"""Insert deterministic MVP fixtures into Supabase."""

from __future__ import annotations

from datetime import datetime, timezone

from db.supabase_client import get_supabase_client

CUSTOMERS = [
    {"id": "00000000-0000-0000-0000-000000000101", "full_name": "Maya Patel", "phone": "+14155550101", "email": "maya@example.com", "address": "12 Market St, San Francisco", "service_area": "san_francisco"},
    {"id": "00000000-0000-0000-0000-000000000102", "full_name": "Ethan Brooks", "phone": "+14155550102", "email": "ethan@example.com", "address": "44 Oak Ave, Oakland", "service_area": "oakland"},
    {"id": "00000000-0000-0000-0000-000000000103", "full_name": "Sofia Nguyen", "phone": "+14155550103", "email": "sofia@example.com", "address": "8 Cedar Rd, San Jose", "service_area": "san_jose"},
]

TECHNICIANS = [
    {"id": "00000000-0000-0000-0000-000000000201", "full_name": "Jordan Lee", "phone": "+14155550201", "skills": ["hvac", "plumbing"], "service_areas": ["san_francisco", "oakland"], "status": "active"},
    {"id": "00000000-0000-0000-0000-000000000202", "full_name": "Riley Morgan", "phone": "+14155550202", "skills": ["electrical", "hvac"], "service_areas": ["san_jose", "san_francisco"], "status": "active"},
    {"id": "00000000-0000-0000-0000-000000000203", "full_name": "Casey Rivera", "phone": "+14155550203", "skills": ["plumbing"], "service_areas": ["oakland"], "status": "active"},
    {"id": "00000000-0000-0000-0000-000000000204", "full_name": "Avery Kim", "phone": "+14155550204", "skills": ["hvac"], "service_areas": ["san_francisco"], "status": "active"},
    {"id": "00000000-0000-0000-0000-000000000205", "full_name": "Morgan Diaz", "phone": "+14155550205", "skills": ["hvac", "electrical"], "service_areas": ["san_francisco", "san_jose"], "status": "active"},
    {"id": "00000000-0000-0000-0000-000000000206", "full_name": "Taylor Singh", "phone": "+14155550206", "skills": ["plumbing", "hvac"], "service_areas": ["oakland", "san_francisco"], "status": "active"},
]

JOBS = [
    {"id": "00000000-0000-0000-0000-000000000301", "customer_id": CUSTOMERS[0]["id"], "service_type": "hvac", "description": "Air conditioner is not cooling", "address": CUSTOMERS[0]["address"], "priority": "normal", "status": "open"},
    {"id": "00000000-0000-0000-0000-000000000302", "customer_id": CUSTOMERS[1]["id"], "service_type": "plumbing", "description": "Kitchen sink has a slow leak", "address": CUSTOMERS[1]["address"], "priority": "normal", "status": "scheduled"},
    {"id": "00000000-0000-0000-0000-000000000303", "customer_id": CUSTOMERS[2]["id"], "service_type": "electrical", "description": "Outlet intermittently loses power", "address": CUSTOMERS[2]["address"], "priority": "high", "status": "open"},
]

APPOINTMENTS = [
    {"id": "00000000-0000-0000-0000-000000000501", "customer_id": CUSTOMERS[1]["id"], "job_id": JOBS[1]["id"], "technician_id": TECHNICIANS[2]["id"], "scheduled_start": "2026-10-02T10:00:00+00:00", "scheduled_end": "2026-10-02T11:00:00+00:00", "status": "confirmed", "notes": "Customer prefers a call before arrival."},
    {"id": "00000000-0000-0000-0000-000000000502", "customer_id": CUSTOMERS[0]["id"], "job_id": JOBS[0]["id"], "technician_id": TECHNICIANS[0]["id"], "scheduled_start": "2026-09-30T10:00:00+00:00", "scheduled_end": "2026-09-30T11:00:00+00:00", "status": "requested", "notes": "Seeded conflict."},
    {"id": "00000000-0000-0000-0000-000000000503", "customer_id": CUSTOMERS[0]["id"], "job_id": JOBS[0]["id"], "technician_id": TECHNICIANS[1]["id"], "scheduled_start": "2026-10-01T14:00:00+00:00", "scheduled_end": "2026-10-01T15:00:00+00:00", "status": "rescheduled", "notes": "Seeded conflict."},
    {"id": "00000000-0000-0000-0000-000000000504", "customer_id": CUSTOMERS[2]["id"], "job_id": JOBS[2]["id"], "technician_id": TECHNICIANS[1]["id"], "scheduled_start": "2026-10-03T09:00:00+00:00", "scheduled_end": "2026-10-03T10:00:00+00:00", "status": "dispatched", "notes": "Seeded conflict."},
]


def seed() -> None:
    client = get_supabase_client()
    client.from_("customers").upsert(CUSTOMERS, on_conflict="id").execute()
    client.from_("technicians").upsert(TECHNICIANS, on_conflict="id").execute()

    schedules = [
        (0, "2026-09-30", "09:00", "13:00"), (0, "2026-10-01", "09:00", "17:00"),
        (0, "2026-10-04", "10:00", "16:00"), (1, "2026-09-30", "13:00", "17:00"),
        (1, "2026-10-01", "10:00", "18:00"), (1, "2026-10-05", "09:00", "15:00"),
        (2, "2026-10-02", "08:00", "16:00"), (2, "2026-10-06", "10:00", "14:00"),
        (3, "2026-10-02", "12:00", "18:00"), (3, "2026-10-07", "09:00", "17:00"),
        (4, "2026-10-03", "09:00", "13:00"), (4, "2026-10-06", "13:00", "18:00"),
        (5, "2026-09-30", "11:00", "17:00"), (5, "2026-10-04", "09:00", "12:00"),
    ]
    availability = [
        {"id": f"00000000-0000-0000-0000-{401 + index:012d}", "technician_id": TECHNICIANS[tech]["id"], "available_start": f"{day}T{start}:00+00:00", "available_end": f"{day}T{end}:00+00:00", "status": "available"}
        for index, (tech, day, start, end) in enumerate(schedules)
    ]
    client.from_("technician_availability").upsert(availability, on_conflict="id").execute()
    client.from_("jobs").upsert(JOBS, on_conflict="id").execute()

    client.from_("appointments").upsert(APPOINTMENTS, on_conflict="id").execute()
    client.from_("appointment_events").upsert([
        {"id": f"00000000-0000-0000-0000-{601 + index:012d}", "appointment_id": appointment["id"], "event_type": "seeded", "metadata": {}}
        for index, appointment in enumerate(APPOINTMENTS)
    ], on_conflict="id").execute()
    print("Seeded ServiceFlow MVP fixtures.")


if __name__ == "__main__":
    seed()

