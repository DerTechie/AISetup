# 2026-05-20 — Phase 2 design: the triage advisor skill

## Context / where this started

Phase 1 (routing) is done and validated. The next phase is the **triage advisor** the design always anticipated: a Hermes skill where the **local** model judges "deep or local, and why?" and recommends, with me confirming. The spec described the *what*; this entry records the design decisions and the **why** behind the trickier ones.

## What we decided and why

- **Hybrid trigger: manual spine + a fact-triggered nudge.** The interesting decision. Pure "agent proposes it when the task looks heavy" has a **bootstrapping problem** — deciding whether a task is heavy enough to consider cloud is *the same hard judgment we're trying to offload*. If the local model could reliably make that call unprompted, we wouldn't need the advisor. A fuzzy "feels hard → propose" reflex fails both ways: nags on trivial turns, or stays silent on exactly the heavy task that then goes local and gives the dumb answer we fear — which is the very thing §5 rejected ("guessing difficulty from raw prompt text"). So: *manual* is the primary path (opt-in, no latency on normal turns, doesn't get in the way because it only runs when reached for), and the *auto-propose backstop fires only on concrete signals* — large context, or explicit market/strategic/current-info research. Those are facts/keywords, not a vibe, so the agent can do them reliably. This reuses the design's existing philosophy: route on capability facts, not difficulty guesses. Mechanically, the nudge is just the Hermes skill's **description** naming those conditions — the same way skills already auto-trigger.

- **Recommend-only, never auto-switch.** Kept from the spec. The skill prints a recommendation + reason; I run `/model deep` (or not). Auto-routing is a later, *earned* change once the log shows its judgment is trustworthy. Trigger axis (when it runs) and action axis (what it does with the answer) are deliberately kept independent.

- **The judge runs on `local` (no-think) to start, revisit empirically.** The recommendation is a small structured classification, so a tight rubric on the fast no-think route should suffice — and keeping it fast is the whole point of a low-friction advisor. If the dashboard later shows poor recommendations on hard cases, switch the advisor to `local-think`. Measure-then-decide, matching the project style.

- **The skill calls the gateway's `local` route over HTTP directly** — not via any Hermes-internal "skill invokes a model" mechanism. This **resolves the open §12 item by sidestepping it**: no dependence on undocumented Hermes internals, and it's engine-agnostic, so it survives the Phase 4 Ollama→MLX swap unchanged. The judge call also shows up in the dashboard as a `local` request, giving observability for free.

- **A small local recommendation log for trust-building.** The skill appends one line per call (timestamp, task gist, recommendation, reason) so I can later eyeball "was it right?" before ever considering auto-routing. Directly serves the "observe its judgment before automating" intent from the initial design.

## What we rejected / deferred

- **Pure agent-proposes triggering** — the bootstrapping problem above.
- **Every-turn pre-hook** — adds local-judge latency to every single turn, fighting the latency we're already managing (and Phase 4 is about reducing).
- **Auto-routing now** — premature; it must be earned via the log.
- **`local-think` for the judge now** — only if no-think proves weak.

## Open questions (carry into the Phase 2 plan)

- Exact log location and format on the Arch box.
- How the skill surfaces the recommendation in Hermes' UI (inline text vs a structured prompt).
- Confirming the fact-triggered description actually makes Hermes propose the skill at the right times (validate empirically once installed).
