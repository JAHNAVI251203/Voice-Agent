"""Phase 0 English microphone loop. Run: python main.py console"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, TurnHandlingOptions, function_tool, inference
from livekit.plugins import deepgram, groq, sarvam, silero

from harness import MODEL, get_demo_status

load_dotenv(Path(__file__).with_name(".env"))
logger = logging.getLogger("anyaa.phase0")
server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext) -> None:
    missing = [name for name in ("DEEPGRAM_API_KEY", "GROQ_API_KEY", "SARVAM_API_KEY")
               if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError("Missing credentials in anyaa-voice/.env: " + ", ".join(missing))

    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="en"),
        llm=groq.LLM(model=MODEL, reasoning_effort="low"),
        tts=sarvam.TTS(model="bulbul:v3", target_language_code="en-IN", speaker="shubh"),
        vad=silero.VAD.load(),
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(version="v1-mini"),
            interruption={"mode": "vad"},
        ),
    )

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
        agent=Agent(
            instructions=(
                "You are a concise household voice assistant. Keep replies under two sentences. "
                "Use get_demo_status when asked whether the demo is ready. "
                "Speak plain English without JSON or formatting. "
                "Only claim readiness after the tool returns successfully."
            ),
            tools=[function_tool(get_demo_status)],
        ),
    )
    logger.info("Voice loop listening. Say: Check whether the demo is ready.")


if __name__ == "__main__":
    agents.cli.run_app(server)
