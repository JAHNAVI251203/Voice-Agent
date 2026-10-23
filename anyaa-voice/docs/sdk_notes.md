# SDK notes and verification history

Current delivery: two agent surfaces with shared JSON state, offline planner/cook
verification, and direct Mistral configuration. Final live checkout and cook
voice remain pending due to free-provider availability. The checkpoints below
are chronological; earlier provider/phase descriptions are historical.

## Historical Phase 0 SDK spike — verified results

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

Historical Phase 0 checkpoint: PASS, with Kannada accuracy and unreconstructed
historical timeout limitations documented above. Later phase results follow below.

## Phase 3 integration facts

The Phase 0 spike remains available as `harness.py` and `harness.py --self-test`.
Planner verification uses `harness.py --planner` with an isolated temporary state.
No provider/model change: shared `create_llm()` still uses Groq
`openai/gpt-oss-20b`, `reasoning_effort="low"`, `temperature=0`; `parallel_tool_calls=False` keeps
dependent state edits sequential. The constructor parameter was verified in the
installed `livekit/plugins/groq/services.py`.

Installed LiveKit 1.8.8 source confirms:

- `Agent.on_user_turn_completed(turn_ctx, new_message)` runs before the reply;
  `new_message.id` and `new_message.text_content` identify the committed user turn.
- `Agent` discovers async methods decorated with `@function_tool` automatically.
- `RunContext` is injected by LiveKit and excluded from the model's tool schema.
  It exposes `speech_handle.interrupted`, `session.current_speech`, and
  `session.history`. New speaking activity uses public `user_state_changed` events.
- `llm.utils.validated_arguments` validates tool arguments; the text harness uses
  it and executes the same async wrappers with a real `RunContext`/`SpeechHandle`.
- Groq rejects unrestricted nested object schemas and repeatedly generated invalid
  nested ingredient arguments during integration. The planner save adapter accepts
  one `meal_json` string, parses it with stdlib JSON, and delegates all ingredient,
  restriction and mutation validation to the unchanged shared save tool. No meal
  or tool result is hardcoded, and invalid JSON cannot mutate state.
- The actual demo phrase `Yes, place it.` needs punctuation normalization in the
  shared affirmative allowlist. Only matching is normalized; the exact original
  user transcript is passed into the shared placement function. Hindi affirmatives
  also accept danda punctuation. Older Phase 2 tests are retained unchanged.
  Real voice testing also produced `Okay. Go ahead.` and `Okay. Place the order.`;
  these exact whole-phrase affirmatives are explicitly allowlisted. Bare `okay`,
  `maybe okay go ahead` and `okay add chicken` remain insufficient. The original
  compound transcript is passed through unchanged, without fuzzy intent matching.

The application requires a preview from an earlier relevant turn, a matching
current-user message ID, and a non-interrupted current response. A new request
clears pending confirmation; ambiguous `okay` asks again. The model cannot supply
`user_quote`. Reconnection requires a fresh preview instead of trusting saved
confirmation context. No live conversation or provider success is implied by the
offline confirmation checks.

Real console testing exposed a LiveKit preemptive-reply integration hazard:
`agent_activity.py` reconciles a speculative message's content/confidence/metrics
with the final message but retains its earlier ID, and can reuse a generation
started before the completed-turn hook updates application state. The planner
therefore sets public `TurnHandlingOptions(preemptive_generation={"enabled": False})`.
Installed `voice/turn.py` verifies this option. `endpointing={"min_delay": 1.0}`
allows more time for the final STT segments after an observed late-transcript
warning. Deepgram/Groq/Sarvam models, microphone and speaker remain the same.

The Groq free plan reported an 8,000 TPM limit during the scripted run. That test
paces sequential requests from actual token usage below that limit; there is no
artificial delay in the voice agent. Planner previews refresh grocery gaps before
building the cart, because shared meal/pantry mutations intentionally clear that cache.

## Phase 3 verification checkpoint — 2026-10-23

Status: PARTIAL. Phase 4 has not been started.

- The live `harness.py --planner` completed the full scripted flow before voice
  testing: draft save, move without duplication, grocery gaps, fresh simulated
  preview, exploration without placement, ambiguous acknowledgment, exact latest
  `Yes, place it.` passed to placement, and exactly one simulated order. The
  prompt-disclosure probe was refused. This was an earlier live run, not a rerun
  of the final voice-related changes.
- Real microphone/speaker testing produced a saved draft and audible meal plan.
  The user also heard the cart preview and the explicit clarification question.
  Some responses arrived late, with pauses between complete phrases.
- Voice checkout has NOT passed. Although an earlier user report described hearing
  a simulated-order confirmation, the inspected runtime history contains no
  `simulated_order_placed` event. The latest user report says the agent requested
  another confirmation or preview. Audible wording alone is not proof of placement.
- A traced voice attempt committed `Okay. Go ahead.`. The speech/message checks
  matched, but the original whole-phrase allowlist rejected this compound phrase.
  The explicit phrase is now accepted without changing the original transcript
  sent to the placement tool. This correction passes offline tests; its live
  voice result remains unverified. Bare `okay` and exploratory phrases still fail.
- Subsequent console requests failed with provider HTTP 429: the Groq response
  reported a 200,000 tokens/day limit with approximately 199,100 used, leaving
  insufficient capacity for the requested completion. Earlier requests also hit
  the 8,000 tokens/minute limit. Do not keep retrying or change providers to hide
  this verification blocker. A full final text rerun and clean voice checkout
  remain required when adequate quota is available.
- The owned microphone console was stopped after background playback was being
  transcribed. Resume with background audio paused, one console only, a new
  preview, and a separate explicit confirmation after playback finishes.
- Final local checks: `python -m unittest discover -s tests -q` passed all 76
  tests (64 original tests plus 12 planner tests); `harness.py --self-test` passed;
  compilation and `git diff --check` passed. Protected state/schema/simulator,
  seed/catalog data, original tests, requirements and env-example hashes match
  their pre-Phase-3 values. Shared confirmation matching is the intentional
  integration fix.
- Temporary confirmation diagnostics remain for the pending live check. The
  requested Ponytail simplification is deferred under the phase instruction
  "Only after everything works"; no completion or post-simplification live
  verification is claimed. No commit or push was performed.

Resume commands from `anyaa-voice`, sequentially (not concurrently):

```powershell
.\.venv\Scripts\python.exe -X utf8 harness.py --planner
.\.venv\Scripts\python.exe -X utf8 main.py console --input-device 1 --output-device 3 --log-level info
```

Verify a successful placement tool result, consumed confirmation, one persisted
`simulated_order_placed` event, and the user's audible confirmation before
marking the voice flow passed. Then perform the requested changed-files-only
simplification and rerun verification. Phase 4 is not yet cleared to start.

## Phase 3 Gemini-only retry — 2026-10-23

The user's next instruction authorizes replacing the exhausted Groq path only.
Current primary configuration is now `gemini-3.8-flash` through the already
installed `livekit-plugins-google==1.8.8`; it is configured but NOT verified for
the full planner flow. No automatic fallback or provider hierarchy was added.
Groq is no longer required by either entry point. Its existing dependency/key
placeholder remains available for later experiments.

Inspected installed `livekit/plugins/google/llm.py` before use. Its constructor
accepts model strings, `thinking_config`, and `http_options`; it reads
`GOOGLE_API_KEY`, preserves tool-call thought signatures, and filters thought
parts from spoken text. The plugin otherwise defaults Gemini 3 Flash thinking
to `minimal`. The live 3.8 API rejected that level with HTTP 400. Explicit
`thinking_level="low"`, `include_thoughts=False` fixes that argument mismatch.
Google's model documentation confirms low/medium/high and rejects minimal.

Bounded smoke attempts used the unchanged existing `run_harness(model)` tool
round trip, with `APIConnectOptions(max_retry=1, timeout=30)` and Google
`HttpOptions(timeout=30000, retry_options=HttpRetryOptions(attempts=1))` to avoid
nested retries. No keys were printed or changed.

- `gemini-3.8-flash` with low thinking returned a valid, argument-free
  `get_demo_status` call. The harness executed it successfully. Generating the
  answer from that result returned HTTP 504, then HTTP 503 high demand. The
  complete round trip therefore FAILED; a valid initial call is not a pass.
- Tested exactly one alternative, the requested `gemini-3.7-flash`, with low
  thinking. It failed after two attempts, ending in HTTP 504. Google's current
  deprecation notice says this endpoint routes to 3.8; it is not an independent
  capacity fallback. No third model was tried.
- The full Gemini planner script and voice checkout were NOT run because the
  prerequisite smoke round trip failed. No voice order was created in this run.
  Exact latest transcript and exactly-once protections remain covered offline;
  their Gemini live checkout proof is still pending.
- All 76 tests pass after the provider-only change; `pip check` reports no
  broken requirements. Planner/tools/state/prompts and voice turn/device wiring
  were not changed in this retry. Only the LLM factory, key checks, harness
  provider selection/pacing, requirements, env example and these notes changed.

Status remains PARTIAL: provider availability blocks live verification. Phase 4
is not cleared or started. The requested final simplification remains deferred
until the phase works; no additional provider calls are justified in this run.

Official sources checked:
- [3.8 model, function calling and thinking levels](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)
- [3.7 deprecation and routing](https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash)
- [Gemini API free-tier pricing](https://ai.google.dev/gemini-api/docs/pricing)

## Phase 3 Mistral setup — 2026-10-23

User authorized an LLM-only move to Mistral, with no more Gemini retries.
Verified the official LiveKit API reference before changing application code:
package `livekit-plugins-mistralai`, import `from livekit.plugins import mistralai`,
constructor `mistralai.LLM(model=..., api_mode="chat_completions", temperature=...)`.
`MISTRAL_API_KEY` is read from the environment. Chat Completions supports function
tools and is documented as lower latency than the default Conversations API.
The matching 1.8.8 plugin is being installed; installed source will be checked
before selecting the final model. No model access or live success is claimed yet.

Installation completed: `livekit-plugins-mistralai==1.8.8`, with its required
`mistralai==2.10.1`. Installed `mistralai/llm.py` and `__init__.py` confirm the
documented import, constructor parameters, Chat Completions enum, environment key,
and `client.models.list_async()` availability. Existing LiveKit packages were
not upgraded. The application provider/model change awaits authenticated model
availability; the key was absent at the initial check.

Mistral's official setup guide documents Free mode with limited API usage and no
credit card required. Account model availability and function-calling capability
must be checked with the user's key before a model is made primary.

References:
- [LiveKit Mistral API and Chat Completions](https://docs.livekit.io/reference/python/livekit/plugins/mistralai/)
- [Official plugin package registration](https://github.com/livekit/agents/blob/main/livekit-agents/pyproject.toml)
- [Mistral Free mode and API key setup](https://docs.mistral.ai/getting-started/quickstarts/studio/activate-and-generate-api-key)

Authenticated `GET https://api.mistral.ai/v1/models` succeeded with the saved key.
Selected ONE model, exact version `mistral-small-2603` (Mistral Small 4), returned
with `completion_chat=true`, `function_calling=true`, max context 262144 and alias
`mistral-small-latest`. The official model card confirms Chat Completions and
function calling. This pins the verified version instead of a moving alias.
Configuration: `mistralai.LLM(model="mistral-small-2603",
api_mode="chat_completions", temperature=0)`. No reasoning text is requested.
Installed plugin source confirms strict OpenAI-format function schemas, matching
tool-call IDs, streaming via the direct Mistral SDK `client.chat.stream_async`,
and conversion of LiveKit timeouts to `timeout_ms`. Only this model will be used
for the smoke, planner harness and final checkout.

Mistral verification checkpoint:
- The model-list request succeeded, proving authentication and advertised model
  access, but does not prove chat-generation allowance.
- The existing simple `harness.py` failed on its first generation request with
  HTTP 429, code `1300`, `type="rate_limited"`. LiveKit's default three retries
  also failed. No tool was executed and no final answer was generated.
- After a cooldown, one final attempt used the same harness round trip and
  `APIConnectOptions(max_retry=0, timeout=30)`. It failed with the same HTTP 429.
  Inspecting only rate-limit headers from the underlying SDK response showed
  `x-ratelimit-limit-req-minute: 0` and
  `x-ratelimit-remaining-req-minute: 0`. This is a zero chat-request allocation
  for the current workspace/model, not evidence of invalid tool schemas or a
  microphone/confirmation bug. The headers do not explain why allocation is zero.
- Further generation calls are paused pending the user checking Free mode and
  Admin > API > Limits. API keys use their workspace/organization limits;
  creating a new key alone does not establish a new quota.
- Full Mistral planner harness and live checkout have NOT run because the simple
  smoke test has not passed. No SIM order was created by these attempts.
- All 76 tests pass after the provider change. `pip check` passes. No planner,
  prompt, shared tool/state, simulator or confirmation tests were edited.

Account-limit references:
- [Mistral usage and limits](https://docs.mistral.ai/admin/billing-usage/usage-limits)
- [API key workspace scope](https://docs.mistral.ai/admin/identity-access/api-keys)

Follow-up after user supplied Limits screenshots:
- The dashboard shows `mistral-small-2603` at 20,000 tokens/minute and 1 request
  per second in Default Workspace. This conflicts with the API's zero-RPM
  headers; those headers alone do not establish the dashboard's configured limit.
- A secret-free check confirmed no preexisting process-level Mistral key and that
  the effective loaded key exactly matches the local `.env` value. Stale inherited
  credentials are not the explanation in this test.
- One bounded smoke attempt used the same model with `max_completion_tokens=512`
  and `APIConnectOptions(max_retry=0, timeout=30)`. It again failed before any tool
  call with HTTP 429/code1300 and the same zero request-minute headers. A small
  output budget did not resolve it; no application configuration was changed.
- Remaining diagnosis requires checking the key's workspace/organization and
  Subscription's included monthly usage/access status. No specific exhaustion,
  verification requirement or billing cause is proven by the current response.
  The official Subscription guide says included monthly usage is shared across
  Studio/API/Vibe and can stop access when exhausted. Do not assume that is the
  cause without viewing the account's usage. No more generation retries yet.
- [Subscription and included monthly usage](https://docs.mistral.ai/admin/billing-usage/subscriptions)

## Phase 4 — Cook Assistant

The user explicitly accepted Phase 3 as functionally sufficient to continue.
Its final live simulated checkout remains pending because Groq exhausted its
free quota, Gemini returned 503/504, and Mistral returned 429. This acceptance
does not turn the missing checkout evidence into a live pass. Phase 4 makes no
provider retries, paid requests, billing changes or credit purchases.

The cook is a separate `Agent` using the same `StateStore` and six existing
shared tools, without planner inheritance, commerce tools or orchestration.
Menu reads default to today's actual date. Ingredient/attendance operations
require today's planned/confirmed meal; a draft is not silently approved.
Substitutions first record missing pantry stock and a session proposal, then
delegate the confirmed change to the existing atomic restriction-validating
tool. A new relevant explicit confirmation and an unchanged meal are required.
Unrelated speech, uncertainty and interrupted speech cannot reuse eligibility.
Pending ingredient context is retained for a local handoff. No tool/state/schema
or simulator implementation is changed.

Installed SDK checks before voice wiring:
- LiveKit 1.8.8 `voice/turn.py` declares `"vad"` as a supported turn detection
  mode. Cook sessions use it with existing Silero VAD/endpointing; no unsupported
  Kannada semantic detector is constructed. Planner mode retains v1-mini.
- `ChatMessage.transcript_confidence` is optional. A cook transcript below 0.65
  is conservatively treated as uncertain (a demo heuristic, not calibrated Kannada
  accuracy). Missing confidence is not treated as evidence of failure. Empty input
  and the public `user_transcription_timeout` event also trigger clarification.
- `Agent.on_user_turn_completed`, public `user_state_changed` events, and
  `AgentSession.say` support the simple counter/clarification path. A model can
  also report unclear meaning through the existing handoff tool's cook-only
  `misunderstood` argument; no seventh tool or intent framework is introduced.
- First failure asks a repeat, second offers Hindi/English, third calls the real
  local handoff export. A successful understood shared-tool action resets the
  counter. Failed persistence never claims a handoff was saved. Urgent handoff
  latches an emergency-help response and blocks further cooking actions.
- The already verified Deepgram `nova-3` / `language="kn"` configuration is reused.
  Installed Sarvam language literals include `kn-IN`, `hi-IN`, `en-IN`; its public
  constructor accepts `target_language_code`. Bulbul v3/shubh remain unchanged.

Selection is local and explicit: `VOICE_AGENT_MODE=cook` (default remains planner),
`COOK_LANGUAGE=kn` (cook default), `hi` or `en`. The same provider factory and
credentials are used. Kannada is not assumed to work in Deepgram `multi`; changing
the voice STT locale requires a new session. Text instructions mirror Kannada,
Hindi/Hinglish and English; generated multilingual voice quality is unverified.
Both modes use the same default `out/state.json`, sequentially; this remains the
existing single-process state design, not concurrent multi-agent coordination.

`harness.py --cook` is an OFFLINE script: predetermined model choices/replies
exercise real LiveKit argument validation, real cook wrappers and real shared
tools with a temporary confirmed meal for today. It never overwrites the user's
runtime or seed meal date. It is application/tool evidence, not evidence of LLM
language understanding, generated Kannada quality or microphone playback.

Phase 4 verification result: PASS under the user's offline acceptance condition.
- `python -m unittest discover -s tests -q`: 88 passed, comprising the unchanged
  76 prior tests and 12 new cook integration tests.
- `python -X utf8 harness.py --cook`: passed today-menu retrieval, missing-spinach
  stock fact, later confirmed methi substitution preserving 2 bunches, rejection
  of chicken in the shared vegetarian meal, Appa's removal/count 3, history and
  persisted handoff context. All state was isolated in a temporary directory.
- `harness.py --self-test`, compilation and `git diff --check` passed.
- Mode-wiring tests construct mocked kn/hi/en cook sessions and the existing
  English planner session. Cook uses VAD; planner still constructs v1-mini.
  No STT, TTS, LLM requests or model downloads occur in these tests.
- Verified misunderstanding escalation, once per user turn even if the model
  repeats the same tool call, deduplicated third-failure handoff, reset on an
  understood action, pending issue preservation, failed-export behavior, and
  urgent emergency handoff blocking cooking mutations.
- Applied the local Ponytail skill to Phase 4 changed code only after the flow
  passed. Removed a redundant nested meal lookup and duplicate confirmation
  parsing; retained safety checks. Reran the full suite and both offline
  harnesses afterward. Original planner code/tests, shared tools/state/schema,
  simulator and seed/catalog hashes remain unchanged. No dependencies added.
- Kannada/Hindi examples and prompt/tool routing are present. Native wording
  review, actual generated-language quality, and cook mic-to-speaker validation
  remain pending. Phase 0's basic Kannada STT-on-repeat result still applies;
  it is not a claim that this cook conversation was heard successfully.
- Voice is provider-blocked, explicitly non-blocking for this phase. No provider
  was retried, billing enabled, credits purchased, or microphone console started.
  Phase 3 final live checkout is still pending; no new SIM order is claimed.

Reproduce the offline verification from `anyaa-voice`:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -X utf8 harness.py --cook
```

Once a free provider is independently known usable, select a voice surface:

```powershell
$env:VOICE_AGENT_MODE = "cook"
$env:COOK_LANGUAGE = "kn" # use hi or en for a different STT/TTS session locale
.\.venv\Scripts\python.exe -X utf8 main.py console --input-device 1 --output-device 3 --log-level info
# After stopping the cook console, restore the planner selection:
$env:VOICE_AGENT_MODE = "planner"
```

The real runtime is not seeded with a fabricated confirmed meal. If no confirmed
or planned meal exists today, the cook must request household confirmation; the
offline harness sets up its own today fixture. Delivery/polish can proceed under
the user's acceptance; live-demo readiness still depends on provider availability
and the pending voice checks. At this checkpoint, delivery/polish had not started.

## Delivery / polish — 2026-10-23

Created the repository-root README, one-page product write-up and a 4–6 minute
demo script. They separate real tools/state and historically proven voice from
simulated grocery commerce and unconnected WhatsApp/SIP delivery. No WhatsApp
implementation or actual human transfer is implied by the local handoff export.

Added `harness.py --planner-offline` as delivery verification, reusing the exact
existing planner scenario, argument validation, real wrappers and latest-turn
gate. Only model choices/replies are scripted. It uses temporary state, needs
no credentials, and explicitly prints its offline status and actual checkout
evidence (`SIM-`, simulated flag, exact latest quote, one persisted history event).
The cook replay now prints an excerpt from its actual temporary handoff export.
Neither replay seeds or overwrites the user's runtime, and their independent
fixtures are not represented as a continuous live planner-to-cook handoff.

Removed only the temporary confirmation INFO diagnostics/import from the planner;
all guards remain. Corrected the provider module docstring: the Mistral SDK
configuration is verified, but a successful live Mistral completion is not claimed.

The native Kannada-speaking developer reviewed five sample/clarification lines
and supplied corrections during delivery. Applied that wording verbatim (apart
from surrounding whitespace) to scripted menu/proposal examples and the three
short Kannada clarification replies. This resolves review of these lines only;
generated multilingual responses and actual cook voice remain unverified.

Final delivery checks after the Kannada edits and changed-files-only Ponytail
review:
- 89/89 tests pass: the previous 88 plus a no-provider regression check for the
  full offline planner replay. Compilation of application, harness and tests passes.
- `harness.py --planner-offline`, `harness.py --cook`, `harness.py --self-test`
  all pass; `pip check` reports no broken requirements. The planner replay
  records exactly one SIM order and rejects replayed confirmation in temporary
  state. These are offline results, not new live provider evidence.
- Removed unused Groq-specific pacing branches from the already-changed harness
  after confirming there were no callers. No core state/tool/provider behavior
  was refactored. All checks above were rerun afterward.
- Shared tools/state/schema, simulator, seed/catalog and original tool-test
  hashes match the prior checkpoint. The user's actual `out/state.json` hash is
  unchanged; no fake live order or confirmed cook meal was seeded there.
- Secret scan of publishable files and all reachable Git history found no saved
  credential matches or configured credential-pattern matches. The repository
  has one historical commit/eight unique blobs at this checkpoint. No `.env`
  file is tracked or present in history; `.env` is ignored and `.env.example`
  assignments are all blank. Keys are not printed in scan output.
- No live voice/provider calls, billing changes or purchases were made during
  delivery. Free LLM availability and the final live checkout/cook voice remain
  pending, explicitly described in the README and demo script.
- Ready for a prototype review and user-managed commit/push. Nothing was
  committed, pushed, sent to another person or published by the assistant.
