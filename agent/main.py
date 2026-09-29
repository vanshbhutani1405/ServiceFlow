"""ServiceFlow LiveKit agent entrypoint."""

from __future__ import annotations

import logging
import os
from typing import Any

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import (
    AgentServer,
    AgentSession,
    TurnHandlingOptions,
    cli,
    inference,
    room_io,
)
from livekit.plugins import silero

from agent.agents.scheduling_agent import SchedulingAgent
from agent.state import WorkflowState

load_dotenv()

logger = logging.getLogger("serviceflow")

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


server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext) -> None:
    await ctx.connect()

    vad = silero.VAD.load()
    stt = inference.STT(model="deepgram/nova-3", language="multi")

    @vad.on("metrics_collected")
    def on_vad_metrics(metrics: Any) -> None:
        logger.info(
            "voice_input vad_metrics inference_count=%s inference_duration_s=%s",
            getattr(metrics, "inference_count", None),
            getattr(metrics, "inference_duration_total", None),
        )

    @stt.on("metrics_collected")
    def on_stt_metrics(metrics: Any) -> None:
        logger.info(
            "voice_input stt_metrics audio_duration_s=%s streamed=%s duration_s=%s",
            getattr(metrics, "audio_duration", None),
            getattr(metrics, "streamed", None),
            getattr(metrics, "duration", None),
        )

    workflow_state = WorkflowState(customer_id=os.getenv("SERVICEFLOW_CUSTOMER_ID") or None)
    session = AgentSession(
        userdata=workflow_state,
        vad=vad,
        stt=stt,
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
        transcription_timeout=5.0,
    )

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event: Any) -> None:
        logger.info(
            "voice_input transcript=%r final=%s language=%s",
            getattr(event, "transcript", None),
            getattr(event, "is_final", None),
            getattr(event, "language", None),
        )

    @session.on("user_transcription_timeout")
    def on_user_transcription_timeout(event: Any) -> None:
        logger.warning(
            "voice_input transcription_timeout speech_duration_s=%s vad_speech_started_at=%s",
            getattr(event, "speech_duration", None),
            getattr(event, "vad_speech_started_at", None),
        )

    @session.on("user_state_changed")
    def on_user_state_changed(event: Any) -> None:
        logger.info(
            "voice_input user_state old=%s new=%s",
            getattr(event, "old_state", None),
            getattr(event, "new_state", None),
        )

    @session.on("agent_state_changed")
    def on_agent_state_changed(event: Any) -> None:
        logger.info(
            "barge_in agent_state old=%s new=%s",
            getattr(event, "old_state", None),
            getattr(event, "new_state", None),
        )

    @session.on("agent_false_interruption")
    def on_agent_false_interruption(event: Any) -> None:
        logger.info("barge_in false_interruption resumed=%s", getattr(event, "resumed", None))

    @session.on("error")
    def on_session_error(event: Any) -> None:
        logger.error("voice_input session_error=%s", event)

    async def on_session_end() -> None:
        _log_session_metrics(session)

    ctx.add_shutdown_callback(on_session_end)

    agent = SchedulingAgent(customer_id=workflow_state.customer_id, state=workflow_state)
    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(audio_input=True, audio_output=True),
    )
    linked_participant = session.room_io.linked_participant
    logger.info(
        "voice_input ready audio_enabled=%s audio_source=%s linked_participant=%s",
        session.input.audio_enabled,
        type(session.input.audio).__name__ if session.input.audio is not None else None,
        linked_participant.identity if linked_participant is not None else None,
    )
    await session.generate_reply(
        instructions="Greet the caller briefly and ask whether they need to book, reschedule, or cancel an appointment."
    )


def run() -> None:
    for noisy_logger in ("httpx", "httpcore", "hpack", "h2"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cli.run_app(server)


if __name__ == "__main__":
    run()

