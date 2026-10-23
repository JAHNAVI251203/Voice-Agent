# Anyaa Voice — Product Write-up

## What I optimized for

- **Realism:** household preferences, pantry shortages, meal corrections and changing attendance reflect everyday kitchen decisions.
- **Product fit:** planning produces a shared meal; cook execution turns it into short, practical instructions.
- **Safe state mutation:** requests must produce validated meal, pantry and history updates, rather than unsupported claims of success.
- **Concise voice UX:** keep replies brief, give one instruction or clarification at a time, and avoid reading long lists aloud.
- **Low complexity:** plain Python, focused tools and shared JSON state keep behavior straightforward to inspect and test.

## Key decisions

- **Two agents, shared state:** planning and cooking need different permissions and conversational styles, with shared household facts and validation rules.
- **Planner vs cook responsibilities:** the planner manages meals, corrections and grocery/cart flow. The cook handles today's meal, ingredient issues, substitutions and attendance.
- **Simulated Instamart:** fixed catalog, stock, prices and ETA make the cart workflow repeatable. Order results are explicitly simulated and use `SIM-` identifiers.
- **Code-enforced confirmation:** a fresh, explicit user confirmation is required after a preview. Cart changes invalidate approval, and consumed confirmations cannot place another order.
- **Hard dietary safeguards:** restrictions apply to shared ingredients and member-specific variants. Substitutions go through the same checks before changing the meal.
- **Multilingual cook experience:** Kannada/Hindi-oriented replies keep daily instructions accessible. Key Kannada wording was manually reviewed; uncertain input prompts clarification instead of guessed ingredients or quantities.
- **Context-preserving handoff:** repeated misunderstanding or an unresolved issue produces a local summary with the meal, recent history and pending action for household review.

## Trade-offs / intentionally left out

- **Real Instamart:** a simulated cart validates the interaction before introducing partner access and transaction dependencies.
- **WhatsApp integration:** message delivery follows once the cooking instructions and update workflow are useful.
- **SIP/telephony:** local handoff context tests what a person needs before connecting a transfer channel.
- **DB/auth:** local persistence suits a single-household prototype; production needs identity, access control and concurrent state handling.
- **Nutrition database:** the prototype respects stored preferences and restrictions without making clinical or nutrient-accuracy claims.
- **Polished frontend:** the core question is whether conversation can produce useful, safe kitchen actions.
- **Complex model routing:** one configured LLM integration keeps the execution path understandable and avoids extra operational complexity.

## What I'd build next

- **Backend integration:** connect Anyaa's household records and meal lifecycle so voice works within the existing product.
- **Real grocery flow:** connect partner catalog, cart review and purchase confirmation.
- **Observability and latency measurement:** track tool outcomes, response delays, corrections and task completion.
- **Stronger multilingual testing:** evaluate Kannada/Hindi understanding, wording and noisy-kitchen audio with representative speakers.
- **Production handoff:** route the saved context to a household member or support channel, adding telephony only when needed.
- **Real user testing:** observe household and cook tasks to refine instructions and permission boundaries around actual mistakes.
