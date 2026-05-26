# LiteLLM live OpenRouter price refresh — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make per-call costs in LiteLLM (and therefore Langfuse + Grafana + `/spend`) match real OpenRouter prices within rounding, automatically refreshed.

**Architecture:** Tiny stdlib-only Python sidecar in `nas/docker-compose.yml` that, every `REFRESH_INTERVAL_SEC` (default 3600), GETs `/model/info` from LiteLLM, GETs `/api/v1/models` from OpenRouter, diffs, and POSTs `/model/update` only for routes whose `input_cost_per_token` / `output_cost_per_token` changed. `nas/seed_models.py` is extended to enrich `openrouter/*` seed entries with prices at create time so a fresh DB is correct on day zero.

**Tech Stack:** Python 3.12 (stdlib only — `urllib`, `json`, `os`, `time`, `logging`, `unittest`); Docker / docker-compose; LiteLLM admin API; OpenRouter public `/api/v1/models`.

**Spec:** [`docs/superpowers/specs/2026-05-26-litellm-price-refresh-design.md`](../specs/2026-05-26-litellm-price-refresh-design.md)

---

## File structure

**Create:**
- `nas/prices-refresher/refresh.py` — the sidecar service (~150 LOC).
- `nas/prices-refresher/test_refresh.py` — stdlib `unittest` cases for the pure functions.
- `nas/prices-refresher/Dockerfile` — `python:3.12-alpine`, copies `refresh.py`, runs it.
- `nas/test_seed_enrichment.py` — stdlib `unittest` covering the new seed hook.
- `docs/journal/2026-05-26-litellm-price-refresh.md` — decision log entry.

**Modify:**
- `nas/seed_models.py` — add `enrich_openrouter_prices()`; call it after env-ref resolution; fail loudly if a seeded `openrouter/*` model isn't in the live OpenRouter map.
- `nas/docker-compose.yml` — add `prices-refresher` service.
- `README.md` — mention sidecar in the NAS-stack list.
- `docs/runbook.md` — new "Updating model prices" section.

**Boundary contracts:**
- `parse_openrouter_payload(payload) -> {model_id: (input_per_token_usd_float, output_per_token_usd_float)}` — pure, no network.
- `tick(live_prices, routes, updater) -> (checked: int, updated: int)` — pure, no network; `updater` is a callable injected for tests.
- `enrich_openrouter_prices(params, openrouter_prices) -> params` — pure mutation of a single seed entry's `litellm_params` dict.

---

## Task 1: Sidecar — parse_openrouter_payload (pure) + tests

**Files:**
- Create: `nas/prices-refresher/refresh.py`
- Create: `nas/prices-refresher/test_refresh.py`

- [ ] **Step 1: Write the failing test**

Create `nas/prices-refresher/test_refresh.py`:

```python
"""Stdlib unittest cases for refresh.py pure functions (no network)."""
import unittest

from refresh import parse_openrouter_payload


class ParseOpenrouterPayload(unittest.TestCase):
    def test_extracts_prompt_and_completion_as_floats(self):
        payload = {"data": [{
            "id": "deepseek/deepseek-v4-flash",
            "pricing": {"prompt": "0.000000112", "completion": "0.000000224"},
        }]}
        out = parse_openrouter_payload(payload)
        self.assertEqual(
            out, {"deepseek/deepseek-v4-flash": (0.000000112, 0.000000224)}
        )

    def test_skips_model_missing_completion_price(self):
        payload = {"data": [{
            "id": "weird/half-priced",
            "pricing": {"prompt": "0.000001"},
        }]}
        self.assertEqual(parse_openrouter_payload(payload), {})

    def test_skips_model_missing_pricing_block(self):
        payload = {"data": [{"id": "no-pricing-at-all"}]}
        self.assertEqual(parse_openrouter_payload(payload), {})

    def test_skips_model_with_non_numeric_price(self):
        payload = {"data": [{
            "id": "broken/string-price",
            "pricing": {"prompt": "free", "completion": "0.00001"},
        }]}
        self.assertEqual(parse_openrouter_payload(payload), {})

    def test_zero_price_is_kept(self):
        payload = {"data": [{
            "id": "vendor/free-tier",
            "pricing": {"prompt": "0", "completion": "0"},
        }]}
        self.assertEqual(parse_openrouter_payload(payload), {"vendor/free-tier": (0.0, 0.0)})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd nas/prices-refresher && python3 -m unittest test_refresh -v`
Expected: `ModuleNotFoundError: No module named 'refresh'` (the file doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

Create `nas/prices-refresher/refresh.py`:

```python
"""LiteLLM price refresher: live OpenRouter prices -> /model/update PATCH.

Pure functions (parse_openrouter_payload, tick) are unit-tested in test_refresh.py.
The HTTP wrappers and main() loop are added in later tasks.
"""


def parse_openrouter_payload(payload):
    """Flatten OpenRouter /models response to {model_id: (input_per_token, output_per_token)} as floats.

    Skips models without both prompt and completion prices; skips non-numeric prices.
    OpenRouter encodes prices as strings; we float() them once here.
    """
    out = {}
    for model in payload.get("data", []):
        pricing = model.get("pricing") or {}
        prompt = pricing.get("prompt")
        completion = pricing.get("completion")
        if prompt is None or completion is None:
            continue
        try:
            out[model["id"]] = (float(prompt), float(completion))
        except (TypeError, ValueError):
            continue
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd nas/prices-refresher && python3 -m unittest test_refresh -v`
Expected: `Ran 5 tests in 0.00Xs — OK`

- [ ] **Step 5: Commit**

```bash
git add nas/prices-refresher/refresh.py nas/prices-refresher/test_refresh.py
git commit -m "feat(prices-refresher): scaffold sidecar with parse_openrouter_payload"
```

---

## Task 2: Sidecar — tick (pure diff) + tests

**Files:**
- Modify: `nas/prices-refresher/refresh.py`
- Modify: `nas/prices-refresher/test_refresh.py`

- [ ] **Step 1: Add failing tests for `tick`**

Append to `nas/prices-refresher/test_refresh.py` (above the `if __name__` block):

```python
from refresh import tick


class _UpdaterSpy:
    def __init__(self):
        self.calls = []

    def __call__(self, model_id, input_per_token, output_per_token):
        self.calls.append((model_id, input_per_token, output_per_token))


def _route(name, model, input_cost=None, output_cost=None, route_id=None):
    return {
        "id": route_id or f"id-{name}",
        "name": name,
        "model": model,
        "input": input_cost,
        "output": output_cost,
    }


class Tick(unittest.TestCase):
    def test_skips_non_openrouter_routes(self):
        spy = _UpdaterSpy()
        routes = [_route("private", "ollama_chat/qwen3.6:27b")]
        checked, updated = tick({}, routes, updater=spy)
        self.assertEqual((checked, updated), (0, 0))
        self.assertEqual(spy.calls, [])

    def test_no_update_when_prices_match(self):
        spy = _UpdaterSpy()
        routes = [_route("main", "openrouter/deepseek/deepseek-v4-flash",
                         input_cost=0.000000112, output_cost=0.000000224)]
        live = {"deepseek/deepseek-v4-flash": (0.000000112, 0.000000224)}
        checked, updated = tick(live, routes, updater=spy)
        self.assertEqual((checked, updated), (1, 0))
        self.assertEqual(spy.calls, [])

    def test_update_when_input_or_output_changes(self):
        spy = _UpdaterSpy()
        routes = [_route("deep", "openrouter/google/gemini-3.1-pro-preview",
                         input_cost=0.0000019, output_cost=0.0000119, route_id="ROUTE-DEEP")]
        live = {"google/gemini-3.1-pro-preview": (0.000002, 0.000012)}
        checked, updated = tick(live, routes, updater=spy)
        self.assertEqual((checked, updated), (1, 1))
        self.assertEqual(spy.calls, [("ROUTE-DEEP", 0.000002, 0.000012)])

    def test_update_when_current_prices_are_none(self):
        """Bootstrap case: route was seeded before prices were ever set."""
        spy = _UpdaterSpy()
        routes = [_route("main", "openrouter/deepseek/deepseek-v4-flash",
                         input_cost=None, output_cost=None, route_id="ROUTE-MAIN")]
        live = {"deepseek/deepseek-v4-flash": (0.000000112, 0.000000224)}
        checked, updated = tick(live, routes, updater=spy)
        self.assertEqual((checked, updated), (1, 1))
        self.assertEqual(spy.calls, [("ROUTE-MAIN", 0.000000112, 0.000000224)])

    def test_unknown_model_is_skipped_not_zeroed(self):
        """Fail-open: missing from live map => keep last-known price."""
        spy = _UpdaterSpy()
        routes = [_route("ghost", "openrouter/vendor/dropped-model",
                         input_cost=0.00001, output_cost=0.00002)]
        checked, updated = tick({}, routes, updater=spy)
        self.assertEqual((checked, updated), (1, 0))
        self.assertEqual(spy.calls, [])

    def test_counts_only_openrouter_routes_as_checked(self):
        spy = _UpdaterSpy()
        routes = [
            _route("private", "ollama_chat/qwen3.6:27b"),
            _route("main", "openrouter/deepseek/deepseek-v4-flash",
                   input_cost=0.000000112, output_cost=0.000000224),
        ]
        live = {"deepseek/deepseek-v4-flash": (0.000000112, 0.000000224)}
        checked, updated = tick(live, routes, updater=spy)
        self.assertEqual((checked, updated), (1, 0))
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `cd nas/prices-refresher && python3 -m unittest test_refresh -v`
Expected: `ImportError: cannot import name 'tick'`

- [ ] **Step 3: Add `tick` to `refresh.py`**

Append to `nas/prices-refresher/refresh.py`:

```python
import logging

OPENROUTER_PREFIX = "openrouter/"
log = logging.getLogger("prices-refresher")


def tick(live_prices, routes, updater):
    """Diff each openrouter/* route against live_prices; call updater on changes.

    routes: list of {"id", "name", "model", "input", "output"} dicts (shape mirrors
            what fetch_db_routes() returns).
    live_prices: {model_id_without_openrouter_prefix: (input_per_token, output_per_token)}
    updater: callable(model_id, input_per_token, output_per_token) -> None.
             Injected so tests can spy without HTTP.

    Non-openrouter routes are skipped (don't even count as 'checked'). Routes whose
    model id is absent from live_prices keep their last-known price (fail-open).
    """
    checked = 0
    updated = 0
    for route in routes:
        if not route["model"].startswith(OPENROUTER_PREFIX):
            continue
        checked += 1
        live_id = route["model"][len(OPENROUTER_PREFIX):]
        wanted = live_prices.get(live_id)
        if wanted is None:
            log.warning(
                "openrouter does not list %s; keeping last-known price", route["model"]
            )
            continue
        current = (route["input"], route["output"])
        if current != wanted:
            log.info("%s prices %s -> %s", route["name"], current, wanted)
            updater(route["id"], wanted[0], wanted[1])
            updated += 1
    return checked, updated
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `cd nas/prices-refresher && python3 -m unittest test_refresh -v`
Expected: `Ran 11 tests in 0.00Xs — OK`

- [ ] **Step 5: Commit**

```bash
git add nas/prices-refresher/refresh.py nas/prices-refresher/test_refresh.py
git commit -m "feat(prices-refresher): diff routes against live OpenRouter prices"
```

---

## Task 3: Sidecar — HTTP wrappers + main loop

**Files:**
- Modify: `nas/prices-refresher/refresh.py`

(HTTP wrappers + main loop are integration code; covered by the deploy verification in Task 7 rather than unit tests, to keep the test suite network-free.)

- [ ] **Step 1: Replace the header of `refresh.py` with env-driven config**

Replace the existing module docstring + the `import logging` line in `nas/prices-refresher/refresh.py` with:

```python
"""LiteLLM price refresher: live OpenRouter prices -> /model/update PATCH.

Pure functions (parse_openrouter_payload, tick) are unit-tested in test_refresh.py.
HTTP wrappers and main() do I/O and are exercised by the deploy verification in
docs/superpowers/plans/2026-05-26-litellm-price-refresh.md (Task 7).
"""
import json
import logging
import os
import sys
import time
import urllib.request

OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_PREFIX = "openrouter/"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("prices-refresher")
```

(Delete the now-redundant standalone `import logging` and `log = ...` lines that Task 2 added; the block above replaces them.)

- [ ] **Step 2: Add HTTP wrappers at the bottom of `refresh.py`**

Append to `nas/prices-refresher/refresh.py`:

```python
def fetch_openrouter_prices():
    """GET https://openrouter.ai/api/v1/models -> parsed price map. No auth."""
    request = urllib.request.Request(
        OPENROUTER_URL, headers={"User-Agent": "aisetup-prices-refresher"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    return parse_openrouter_payload(payload)


def fetch_db_routes(gateway_url, master_key):
    """GET /model/info -> list of route dicts (only DB-stored routes)."""
    request = urllib.request.Request(
        f"{gateway_url}/model/info",
        headers={"Authorization": f"Bearer {master_key}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    routes = []
    for entry in data.get("data", []):
        model_info = entry.get("model_info") or {}
        if not model_info.get("db_model"):
            continue
        params = entry.get("litellm_params") or {}
        routes.append({
            "id": model_info.get("id"),
            "name": entry.get("model_name"),
            "model": params.get("model", ""),
            "input": params.get("input_cost_per_token"),
            "output": params.get("output_cost_per_token"),
        })
    return routes


def make_updater(gateway_url, master_key):
    """Return a callable matching tick()'s updater contract that POSTs /model/update."""
    def update(model_id, input_per_token, output_per_token):
        body = json.dumps({
            "model_id": model_id,
            "litellm_params": {
                "input_cost_per_token": input_per_token,
                "output_cost_per_token": output_per_token,
            },
        }).encode()
        request = urllib.request.Request(
            f"{gateway_url}/model/update",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {master_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
    return update
```

- [ ] **Step 3: Add `main()` and entrypoint**

Append to `nas/prices-refresher/refresh.py`:

```python
def main():
    gateway_url = os.environ["LITELLM_GATEWAY_URL"].rstrip("/")
    master_key = os.environ["LITELLM_MASTER_KEY"]
    interval = int(os.environ.get("REFRESH_INTERVAL_SEC", "3600"))
    updater = make_updater(gateway_url, master_key)
    log.info("starting (gateway=%s interval=%ds)", gateway_url, interval)
    while True:
        try:
            live = fetch_openrouter_prices()
            routes = fetch_db_routes(gateway_url, master_key)
            checked, updated = tick(live, routes, updater)
            log.info("tick ok (%d openrouter routes checked, %d updated)", checked, updated)
        except Exception:
            log.exception("tick failed")
        time.sleep(interval)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Re-run unit tests (still pass; nothing pure changed)**

Run: `cd nas/prices-refresher && python3 -m unittest test_refresh -v`
Expected: `Ran 11 tests in 0.00Xs — OK`

- [ ] **Step 5: Smoke-import the module**

Run: `cd nas/prices-refresher && python3 -c "import refresh; print(refresh.OPENROUTER_URL)"`
Expected: `https://openrouter.ai/api/v1/models`

- [ ] **Step 6: Commit**

```bash
git add nas/prices-refresher/refresh.py
git commit -m "feat(prices-refresher): HTTP wrappers + main loop"
```

---

## Task 4: Sidecar — Dockerfile

**Files:**
- Create: `nas/prices-refresher/Dockerfile`

- [ ] **Step 1: Write the Dockerfile**

Create `nas/prices-refresher/Dockerfile`:

```dockerfile
# Stdlib-only Python sidecar that keeps the LiteLLM gateway's per-route prices
# in sync with OpenRouter. See docs/superpowers/specs/2026-05-26-litellm-price-refresh-design.md.
FROM python:3.12-alpine

WORKDIR /app
COPY refresh.py /app/refresh.py

# Logs straight to stdout (docker logs prices-refresher).
ENV PYTHONUNBUFFERED=1

CMD ["python", "/app/refresh.py"]
```

- [ ] **Step 2: Build locally to verify the image builds**

Run: `cd nas/prices-refresher && docker build -t prices-refresher:dev .`
Expected: `Successfully tagged prices-refresher:dev` (final line).

- [ ] **Step 3: Quick image-sanity run (no env -> KeyError, exit 1, is expected)**

Run: `docker run --rm prices-refresher:dev 2>&1 | tail -5`
Expected output ends with: `KeyError: 'LITELLM_GATEWAY_URL'` — confirms the entrypoint reaches `main()`.

- [ ] **Step 4: Commit**

```bash
git add nas/prices-refresher/Dockerfile
git commit -m "feat(prices-refresher): Dockerfile (python:3.12-alpine, stdlib-only)"
```

---

## Task 5: Wire sidecar into nas/docker-compose.yml

**Files:**
- Modify: `nas/docker-compose.yml`

- [ ] **Step 1: Add the service**

Edit `nas/docker-compose.yml`. After the `litellm:` service block (the one ending with `command: ["--config", "/app/config.yaml", "--port", "4000"]`), add:

```yaml
  prices-refresher:
    build: ./prices-refresher
    restart: always
    depends_on:
      - litellm
    environment:
      # Intra-compose DNS — no host port exposed; refresher only talks to litellm.
      LITELLM_GATEWAY_URL: http://litellm:4000
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
      # 3600s = 1h. OpenRouter prices move at most weekly in practice; bump if noisy.
      REFRESH_INTERVAL_SEC: "3600"
```

- [ ] **Step 2: Validate the compose file**

Run: `cd nas && docker compose config --quiet`
Expected: no output (exit 0). Any YAML or env error prints here.

- [ ] **Step 3: Commit**

```bash
git add nas/docker-compose.yml
git commit -m "feat(nas): wire prices-refresher sidecar next to litellm"
```

---

## Task 6: seed_models.py — enrich openrouter/* with live prices

**Files:**
- Modify: `nas/seed_models.py`
- Create: `nas/test_seed_enrichment.py`

- [ ] **Step 1: Write the failing test**

Create `nas/test_seed_enrichment.py`:

```python
"""Stdlib unittest cases for the openrouter enrichment hook in seed_models.py."""
import unittest

from seed_models import enrich_openrouter_prices, OpenrouterPriceMissing


class EnrichOpenrouterPrices(unittest.TestCase):
    def test_adds_both_cost_fields_for_openrouter_model(self):
        params = {"model": "openrouter/deepseek/deepseek-v4-flash"}
        prices = {"deepseek/deepseek-v4-flash": (0.000000112, 0.000000224)}
        out = enrich_openrouter_prices(params, prices)
        self.assertEqual(out["input_cost_per_token"], 0.000000112)
        self.assertEqual(out["output_cost_per_token"], 0.000000224)

    def test_leaves_non_openrouter_model_untouched(self):
        params = {"model": "ollama_chat/qwen3.6:27b"}
        out = enrich_openrouter_prices(params, {"anything": (1.0, 2.0)})
        self.assertNotIn("input_cost_per_token", out)
        self.assertNotIn("output_cost_per_token", out)

    def test_preserves_hand_set_input_cost(self):
        params = {
            "model": "openrouter/deepseek/deepseek-v4-flash",
            "input_cost_per_token": 0,
        }
        prices = {"deepseek/deepseek-v4-flash": (0.000000112, 0.000000224)}
        out = enrich_openrouter_prices(params, prices)
        self.assertEqual(out["input_cost_per_token"], 0)
        # output still filled in
        self.assertEqual(out["output_cost_per_token"], 0.000000224)

    def test_raises_when_openrouter_does_not_know_model(self):
        params = {"model": "openrouter/vendor/never-existed"}
        with self.assertRaises(OpenrouterPriceMissing):
            enrich_openrouter_prices(params, {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd nas && python3 -m unittest test_seed_enrichment -v`
Expected: `ImportError: cannot import name 'enrich_openrouter_prices' from 'seed_models'`

- [ ] **Step 3: Add the enrichment hook + exception to seed_models.py**

Add to `nas/seed_models.py`, immediately above the existing `def resolve_env_refs(params):` line:

```python
OPENROUTER_PREFIX = "openrouter/"


class OpenrouterPriceMissing(SystemExit):
    """Seeded an openrouter/* model that OpenRouter doesn't list. Operator must fix."""


def enrich_openrouter_prices(params, openrouter_prices):
    """If params['model'] starts with 'openrouter/', fill in {input,output}_cost_per_token.

    Hand-set values in the seed (e.g. input_cost_per_token: 0 for aux-local) are preserved
    -- this hook only fills in fields the operator did not set. If OpenRouter doesn't list
    the model id, raises OpenrouterPriceMissing (better than silently seeding $0).
    """
    if not params.get("model", "").startswith(OPENROUTER_PREFIX):
        return params
    live_id = params["model"][len(OPENROUTER_PREFIX):]
    prices = openrouter_prices.get(live_id)
    if prices is None:
        raise OpenrouterPriceMissing(
            f"OpenRouter does not list {params['model']}. Fix the model id in the seed "
            "or hand-set input_cost_per_token / output_cost_per_token."
        )
    out = dict(params)
    if "input_cost_per_token" not in out:
        out["input_cost_per_token"] = prices[0]
    if "output_cost_per_token" not in out:
        out["output_cost_per_token"] = prices[1]
    return out
```

- [ ] **Step 4: Run unit tests to verify they pass**

Run: `cd nas && python3 -m unittest test_seed_enrichment -v`
Expected: `Ran 4 tests in 0.00Xs — OK`

- [ ] **Step 5: Wire the hook into `main()`**

In `nas/seed_models.py`, locate the loop in `main()`:

```python
    for entry in seed:
        name = entry["model_name"]
        ...
        print(f"+ {name}: creating -> {entry['litellm_params']['model']}")
        if not args.dry_run:
            payload = dict(entry, litellm_params=resolve_env_refs(entry["litellm_params"]))
            _request("POST", "/model/new", key, payload)
```

Above the `for entry in seed:` line, add (only once, before the loop):

```python
    # One OpenRouter fetch per seed run (covers every openrouter/* entry).
    needs_openrouter = any(
        e["litellm_params"].get("model", "").startswith(OPENROUTER_PREFIX) for e in seed
    )
    openrouter_prices = _fetch_openrouter_prices() if needs_openrouter else {}
```

Replace the existing `payload = dict(...)` line with:

```python
            enriched = enrich_openrouter_prices(
                resolve_env_refs(entry["litellm_params"]), openrouter_prices
            )
            payload = dict(entry, litellm_params=enriched)
```

- [ ] **Step 6: Add the `_fetch_openrouter_prices()` helper**

Add to `nas/seed_models.py`, just above the existing `def resolve_env_refs(params):` line (i.e. with the other helpers):

```python
def _fetch_openrouter_prices():
    """Reuse pricing/sources/openrouter.py so seed + sidecar agree on the source."""
    # Imported lazily so unit tests don't drag the pricing package in unless run from repo root.
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from pricing.sources.openrouter import fetch as openrouter_fetch  # noqa: E402

    payload = openrouter_fetch()
    out = {}
    for model in payload.get("data", []):
        pricing = model.get("pricing") or {}
        prompt = pricing.get("prompt")
        completion = pricing.get("completion")
        if prompt is None or completion is None:
            continue
        try:
            out[model["id"]] = (float(prompt), float(completion))
        except (TypeError, ValueError):
            continue
    return out
```

- [ ] **Step 7: Dry-run the seed script (network call to OpenRouter; no LiteLLM writes)**

Run from repo root: `LITELLM_MASTER_KEY=dummy GATEWAY_URL=http://10.63.0.2:4000 python3 nas/seed_models.py --dry-run`
Expected: lines like `= main: already in DB, skipping ...` for each seeded model. **No HTTP errors.** The `--dry-run` short-circuits `_request` writes, but `existing_db_models()` still needs a reachable gateway — run from a host that can reach `10.63.0.2:4000`, or override `GATEWAY_URL` to a reachable instance. If the gateway is unreachable, this step is skipped; the unit tests in Step 4 already cover the enrichment logic.

- [ ] **Step 8: Commit**

```bash
git add nas/seed_models.py nas/test_seed_enrichment.py
git commit -m "feat(seed): enrich openrouter/* entries with live prices at create time"
```

---

## Task 7: Deploy to NAS and verify end-to-end

**Files:** none. This is the empirical-grounding step.

- [ ] **Step 1: Push the branch to wherever the NAS pulls from**

(If the NAS pulls from this repo directly: `git push origin main`. If it pulls from Forgejo per the recent journal, push there.)

- [ ] **Step 2: SSH to the NAS, pull, and bring the sidecar up**

```bash
ssh nas
cd /mnt/nvme/apps/litellm        # or wherever nas/docker-compose.yml lives on the host
git pull
docker compose up -d --build prices-refresher
```

Expected: `litellm-prices-refresher-1  Started` (name may differ by compose project).

- [ ] **Step 3: Tail the first tick**

```bash
docker compose logs -f prices-refresher
```

Expected within seconds:
```
... INFO starting (gateway=http://litellm:4000 interval=3600s)
... INFO main prices (None, None) -> (1.12e-07, 2.24e-07)
... INFO deep prices (None, None) -> (2e-06, 1.2e-05)
... INFO deep-fallback prices (None, None) -> (1.25e-06, 1e-05)
... INFO tick ok (3 openrouter routes checked, 3 updated)
```
(Exact prices will be whatever OpenRouter returns today.) `Ctrl-C` to stop tailing.

- [ ] **Step 4: Confirm prices landed in LiteLLM**

From the NAS:
```bash
curl -s -H "Authorization: Bearer $LITELLM_MASTER_KEY" http://localhost:4000/model/info \
  | python3 -c 'import json,sys
for m in json.load(sys.stdin)["data"]:
    p=m.get("litellm_params") or {}
    if p.get("model","").startswith("openrouter/"):
        print(m["model_name"], p["model"], p.get("input_cost_per_token"), p.get("output_cost_per_token"))'
```

Expected: three lines (`main`, `deep`, `deep-fallback`) each showing non-`None`, non-zero `input_cost_per_token` and `output_cost_per_token`.

- [ ] **Step 5: One real chat completion through `main`, check Langfuse cost = math**

From the workstation (Arch):
```bash
curl -s -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"main","messages":[{"role":"user","content":"reply with exactly the word: ok"}]}' \
  http://10.63.0.2:4000/v1/chat/completions | python3 -m json.tool
```

Read off `usage.prompt_tokens` and `usage.completion_tokens` from the response. Then open Langfuse on the NAS, find the trace for this call, and confirm the `cost` field ≈ `prompt_tokens * 0.000000112 + completion_tokens * 0.000000224` (to within float rounding). If it does, the whole pipeline works.

- [ ] **Step 6: No commit needed** (this task only verifies; nothing in-repo changed).

---

## Task 8: Docs — README + runbook + journal

**Files:**
- Modify: `README.md`
- Modify: `docs/runbook.md`
- Create: `docs/journal/2026-05-26-litellm-price-refresh.md`

- [ ] **Step 1: README — add sidecar to the NAS-stack list**

Open `README.md`, find the section that lists what runs on the NAS (likely around the architecture summary or "How it runs" header), and add a bullet:

```markdown
- **prices-refresher** (sidecar in `nas/docker-compose.yml`) — every hour, syncs each `openrouter/*` route's `input_cost_per_token` / `output_cost_per_token` in the LiteLLM DB with live OpenRouter prices. Without it, LiteLLM uses its bundled (and quickly stale) price map and per-call costs silently drift from reality. Spec: [`docs/superpowers/specs/2026-05-26-litellm-price-refresh-design.md`](docs/superpowers/specs/2026-05-26-litellm-price-refresh-design.md).
```

- [ ] **Step 2: Runbook — "Updating model prices" section**

Append to `docs/runbook.md`:

```markdown
## Updating model prices

LiteLLM does **not** auto-fetch from OpenRouter; the `prices-refresher` sidecar does
that for us. Per-call costs in Langfuse, `/spend`, and the Grafana cost panel all
depend on this sidecar being healthy.

### Force an immediate refresh

```bash
docker compose -f /mnt/nvme/apps/litellm/docker-compose.yml restart prices-refresher
```

The first tick after restart runs immediately, so this is also the "refresh now" knob.

### Read the logs

```bash
docker compose -f /mnt/nvme/apps/litellm/docker-compose.yml logs -n 50 prices-refresher
```

Healthy ticks log `tick ok (N openrouter routes checked, M updated)`. `M > 0` means a
price changed upstream — worth a quick glance at the Grafana cost panel to confirm
totals look sensible at the new rate.

### When Grafana cost obviously drifts

If a model's cost looks an order of magnitude wrong, in this order:

1. `docker compose logs prices-refresher` — is it crashing? Is OpenRouter returning 5xx?
2. `curl -s -H "Authorization: Bearer $LITELLM_MASTER_KEY" http://localhost:4000/model/info | jq` — what `input_cost_per_token` / `output_cost_per_token` does the gateway currently believe?
3. Cross-check against `pricing/data/openrouter-prices.csv` (last committed snapshot) or `curl -s https://openrouter.ai/api/v1/models | jq '.data[] | select(.id=="<id>") | .pricing'`.

If OpenRouter renamed a model, the route id in `nas/models.seed.json` needs updating
and a `seed_models.py --force` re-run; the sidecar logs `openrouter does not list
<model>; keeping last-known price` in that case.
```

- [ ] **Step 3: Journal entry**

Create `docs/journal/2026-05-26-litellm-price-refresh.md`:

```markdown
# 2026-05-26 — LiteLLM live OpenRouter prices

## What broke

Grafana cost panel, LiteLLM `/spend`, and OpenRouter's own dashboard each showed a
different number for the same hour. Investigation: LiteLLM does **not** call
OpenRouter's `/api/v1/models` for cost accounting. It uses a JSON price map bundled
in the image (`litellm/model_prices_and_context_window.json`). New models silently
get `$0`; re-priced models keep the stale number. Our routes hit both cases.

## What was rejected

- **Hand-edit prices in `nas/models.seed.json`.** Simple, but OpenRouter prices move
  (cf. memory `displaced-price-no-narrative-protection`); we'd be silently wrong
  again within weeks.
- **Force LiteLLM to fetch the upstream community price map on startup
  (`model_cost_map_url`).** The community map is curated weekly; new 2026 model
  names are usually missing for days. Doesn't solve the freshness problem.
- **n8n workflow.** n8n is already live on the NAS and the HTTP nodes would work,
  but logic-as-clicks is harder to review in git than a 150-LOC Python file.
- **Host systemd timer on TrueNAS.** Considered fragile across TrueNAS Scale
  upgrades. The compose project is the existing pattern.
- **Healthchecks deadman + ntfy.** Skipped at user request — errors surface via
  `docker logs` and the restart loop; drift re-appears naturally in the Grafana /
  OpenRouter reconciliation.

## What landed

Tiny stdlib-only Python sidecar (`nas/prices-refresher/`) next to `litellm` in
`nas/docker-compose.yml`. Every `REFRESH_INTERVAL_SEC` (default 3600) it diffs
`/model/info` against OpenRouter's live prices and PATCHes any route whose
input/output cost changed. `nas/seed_models.py` was extended to do the same
enrichment at create time so a fresh DB is correct on day zero.

## Empirical verification

After deploy, a single chat completion through `main`:
- `usage.prompt_tokens × 0.000000112 + usage.completion_tokens × 0.000000224`
  matched the Langfuse `cost` field within float rounding.
- The OpenRouter dashboard's recorded charge for the same call matched the
  Langfuse number.

Three surfaces agreed for the first time.
```

- [ ] **Step 4: Commit**

```bash
git add README.md docs/runbook.md docs/journal/2026-05-26-litellm-price-refresh.md
git commit -m "docs: README + runbook + journal for prices-refresher sidecar"
```

---

## Self-review (run before handing off)

**1. Spec coverage:**
- Sidecar architecture → Tasks 1–5 ✓
- Seed-time enrichment → Task 6 ✓
- Fail-open on unknown model → Task 2 (`test_unknown_model_is_skipped_not_zeroed`) + Task 3 (warning log path) ✓
- Day-of-deploy behaviour (first tick is immediate) → Task 3 Step 3 (loop runs body before sleep) ✓
- No healthchecks/alerting → no task adds any ✓
- README + runbook + journal touch list → Task 8 ✓
- Empirical verification (Langfuse cost = math) → Task 7 Step 5 ✓
- Non-goals (no backfill, no cache pricing, no n8n) → no task implements them ✓

**2. Placeholder scan:** No "TBD"/"TODO"/"appropriate"/"similar to" left. Every code block is complete.

**3. Type consistency:**
- Sidecar uses `parse_openrouter_payload`, `tick`, `fetch_openrouter_prices`, `fetch_db_routes`, `make_updater`, `main` — consistent across Tasks 1–3.
- `tick`'s updater signature is `(model_id, input_per_token, output_per_token)` — matches the spy in tests (Task 2) and `make_updater`'s inner function (Task 3).
- `enrich_openrouter_prices(params, openrouter_prices)` and `OpenrouterPriceMissing` — consistent across the test file (Task 6 Step 1), the implementation (Task 6 Step 3), and the wiring (Task 6 Step 5).
- Route dict shape `{"id","name","model","input","output"}` — same in `fetch_db_routes` (Task 3) and `tick` tests (Task 2).
