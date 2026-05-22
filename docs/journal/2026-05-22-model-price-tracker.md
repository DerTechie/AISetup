# 2026-05-22 — Building a model price & quality tracker

## Context / where this started

The hybrid routing design commits to a "cheapest equal-quality cloud" principle: when a task genuinely needs cloud reasoning, pick the cheapest model that meets the quality bar — not a fixed model. Honouring that principle requires two things: accurate, dated prices (not remembered impressions) and a quality axis (not just cost). I had neither. Re-hitting the API on every comparison is wasteful and loses history. This entry records the design, the calls made, and what was explicitly considered and rejected.

The output is `pricing/` — a zero-dependency Python package that snapshots OpenRouter prices and Artificial Analysis quality scores into committed CSVs, joins them, and lets git carry the time-series. Numbers at the time of the first snapshot (2026-05-22): **358 OpenRouter models**, **3746 AA score rows** across benchmarks, **137 models** mapped to an AA intelligence index.

## Decisions and why

### CSV over SQLite

The data is tiny (~80 KB). The repo already treats git history as the reconstructable path (see CLAUDE.md). Storing the snapshots as CSV means `git diff` and `git log -p pricing/data` give a human-readable price time-machine with no tooling beyond git. A binary `.sqlite` gives no diff — you'd see "binary files differ" and lose the entire audit trail. SQLite was considered explicitly; the decisive objection was the diff problem, not the size. SQLite is not ruled out forever (it becomes useful if ad-hoc queries grow complex), but it is deferred: CSV covers everything needed now.

### Git as the history layer — full-snapshot overwrite, not an in-file change-log

Each refresh overwrites the CSVs entirely (re-fetch → write → commit). An alternative was an append-only log (one row per model per date, growing over time). Rejected: it requires a date column in every CSV, makes the current-state view require a latest-row filter, and the rows grow unboundedly. The full-snapshot approach is simpler — the CSVs always reflect the current state, and git provides the diff between any two dates. `git log -p pricing/data` is the price time-machine; no extra tooling needed.

### USD as source of truth; no stored EUR

OpenRouter's API returns prices in USD. Storing a EUR conversion would embed a stale FX rate that drifts between snapshots and confuses comparisons across time. EUR conversion is left to ad-hoc analysis (multiply by spot rate at the time). This also keeps the data portable — anyone reading the CSVs gets the source-of-truth figures without having to know what EUR/USD was on a given day.

### Prices and scores as separate files joined by a curated id-map

OpenRouter and Artificial Analysis are independent sources with different update cadences, different model naming conventions, and different failure modes. Keeping them separate means a scores-API outage doesn't block a price refresh, and vice versa. The join key (`model-id-map.csv`) is a hand-curated `openrouter_id,aa_slug` mapping; it lives in its own file so it can be audited, extended over time, and committed as a first-class artefact rather than hidden inside code.

### Precision-first id-map: exact normalized matches only (137 of 358)

The initial map was seeded from exact normalized-name matches. Fuzzy suggestions (`suggest-map` command, cutoff 0.8) were generated and reviewed — and mostly rejected. The problem: at 0.8 cosine similarity, fuzzy matching produced version/size conflations that look plausible but are wrong (e.g. `claude-opus-4` → `claude-opus-4-7`, `qwen3-14b` → `qwen3-5-4b`, `llama-3-70b` → `llama-3-instruct-8b`). Attaching the wrong quality score to a price corrupts the entire price-vs-quality comparison — a 14B score on a 70B entry makes the 70B look cheap-per-quality-point, which is false. **Accuracy beats coverage.** The map grows by hand over time: run `suggest-map`, review suggestions against the actual model specs, add only the ones that are clearly the same model. 137 mapped rows out of 358 is an honest starting point.

### Zero runtime dependencies

The fetch and join code uses only Python stdlib (urllib, csv, json, decimal, difflib). This keeps the package trivially installable and runnable anywhere Python 3.8+ is present — no pip install, no venv activation, just `python3 -m pricing.pricetrack`. The dev-only dependency (`pytest`) lives in `pricing/.venv` and is never imported by the runtime code.

## What was rejected / deferred

- **In-file change-log:** append-only CSVs with a date column. Rejected in favour of git diffs — simpler current-state view, same history, less schema complexity.
- **LMArena as a second score source:** LMArena has useful human-preference data that complements AA's benchmark scores. Not included now because the format is fragile (HTML scraping or undocumented JSON endpoints). Deferred. The schema is already ready for it: `scores-artificialanalysis.csv` is long-format with a `source_model_name` column, and the per-source-file layout (`scores-*.csv`) means a second source drops in without changing the join logic.
- **Automated cron refresh:** prices change infrequently (weeks to months for most models); a cron that commits daily would generate noise. Deliberate manual refresh (run, review the diff, commit) is the right cadence for now.
- **Storing AA's own prices:** AA also publishes price data. Not used — OpenRouter is the price truth because that is the actual routing layer. AA's prices may differ (different negotiated rates, different date) and using them would introduce a second source of truth for the same thing.

## State

- Package live, tests passing (21 tests: csvio 6, openrouter 7, artificialanalysis 3, join 5).
- First snapshot committed in `pricing/data/`.
- The `suggest-map` command is the ongoing tool for growing the id-map as new models appear or as time is available for manual curation.
