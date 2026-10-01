<div align="center">

# ServiceFlow

### Real-time voice AI for field-service businesses

Handle customer calls, find technician availability, book appointments, and dispatch technicians — through a natural voice conversation, end to end.

**Call → Understand → Find availability → Book → Dispatch**

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![LiveKit](https://img.shields.io/badge/Realtime-LiveKit-FF4D4D)
![Deepgram](https://img.shields.io/badge/STT-Deepgram%20Nova--3-13EF93?logoColor=white)
![Gemma](https://img.shields.io/badge/LLM-Gemma%204%2031B%20IT-4285F4?logo=google&logoColor=white)
![Fish Audio](https://img.shields.io/badge/TTS-Fish%20Audio%20S2.1%20Pro-FF6B9D)
![Supabase](https://img.shields.io/badge/Database-Supabase-3ECF8E?logo=supabase&logoColor=white)
![Tests](https://img.shields.io/badge/tests-85%20passing-brightgreen)

**[▶ Watch the demo](#demo)** · **[Architecture](#architecture)** · **[Performance](#performance)** · **[Quickstart](#getting-started)**

</div>

<br>

<table>
<tr>
<td width="55%" valign="top">

### What is ServiceFlow?

Service businesses lose time when every call turns into manual scheduling work. Someone has to check a calendar, text a technician, call the customer back, and hope nothing double-books.

ServiceFlow is a realtime voice agent built around that exact workflow. A customer calls, describes the service they need, provides their details, asks for the next available appointment, picks a real slot, and the job is created and dispatched automatically through the backend, no human in the loop.

</td>
<td width="45%" valign="top">

> **The LLM understands what the customer wants.**
> **The backend decides what can actually happen.**

Every technician match, every available slot, every booking confirmation comes from real backend state. The model never invents a time, a technician, or a successful outcome.

</td>
</tr>
</table>

---

## Demo

<div align="center">

<a href="https://youtu.be/2RT9L7zd2Nw" target="_blank">
  <img src="https://img.youtube.com/vi/2RT9L7zd2Nw/maxresdefault.jpg" alt="ServiceFlow demo video" width="760">
</a>

**[▶ Watch on YouTube](https://youtu.be/2RT9L7zd2Nw)** — a full call from first "hello" to a dispatched technician, no edits.

`Customer call` → `Intake` → `Availability` → `Slot selection` → `Booking` → `Technician assignment` → `Dispatch` → `Confirmation`

</div>

---

## What it handles

| Customer says | ServiceFlow does |
|---|---|
| "I need an AC repair." | Identifies the requested service |
| "Any time tomorrow is fine." | Searches real flexible availability |
| "1 PM works." | Validates and books that exact slot |
| "Can you send someone?" | Assigns an eligible technician deterministically |
| "I need to change my appointment." | Reschedules through the same workflow |
| "I need to cancel." | Cancels the appointment safely |

**Built in from day one:** returning-customer context · interruption / barge-in · deterministic technician matching · idempotent booking · failure recovery · a safety / triage path for emergencies.

---

## Architecture

<div align="center">
<img src="./ServiceFlow%20Architecture.png" alt="ServiceFlow Architecture" width="880">
</div>

<table>
<tr>
<td width="58%" valign="top">

```text
Customer
   │
   ▼
WebRTC / LiveKit
   │
   ▼
AgentSession
   ├── VAD + Turn Detection
   ├── Streaming STT
   └── Barge-in / Interruption
   │
   ▼
Workflow Routing
   ├── Scheduling
   ├── Dispatch
   └── Triage
   │
   ▼
Internal Async Tools
   │
   ▼
Supabase PostgreSQL
   ├── Customers      ├── Technicians
   ├── Jobs           └── Availability
   └── Appointments
   │
   ▼
LLM → TTS → Customer
```

</td>
<td width="42%" valign="top">

### Core design principle

**LLM = intent and conversation.**
**Backend = validation, state, availability, assignment, and side effects.**

Technicians are never picked by the model. Availability comes from real backend data. A selected slot is revalidated right before the booking is committed, so nothing is booked on stale information.

</td>
</tr>
</table>

### Why the workflow is deterministic

Voice agents can sound completely convincing while still making the wrong backend decision. ServiceFlow keeps those two jobs separate on purpose:

```text
"Tomorrow afternoon works."
        │
        ▼
     LLM — understands date + time window
        │
        ▼
   Backend — finds real slots, eligible technicians, conflicts
        │
        ▼
 Customer selects a real returned slot
        │
        ▼
   Backend revalidates → Job → Appointment → Technician → Dispatch
```

The model never generates the final technician, the appointment ID, or the booking outcome, that's backend-authoritative, every time.

---

## Performance

<div align="center">

| Metric | Approx. P95 |
|---|---|
| **Time to first agent audio** | **~900 ms** |
| **End-to-end response** | **~1.5 s** |
| STT latency | ~380 ms |
| LLM time-to-first-token | ~420 ms |
| TTS time-to-first-byte | ~310 ms |
| Tool / database execution | ~340 ms |
| Interruption (barge-in) response | ~220 ms |

</div>

> Demo-baseline figures from real calls, not a formal benchmark. Voice latency shifts with network conditions, provider load, and which tool path a turn takes. Stages overlap through streaming, so they don't sum linearly to the end-to-end number. To be replaced with a repeatable benchmark once a larger clean-call sample is collected.

---

## Engineering highlights

| Decision | Why it matters |
|---|---|
| **Realtime-first** | Keeps the call path inside LiveKit, no unnecessary HTTP hop between the customer and the model |
| **Deterministic matching** | The model cannot invent or rank technicians, backend logic does |
| **Canonical slot selection** | Only a slot the backend actually generated can be booked |
| **Idempotent operations** | Retries never create duplicate jobs or appointments |
| **Explicit tool states** | A failed operation is never presented to the customer as successful |
| **Stateful workflows** | Captured customer and booking state survives across turns and reconnects |

> **The model decides what the customer wants. The backend decides what the system is allowed to do.**

---

## Voice stack

| Layer | Technology |
|---|---|
| Voice transport | LiveKit WebRTC |
| Realtime runtime | LiveKit Agents |
| VAD / turn handling | Silero + AgentSession |
| Speech-to-text | Deepgram Nova-3 (LiveKit Inference) |
| Language model | Gemma 4 31B IT (LiveKit Inference) |
| Text-to-speech | Fish Audio S2.1 Pro (LiveKit Inference) |
| Orchestration | LiveKit Agents + typed workflow state |
| Database | Supabase PostgreSQL |
| Backend tools | Async Python |
| Frontend | React |
| Deployment | Azure |

---

<details>
<summary><b>Project structure</b></summary>

```text
ServiceFlow/
├── agent/
│   ├── main.py
│   ├── state.py
│   ├── routing.py
│   └── agents/
│       ├── scheduling_agent.py
│       ├── dispatch_agent.py
│       └── triage_agent.py
├── db/
│   ├── supabase_client.py
│   ├── tools.py
│   ├── seed.py
│   └── schema.sql
├── tests/
├── ServiceFlow Architecture.png
├── .env.example
├── AGENTS.md
└── README.md
```

</details>

<details>
<summary><b>Getting started</b></summary>

**Requirements:** Python 3.12+, a LiveKit Cloud project, a Supabase project, and credentials for the configured inference providers.

```bash
git clone https://github.com/vanshbhutani1405/ServiceFlow.git
cd ServiceFlow

python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
```

```bash
copy .env.example .env
# add your LiveKit, Supabase, and inference credentials
```

```bash
python -m db.seed        # initialize the database
lk agent dev agent/main.py   # start the agent
```

</details>

<details>
<summary><b>Testing</b></summary>

ServiceFlow currently has **85 automated tests** covering the core workflow.

```bash
pytest -q
python -m compileall -q agent db tests
git diff --check
```

Coverage includes routing, intake/state handling, availability search, slot validation, booking idempotency, technician assignment, dispatch handoff, failure behavior, and startup regressions.

</details>

---

## Reliability model

> **Never confirm an action the backend did not successfully perform.**

That applies to availability, appointment creation, rescheduling, cancellation, technician assignment, and dispatch. If a tool fails, the conversation stays truthful and recovers where it can, instead of hallucinating a successful outcome.

---

<details>
<summary><b>Roadmap</b></summary>

- [x] Realtime voice runtime
- [x] Appointment booking
- [x] Flexible availability
- [x] Rescheduling and cancellation
- [x] Deterministic technician dispatch
- [x] Returning-customer context
- [x] Barge-in handling
- [x] Failure recovery
- [x] Safety / triage path
- [ ] Production phone-number integration
- [ ] Larger automated voice evaluation suite
- [ ] Multi-business / multi-tenant support

</details>

---

<div align="center">

### Built to explore what production-grade voice agents should actually handle.

**ServiceFlow** · realtime voice · deterministic workflows · safe backend actions

[GitHub](https://github.com/vanshbhutani1405/ServiceFlow) · [Demo video](https://youtu.be/2RT9L7zd2Nw)

</div>