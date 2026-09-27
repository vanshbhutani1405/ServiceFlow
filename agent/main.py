"""Minimal Phase 1 ServiceFlow voice agent.

The business workflow is intentionally absent here. This module only proves
the streaming LiveKit Inference STT -> LLM -> TTS loop and records the
latencies exposed by the AgentSession conversation history.
"""

from __future__ import annotations

import logging
from typing import Any

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentSession, TurnHandlingOptions, inference
from livekit.plugins import silero

load_dotenv()

logger = logging.getLogger("serviceflow")

SYSTEM_INSTRUCTIONS = """
You are a friendly, concise receptionist for a home-service company.
Answer naturally and keep replies brief for a voice conversation.
This is a voice-pipeline demonstration: do not book, reschedule, or cancel
anything; do not call business tools; and do not invent customer, service,
technician, or appointment information. If asked to take an action, explain
that this demo can only answer general questions and offer to take a message.
""".strip()


def _metric_value(metrics: Any, name: str) -> float | None:
    value = getattr(metrics, name, None)
    return float(value) if isinstance(value, (int, float)) else None


def _log_session_metrics(session: AgentSession) -> None:
    """Log per-turn metrics captured by the documented STT-LLM-TTS pipeline."""
    for item in session.history.items:
        metrics = getattr(item, "metrics", None)
        if metrics is None:
            continue

        role = getattr(item, "role", None)
        if role == "user":
            logger.info(
                "voice_metrics role=user transcription_delay_s=%s end_of_turn_delay_s=%s",
                _metric_value(metrics, "transcription_delay"),
                _metric_value(metrics, "end_of_turn_delay"),
            )
        elif role == "assistant":
            logger.info(
                "voice_metrics role=assistant llm_ttft_s=%s tts_ttfb_s=%s "
                "playback_latency_s=%s ttfa_s=%s",
                _metric_value(metrics, "llm_node_ttft"),
                _metric_value(metrics, "tts_node_ttfb"),
                _metric_value(metrics, "playback_latency"),
                _metric_value(metrics, "e2e_latency"),
            )


async def entrypoint(ctx: agents.JobContext) -> None:
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=inference.STT(model="deepgram/nova-3", language="multi"),
        llm=inference.LLM(model="google/gemma-4-31b-it"),
        tts=inference.TTS(
            model="fishaudio/s2.1-pro",
            voice="fa4c9eb3dccc4806b382b40d61c6b10a",
            language="en",
        ),
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),
            endpointing={"mode": "fixed", "min_delay": 0.5, "max_delay": 3.0},
            interruption={
                "mode": "adaptive",
                "min_duration": 0.5,
                "resume_false_interruption": True,
            },
            preemptive_generation={"preemptive_tts": True},
        ),
    )

    async def on_session_end() -> None:
        _log_session_metrics(session)

    ctx.add_shutdown_callback(on_session_end)

    await session.start(
        agent=Agent(instructions=SYSTEM_INSTRUCTIONS),
        room=ctx.room,
    )
    await session.generate_reply(
        instructions="Greet the caller briefly and say you are ready to help with general questions."
    )


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))


if __name__ == "__main__":
    run()

