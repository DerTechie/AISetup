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

## Bug 3 (caught in Hermes): the skill subprocess had no gateway creds

First real run *inside Hermes* failed instantly: the SKILL.md told Hermes to run `python3 …/triage_advisor.py`, but that subprocess doesn't inherit `LITELLM_BASE_URL`/`LITELLM_MASTER_KEY` — Hermes keeps its gateway creds in `~/.hermes/config.yaml` (`model.base_url` + `model.api_key`), not as env vars. The script's guard correctly refused to run, which is how we spotted it. **Fix:** the script now falls back to reading `model.base_url`/`model.api_key` from `~/.hermes/config.yaml` when the env vars are unset (env still wins; `HERMES_CONFIG_PATH` overrides the path for tests). A minimal stdlib parser scoped to the top-level `model:` block — no PyYAML, since the skill runs under a bare `python3`. Single source of truth, zero extra setup. Verified end-to-end: deployed copy, no env vars, reads config → correct `local` recommendation.

## Bug 4 (caught while verifying Bug 3): read timeouts dumped a traceback

The first deployed run timed out (the gateway was busy serving a parallel Hermes research session) and raised `TimeoutError` — which is **not** a subclass of `urllib.error.URLError`, so the clean-error handler missed it and a traceback leaked. **Fix:** also catch `TimeoutError` in `main()` and report it cleanly (exit 1). Lesson: `socket`/read timeouts are `TimeoutError`/`OSError`, not `URLError`; both paths need handling.

## State / what's still pending

- **Verified:** script end-to-end against the live gateway (env-based *and* config-fallback, no env vars), judge quality on the two canonical cases (plan Task 5), and the deployed copy under `~/.hermes/`. 18 unit tests.
- **Partly verified (Task 7):** Hermes *does* load and select the skill — the failing run was Hermes invoking it, which proves loading + manual triggering. Manual invocation should now fully work post-redeploy.
- **Open observation (Task 7):** on a genuine "market overview of MCP servers" turn, Hermes loaded its `native-mcp` skill + web search and did **not** propose the triage advisor — a more specific skill out-competed the advisor's fact-keyed trigger. Needs a fresh observation now that the skill actually runs; if it recurs, the trigger description (or skill priority) needs tuning. Also still open: confirm a routine turn does *not* trigger the nudge.
- **Possible follow-up:** `SKILL.md`'s trigger description still lists "needs current/web information" as a propose-signal — same muddled concept as Bug 2, but in the lower-stakes *proposing* role. Left unchanged for now to avoid bundling an unverified change.
