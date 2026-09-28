create extension if not exists pgcrypto;

create table if not exists customers (
    id uuid primary key default gen_random_uuid(),
    full_name text not null,
    phone text not null unique,
    email text,
    address text,
    service_area text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists technicians (
    id uuid primary key default gen_random_uuid(),
    full_name text not null,
    phone text,
    skills text[] not null default '{}',
    service_areas text[] not null default '{}',
    status text not null default 'active' check (status in ('active', 'inactive')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists technician_availability (
    id uuid primary key default gen_random_uuid(),
    technician_id uuid not null references technicians(id) on delete cascade,
    available_start timestamptz not null,
    available_end timestamptz not null,
    status text not null default 'available' check (status in ('available', 'blocked')),
    constraint technician_availability_valid_window check (available_end > available_start)
);

create table if not exists jobs (
    id uuid primary key default gen_random_uuid(),
    customer_id uuid not null references customers(id) on delete restrict,
    service_type text not null,
    description text not null,
    address text not null,
    priority text not null default 'normal' check (priority in ('low', 'normal', 'high', 'emergency')),
    status text not null default 'open' check (status in ('open', 'scheduled', 'dispatched', 'in_progress', 'completed', 'cancelled')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists appointments (
    id uuid primary key default gen_random_uuid(),
    customer_id uuid not null references customers(id) on delete restrict,
    job_id uuid not null references jobs(id) on delete restrict,
    technician_id uuid references technicians(id) on delete restrict,
    scheduled_start timestamptz not null,
    scheduled_end timestamptz not null,
    status text not null default 'requested' check (status in ('requested', 'confirmed', 'rescheduled', 'dispatched', 'completed', 'cancelled')),
    notes text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint appointments_valid_window check (scheduled_end > scheduled_start)
);

create table if not exists calls (
    id uuid primary key default gen_random_uuid(),
    customer_id uuid references customers(id) on delete set null,
    livekit_room_name text not null unique,
    status text not null default 'active' check (status in ('active', 'completed', 'failed')),
    started_at timestamptz not null default now(),
    ended_at timestamptz
);

create table if not exists transcripts (
    id uuid primary key default gen_random_uuid(),
    call_id uuid not null references calls(id) on delete cascade,
    speaker text not null check (speaker in ('customer', 'agent', 'system')),
    text text not null,
    created_at timestamptz not null default now()
);

create table if not exists tool_executions (
    id uuid primary key default gen_random_uuid(),
    call_id uuid references calls(id) on delete set null,
    tool_name text not null,
    status text not null check (status in ('success', 'failure', 'not_found', 'unavailable')),
    input jsonb not null default '{}'::jsonb,
    output jsonb,
    error text,
    created_at timestamptz not null default now()
);

create table if not exists appointment_events (
    id uuid primary key default gen_random_uuid(),
    appointment_id uuid not null references appointments(id) on delete cascade,
    event_type text not null,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_jobs_customer_id on jobs(customer_id);
create index if not exists idx_appointments_customer_id on appointments(customer_id);
create index if not exists idx_appointments_job_id on appointments(job_id);
create index if not exists idx_appointments_technician_window on appointments(technician_id, scheduled_start, scheduled_end);
create index if not exists idx_technician_availability_window on technician_availability(technician_id, available_start, available_end);
create index if not exists idx_calls_customer_id on calls(customer_id);
create index if not exists idx_transcripts_call_id on transcripts(call_id);
create index if not exists idx_tool_executions_call_id on tool_executions(call_id);
create index if not exists idx_appointment_events_appointment_id on appointment_events(appointment_id);

create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists customers_set_updated_at on customers;
create trigger customers_set_updated_at before update on customers
for each row execute function set_updated_at();

drop trigger if exists technicians_set_updated_at on technicians;
create trigger technicians_set_updated_at before update on technicians
for each row execute function set_updated_at();

drop trigger if exists jobs_set_updated_at on jobs;
create trigger jobs_set_updated_at before update on jobs
for each row execute function set_updated_at();

drop trigger if exists appointments_set_updated_at on appointments;
create trigger appointments_set_updated_at before update on appointments
for each row execute function set_updated_at();

