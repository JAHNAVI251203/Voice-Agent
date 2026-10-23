# Phase 0 SDK spike — verified results

Verified 2026-10-23 on Windows, Python 3.13.15. Phase 0 is complete with Groq as
its single primary LLM, following the user's explicit provider-change instruction.
No Phase 1 agents, state, product tools or fallback hierarchy were built.

## Dependencies and environment

Direct dependencies pinned and installed:

- `livekit-agents==1.8.8`
- `livekit-plugins-deepgram==1.8.8`
- `livekit-plugins-groq==1.8.8`
- `livekit-plugins-sarvam==1.8.8`
- `livekit-plugins-silero==1.8.8`
- `python-dotenv==1.2.2`

Relevant installed transitive dependencies: `livekit==1.1.20`,
`livekit-local-inference==0.2.7`, `livekit-plugins-openai==1.8.8`,
`openai==3.37.0`, `sounddevice==0.5.6`, `numpy==2.5.4`, `aiohttp==3.14.5`.
The OpenAI adapter is an upstream Groq/Sarvam dependency; the LLM endpoint is Groq.
Earlier Google packages may remain in this development venv but are no longer
required or imported by the implementation. No OpenRouter is used.

Required for harness: `GROQ_API_KEY`. Voice additionally requires
`DEEPGRAM_API_KEY` and `SARVAM_API_KEY`. Room mode also needs `LIVEKIT_URL`,
`LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`; local console mode does not.
`.env.example` contains only these six variables. Keys stay in ignored `.env`.
`load_dotenv(Path(__file__).with_name(".env"))` preserves existing env values.

## Verified SDK configuration

Inspected installed metadata, signatures and Python source before use:
`livekit/plugins/{deepgram/stt.py,groq/services.py,sarvam/tts.py,silero/vad.py}`;
`livekit/agents/{llm/llm.py,llm/chat_context.py,llm/tool_context.py,vad.py,stt/stt.py}`;
`livekit/agents/voice/{agent.py,agent_session.py,turn.py,events.py,tool_executor.py}`;
`livekit/agents/inference/eot/{detector.py,languages.py}` and `livekit/rtc/audio_frame.py`.

Imports: `from livekit.agents import Agent, AgentServer, AgentSession,
TurnHandlingOptions, function_tool, inference`; providers from `livekit.plugins`.

- STT: `deepgram.STT(model="nova-3", language="en")`, reading `DEEPGRAM_API_KEY`.
  Streams PCM over Deepgram's `/v1/listen` WebSocket API. Kannada spike uses `kn`.
- LLM: `groq.LLM(model="openai/gpt-oss-20b", reasoning_effort="low")`, reading
  `GROQ_API_KEY`. Plugin endpoint default: `https://api.groq.com/openai/v1`.
  The selected model is listed in Groq's free-plan limits and supports tool use.
- TTS: `sarvam.TTS(model="bulbul:v3", target_language_code="en-IN", speaker="shubh")`,
  reading `SARVAM_API_KEY`. Installed source validates this pairing. Defaults:
  streaming, 22050 Hz, mono, MP3; endpoint `wss://api.sarvam.ai/text-to-speech/ws`.
- VAD: `silero.VAD.load()`, using its bundled ONNX model, CPU, 16000 Hz,
  minimum silence 0.55 seconds.
- Audio turn detector: `TurnHandlingOptions(turn_detection=
  inference.TurnDetector(version="v1-mini"), interruption={"mode": "vad"})`.
  This uses built-in local inference, without the legacy text-detector plugin.
  The audio model supports English/Hindi, not Kannada. Silero's silence default
  exceeds its required 0.25 seconds. Explicit VAD interruption avoids the SDK's
  console auto-selection of LiveKit cloud adaptive interruption.

`TurnHandlingOptions` is a TypedDict. The local audio model was also run directly
on a silent int16 buffer and returned a valid probability on Windows.
Native audio imports hung inside this execution sandbox; the same imports and
all successful live tests ran outside it. No SDK source files were modified.

## Startup and tool mechanism

`server = AgentServer()` and `@server.rtc_session()` register the async job
entrypoint. `AgentSession(stt=..., llm=..., tts=..., vad=..., turn_handling=...)`
is started with `await session.start(room=ctx.room, agent=Agent(...))`.
`agents.cli.run_app(server)` supplies console and room startup commands.
Python console mode works in this installed version, although it is deprecated
in favor of `lk agent console`.

Exactly one shared tool exists: `async def get_demo_status() -> dict`, returning
`{"ok": true, "summary": "Anyaa demo environment is ready."}`.
Voice registers it with `Agent(tools=[function_tool(get_demo_status)], instructions=...)`.
The installed executor calls `await tool(...)`: a synchronous function originally
caused `TypeError: object dict can't be used in 'await' expression`. Changing the
shared function to async fixed the actual voice execution. The offline check
now awaits the real LiveKit tool wrapper as a regression check.

Harness and voice use the same Groq plugin. The harness builds `llm.ChatContext.empty()`,
adds system/user messages with `add_message(role=..., content=...)`, then awaits
`model.chat(chat_ctx=..., tools=[function_tool(get_demo_status)], tool_choice="auto").collect()`.
`CollectedResponse.tool_calls` contains name, JSON-string arguments, call_id and
optional extra fields. The harness requires exactly one matching parameterless
call, awaits the real function, inserts matching `FunctionCall` and
`FunctionCallOutput(call_id=..., name=..., output=..., is_error=False)` items,
then collects the final response with `tool_choice="none"`. It never fabricates
success when a model omits the call. Only tool output and final text are printed.

Voice logs final `user_input_transcribed` events via `event.is_final` and
`event.transcript`; `function_tools_executed` uses `event.zipped()` and
`call.name`, `output.is_error`, `output.output`. Prompts/reasoning are not logged
by the application callbacks.

## Microphone diagnosis and English proof

- Selected input: device **1**, Microphone (Realtek(R) Audio), MME.
  Selected output: device **3**, Speakers (Realtek(R) Audio).
- Windows current-user microphone and desktop-app (`NonPackaged`) consent both
  read `Allow`; no permission setting was changed.
- Direct STT-only capture delivered live frames and actual Deepgram WebSocket
  writes. Around the spoken test, raw peak reached 18025, frame RMS approximately
  -12.8 dBFS, Silero probability reached 1.0, and speech start/end events fired.
- The user said "This is an English microphone test." Deepgram returned exactly
  "This is an English microphone test." This was real input, not fixture audio.
- Temporary console instrumentation independently observed microphone callbacks,
  console-bridge frames and successful socket writes increasing continuously;
  the previous no-audio timeout did not recur. That private-console instrumentation
  was removed after diagnosis. `audio_spike.py` retains the isolated repeatable test.
- The original code-1011 / NET-0001 error establishes that Deepgram's earlier
  socket received no audio/text within its timeout. Its precise original trigger
  cannot be reconstructed from the earlier uninstrumented run. The current tests
  rule out a continuing permission/device/capture fault; do not claim a more
  specific historical root cause. Background playback was initially captured;
  it was paused before the user's exact test sentence.

`audio_spike.py` uses mono int16 16 kHz sounddevice capture, constructs
`rtc.AudioFrame(data, 16000, 1, samples_per_channel)`, and pushes each frame to
both `silero.VAD.load().stream()` and `deepgram.STT(..., http_session=...).stream()`.
It consumes VAD `START_OF_SPEECH` / `END_OF_SPEECH` / inference probability and
STT `INTERIM_TRANSCRIPT` / `FINAL_TRANSCRIPT` with `alternatives[].text`.
The installed Deepgram sender uses `aiohttp.ClientWebSocketResponse.send_bytes`
for PCM; diagnostic instrumentation counts bytes after actual completed sends,
not just frames queued in the plugin. No audio is saved to disk. Streams are
finished with `end_input()` and closed with `aclose()`.

## LLM and complete English voice proof

Gemini `gemini-3.8-flash` previously returned HTTP 504 and repeated provider-side
HTTP 503 high-demand responses. Two independent retries in this continuation
also returned 504. Following the user's deadline instruction, it was replaced
by one Groq primary; Gemini is not a Phase 0 requirement or automatic fallback.

Live text harness PASS with Groq GPT-OSS 20B: model-selected call, actual async
tool execution, matching tool result submitted, final response
"The demo environment is ready." Offline protocol/rejection/wrapper checks pass.

Complete English voice PASS with actual microphone, Deepgram, Groq, tool,
Sarvam and speakers. After the async-tool fix, observed:

- Final transcript: "Please check whether the demo is ready."
- `get_demo_status` completed with `is_error=False` and the exact ready-status dict.
- Sarvam `bulbul:v3` / `shubh` WebSocket synthesis completed successfully.
- The user confirmed: "Yes, I heard the readiness confirmation."

The earlier audible response to a failed synchronous tool was not counted as
success. The corrected run above is the passing evidence.

## Kannada STT result

Tested only after the complete English loop passed. Same input device,
`deepgram.STT(model="nova-3", language="kn")`, no LLM/TTS or Kannada agent logic.
The user confirmed speaking "ಇವತ್ತು ಏನು ಮಾಡಬೇಕು?".

- First interim: `ಇವತ್ತು ಏನು ಮಾಡಬೇಕು`; first final: `ಇವತ್ತಿನ ಮಾಡಬೇಕು` (inaccurate).
- Repeated utterance final: `ಇವತ್ತೇನು ಮಾಡಬೇಕು` (contracted wording preserving the
  intended phrase; punctuation omitted).
- Capture finished successfully: 1200 frames / 3,840,000 PCM bytes sent, VAD
  speech detected, final transcripts returned. This establishes basic Kannada
  STT on repeat, with an observed first-attempt accuracy limitation.

Kannada is monolingual `kn`; do not assume `multi` includes it. A future Kannada
voice session should use VAD/endpointing because the audio turn detector's
supported language list excludes Kannada. No such session is implemented here.

## Reproduce

From `anyaa-voice` in PowerShell, with keys saved locally:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe harness.py --self-test
.\.venv\Scripts\python.exe harness.py
.\.venv\Scripts\python.exe audio_spike.py --device 1 --seconds 60
.\.venv\Scripts\python.exe main.py console --input-device 1 --output-device 3 --log-level info
# Stop the English console before the Kannada-only check.
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe audio_spike.py --device 1 --language kn --seconds 60
```

Device IDs are local to this Windows configuration; enumerate another machine's
choices with `main.py console --list-devices`. Pause background playback and wait
for the listening message before speaking. Diagnostic exit 0 means at least one
final transcript, not an automatic accuracy judgment. Test processes and their
microphone streams were stopped after verification.

## Official references

- [LiveKit Groq plugin](https://docs.livekit.io/agents/models/llm/groq/)
- [Groq GPT-OSS 20B and tool support](https://console.groq.com/docs/model/openai/gpt-oss-20b)
- [Groq free-plan limits](https://console.groq.com/docs/rate-limits)
- [LiveKit voice startup](https://docs.livekit.io/agents/start/voice-ai/)
- [Deepgram plugin](https://docs.livekit.io/agents/models/stt/deepgram/)
- [Sarvam Bulbul configuration](https://docs.livekit.io/agents/models/tts/sarvam/)
- [Audio turn detector](https://docs.livekit.io/agents/logic/turns/turn-detector/)
- [Nova-3 Kannada support](https://developers.deepgram.com/changelog/2026/1/21)

Phase 0: PASS, with Kannada accuracy and unreconstructed historical timeout
limitations documented above. Phase 1 has not been started.
