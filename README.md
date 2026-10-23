# Anyaa Voice

A voice layer for household meal planning and cook execution, designed as a product-feature prototype for Anyaa. Both experiences use shared state to keep household context and kitchen actions consistent.

## What it does

- Household meal planning around preferences and dietary constraints.
- Pantry-aware planning.
- Shared meals with member-specific variants.
- Meal corrections and moves.
- Grocery-gap generation.
- Simulated Instamart cart and order flow.
- Cook-side daily meal execution.
- Missing ingredient handling.
- Safe substitutions.
- Attendance and headcount updates.
- Context-preserving human escalation through local handoff summaries.
- Kannada/Hindi-oriented cook interaction.

## Product structure

- Both agents share household, pantry, meal and history state.
- They operate independently and do not talk to each other; there is no multi-agent orchestration.

### HouseholdPlannerAgent

- Plans meals around household preferences and restrictions.
- Manages grocery gaps and the simulated cart/order flow.

### CookAssistantAgent

- Reads today's meal.
- Handles missing ingredients and substitutions.
- Updates attendance.
- Saves escalation context when help is needed.

## Architecture

```text
Voice
  ↓
Deepgram STT
  ↓
LiveKit Agent
  ↓
LLM + Tools
  ↓
Shared State
  ↓
Sarvam TTS
```

- Shared JSON state for household, pantry, meals, groceries and history.

## Stack

- Python
- LiveKit Agents
- Deepgram Nova-3 STT
- Sarvam Bulbul v3 TTS
- LLM tool-calling integration (Mistral adapter currently configured)
- Silero VAD
- JSON state persistence

## Key engineering decisions

- Separate agents for household planning and cook execution: two different user jobs.
- Shared state carries context between experiences.
- Hard dietary restrictions enforced in code.
- Explicit confirmation before simulated ordering.
- Stale confirmations invalidated after relevant changes.
- Consumed confirmations prevent double orders.
- Simulated external integrations clearly labelled.
- Concise replies without exposing hidden reasoning or instructions.
- India-native household and language choices, with a design that can extend to other markets.

## Prototype note

Meal/pantry state, tool calls, restriction checks, confirmations and handoff context are implemented locally. Instamart ordering is simulated; WhatsApp delivery and live human transfer are not connected.

## Multilingual support

- English/Hinglish planner flow.
- Hindi cook flow tested through offline tool/state scenarios.
- Kannada STT and basic offline cook flow tested.
- Key Kannada cook wording manually reviewed.
- Multilingual and noisy-kitchen coverage is prototype-level, not production-grade.

## Tests

- **89/89 tests passing.**
- Covers state mutation, meal movement, dietary restrictions, grocery gaps, substitutions, confirmations and handoff behavior.

## Limitations

- Simulated Instamart; WhatsApp and live human transfer are unconnected.
- Local JSON state instead of a production database, authentication and multi-household isolation.
- Prototype multilingual coverage; full cook voice validation remains pending.
- Food-planning guidance is not medical advice.
- Current build is a prototype, not a production deployment.

## What I'd build next

- Integrate with Anyaa's backend and household state.
- Connect a real grocery partner/cart flow.
- Add WhatsApp and cook delivery workflows.
- Add observability and latency metrics.
- Broaden Kannada/Hindi and noisy-kitchen testing.
- Connect real human handoff/telephony if needed.

## Run locally

Use Python 3.13. From the repository root in PowerShell:

```powershell
Set-Location anyaa-voice
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

- Set the configured LLM key (`MISTRAL_API_KEY` in the current setup), plus `DEEPGRAM_API_KEY` and `SARVAM_API_KEY` in the ignored `.env`.
- Console microphone mode does not require LiveKit room credentials.

Run tests or offline scenarios without API keys:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -X utf8 harness.py --planner-offline
.\.venv\Scripts\python.exe -X utf8 harness.py --cook
```

Start the planner:

```powershell
$env:VOICE_AGENT_MODE = "planner"
.\.venv\Scripts\python.exe -X utf8 main.py console
```

For the cook, stop the current session, then select a language:

```powershell
$env:VOICE_AGENT_MODE = "cook"
$env:COOK_LANGUAGE = "kn" # kn, hi or en
.\.venv\Scripts\python.exe -X utf8 main.py console
```

- List available devices with `main.py console --list-devices` and select your own input/output devices.
- Cook mode requires a planned or confirmed meal for today; offline scenarios provide their own isolated fixtures.

[Product write-up](anyaa-voice/docs/product_writeup.md)
