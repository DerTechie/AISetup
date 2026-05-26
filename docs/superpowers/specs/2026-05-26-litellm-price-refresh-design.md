# 2026-05-26 — LiteLLM price refresh (live OpenRouter pricing in the gateway)

## Problem

The LiteLLM gateway shows wrong per-call costs in three surfaces — Grafana cost
dashboard, the LiteLLM UI (`/spend`, `/usage`), and reconciliation against the actual
OpenRouter dashboard. The mismatch is large enough that none of the three numbers can
currently be trusted.

Root cause: LiteLLM does **not** call OpenRouter's `/api/v1/models` to learn prices.
It looks each model up in a JSON price map that ships bundled in the container image
(`litellm/model_prices_and_context_window.json`). For a model not in that map the cost
silently defaults to `0`; for a model that *is* present but has been re-priced upstream,
the map value is stale.

Our DB-stored routes ([`nas/models.seed.json`](../../../nas/models.seed.json)) — `openrouter/deepseek/deepseek-v4-flash`,
`openrouter/google/gemini-3.1-pro-preview`, `openrouter/openai/gpt-5` — hit both failure
modes. Real prices from `pricing/data/openrouter-prices.csv` (2026-05-22):

| route id | in $/Mtok | out $/Mtok |
|---|---|---|
| `deepseek/deepseek-v4-flash` | 0.112 | 0.224 |
| `google/gemini-3.1-pro-preview` | 2.00 | 12.00 |
| `openai/gpt-5` | 1.25 | 10.00 |

The "fix" cannot be "hand-edit the seed once" — OpenRouter prices drift (cf. memory
`[[displaced-price-no-narrative-protection]]`: opens have already climbed a tier this
period). The gateway needs day-accurate prices automatically.

## Goals

- Per-call cost in LiteLLM (and therefore Langfuse + Grafana + `/spend`) matches the
  OpenRouter dashboard within rounding (~ ±0.5% on a billing cycle).
- Refresh is automatic and visible-in-git: code lives in this repo, runs in the same
  `nas/docker-compose.yml`, restart policy = `always`.
- First-time DB seed produces correct prices immediately — no "wrong until the first
  refresh tick" window.
- Zero new external services. Stdlib-only Python in the sidecar; reuses existing
  `pricing/sources/openrouter.py` where it runs from the host (seed path).
- One source of truth for *which* models are tracked: the DB-stored routes themselves.
  Adding a new route in the LiteLLM UI is automatically picked up on the next tick.

## Non-goals

- **No backfill** of historical `LiteLLM_SpendLogs` rows that were already written with
  wrong cost. Past totals stay wrong; a separate decision (truncate the table vs. live
  with it) is out of scope here. Flagged in [[Future work]].
- **No alerting** on refresh failure. No healthchecks deadman, no ntfy. Errors surface
  via `docker logs prices-refresher` and a restart loop visible in `docker ps`. Drift
  re-appears in Grafana / OpenRouter reconciliation naturally if the refresher dies.
  (User decision, 2026-05-26 — explicitly no monitoring on this loop.)
- **No price source besides OpenRouter.** Local routes (`private`, `aux-local`) stay at
  the hand-set `$0` already in the seed; they're not openrouter/* models.
- **No support for cost dimensions we don't use** — cache-read/write, per-image,
  per-request, web-search. Only `prompt` (input) and `completion` (output) per token.
  Revisit if a route starts using a paid cache or image modality.
- **No automated routes-as-code reconciliation.** Live UI edits are still allowed to
  drift from `nas/models.seed.json` — that trade-off was already accepted (see
  `nas/litellm-config.yaml` header comment).

## Empirical grounding (verified 2026-05-26)

- `GET https://openrouter.ai/api/v1/models` returns per-model `pricing.prompt` and
  `pricing.completion` as **USD-per-token strings** (no auth, no rate limit issue at
  hourly cadence). Already wrapped in `pricing/sources/openrouter.py::fetch()`.
- LiteLLM admin API exposes `GET /model/info` (lists all routes, including DB-stored
  ones flagged `model_info.db_model: true`) and `POST /model/update` (PATCH a route by
  `id`, including its `litellm_params`). Both require `Authorization: Bearer
  $LITELLM_MASTER_KEY`. Confirmed against the existing `nas/seed_models.py` which uses
  the same endpoints.
- LiteLLM honours `litellm_params.input_cost_per_token` and
  `litellm_params.output_cost_per_token` (absolute USD per token, not per-Mtok) as
  per-route overrides, taking precedence over the bundled price map. Already used in
  the seed for `aux-local` (`input_cost_per_token: 0`).
- Gateway lives at `http://10.63.0.2:4000` (NAS), reachable from inside the
  `nas/docker-compose.yml` network as the `litellm` service name on port `4000`.

## Architecture

Two cooperating pieces in this repo:

```
nas/
  prices-refresher/
    Dockerfile           # FROM python:3.12-alpine; copies refresh.py; CMD python refresh.py
    refresh.py           # stdlib-only loop: fetch -> diff -> POST /model/update
  seed_models.py         # extended: enrich openrouter/* entries with prices at create time
  docker-compose.yml     # new service `prices-refresher` next to litellm
  models.seed.json       # unchanged (prices NOT hand-edited here)
```

### Component: `prices-refresher` (sidecar)

Single-file Python service, runs in its own container next to `litellm` in the same
compose project. No package deps beyond `python:3.12-alpine` stdlib (`urllib`, `json`,
`os`, `time`, `logging`). The sidecar deliberately does **not** import
`pricing/sources/openrouter.py` — it ships its own ~20-line OpenRouter `fetch()` so the
container build doesn't need to mount the host's `pricing/` package. The two fetchers
hit the same URL and use the same field names; keeping them separate is cheaper than
coupling the sidecar image to the host file layout.

```
loop forever:
    try:
        live = openrouter_prices()        # { "deepseek/deepseek-v4-flash": (0.112e-6, 0.224e-6), ... }
        routes = litellm_model_info()     # only model_info.db_model == True
        for route in routes:
            if not route.model.startswith("openrouter/"):
                continue
            wanted = live.get(strip_openrouter_prefix(route.model))
            if wanted is None:
                log.warning("openrouter dropped %s; leaving last-known price", route.model)
                continue
            current = (route.input_cost_per_token, route.output_cost_per_token)
            if current != wanted:
                litellm_update(route.id, input=wanted[0], output=wanted[1])
                log.info("%s prices %s -> %s", route.name, current, wanted)
        log.info("tick ok (%d routes checked, %d updated)", checked, updated)
    except Exception:
        log.exception("tick failed")      # next tick retries
    sleep(REFRESH_INTERVAL_SEC)           # default 3600s
```

The loop runs the refresh **first**, then sleeps. Two consequences worth knowing:
restarting the container (`docker compose restart prices-refresher`) is the supported
way to force an immediate refresh, and the very first tick after `docker compose up`
runs as soon as `litellm` is reachable.

Key invariants:

- **Diff-then-update.** No `/model/update` call when both numbers are unchanged. Keeps
  LiteLLM and Postgres logs quiet on the common path (most ticks change nothing).
- **Fail-open on missing model.** If OpenRouter no longer returns a model that
  LiteLLM still has a route for (rename, regional drop), we keep the last-known price
  rather than zeroing it — a stale price is less wrong than $0.
- **Crash-on-startup-config errors.** Missing `LITELLM_MASTER_KEY` or unreachable
  gateway on the *first* tick → exception → container exits → compose restart loop is
  visible. After the first successful tick, transient failures only log.
- **No persistent state.** The refresher holds nothing across restarts; truth lives in
  the LiteLLM DB. Restarts are safe and frequent (compose redeploys, image rebuilds).

### Component: `seed_models.py` enrichment

Today `seed_models.py` does an env-var expansion on `litellm_params` (for
`os.environ/OPENROUTER_API_KEY`) before POSTing `/model/new`. Extend that same hook so
`openrouter/*` entries also pick up live prices:

```
if entry.litellm_params.model.startswith("openrouter/"):
    if input_cost_per_token not in entry.litellm_params:
        prices = lookup_in_openrouter_fetch(strip_prefix(entry.litellm_params.model))
        entry.litellm_params.input_cost_per_token = prices.input
        entry.litellm_params.output_cost_per_token = prices.output
```

- If the seed file *does* set `input_cost_per_token` (e.g. `aux-local` set to 0),
  the hand value wins. No surprise overrides.
- The seed script can reuse `pricing/sources/openrouter.py::fetch()` directly — it runs
  on the host where the `pricing/` package is importable.
- If OpenRouter doesn't know the model id at seed time, fail loudly (`sys.exit`) —
  better than seeding $0 silently. The operator can hand-set a price in the seed and
  re-run.

This closes the bootstrap gap: a fresh DB is correct from minute zero, not "0 until
~3600s later when the first refresh tick lands."

### Component: `nas/docker-compose.yml` change

New service alongside `litellm` and `litellm-db`:

```yaml
prices-refresher:
  build: ./prices-refresher
  restart: always
  depends_on:
    - litellm
  environment:
    LITELLM_GATEWAY_URL: http://litellm:4000   # intra-network DNS, no host port
    LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
    REFRESH_INTERVAL_SEC: "3600"               # 1h default; bump to 86400 if noisy
```

No new env file entries (re-uses existing `LITELLM_MASTER_KEY`). No host port exposed —
the refresher only needs to talk to LiteLLM on the compose network.

## Data flow

```
  OpenRouter /api/v1/models                  LiteLLM Postgres (DB-stored routes)
            │                                              │
            │ fetch (every REFRESH_INTERVAL_SEC)           │ GET /model/info
            ▼                                              ▼
  ┌────────────────────────────────────────────────────────────┐
  │                 prices-refresher tick                      │
  │   live[id] vs routes[id].(input,output)_cost_per_token     │
  │                       diff                                 │
  └──────────────────────────┬─────────────────────────────────┘
                             │ POST /model/update (only when changed)
                             ▼
                  LiteLLM in-memory router + DB row updated
                             │
                             ▼
              Next request priced with new $/token
                             │
                             ▼
        Langfuse trace + LiteLLM /spend + Grafana cost panel
```

Bootstrap path (fresh DB):

```
  operator runs:  LITELLM_MASTER_KEY=... python3 nas/seed_models.py --force
                             │
                             ▼
       seed_models.py reads models.seed.json
                             │
       for each openrouter/* entry without an explicit price:
           fetch openrouter prices via pricing/sources/openrouter.py
           inject input_cost_per_token / output_cost_per_token into payload
                             │
                             ▼
       POST /model/new — route lands in DB with correct prices on day zero
```

## Failure modes & behaviour

| Scenario | Behaviour |
|---|---|
| OpenRouter `/models` 5xx / timeout | Log exception, skip tick, retry next interval. Prices unchanged. |
| LiteLLM gateway unreachable | Same — log + skip. `depends_on: litellm` covers cold start. |
| Wrong / missing `LITELLM_MASTER_KEY` | First tick raises, container exits, compose restart loop. Visible in `docker ps`. |
| Route exists in DB but absent from OpenRouter response (rename, regional drop) | Log warning, leave last-known price in DB. Fail-open. |
| New route added in LiteLLM UI | Picked up automatically on next tick (we iterate `/model/info`, not the seed). |
| Route's `litellm_params.model` doesn't start with `openrouter/` | Skipped — local Ollama routes and any future direct-vendor routes are untouched. |
| OpenRouter price unchanged | No `/model/update` call. Logs say "tick ok (N checked, 0 updated)". |
| Seed-time enrichment hits an unknown model id | `seed_models.py` exits non-zero. Operator either hand-sets the price in the seed or fixes the model id. |

## Testing

- **`nas/prices-refresher/test_refresh.py`** — pytest-free stdlib unittest module so the
  container build needs no test deps. Cases:
  - `parse_openrouter_payload_extracts_floats` — handles string-encoded prices, missing
    `pricing` block, missing `completion` field.
  - `diff_finds_changed_routes_only` — given a stub `/model/info` payload + a stub live
    map, asserts only changed routes appear in the update list.
  - `non_openrouter_routes_skipped` — `ollama_chat/*` and bare model names are ignored.
  - `missing_in_live_map_keeps_last_known` — route absent from OpenRouter response →
    no update emitted, warning logged.
- **`nas/test_seed_enrichment.py`** — unit-test the new enrichment hook in
  `seed_models.py` with a fake `fetch()` return; assert hand-set prices in the seed
  are preserved.
- **One manual verification** (recorded in the journal entry, not automated): after
  deploying the sidecar, run a single chat completion through `main`, check Langfuse
  trace cost = `tokens × (0.112e-6 in + 0.224e-6 out)` to within float rounding, and
  reconcile against the OpenRouter dashboard for the same hour.

## Day-of-deploy behaviour

No special one-time migration needed. As soon as the `prices-refresher` container is
up, its first tick (~immediate after a successful `litellm` healthcheck) reads
`/model/info`, diffs against live OpenRouter prices, and PATCHes every wrong route in
one pass. The existing DB rows pick up correct prices within seconds, not
`REFRESH_INTERVAL_SEC`.

The seed-side enrichment matters only for **fresh DB seeding** (a new deploy, a DR
restore from the `borgmatic` snapshot, or `seed_models.py --force` on a model that was
deleted from the DB). Both paths converge on the same prices; they're just two
different points where prices enter the system.

## Doc-and-journal touch list

- `README.md` — mention the new sidecar in the "what runs on the NAS" section.
- `docs/runbook.md` — add an "Updating model prices" section: how to force a refresh
  (`docker compose restart prices-refresher`), how to read its logs, what to do if
  Grafana shows obvious price drift.
- `docs/journal/2026-05-26-litellm-price-refresh.md` — decision log entry recording the
  root cause (LiteLLM does not auto-fetch from OpenRouter), what was rejected
  (hand-edit seed, host-cron, n8n), and the empirical verification (Langfuse trace
  matches the math).

## Future work

- **Wipe / mark `LiteLLM_SpendLogs` rows written before this deploy** so historical
  Grafana totals stop being a mix of "wrong" and "right". Separate decision because
  it affects the journal's spend narrative.
- **Add price-drift logging to the journal pipeline** — if a tick changes a price,
  emit a structured line that Loki/Grafana can chart over time. Postponed until the
  Grafana panel design exists.
- **Per-route alert on price increase > X%** — only worth wiring once a price spike
  has actually surprised us. YAGNI for now.
- **Cache-read pricing** — needed only if a route starts using OpenRouter's prompt
  caching. Out of scope until then.
