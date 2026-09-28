# ServiceFlow

ServiceFlow is a low-latency AI voice agent for home-service businesses. This
repository currently contains Phase 1 only: a minimal LiveKit voice loop.

## Phase 1 scope

The agent demonstrates:

`customer speech -> LiveKit -> AgentSession -> streaming STT -> LLM -> streaming TTS -> response audio`

No scheduling, database, dispatch, triage, dashboard, Langfuse, or deployment
code belongs in this phase.

Phase 2 adds the Supabase schema and independently callable internal database
tools under `db/`. Those tools are not wired into the voice conversation yet.

## Requirements

- Python 3.10 through 3.14
- A LiveKit Cloud project with Inference enabled
- LiveKit URL, API key, and API secret
- `uv` or `pip`

LiveKit Inference supplies the model providers, so this phase does not require
separate Deepgram, Google, or Fish Audio API keys.

## Setup

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
Copy-Item .env.example .env
```

Fill in the three LiveKit values in `.env`. Download the VAD model files once:

```powershell
python -m agent.main download-files
```

For Phase 2, also set `SUPABASE_URL` and the server-side
`SUPABASE_SERVICE_ROLE_KEY` in `.env`. Run `db/schema.sql` in the Supabase SQL
Editor, then seed deterministic MVP fixtures with:

```powershell
python -m db.seed
```

The database tools are available from `db.tools` and return explicit
`success`, `failure`, `not_found`, or `unavailable` results. Run their focused
unit tests with:

```powershell
python -m pytest
```

## Run locally

```powershell
python -m agent.main dev
```

The LiveKit Agents CLI's `dev` mode starts the worker and lets a LiveKit
 Playground or another room participant connect to it. The agent greets the
 caller, then responds to short spoken turns.

## Model configuration

- STT: `deepgram/nova-3`, language `multi`
- LLM: `google/gemma-4-31b-it`
- TTS: `fishaudio/s2.1-pro`, voice `fa4c9eb3dccc4806b382b40d61c6b10a`, language `en`
- VAD: `silero.VAD.load()`
- Turn detection: `inference.TurnDetector()`
- Interruption: adaptive LiveKit interruption handling

The model IDs and session/turn-handling APIs follow the current LiveKit Agents
documentation. The exact dependency versions are pinned in `pyproject.toml`.

## Latency logs

At session shutdown, the agent logs the metrics captured by the STT-LLM-TTS
pipeline for each conversation item:

- transcription delay and end-of-turn delay
- LLM time to first token (`llm_node_ttft`)
- TTS time to first byte/audio (`tts_node_ttfb`)
- playback latency
- end-to-end response latency (`e2e_latency`, the Phase 1 TTFA proxy)

Actual values require a connected LiveKit session. P50/P95/P99 aggregation is
intentionally deferred until there are enough calls to measure.

