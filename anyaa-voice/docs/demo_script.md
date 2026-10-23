# Anyaa Voice — 4–6 minute demo

Target: 4 minutes 40 seconds, with a 20-second buffer. Use the verified offline route while free LLM access remains blocked. It exercises real application tools and persistence; model choices and replies are scripted. Do not present terminal replay as live conversation.

## Before recording

From `anyaa-voice`, run the tests once and prepare two terminal commands:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -X utf8 harness.py --planner-offline
.\.venv\Scripts\python.exe -X utf8 harness.py --cook
```

Open the repository README beside the terminal. Keep keys, `.env`, account pages and unrelated audio off screen. The developer's corrections are applied to the key Kannada sample and clarification lines; this is wording review, not generated speech validation. Do not retry a blocked provider during the demo.

The replays each use an isolated household fixture. Cook mode has a confirmed meal for today; the planner scenario moves a draft to Saturday. The actual two voice surfaces share `out/state.json`, but these replays do not stage an automatic transfer between agents. Dates are computed when run; on a Friday, moving tomorrow's Saturday dinner to Saturday means the following Saturday.

## Opening — 0:00–0:20

“Anyaa Voice adds two conversational surfaces to a shared kitchen workflow: household planning and daily cook execution. Each conversation changes the meal, pantry and history through validated tools, so the next step has context.”

State the verification limit once: **“Final live checkout and cook verification are temporarily limited by free-provider availability; this replay shows the tested tools and state changes.”**

## Household Planner — 0:20–2:00

Run `harness.py --planner-offline` once. Walk through its printed turns rather than typing into a nonexistent interactive prompt.

**Request:**

“Tomorrow my family is having dinner together. Rohan needs high protein, Meera is vegetarian, Appa needs lower-sugar food, and Aarav doesn't eat spicy food. We already have dosa batter and paneer.”

Point to the short proposal and `Saved 1 meals as draft`. The shared vegetarian base accommodates the household; member variants carry separate additions.

**Correction:** “Actually we're eating out tomorrow. Move it to Saturday.”

Point to the unchanged meal ID and `meals=1` after the date changes.

**Groceries/cart:** Show the grocery gap and simulated preview. Spinach is unavailable; methi is suggested but not automatically added. The fixture's first available-items cart is INR 129. “Maybe add chicken too” updates only Rohan's variant and generates a fresh simulated INR 309 preview with an 18–25 minute ETA. These are fixture prices and ETA, not a partner quote.

**Confirmation:** “Okay” asks once more and leaves `orders=0`. “Yes, place it” passes the actual replay turn's exact text into the real gate. Show `SIM-...`, `simulated: true` and `orders=1` in the checkout evidence. The repeated yes leaves the count at one. This proves deterministic application behavior, not a completed live microphone checkout.

## Cook Assistant — 2:00–3:30

Run `harness.py --cook` once and walk through the output.

- **“ಇವತ್ತು ಏನು ಮಾಡಬೇಕು?”** — show today's confirmed menu retrieval, main dish and Rohan's separate chicken variant.
- **“ಪಾಲಕ್ ಇಲ್ಲ.”** — show the missing spinach fact and checked methi stock. The first proposal leaves the meal unchanged.
- **“ಹೌದು, ಮೆಂತ್ಯ ಹಾಕಿ.”** — show the substitution, preserved quantity of 2 bunches, and history update.
- **“Appa aaj raat nahi khayenge.”** — show the Hindi response, Appa removed and headcount changed to three. Portions are not silently recalculated.

The final safety probe deliberately tries chicken in the shared meal; the code rejects it for Meera with no meal mutation. Point out that both surfaces use the existing household data and tool rules. The offline cook responses are scripted examples, not a claim of generated Kannada/Hindi accuracy.

## Handoff — 3:30–4:00

Show the `handoff.json excerpt` printed by the cook harness from its actual temporary export. Highlight the current meal, pending ingredient/substitute and severity after three misunderstandings. The file is removed with the isolated fixture after the replay; a real session writes its export beside runtime state.

“A household member can review the cooking context without starting over. This creates the context locally; WhatsApp and actual human/SIP transfer are not connected.”

## Closing — 4:00–4:40

Show the README architecture diagram and real-vs-simulated table. Summarize the boundary: real voice components, tools, validation and state; simulated grocery commerce; future messaging/telephony.

“The next step is integration with Anyaa's backend and grocery workflow, then broader kitchen-language and latency testing.”

Finish by 5:00. If free LLM access has already been independently verified, a short live interaction may replace a replay segment; keep the same evidence standard and time limit. Do not spend recording time retrying provider errors or claim an order without its successful persisted tool result.
