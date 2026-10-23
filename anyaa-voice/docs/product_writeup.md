# Anyaa Voice — product write-up

Anyaa Voice tests whether household planning and daily kitchen execution can become short, stateful conversations. The useful outcome is a reliable change to the kitchen's shared facts: a saved meal, a corrected date, a missing ingredient, or an updated headcount.

## What I optimized for

**Realism and product fit.** The household includes a vegetarian, a higher-protein preference, a visiting family member with lower-sugar/lower-salt preferences, and a child who prefers mild food. The planner works toward a shared meal with variants, while the cook needs brief instructions about today's meal.

**Clear state mutation and safety.** Responses are backed by tools that update persisted state and history. Hard restrictions and simulated order confirmation are checked in code. Unknown quantities and ambiguous input require clarification. A handoff preserves the meal and pending issue instead of asking a person to start over.

**Low complexity.** Plain Python, one JSON state store and a small set of tools are enough to test these workflows. There is no additional database, conversation framework or automatic model routing.

## Key decisions

- **Two agents, shared state:** planning and cook execution are separate jobs with different permissions and language needs. The planner can edit meals and preview a simulated cart. The cook can report ingredient problems, apply confirmed substitutions and update attendance. Both use the same state and validation functions.
- **Simulated Instamart:** fixed catalog, prices, stock and ETA make grocery behavior repeatable without implying access to a real partner integration. Order results are explicitly marked `simulated=true` and use `SIM-` IDs.
- **Application-owned safeguards:** ordering requires the exact latest relevant transcript after a preview; stale or consumed confirmations fail. Dietary safeguards apply to shared ingredients and member-specific variants. Cook substitutions use the existing restriction checks.
- **Multilingual cook experience:** short Kannada/Hindi-oriented replies, a configurable STT/TTS session locale, and clarification rather than guessing. Basic Kannada STT was tested. The native Kannada-speaking developer corrected key sample and clarification lines; full generated cook voice verification remains pending.

## Trade-offs / what I intentionally left out

Real Instamart, WhatsApp delivery, SIP transfer, production DB/auth, a nutrition database, a frontend and complex model routing do not help establish the initial hypothesis: can a short conversation safely update household work and carry context into cooking? These integrations matter after the interaction earns its place in the existing product.

Free LLM availability limited final live verification. Earlier live runs demonstrated English voice/tool interaction and planner/cart responses. The repeatable delivery demo uses openly labeled scripted model responses with real tool validation and state persistence; it is not evidence of a successful current Mistral voice checkout.

## What I would do next

Connect Anyaa's actual backend/state, validate the grocery partner flow, and design the cook delivery channel. Add production observability and measure latency and task completion. Expand native Kannada/Hindi and noisy-kitchen testing before claiming language reliability. Add a real human/telephony handoff only if the product needs it. Meal preferences remain distinct from medical advice.
