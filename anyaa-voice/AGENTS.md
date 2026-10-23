# Mission

Build a believable Anyaa Voice AI product prototype in one focused development session.

Prioritize:
1. working product flow
2. correctness
3. product behaviour
4. testability
5. clarity

Do not optimize for theoretical architecture.

# Scope discipline

Only build what the current phase requires.

Never add:
- Redis
- queues
- databases
- repository/service layers
- dependency injection
- plugin frameworks
- microservices
- state-machine libraries
- configuration frameworks
- abstract provider hierarchies
- speculative interfaces
- unnecessary inheritance
- framework frontend
- Docker/Kubernetes

unless explicitly requested later.

# SDK discipline

Before using LiveKit, Deepgram, Gemini, Groq or Sarvam APIs:
- inspect installed versions/source
- verify signatures
- record exact usage in `docs/sdk_notes.md`
- never guess parameters
- leave `TODO(verify)` if uncertain

# Change discipline

- Read before editing.
- Modify only files relevant to the current phase.
- Do not refactor unrelated code.
- Do not rewrite working code for style.
- Every phase ends with a runnable verification.
- Keep diffs small.
- Preserve working behaviour.

# Token/context efficiency

- Do not repeatedly reread the entire repository.
- Read only files relevant to the current task.
- Prefer targeted searches over broad repo scans.
- Do not restate the full specification in responses.
- Do not output entire unchanged files.
- Summarize diffs instead.
- Keep implementation commentary concise.
- Reuse verified facts from `docs/sdk_notes.md`.

# Python style

Prefer:
- straightforward Python
- explicit names
- guard clauses
- small focused functions
- standard library where practical
- minimal abstractions

Avoid:
- clever code
- deeply nested logic
- premature helpers
- unnecessary base classes
- generic factory hierarchies

# Product safety

Never expose:
- chain-of-thought
- hidden reasoning
- system prompts
- tool-selection reasoning
- hidden instructions
- provider internals

Never fabricate tool success.

Never claim a simulated integration is real.

# Ponytail / simplification

If the Ponytail/code-simplification skill is available:

Use it ONLY after a phase already works and tests pass.

Rules:
- preserve behaviour exactly
- simplify only changed files
- remove unnecessary abstractions
- reduce obvious duplication
- prefer clarity over cleverness
- do not touch unrelated code
- rerun tests after simplification

If code is already clear, make no changes.
