"""Planner/cook microphone loop. Select cook with VOICE_AGENT_MODE=cook."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import AgentServer, AgentSession, TurnHandlingOptions, inference
from livekit.plugins import deepgram, sarvam, silero

from agents.household_planner import HouseholdPlannerAgent
from agents.cook_assistant import CookAssistantAgent
from shared.providers import create_llm
from shared.state import StateStore

load_dotenv(Path(__file__).with_name(".env"))
logger = logging.getLogger("anyaa.planner")
server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext) -> None:
    mode = os.getenv("VOICE_AGENT_MODE", "planner")
    language = os.getenv("COOK_LANGUAGE", "kn") if mode == "cook" else "en"
    if mode not in {"planner", "cook"} or language not in {"kn", "hi", "en"}:
        raise ValueError("Use VOICE_AGENT_MODE=planner/cook and COOK_LANGUAGE=kn/hi/en.")
    missing = [name for name in ("DEEPGRAM_API_KEY", "MISTRAL_API_KEY", "SARVAM_API_KEY")
               if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError("Missing credentials in anyaa-voice/.env: " + ", ".join(missing))

    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language=language),
        llm=create_llm(),
        tts=sarvam.TTS(model="bulbul:v3", target_language_code=f"{language}-IN", speaker="shubh"),
        vad=silero.VAD.load(),
        turn_handling=TurnHandlingOptions(
            turn_detection="vad" if mode == "cook" else inference.TurnDetector(version="v1-mini"),
            interruption={"mode": "vad"},
            # Confirmation must use the committed transcript, not a speculative reply.
            preemptive_generation={"enabled": False},
            endpointing={"min_delay": 1.0},
        ),
    )
    agent = CookAssistantAgent(StateStore(), language=language) if mode == "cook" else HouseholdPlannerAgent(StateStore())

    @session.on("user_state_changed")
    def on_user_state(event) -> None:
        if event.new_state == "speaking":
            agent.user_started_speaking()

    @session.on("user_transcription_timeout")
    def on_transcription_timeout(event) -> None:
        if mode == "cook":
            agent.record_misunderstanding()
            session.say(agent.clarification_reply)

    @session.on("conversation_item_added")
    def on_reply(event) -> None:
        if getattr(event.item, "role", None) == "assistant":
            logger.info("Anyaa (interrupted=%s): %s", event.item.interrupted, event.item.text_content)

    @session.on("user_input_transcribed")
    def on_transcript(event: agents.UserInputTranscribedEvent) -> None:
        if event.is_final:
            logger.info("Final transcript: %s", event.transcript)

    @session.on("function_tools_executed")
    def on_tools_executed(event: agents.FunctionToolsExecutedEvent) -> None:
        for call, output in event.zipped():
            logger.info("Tool %s completed; is_error=%s; output=%s",
                        call.name, output.is_error, output.output)

    await session.start(
        room=ctx.room,
        agent=agent,
    )
    logger.info("Cook assistant listening (%s). Ask for today's meal." if mode == "cook"
                else "Household planner listening (%s). Start with tomorrow's dinner.", language)


if __name__ == "__main__":
    agents.cli.run_app(server)
