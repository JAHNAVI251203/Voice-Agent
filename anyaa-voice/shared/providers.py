"""Explicit free OpenRouter model shared by text and voice."""

from livekit.plugins import openai

MODEL = "nvidia/nemotron-3.5-lightning:free"


def create_llm() -> openai.LLM:
    return openai.LLM.with_openrouter(
        model=MODEL, temperature=0, provider={"require_parameters": True},
    )
