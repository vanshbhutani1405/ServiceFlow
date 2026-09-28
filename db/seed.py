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
]

JOBS = [
    {"id": "00000000-0000-0000-0000-000000000301", "customer_id": CUSTOMERS[0]["id"], "service_type": "hvac", "description": "Air conditioner is not cooling", "address": CUSTOMERS[0]["address"], "priority": "normal", "status": "open"},
    {"id": "00000000-0000-0000-0000-000000000302", "customer_id": CUSTOMERS[1]["id"], "service_type": "plumbing", "description": "Kitchen sink has a slow leak", "address": CUSTOMERS[1]["address"], "priority": "normal", "status": "scheduled"},
    {"id": "00000000-0000-0000-0000-000000000303", "customer_id": CUSTOMERS[2]["id"], "service_type": "electrical", "description": "Outlet intermittently loses power", "address": CUSTOMERS[2]["address"], "priority": "high", "status": "open"},
]


def seed() -> None:
    client = get_supabase_client()
    client.from_("customers").upsert(CUSTOMERS, on_conflict="id").execute()
    client.from_("technicians").upsert(TECHNICIANS, on_conflict="id").execute()

    availability = [
        {"id": "00000000-0000-0000-0000-000000000401", "technician_id": TECHNICIANS[0]["id"], "available_start": "2026-10-01T09:00:00+00:00", "available_end": "2026-10-01T17:00:00+00:00", "status": "available"},
        {"id": "00000000-0000-0000-0000-000000000402", "technician_id": TECHNICIANS[1]["id"], "available_start": "2026-10-01T10:00:00+00:00", "available_end": "2026-10-01T18:00:00+00:00", "status": "available"},
        {"id": "00000000-0000-0000-0000-000000000403", "technician_id": TECHNICIANS[2]["id"], "available_start": "2026-10-02T08:00:00+00:00", "available_end": "2026-10-02T16:00:00+00:00", "status": "available"},
    ]
    client.from_("technician_availability").upsert(availability, on_conflict="id").execute()
    client.from_("jobs").upsert(JOBS, on_conflict="id").execute()

    appointments = [
        {"id": "00000000-0000-0000-0000-000000000501", "customer_id": CUSTOMERS[1]["id"], "job_id": JOBS[1]["id"], "technician_id": TECHNICIANS[2]["id"], "scheduled_start": "2026-10-02T10:00:00+00:00", "scheduled_end": "2026-10-02T11:00:00+00:00", "status": "confirmed", "notes": "Customer prefers a call before arrival."},
    ]
    client.from_("appointments").upsert(appointments, on_conflict="id").execute()
    client.from_("appointment_events").upsert({"id": "00000000-0000-0000-0000-000000000601", "appointment_id": appointments[0]["id"], "event_type": "seeded", "metadata": {}}, on_conflict="id").execute()
    print("Seeded ServiceFlow MVP fixtures.")


if __name__ == "__main__":
    seed()

