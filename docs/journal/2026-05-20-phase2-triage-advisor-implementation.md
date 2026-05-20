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
- **Verified in Hermes (Task 7):** **manual invocation works end-to-end** — asked to triage a German-AI-market research task, Hermes ran the deployed script (creds via config-fallback, no env vars) and returned a correct `deep` recommendation with good reasoning ("synthesize complex regulatory/market data to draw non-obvious conclusions"). The full chain (skill load → script → judge → recommend) works.
- **Auto-nudge: did NOT fire, twice — but neither test was clean.** (1) The "MCP market overview" turn loaded `native-mcp` + web search instead. (2) The German-market turn went straight to web search — but the user was *already on `deep`*, and Hermes' stated reason was sound: already on the recommended model, nothing to triage. So the nudge has never had a fair test (one out-competed by a specific skill, one pre-set to deep). Underlying reality, exactly as predicted at design time: **auto-triggering is probabilistic — Hermes tends to just-do-the-task — which is why the design makes manual the reliable spine and the nudge a backstop.**
- **Trigger strengthened (this session):** rewrote the `SKILL.md` description to be imperative ("Use BEFORE starting … research; run it FIRST, before any web search") and dropped the muddled "current/web information" clause (the Bug 2 concept, in its propose-role), reframing on heavy-reasoning/synthesis. Added a body line: run at the *start* of a qualifying task. Redeployed.
- **Still to do:** a **clean** nudge test — fresh Hermes session defaulting to `local` (not deep), a research prompt, no mention of the advisor — to see if it now proposes itself before researching. And confirm a routine turn does *not* trigger it. Honest expectation: skill auto-triggering can be improved but never guaranteed; the manual path is the dependable one and is confirmed working.
