# 2026-05-22 — Re-picking `main` and `deep` on tool-use data (tau2), not just intelligence

## Context / where this started

The cloud routes were chosen before the price-vs-quality tracker existed and on
the AA **Intelligence Index** alone. Two suspicions had accumulated:

- `main` = **GPT-5-mini** (AA-II 41.2, blended $0.69) looked beaten on *both* price
  and intelligence by cheaper models.
- `deep` = **GPT-5** (AA-II 44.6) was scoring *below* its own `deep-fallback`,
  gemini-3.1-pro (AA-II 57.2).

But the every-turn `main` driver lives or dies on **tool-use reliability**, which
the Intelligence Index does not measure. So the rule was: get a tool-use data point
*first*, then re-pick. This entry is both.

## The new data point (tool value in the CSV)

Artificial Analysis's API exposes more `evaluations` keys than we were recording.
Added all the newly-surfaced ones to the scores snapshot (`aime_25`, `ifbench`,
`lcr`, `terminalbench_hard`, `tau2`) and surfaced two as columns in the derived
`price-vs-quality.csv`:

- **`aa_tau2`** — τ²-Bench (Telecom): tool-use / function-calling reliability. *This
  is the "tool value."*
- **`aa_terminalbench_hard`** — agentic terminal-task completion.

Pipeline-level change, not a hand-edited cell: `price-vs-quality.csv` is generated,
so a manually-added column would be wiped on the next `join`. Touched
`pricing/sources/artificialanalysis.py` (EVAL_KEYS) + `pricing/join.py`
(`_QUALITY_COLUMNS`, JOIN_FIELDNAMES, `build_price_vs_quality`), added a join test,
re-ran `fetch-scores` + `join`. 22 tests green; 5674 score rows, 358 joined.

## The lesson that nearly went into the record wrong

First read of a hand-built comparison table had the columns **misaligned** (an empty
price cell shifted everything left), so I initially concluded tau2 *vindicated*
keeping gpt-5-mini — "best tool-use in class, 0.684 vs deepseek's 0.356." Wrong. Read
straight from the regenerated CSV, deepseek-v4-flash's tau2 is **0.950**, not 0.356;
gpt-5-mini's 0.684 is the *low* one. The conclusion flipped completely. The fix was
to stop trusting the ad-hoc print and query the committed CSV. Verify against the
artifact, not a transcription of it.

## What we decided and why

The corrected numbers (all from the committed `price-vs-quality.csv`):

| route | model | blended $ | AA-II | tau2 | termBench |
|---|---|---|---|---|---|
| old `main` | gpt-5-mini | 0.69 | 41.2 | 0.684 | 0.333 |
| **new `main`** | **deepseek-v4-flash** | **0.14** | 46.5 | **0.950** | 0.356 |
| old `deep` | gpt-5 | 3.44 | 44.6 | 0.848 | 0.326 |
| **new `deep`** | **gemini-3.1-pro** | 4.50 | 57.2 | 0.956 | 0.538 |

- **`main` = DeepSeek V4 Flash (`openrouter/deepseek/deepseek-v4-flash`).** Dominates
  the old `main` on *all three measured axes* — ~5× cheaper, higher intelligence, and
  ~1.4× the tool-use reliability. Side benefit: it sidesteps the GPT-5 `max_tokens<16`
  silent `main → private` fallback trap that bit us earlier (that quirk is GPT-5-family
  only). 1M context, so the `compression` aux task that routes to `main` is well clear
  of Hermes' 64k summary-window floor.
- **`deep` = Gemini 3.1 Pro (`openrouter/google/gemini-3.1-pro-preview`).** Promoted
  from `deep-fallback`. Old `deep` (GPT-5) was dominated on every axis; Gemini 3.1 Pro
  is the best intelligence/tool-use/price balance of the deep tier (AA-II 57.2, tau2
  0.956) and was already the trusted fallback.
- **`deep-fallback` = GPT-5 (`openrouter/openai/gpt-5`), demoted from primary.** The
  fallback must be a *different vendor* from `deep`; promoting Gemini to `deep` freed
  the GPT-5 slot. GPT-5 is weaker but **first-party-served (OpenAI + Azure)** = high
  availability, which is exactly what outage insurance wants. Clean rotation, no new
  vendor to validate.
- **Budgets unchanged:** `main` $50 / `deep` $35 / `deep-fallback` $15 = ≤$100 (~€92).

## What we rejected / considered

- **Keep gpt-5-mini as `main` for now.** Reputation + known-good live behaviour, but
  dominated on the data. Held only the latency caveat (below), not a reason to keep it.
- **`main` = qwen3.6-35b-a3b** ($0.36, tau2 0.953): comparable tool-use, A3B = fast,
  but pricier than deepseek-v4-flash with lower intelligence. Close second.
- **`deep` = gpt-5.5** (AA-II 60.2, termBench 0.606 — both class-leading): the strongest
  model, but $11.25 blended (2.5× Gemini) and *lower* tau2 (0.939). For rare, capped
  deep use the extra intelligence didn't justify 2.5× the cost at lower tool-use.
- **`deep` = grok-4.3** ($1.56, tau2 0.977 — best tool-use of the tier): cheapest deep
  option with top tool-use, but lower raw intelligence (AA-II 53.2). A strong value pick;
  Gemini won on the intelligence axis that `deep` exists to maximise.

## State / pending

- Config edited in `nas/litellm-config.yaml`; docs synced (`README.md`, `docs/runbook.md`
  route table + cost-control + context-length declarations + the silent-fallback note).
  **Not yet deployed:** needs `docker compose --env-file .env restart litellm` on the NAS,
  then a gateway round-trip per route to verify live.
- **Open caveat — latency.** tau2 covers tool-use *reliability*, not *speed*, and `main`
  runs every turn. Before locking deepseek-v4-flash in, time a real Hermes turn against
  it; if it's materially slower than gpt-5-mini was, reconsider (qwen3.6-35b-a3b is the
  fast A3B alternative). tau2 is also τ²-Bench *Telecom* specifically — a domain proxy,
  not a guarantee on our workloads.
- Hermes `~/.hermes/config.yaml` `models:` context_length map needs the new values
  (`main`/`deep` → 1048576) and a `context_length_cache.yaml` blank to drop stale probes.
