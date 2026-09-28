from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from db.tools import (
    book_appointment,
    cancel_appointment,
    check_technician_availability,
    get_customer_context,
    get_job,
    reschedule_appointment,
)


CUSTOMER_ID = "customer-1"
JOB_ID = "job-1"
TECHNICIAN_ID = "tech-1"
APPOINTMENT_ID = "appointment-1"
START = datetime(2026, 10, 1, 10, tzinfo=timezone.utc)
END = datetime(2026, 10, 1, 11, tzinfo=timezone.utc)


class FakeQuery:
    def __init__(self, client, table):
        self.client, self.table = client, table
        self.filters = []
        self.operation, self.payload = "select", None
        self.single_row = False

    def select(self, *_args): return self
    def eq(self, key, value): self.filters.append((key, "eq", value)); return self
    def in_(self, key, values): self.filters.append((key, "in", values)); return self
    def lt(self, key, value): self.filters.append((key, "lt", value)); return self
    def gt(self, key, value): self.filters.append((key, "gt", value)); return self
    def lte(self, key, value): self.filters.append((key, "lte", value)); return self
    def gte(self, key, value): self.filters.append((key, "gte", value)); return self
    def order(self, *_args, **_kwargs): return self
    def limit(self, *_args): return self
    def single(self): self.single_row = True; return self
    def insert(self, payload): self.operation, self.payload = "insert", payload; return self
    def update(self, payload): self.operation, self.payload = "update", payload; return self

    def execute(self):
        rows = self.client.tables.setdefault(self.table, [])
        selected = [deepcopy(row) for row in rows if self._matches(row)]
        if self.operation == "insert":
            values = self.payload if isinstance(self.payload, list) else [self.payload]
            selected = []
            for value in values:
                row = {"id": str(uuid4()), **deepcopy(value)}
                rows.append(row)
                selected.append(deepcopy(row))
        elif self.operation == "update":
            for row in rows:
                if self._matches(row): row.update(deepcopy(self.payload))
            selected = [deepcopy(row) for row in rows if self._matches(row)]
        if self.single_row:
            return SimpleNamespace(data=selected[0] if selected else None)
        return SimpleNamespace(data=selected)

    def _matches(self, row):
        for key, op, value in self.filters:
            actual = row.get(key)
            if op == "eq" and actual != value: return False
            if op == "in" and actual not in value: return False
            if op == "lt" and not actual < value: return False
            if op == "gt" and not actual > value: return False
            if op == "lte" and not actual <= value: return False
            if op == "gte" and not actual >= value: return False
        return True


class FakeClient:
    def __init__(self, *, conflict=False):
        self.conflict = conflict
        self.tables = {
            "customers": [{"id": CUSTOMER_ID, "full_name": "Maya", "phone": "1"}],
            "jobs": [{"id": JOB_ID, "customer_id": CUSTOMER_ID, "status": "open"}],
            "technicians": [{"id": TECHNICIAN_ID, "full_name": "Jordan", "skills": ["hvac"], "service_areas": ["sf"], "status": "active"}],
            "technician_availability": [{"id": "window-1", "technician_id": TECHNICIAN_ID, "available_start": "2026-10-01T09:00:00+00:00", "available_end": "2026-10-01T17:00:00+00:00", "status": "available"}],
            "appointments": ([{"id": "conflict", "customer_id": CUSTOMER_ID, "job_id": JOB_ID, "technician_id": TECHNICIAN_ID, "scheduled_start": "2026-10-01T10:30:00+00:00", "scheduled_end": "2026-10-01T11:30:00+00:00", "status": "confirmed"}] if conflict else []),
            "appointment_events": [],
        }

    def from_(self, table): return FakeQuery(self, table)


def run(coro): return asyncio.run(coro)


def test_customer_context_and_missing_customer():
    client = FakeClient()
    assert run(get_customer_context(CUSTOMER_ID, client=client)).ok
    assert run(get_customer_context("missing", client=client)).status == "not_found"


def test_job_lookup():
    client = FakeClient()
    assert run(get_job(JOB_ID, client=client)).ok
    assert run(get_job("missing", client=client)).status == "not_found"


def test_availability_and_booking():
    client = FakeClient()
    assert run(check_technician_availability(TECHNICIAN_ID, START, END, client=client)).ok
    result = run(book_appointment(CUSTOMER_ID, JOB_ID, TECHNICIAN_ID, START, END, client=client))
    assert result.ok
    assert client.tables["jobs"][0]["status"] == "scheduled"


def test_missing_technician_is_not_available():
    result = run(check_technician_availability("missing", START, END, client=FakeClient()))
    assert result.status == "not_found"


def test_unavailable_slot_does_not_book():
    client = FakeClient(conflict=True)
    result = run(book_appointment(CUSTOMER_ID, JOB_ID, TECHNICIAN_ID, START, END, client=client))
    assert result.status == "unavailable"
    assert not [row for row in client.tables["appointments"] if row.get("status") == "confirmed" and row.get("id") != "conflict"]


def test_reschedule_and_invalid_reschedule():
    client = FakeClient()
    client.tables["appointments"].append({"id": APPOINTMENT_ID, "customer_id": CUSTOMER_ID, "job_id": JOB_ID, "technician_id": TECHNICIAN_ID, "scheduled_start": START.isoformat(), "scheduled_end": END.isoformat(), "status": "confirmed"})
    result = run(reschedule_appointment(APPOINTMENT_ID, datetime(2026, 10, 1, 12, tzinfo=timezone.utc), datetime(2026, 10, 1, 13, tzinfo=timezone.utc), client=client))
    assert result.ok
    assert run(reschedule_appointment("missing", START, END, client=client)).status == "not_found"


def test_cancellation_and_invalid_cancellation():
    client = FakeClient()
    client.tables["appointments"].append({"id": APPOINTMENT_ID, "customer_id": CUSTOMER_ID, "job_id": JOB_ID, "technician_id": TECHNICIAN_ID, "scheduled_start": START.isoformat(), "scheduled_end": END.isoformat(), "status": "confirmed"})
    assert run(cancel_appointment(APPOINTMENT_ID, client=client)).ok
    assert run(cancel_appointment(APPOINTMENT_ID, client=client)).status == "failure"
    assert run(cancel_appointment("missing", client=client)).status == "not_found"


def test_database_failure_is_not_success():
    class BrokenClient:
        def from_(self, _table):
            raise RuntimeError("database unavailable")

    result = run(get_job(JOB_ID, client=BrokenClient()))
    assert result.status == "failure"
    assert not result.ok

