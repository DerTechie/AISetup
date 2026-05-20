# 2026-05-20 — Phase 2 implementation: building & debugging the triage advisor

## What was built

The triage advisor skill, per its design entry and plan. A stdlib-only Python script (`hermes/skills/triage-advisor/triage_advisor.py`) that calls the gateway's `local` route over HTTP with a routing rubric, parses the JSON answer (falling back to `unknown` on bad output), logs each call to `~/.hermes/triage-advisor.jsonl`, and prints a recommend-only message. Plus `SKILL.md` with the hybrid (manual + fact-keyed) trigger. Built subagent-driven with TDD: 13 unit tests, two-stage review per task.

Two issues were caught in review/verification — both worth recording, because they're the interesting part.

## Bug 1 (caught in review): recommending a command that doesn't exist

The final cross-file review found the script told the user to run `/model fast` to return to local — but the `local`/`local-think` split (commit `b554443`) had already replaced the `fast` alias with explicit `local`. The recommendation pointed at a command that wouldn't work. Root cause: my Phase 2 *plan* carried the stale `fast` alias from the Phase 1 plan. Fixed to `/model local` across script, test, SKILL.md, and the plan. Lesson: when a later phase reuses an earlier phase's interface details, re-check them against the *runbook* (operational truth), not the older plan.

## Bug 2 (caught in live testing): the judge over-routed routine work to cloud

The more interesting one, and a good talk beat. The first live run on the no-think `local` judge:

| Task | Before fix | Reason it gave |
|---|---|---|
| "Summarize today's unread emails into 3 bullets" | **deep** ❌ | "Accessing unread emails requires web integration and external data retrieval beyond a local model's scope." |
| "Analyze my SaaS's competitive positioning vs 3 rivals using current market data" | deep ✅ | "requires current market data ... web access and advanced reasoning." |

Email summarization is *the* flagship LOCAL task in this project — getting it routed to cloud is exactly the failure the whole design tries to avoid. I ran systematic debugging rather than reflexively switching to `local-think`.

**Root cause:** not model depth — the **rubric**. It listed "tasks needing current/web information" as a DEEP criterion and never said tool/data access is the agent's job. So the model committed a category error: *"the task touches external data → needs the cloud model."* But Hermes' tooling fetches emails/web/files regardless of which model reasons over the text. Model choice should depend only on **reasoning difficulty + context size** (and context size is already auto-handled by LiteLLM's `context_window_fallbacks`). The rubric had drifted from the spec, whose LOCAL examples (email triage, summaries, drafting) all touch external data yet are explicitly local.

**Why this matters:** a smarter model (`local-think`) given the same misleading instruction would likely make the same error. The fix had to be the instruction, not the horsepower.

**Fix:** reframe the rubric — state up front that the agent handles all data/tool access, and route ONLY on reasoning difficulty (routine triage/summarize/draft/extract/classify = local, *even on external data*; heavy multi-step reasoning/synthesis = deep). One variable changed; `JUDGE_MODEL` kept at `local` (no-think).

**Verified live (after fix, still no-think):**

| Task | After fix | Reason it gave |
|---|---|---|
| Email summary | **local** ✅ | "standard extraction task ... does not require complex multi-step reasoning" |
| Competitive analysis | **deep** ✅ | "synthesizing multiple external data sources to draw non-obvious strategic conclusions" |

The heavy task's reasoning shifted from "web access" to "synthesis / non-obvious conclusions" — the judge is now reasoning on the right axis. And it works on the fast no-think route, so we keep the latency win. The "switch to local-think" lever stays in reserve, unused.

## State / what's still pending

- **Verified:** script end-to-end against the live gateway (reachable, JSON parsed, logged), and judge quality on the two canonical cases (plan Task 5).
- **Pending (Hermes-side, plan Task 7):** deploy the *fixed* script to `~/.hermes/skills/triage-advisor/`, confirm Hermes loads the skill, manual invocation works through Hermes, the fact-keyed nudge fires on a research task, and a routine turn does **not** trigger it.
- **Possible follow-up:** `SKILL.md`'s trigger description still lists "needs current/web information" as a propose-signal — same muddled concept as Bug 2, but in the lower-stakes *proposing* role (not the decision). Worth reconsidering during the Task 7 nudge checks; left unchanged for now to avoid bundling an unverified change.
