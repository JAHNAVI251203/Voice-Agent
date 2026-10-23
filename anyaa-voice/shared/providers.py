"""Mistral Chat Completions configuration shared by text and voice."""

from livekit.plugins import mistralai

MODEL = "mistral-small-2603"


def create_llm() -> mistralai.LLM:
    return mistralai.LLM(model=MODEL, api_mode="chat_completions", temperature=0)
