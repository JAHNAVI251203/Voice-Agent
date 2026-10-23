# Anyaa Voice

A prototype voice layer for Anyaa focused on household meal planning and cook-side execution. It makes the existing kitchen workflow conversational, stateful and actionable: requests become meal updates, pantry facts and safe next steps that retain household context.

## Product flows

### Household Planner

- Household preferences and dietary constraints guide pantry-aware planning.
- One shared meal supports member-specific variants.
- Corrections move an existing meal without duplication.
- Grocery gaps lead to a simulated Instamart preview.
- A fresh explicit confirmation is required before simulated ordering.

### Cook Assistant

- Retrieves today's meal and its variants.
- Supports a Kannada/Hindi-oriented, short conversational style.
- Records missing ingredients and applies confirmed, safe substitutions.
- Updates attendance and preserves cooking context for human help.

## Architecture

```text
Voice → Deepgram STT → LiveKit Agent → LLM → Tools / State → Sarvam TTS
```

Two separate surfaces, `HouseholdPlannerAgent` and `CookAssistantAgent`, use the same household, pantry, meal plan, grocery list and history in `anyaa-voice/out/state.json`. Select one agent per session. Handoff produces a local context file.

## Stack

Python 3.13, LiveKit Agents 1.8.8, Deepgram Nova-3 STT, Silero VAD, Sarvam Bulbul v3 TTS, atomic JSON state persistence and a simulated Instamart module. A single function in [`shared/providers.py`](anyaa-voice/shared/providers.py) configures direct Mistral Chat Completions with `mistral-small-2603`; there is no automatic provider routing.

Earlier Groq runs established live tool calling and audible English planning/cart previews. Free-provider quota/availability affected final live checkout and cook verification; the currently configured Mistral path returned HTTP 429. Repeatable offline scenarios exercise the real tools and persistence with scripted model responses. See [verification notes](anyaa-voice/docs/sdk_notes.md).

## What is real vs simulated

| Capability | Implementation |
| --- | --- |
| Microphone/speaker pipeline | Real; English loop previously verified |
| Deepgram transcription | Real; English and basic Kannada tested |
| Sarvam TTS | Real; English playback previously heard |
| LLM tool calling | Real; earlier Groq round trip verified |
| Meal and pantry state | Real local JSON persistence |
| Dietary restriction checks | Real application checks |
| Meal movement | Real ID-preserving state update |
| Grocery gaps | Real aggregation against pantry stock |
| Substitution logic | Real validated meal/pantry updates |
| Confirmation logic | Real latest-turn, stale-cart and duplicate-order guards |
| Handoff context generation | Real local JSON export |
| Instamart catalog, prices and ETA | Simulated fixtures |
| Order placement | Simulated `SIM-` result; no purchase |
| WhatsApp | Not implemented or connected |
| Human/SIP transfer | Not connected; context export only |

## Product decisions

Planning and cooking are different user jobs, so each agent has a focused prompt and tool subset. Shared state carries preferences, corrections and ingredient issues between sessions. Code enforces hard dietary restrictions and order confirmation; a model's confident wording cannot bypass these checks.

The experience is India-native, with Kannada/Hindi cook interaction and household-specific variants, while the state/tool design can serve other cuisines and languages. Replies use concise visible decision factors and keep hidden reasoning and instructions private.

## Safety / reliability

Explicit order confirmation, stale-confirmation invalidation and consumed confirmation IDs prevent unintended or repeated simulated orders. Shared tools check hard restrictions and return structured errors. Ambiguous cooking input triggers clarification, then a context-preserving local handoff after repeated misunderstanding. Severe allergy/emergency guidance directs the user to medical help and stops cooking actions; it does not diagnose or prescribe.

## Multilingual

English/Hinglish planner behavior is designed in the prompt and exercised through household tool scenarios; audible planner verification was in English. The Hindi cook attendance flow is tested with scripted responses and real tool/state assertions. Basic Kannada STT succeeded on a simple phrase on repeat, with first-attempt variability. Key Kannada cook sample lines and clarification replies were reviewed and corrected by the native Kannada-speaking developer. Generated multilingual cook speech and noisy-kitchen coverage remain unverified.

## Known limitations

- No real Instamart, WhatsApp or telephony transfer integration.
- Single-process JSON state, with no authentication or multi-household isolation.
- Final full live checkout and cook voice checks remain limited by free-provider availability.
- Nutrition choices represent preferences and restrictions, not medical advice.
- This is not production-grade multilingual or noisy-kitchen coverage.

## What I would build next

1. Integrate Anyaa's actual backend and household state.
2. Connect a real grocery partner/cart workflow.
3. Add WhatsApp/cook delivery workflows.
4. Add production observability and latency metrics.
5. Broaden Kannada/Hindi and noisy-kitchen testing.
6. Connect human handoff/telephony if the product requires it.

## Run locally

For a fresh clone on Windows PowerShell, with Python 3.13 installed:

```powershell
Set-Location anyaa-voice
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

For voice/live text, fill `MISTRAL_API_KEY`, `DEEPGRAM_API_KEY` and `SARVAM_API_KEY` in the ignored `.env`. Console microphone mode needs no LiveKit room credentials. Earlier Google/Groq placeholders are optional. Offline checks need no keys or provider calls:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -X utf8 harness.py --planner-offline
.\.venv\Scripts\python.exe -X utf8 harness.py --cook
```

Both replays use temporary household fixtures and print actual mutations; they leave your saved runtime unchanged. The cook fixture explicitly sets a confirmed meal for today. Production cook mode asks for household confirmation when today's menu is absent or only draft.

When free LLM access is available, run one voice session at a time:

```powershell
# Planner, English
$env:VOICE_AGENT_MODE = "planner"
.\.venv\Scripts\python.exe -X utf8 main.py console --input-device 1 --output-device 3 --log-level info
# Stop the planner console before starting the cook console.
$env:VOICE_AGENT_MODE = "cook"
$env:COOK_LANGUAGE = "kn" # hi or en selects another STT/TTS session locale
.\.venv\Scripts\python.exe -X utf8 main.py console --input-device 1 --output-device 3 --log-level info
```

Device IDs 1/3 are this developer's Realtek setup. Enumerate another machine with `main.py console --list-devices`. Kannada cook mode uses VAD endpointing; changing the voice locale requires a new session. `harness.py` and `harness.py --planner` are live API checks and consume provider quota.

[Product write-up](anyaa-voice/docs/product_writeup.md) · [4–6 minute demo](anyaa-voice/docs/demo_script.md)
