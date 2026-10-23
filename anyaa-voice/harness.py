"""Phase 0: prove one Groq tool round trip without voice."""

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import function_tool, llm
from livekit.plugins import groq

MODEL = "openai/gpt-oss-20b"
PROMPT = "Check whether the Anyaa demo environment is ready."


async def get_demo_status() -> dict:
    """Check whether the Anyaa demo environment is ready."""
    return {"ok": True, "summary": "Anyaa demo environment is ready."}


async def run_harness(model: llm.LLM) -> str:
    """Require a model-selected tool call, execute it, then return its answer."""
    context = llm.ChatContext.empty()
    context.add_message(role="system", content=(
        "You are a concise household voice assistant. Keep replies under two sentences. "
        "Use get_demo_status to check demo readiness. Report the result in one plain "
        "English sentence, without JSON or formatting."
    ))
    context.add_message(role="user", content=PROMPT)
    tools = [function_tool(get_demo_status)]
    response = await model.chat(chat_ctx=context, tools=tools, tool_choice="auto").collect()
    calls = response.tool_calls
    if len(calls) != 1 or calls[0].name != "get_demo_status" or json.loads(calls[0].arguments or "{}") != {}:
        raise RuntimeError("Expected exactly one get_demo_status call with no arguments.")

    call = calls[0]
    result = await get_demo_status()
    print(f"Executed get_demo_status: {json.dumps(result)}", file=sys.stderr)
    if response.text:
        context.add_message(role="assistant", content=response.text)
    context.items.append(llm.FunctionCall(
        call_id=call.call_id, name=call.name, arguments=call.arguments,
        extra=call.extra or {},
    ))
    context.items.append(llm.FunctionCallOutput(
        call_id=call.call_id, name=call.name, output=json.dumps(result), is_error=False,
    ))
    final = await model.chat(chat_ctx=context, tools=tools, tool_choice="none").collect()
    if final.tool_calls or not final.text.strip():
        raise RuntimeError("Groq did not return a final text answer after the tool result.")
    return final.text.strip()


async def self_test() -> None:
    """Offline protocol check; this does not prove live Groq access."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    call = llm.FunctionToolCall(name="get_demo_status", arguments="{}", call_id="demo-call")
    response = llm.CollectedResponse(tool_calls=[call])
    final = llm.CollectedResponse(text="The demo is ready.")
    model = Mock()
    model.chat.side_effect = [SimpleNamespace(collect=AsyncMock(return_value=value))
                              for value in (response, final)]
    assert await run_harness(model) == "The demo is ready."
    history = model.chat.call_args.kwargs["chat_ctx"].items
    assert history[-2].call_id == history[-1].call_id == "demo-call"
    assert json.loads(history[-1].output) == await get_demo_status()
    assert await function_tool(get_demo_status)() == await get_demo_status()
    model.chat.side_effect = [SimpleNamespace(collect=AsyncMock(return_value=final))]
    try:
        await run_harness(model)
    except RuntimeError:
        pass
    else:
        raise AssertionError("A response without a tool call must fail verification.")
    print("PASS: offline tool round trip and missing-tool rejection (mocked; no live API).")


async def main() -> None:
    load_dotenv(Path(__file__).with_name(".env"))
    if not os.getenv("GROQ_API_KEY", "").strip():
        raise SystemExit("Missing GROQ_API_KEY. Fill in anyaa-voice/.env first.")
    async with groq.LLM(model=MODEL, reasoning_effort="low") as model:
        print(await run_harness(model))


if __name__ == "__main__":
    asyncio.run(self_test() if sys.argv[1:] == ["--self-test"] else main())
