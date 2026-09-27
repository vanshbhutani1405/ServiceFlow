# ServiceFlow — Final Project Plan

**One-liner:** A low-latency voice agent for home-service businesses that books, reschedules, and dispatches technicians over a real phone/web call, engineered around response speed, natural interruption handling, and safe failure behavior, not just "an LLM with a microphone."

**Positioning for recruiters:** This is a voice infrastructure project, not a scheduling app that happens to talk. The scheduling workflow is the vehicle used to prove the voice engineering works.

---

## 1. Problem and scope

A caller says something like *"My AC isn't cooling, can someone come today around 2?"* The system must understand the request, identify the customer if it's a returning caller, classify urgency, check technician availability, book or reschedule an appointment, and confirm, all while sounding responsive and handling interruptions naturally.

**In scope for V1:**
- Book, reschedule, cancel an appointment
- Identify returning customers and load their job history
- Match a technician deterministically (skill, availability, area)
- One safety/escalation path (emergency, not schedulable)
- One deliberate tool-failure recovery path
- Full latency instrumentation and an eval suite

**Explicitly out of scope for V1:** LangGraph as an extra orchestration layer, Redis, a standalone FastAPI backend in the call path, MCP for internal tools, RAG on every turn, real Google Calendar, more than 3 agents, LLM-based technician matching.

Every one of these was considered and cut on purpose. If asked in an interview why something isn't there, the answer is "it would have added latency or complexity without proving anything new."

---

## 2. Engineering goal: perceived latency

The core metric is **TTFA — time to first audio**, how long from the caller finishing a turn to the agent's response audio starting. Secondary metrics: STT latency, LLM TTFT, TTS TTFA, tool/DB latency, end-to-end latency, and barge-in latency (detection, TTS cancellation, recovery). All reported as **P50 / P95 / P99**, never a single cherry-picked number.

---

## 3. Model stack — LiveKit Inference

All three voice models are served through **LiveKit Inference** (a single unified interface into hosted STT/LLM/TTS providers, zero data retention by default, no separate provider API keys, latency-tuned on LiveKit's own infrastructure). This keeps the model layer inside the same `inference.STT` / `inference.LLM` / `inference.TTS` classes instead of juggling three separate SDKs.

| Layer | Pick | Why |
|---|---|---|
| **STT** | Deepgram Nova-3 (`deepgram/nova-3`) | Streaming, and LiveKit Inference has infrastructure in Mumbai, which cuts round-trip latency for calls originating in India. |
| **LLM** | Gemma 4 31B IT (`google/gemma-4-31b-it`) | LiveKit's own recommended default LLM for voice agents, specifically tuned for low first-sentence latency on LiveKit's infrastructure rather than raw throughput. |
| **TTS** | Fish Audio S2.1 Pro (`fishaudio/s2.1-pro`) | Available through LiveKit Inference with a low-latency mode, and supports expressive/prosody parameters if natural-sounding delivery is worth showing in the demo. |

```python
from livekit.agents import AgentSession, inference

session = AgentSession(
    stt=inference.STT(model="deepgram/nova-3", language="multi"),
    llm=inference.LLM(model="google/gemma-4-31b-it"),
    tts=inference.TTS(model="fishaudio/s2.1-pro", voice="<voice_id>", language="en"),
)
```

Note for the demo/eval write-up: Gemma-family models can leak internal "channel-thought" markup into the output stream if not filtered. Strip any `<|channel>thought ... <channel|>` segments in the LLM node before they reach TTS or transcript history, the same way you'd strip `<think>` tags from a reasoning model.

---

## 4. Architecture

```
CUSTOMER
   |
   v
WebRTC / LiveKit
   |
   v
LiveKit AgentSession (VAD, turn detection, barge-in)
   |
   v
Streaming STT (Deepgram Nova-3, via LiveKit Inference)
   |
   v
Fast routing --------------------------+
   |                |                  |
   v                v                  v
Scheduling      Dispatch           Triage
  Agent           Agent             Agent
   |                |                  |
   +--------+-------+--------+---------+
            |                |
            v                v
     Internal tools    Safety escalation
            |           (bypasses normal
            v            flow entirely)
   Supabase Postgres
            |
            v
     Gemma 4 31B IT (via LiveKit Inference)
            |
            v
     Fish Audio S2.1 Pro TTS (via LiveKit Inference)
            |
            v
        CUSTOMER

Observability: LiveKit metrics (STT/LLM/TTS/EOU latency) + Langfuse
(per-turn traces: routing decision, tool calls, arguments, outcome)
feed a live dashboard read off in-call session state, and a
post-call eval suite scored against Langfuse traces + Supabase logs.
```

**Why LiveKit is the center, not LangGraph:** LiveKit Agents already provides the realtime voice runtime, AgentSession, tool calling, agent handoffs, and session state. Putting LangGraph in the middle adds a second orchestration layer and a latency hop that buys nothing the voice runtime doesn't already do.

**Fast-path principle:** A simple request like "move my appointment to tomorrow at 11" should go STT → fast routing → Scheduling Agent → tool → streaming response → TTS. It should never pass through a router LLM, multiple agent handoffs, and a guardrail LLM call just to reach the same tool. Every unnecessary model call is latency the user feels.

---

## 5. Agents

| Agent | Responsibility |
|---|---|
| **Scheduling** | check availability, book, reschedule, cancel |
| **Dispatch** | find technician, check technician availability, match technician, estimate ETA, dispatch |
| **Triage** | identify service type, classify urgency, safety classification |

Safety/escalation is a policy path triggered from Triage, not a decorative fourth agent. No agent exists unless it has a distinct, necessary responsibility.

---

## 6. Data model (Supabase Postgres)

Core tables:

```
customers
technicians
jobs
appointments
calls
transcripts
tool_executions
appointment_events
```

Relationship: `Customer → Calls`, `Customer → Jobs → Appointments → Technician`.

Technician availability is just rows in Postgres (e.g. Mike, HVAC, 10:00 available, 14:00 booked). No external calendar integration in V1, tools query this table directly.

**Technician matching is deterministic, not LLM-based.** Backend logic scores skill match, availability, service area, distance, ETA, and workload, then returns the best eligible technician. The LLM decides *what needs to happen*; deterministic code decides *what can happen*. This is one of the strongest engineering signals in the project, call it out explicitly in the demo and in interviews.

---

## 7. Internal tools

Plain async Python functions inside the LiveKit agent process, calling Supabase directly (`asyncpg` or the Supabase client). No FastAPI, no MCP, no network hop for internal operations.

```
get_customer_context()
get_job()
check_technician_availability()
find_best_technician()
book_appointment()
reschedule_appointment()
cancel_appointment()
dispatch_job()
send_confirmation()
```

Rule of thumb: **internal system → plain tool function. External system (real Google Calendar, Twilio, a CRM) → MCP, only when it's genuinely needed.** MCP is not used in V1 because there is no external system to integrate with yet.

---

## 8. Voice engineering (first-class features, not add-ons)

- **Streaming STT** — process partial transcripts as they arrive
- **Streaming LLM** — start generating before the full response is planned
- **Streaming TTS** — start audio as soon as usable text exists
- **VAD** — detect speech quickly and accurately
- **Turn detection** — avoid dead air and premature cutoffs
- **Barge-in** (hero feature): agent speaking → user interrupts → VAD fires → TTS cancelled → generation stopped → new STT turn starts → new response. This must be near-instant and it is the single most impressive 15 seconds of the demo video.

---

## 9. Safety and failure paths

**Safety escalation:** a phrase like "I smell gas from my stove" must never enter the normal scheduling flow. Triage classifies it as a safety risk, normal flow stops immediately, and the system gives emergency guidance and escalates to a human/on-call dispatcher per a predefined policy. This is the moment that proves the system makes decisions, not just conversation.

**Failure recovery:** one tool failure is demonstrated deliberately (e.g. the availability check times out). The system must detect the failure, retry or fall back, and either recover and continue or escalate gracefully. It must **never hallucinate a successful booking** when a tool call failed. This is the moment that proves production thinking.

---

## 10. Observability

Two layers, deliberately not duplicated:

- **LiveKit's built-in metrics events** give raw voice-pipeline latency for free: STT latency, LLM TTFT, TTS TTFA, end-of-utterance delay. This feeds the live dashboard directly, no extra SDK needed for this part.
- **Langfuse** covers what LiveKit doesn't: per-turn traces with the routing decision, tool calls and arguments, and the outcome. This is what makes the eval suite possible (was the right tool called, was the task actually completed), and it gives automatic P50/P95/P99 aggregation across calls.

**Live dashboard for the demo video:** a lightweight script/page reading directly off the agent's in-call session state (already being tracked for the call), rendering TTFA, stage latency, and barge-in timing live while the call is happening. This is not a service or a product, it's a thin rendering layer on data that already exists. No FastAPI required for this.

---

## 11. Evaluation suite

Synthetic scenario categories: normal booking, rescheduling, cancellation, returning customer, barge-in, noisy audio, ambiguous request, emergency, tool failure, LLM failure, escalation.

Scored across three dimensions:
- **Voice:** TTFA, TTFT, P50/P95/P99, turn detection accuracy, barge-in latency
- **Agent:** routing correctness, tool selection, argument correctness, task completion
- **Reliability:** recovery behavior, fallback correctness, escalation correctness

---

## 12. Final tech stack

```
Voice runtime         LiveKit Agents (AgentSession)
Transport              WebRTC / LiveKit
VAD / turn detection   LiveKit voice stack
STT                    Deepgram Nova-3, via LiveKit Inference
Orchestration          LiveKit agents + handoffs + tools (no LangGraph)
LLM                    Gemma 4 31B IT, via LiveKit Inference
TTS                    Fish Audio S2.1 Pro, via LiveKit Inference (low-latency mode)
Database               Supabase Postgres
Vector search          pgvector, only if a knowledge-question path is added
Internal tools         Plain async Python functions, no MCP
External integrations  MCP, only when a real external system is added later
Observability          LiveKit metrics + Langfuse
Live dashboard         Thin script/page reading in-call session state
Frontend               React (for the dashboard only)
Deployment             Azure
Evaluation             Custom synthetic scenario suite

Explicitly not used in V1: Redis, FastAPI in the call path, LangGraph,
MCP for internal tools, real Google Calendar, RAG on every turn.
```

---

## 13. Folder structure

```
serviceflow/
├── README.md
├── .env.example
├── pyproject.toml
│
├── agent/                          # LiveKit voice agent (the call path)
│   ├── main.py                     # entrypoint, AgentSession setup
│   ├── session.py                  # STT/LLM/TTS model config, VAD, turn detection, barge-in
│   ├── routing.py                  # fast routing logic
│   │
│   ├── agents/
│   │   ├── scheduling_agent.py
│   │   ├── dispatch_agent.py
│   │   └── triage_agent.py
│   │
│   ├── tools/
│   │   ├── customer_tools.py       # get_customer_context, get_job
│   │   ├── scheduling_tools.py     # check_availability, book, reschedule, cancel
│   │   ├── dispatch_tools.py       # find_best_technician, dispatch_job
│   │   └── notification_tools.py   # send_confirmation
│   │
│   ├── matching/
│   │   └── technician_matcher.py   # deterministic scoring logic, no LLM
│   │
│   ├── safety/
│   │   └── escalation_policy.py    # safety classification + escalation flow
│   │
│   └── observability/
│       ├── langfuse_client.py      # per-turn trace logging
│       └── session_metrics.py      # writes live TTFA/stage timing to session state
│
├── db/
│   ├── schema.sql                  # customers, technicians, jobs, appointments,
│   │                                # calls, transcripts, tool_executions, appointment_events
│   ├── seed.py                     # mock technicians + availability
│   └── supabase_client.py
│
├── dashboard/                      # live latency view for the demo video
│   ├── src/
│   │   ├── App.tsx
│   │   └── components/LatencyPanel.tsx
│   └── package.json
│
├── eval/
│   ├── scenarios/                  # synthetic call scripts, one file per category
│   │   ├── normal_booking.yaml
│   │   ├── barge_in.yaml
│   │   ├── emergency.yaml
│   │   ├── tool_failure.yaml
│   │   └── ...
│   ├── runner.py                   # plays scenarios against the agent
│   └── scorer.py                   # scores voice/agent/reliability dimensions
│
└── docs/
    ├── architecture.md
    └── demo-script.md
```

---

## 14. Build order

Build in this order, not the order topics were discussed in. Each stage should leave you with something that actually runs.

1. **Raw voice loop** — LiveKit agent wired to Deepgram Nova-3 / Gemma 4 31B / Fish Audio S2.1 Pro via LiveKit Inference, streaming STT → LLM → streaming TTS, no agents, no tools, just talking. Confirm TTFA is reasonable before adding anything else.
2. **Scheduling + Supabase** — real booking flow: check availability, book, reschedule, cancel, backed by real Postgres data.
3. **Dispatch + deterministic matching** — technician matching logic, ETA estimate, dispatch tool.
4. **Barge-in** — get interruption handling solid; this is the hero feature, don't rush it.
5. **Triage + safety escalation** — the emergency path that stops normal flow.
6. **Failure recovery** — deliberately inject one tool failure and handle it correctly.
7. **Observability** — wire LiveKit metrics + Langfuse traces, build the live dashboard last, once there's real data to show.
8. **Eval suite** — synthetic scenarios and scoring, once the system is stable enough to be worth measuring.

---

## 15. Demo video structure (~3 minutes)

| Time | Segment |
|---|---|
| 0:00–0:30 | Normal call, low-latency response, TTFA shown live |
| 0:30–0:55 | Barge-in: agent interrupted mid-sentence, stops instantly |
| 0:55–1:30 | Full booking workflow completes end to end |
| 1:30–2:00 | Safety scenario: normal flow stopped, escalation shown |
| 2:00–2:20 | Deliberate tool failure, retry, recovery or graceful escalation |
| 2:20–2:45 | Observability: TTFA, P50/P95/P99, stage breakdown, Langfuse trace |
| 2:45–3:00 | Architecture recap and closing line |

Closing line: *"The interesting part isn't that it can talk. It's that the entire system is engineered around latency, interruption handling, safety, and reliability, and every one of those numbers is actually measured, not claimed."*

---

## 16. What this project proves

1. **Voice engineering** — streaming, VAD, turn detection, barge-in, measured low latency
2. **AI engineering** — agents, routing, tool calling, context-aware decisions
3. **Systems engineering** — fast paths, deterministic logic where it belongs, failure recovery
4. **Production engineering** — observability, metrics, evaluation
5. **Product thinking** — a real business workflow that actually gets completed by voice

**The one rule not to compromise on:** don't make the architecture more complicated to make the resume look more impressive. Make the voice system itself impressive.
