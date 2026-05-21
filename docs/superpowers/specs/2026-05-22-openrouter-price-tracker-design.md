# 2026-05-22 — Model price tracker (OpenRouter prices + joinable quality scores)

## Problem

To decide *when cloud is worth it* and to honour the "cheapest **equal-quality** cloud"
principle (see memory `displaced-price-no-narrative-protection`), the project needs
**accurate, dated model prices** without re-invoking an API on every question and without
re-checking prices by hand. Prices also move over time ("opens climbed a tier"), so the
*history* matters for the journal/talk narrative.

Prices alone are not enough: ranking by price requires a **quality axis**. OpenRouter's
API gives prices but not quality scores, so scores come from separate benchmark sources
and must be joined to prices by model id.

## Goals

- A local, committed dataset of **current OpenRouter prices** for all models, refreshed on demand.
- **History via git** — the working file stays small; `git log -p` is the time-machine.
- A **joinable quality dataset** from external benchmark sources (Artificial Analysis, LMArena).
- A derived **price-vs-quality** view — the artifact that answers "cheapest equal-quality cloud".
- Zero runtime dependencies (Python stdlib only); faithful to source units; deterministic output.

## Non-goals

- No SQLite/database. Data is tiny (358 models ≈ 80 KB CSV); git carries history. Revisit only
  if we later join against LiteLLM usage logs or many more sources.
- No EUR conversion stored (FX drifts; USD is the API's native unit and the source of truth).
  EUR conversion, if needed, happens in ad-hoc analysis.
- No automated scheduling/cron in this iteration (manual run + commit). Noted as future work.
- No web scraping of rendered pages — only documented APIs / published datasets.

## Empirical grounding (verified 2026-05-22)

- `GET https://openrouter.ai/api/v1/models` → HTTP 200, **358 models**, 426 KB JSON, **no auth**.
  Per-model `pricing` is **USD per token** as strings. Always present: `prompt`, `completion`.
  Sparse: `input_cache_read` (152), `web_search` (88), `input_cache_write` (45), `audio` (22),
  `internal_reasoning` (19), `image` (18). Other useful fields: `id`, `name`, `context_length`,
  `architecture.modality`.
- **Artificial Analysis**: free public API `https://artificialanalysis.ai/api/v2/`, endpoint
  `data/llms/models`, header `x-api-key`, free tier 1000 req/day, **attribution required**.
  Returns `artificial_analysis_intelligence_index`, coding/math indices, MMLU-Pro, GPQA, prices, speed.
- **LMArena**: public HF dataset `lmarena-ai/leaderboard-dataset` (latest/full splits). Accessed via
  the HF **datasets-server REST** endpoint (`https://datasets-server.huggingface.co/rows?...`) to
  avoid the heavy `datasets` library; fallback source `github.com/fboulnois/llm-leaderboard-csv`.

## Architecture

Zero-dependency Python (stdlib `urllib`, `csv`, `json`, `argparse`). New top-level `pricing/`:

```
pricing/
  pricetrack.py             # CLI dispatcher: fetch-prices | fetch-scores | suggest-map | join | all
  sources/
    openrouter.py           # parse_models(json) -> rows   (pure)  + fetch()  (thin HTTP)
    artificialanalysis.py   # parse_aa(json) -> rows        (pure)  + fetch()  (thin HTTP, needs key)
    lmarena.py              # parse_lmarena(json) -> rows    (pure)  + fetch()  (thin HTTP)
  join.py                   # build_price_vs_quality(prices, scores, idmap) -> rows  (pure)
  csvio.py                  # deterministic write (sort + atomic temp-rename), number formatting
  data/
    openrouter-prices.csv         # full current snapshot, committed
    scores-artificialanalysis.csv # long format, committed
    scores-lmarena.csv            # long format, committed
    model-id-map.csv              # curated: openrouter_id -> source names
    price-vs-quality.csv          # derived join, committed
  tests/
    fixtures/                     # saved JSON samples for each source
    test_openrouter.py test_artificialanalysis.py test_lmarena.py test_join.py
  README.md
```

**Design rule — separate fetch from transform.** Each source module splits a pure
`parse_*(data) -> list[dict]` function (unit-tested against a saved fixture) from a thin
`fetch()` that does HTTP. The CLI wires them together. This keeps the data-shaping logic
testable without network and easy to reason about per source.

**Separation of concerns.** Prices, each score source, and the id-map are independent files
with independent cadences. The price fetcher never touches scores. The join step is the only
place the three meet.

## Data flow

```
OpenRouter /models ─ fetch ─> parse_models ─> openrouter-prices.csv ─┐
Artificial Analysis ─ fetch ─> parse_aa ────> scores-artificialanalysis.csv ─┤
LMArena (HF) ─ fetch ─────────> parse_lmarena > scores-lmarena.csv ─┤─ join ─> price-vs-quality.csv
                                              model-id-map.csv ──────┘
```

## CSV schemas

**`openrouter-prices.csv`** — one row per model, sorted by `id`:

```
fetched_date, id, name, context_length, modality,
prompt_usd_per_mtok, completion_usd_per_mtok,
input_cache_read_usd_per_mtok, input_cache_write_usd_per_mtok,
internal_reasoning_usd_per_mtok, audio_usd_per_mtok,
image_usd_per_k, web_search_usd_per_1k, request_usd
```

- Token-priced fields are the API's per-token value **×1e6 → USD per million tokens** (the unit the
  journal already uses, e.g. "€0.04–0.28/M"). Lossless; formatted to a stable precision (≤6 sig-figs,
  no trailing FP noise). Per-request / per-image / per-1k fields kept in their native unit, named accordingly.
- Sparse fields blank when the model lacks them.
- Currency: **USD** (source of truth).

**`scores-<source>.csv`** — long format (one row per model × benchmark), sorted by
`(source_model_name, benchmark)`:

```
fetched_date, source_model_name, benchmark, score
```

Long format absorbs new benchmarks without schema changes and unifies AA's many indices with
LMArena's per-arena scores.

**`model-id-map.csv`** — hand-curated, sorted by `openrouter_id`:

```
openrouter_id, aa_model_name, lmarena_model_name
```

Blank cells mean "no known counterpart". `suggest-map` proposes additions via name fuzzy-match
(stdlib `difflib`) but never auto-writes mappings — a human confirms.

**`price-vs-quality.csv`** — derived join, sorted by `openrouter_id`:

```
openrouter_id, name, prompt_usd_per_mtok, completion_usd_per_mtok,
blended_usd_per_mtok, aa_intelligence_index, lmarena_text_score
```

`blended_usd_per_mtok = (3*prompt + 1*completion) / 4` (3:1 input:output, documented in README;
matches how the journal quotes a single "blended" figure). "Cheapest equal-quality" is then a
sort/filter over this file (e.g. min blended price among rows with intelligence ≥ N).

## Behaviour & error handling

- **Atomic, deterministic writes:** build rows in memory, sort, write to `*.tmp`, `os.replace` over
  the target only on success. A failed fetch never clobbers an existing CSV. Stable sort + fixed
  number formatting → diffs reflect real changes only.
- **AA key:** read from env `ARTIFICIALANALYSIS_API_KEY`. Missing → clear error, non-zero exit, no write.
  Never logged, never committed.
- **Network/HTTP errors:** fail loud with the source and status; non-zero exit.
- **Source shape drift:** parsers assert expected top-level keys; an unexpected shape fails loudly
  rather than writing garbage.
- **Attribution:** AA attribution noted in `pricing/README.md` and the AA source module docstring.

## Testing (TDD)

Each pure `parse_*` and `build_price_vs_quality` is tested against saved fixtures in
`tests/fixtures/` (seeded from real responses, e.g. the already-captured OpenRouter JSON). Tests
cover: sparse-field handling, per-token→per-Mtok conversion, deterministic sort/format, join with
missing map entries, and blended-price math. No network in tests.

## Phasing (build order, all in this iteration)

1. `csvio` + `openrouter` source + CLI `fetch-prices` → `openrouter-prices.csv`. Verify against live data, commit.
2. `artificialanalysis` + `lmarena` sources + CLI `fetch-scores` → score CSVs. Verify, commit.
3. `suggest-map` + curate `model-id-map.csv`.
4. `join` + CLI `join`/`all` → `price-vs-quality.csv`. Verify, commit.

Docs: update `README.md` overview and `docs/runbook.md` (how to run, env var, attribution) in the
same change; add a `docs/journal/` entry capturing the why (CSV-over-SQLite, git-as-history,
USD-as-truth, separate-sources-joined-by-id).

## Open risks

- **id-map upkeep** is manual and the main ongoing cost; fuzzy-match only suggests.
- **LMArena access shape** (HF datasets-server response) is the least-certain source; the parser is
  isolated and fixture-tested so a format change is contained and loud.
