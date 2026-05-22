# pricing — model price & quality tracker

Snapshots OpenRouter model prices and Artificial Analysis quality scores into
committed CSVs, and joins them into a price-vs-quality view. Git carries the
history — re-run and commit; `git log -p pricing/data` shows how prices moved.

## Usage (run from repo root)

```bash
python3 -m pricing.pricetrack fetch-prices   # -> data/openrouter-prices.csv
python3 -m pricing.pricetrack fetch-scores   # -> data/scores-artificialanalysis.csv (needs API key)
python3 -m pricing.pricetrack suggest-map    # prints id-map suggestions to curate by hand
python3 -m pricing.pricetrack join           # -> data/price-vs-quality.csv
python3 -m pricing.pricetrack all            # fetch-prices + fetch-scores + join
```

## Artificial Analysis API key

`fetch-scores` needs a free key (1000 req/day): create an account at
<https://artificialanalysis.ai> (Insights Platform), generate a key, then:

```bash
export ARTIFICIALANALYSIS_API_KEY=<key>
```

Or copy `pricing/.env.example` to `pricing/.env` (gitignored) and fill it in.

Quality data is from Artificial Analysis (<https://artificialanalysis.ai>) — attribution required.

## Data files

| File | What |
|---|---|
| `data/openrouter-prices.csv` | One row per model. Token prices in **USD per 1M tokens**; `*_usd` columns are OpenRouter's native per-unit (per request/image) values. Currency: USD. |
| `data/scores-artificialanalysis.csv` | Long format: one row per model × benchmark (AA `slug` as the key). |
| `data/model-id-map.csv` | Hand-curated `openrouter_id,aa_slug`. The join key between the two sources. |
| `data/price-vs-quality.csv` | Derived: blended price (`(3*prompt+1*completion)/4`) + AA intelligence index + `aa_tau2` (τ²-Bench tool-use reliability) + `aa_terminalbench_hard` (agentic terminal tasks). |

## Cheapest equal-quality lookup

Sort `price-vs-quality.csv` by `blended_usd_per_mtok` among rows with
`aa_intelligence_index >= N` to find the cheapest model at a quality floor.

For an **every-turn, tool-using** route (e.g. the gateway's `main`), the
Intelligence Index alone is misleading — sort/filter on `aa_tau2` (tool-use /
function-calling reliability) too. The 2026-05-22 route re-pick turned on exactly
this: gpt-5-mini's high reputation hid a low tau2 (0.684) vs deepseek-v4-flash
(0.950) at a fifth of the price. See `docs/journal/2026-05-22-model-route-repick-tool-use.md`.

## Tests

```bash
pricing/.venv/bin/pytest pricing/tests -v
```

Runtime code is stdlib-only; `pytest` lives in the dev-only `pricing/.venv`.
