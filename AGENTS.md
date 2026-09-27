# ServiceFlow Engineering Instructions

## Project

ServiceFlow is a low-latency AI voice agent for home-service businesses.

The primary engineering objective is:
1. Low perceived latency
2. Natural interruption / barge-in
3. Correct tool execution
4. Safe failure behavior
5. Measurable observability
6. Simple architecture

Do not add infrastructure just to make the project look more complex.

## Architecture

Use LiveKit Agents as the realtime voice runtime.

Core stack:

- Python
- LiveKit Agents
- LiveKit AgentSession
- LiveKit Inference
- Deepgram Nova-3 STT
- Gemma 4 31B IT LLM
- Fish Audio S2.1 Pro TTS
- Supabase PostgreSQL
- Langfuse
- React dashboard
- Azure deployment

Do NOT introduce:

- LangGraph
- Redis
- MCP for internal tools
- FastAPI in the realtime call path
- External Google Calendar
- RAG on every turn
- LLM-based technician matching

unless explicitly requested.

## LiveKit documentation rule

Before implementing or changing LiveKit functionality:

1. Consult the current official LiveKit Agents documentation.
2. Prefer documented APIs and examples over remembered APIs.
3. Do not invent LiveKit classes, methods, parameters, events, or model identifiers.
4. If the installed SDK version differs from the documentation, inspect the installed package/API before coding.
5. Keep LiveKit-specific logic isolated so SDK changes are easy to update.

Official documentation:

https://docs.livekit.io/agents/

Important documentation areas:

- AgentSession
- Agents and handoffs
- Tools
- Workflows
- Models / LiveKit Inference
- Turn detection
- Interruptions
- Observability
- Agent server

## Coding principles

Prefer:
- small modules
- async Python
- typed interfaces
- deterministic business logic
- explicit error handling
- structured logging
- testable functions

Avoid:
- unnecessary abstractions
- premature microservices
- duplicate state
- unnecessary network calls
- unnecessary LLM calls

## LLM responsibility

The LLM decides:
"What does the customer want?"

Deterministic backend code decides:
"What can actually happen?"

Never let the LLM invent:
- appointment availability
- technician availability
- booking success
- technician assignment
- database state

## Failure behavior

A failed tool call must never be represented to the customer as a successful operation.

Tool failures must:
1. be detected
2. be logged
3. retry when appropriate
4. recover when possible
5. otherwise escalate gracefully

## Performance

Track:

- TTFA
- STT latency
- LLM TTFT
- TTS TTFA
- tool latency
- end-to-end latency
- barge-in latency

Report:
- P50
- P95
- P99

Never optimize based on one cherry-picked latency measurement.