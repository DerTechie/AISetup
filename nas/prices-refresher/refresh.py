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


def parse_openrouter_payload(payload):
    """Flatten OpenRouter /models response to {model_id: (input_per_token, output_per_token)} as floats.

    Skips models without both prompt and completion prices; skips non-numeric prices.
    Skips entries with no id. OpenRouter encodes prices as strings; we float() them once here.
    """
    out = {}
    for model in payload.get("data", []):
        model_id = model.get("id")
        if model_id is None:
            continue
        pricing = model.get("pricing") or {}
        prompt = pricing.get("prompt")
        completion = pricing.get("completion")
        if prompt is None or completion is None:
            continue
        try:
            out[model_id] = (float(prompt), float(completion))
        except (TypeError, ValueError):
            continue
    return out


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
                "openrouter does not list %s; keeping last-known price", live_id
            )
            continue
        current = (route["input"], route["output"])
        if current != wanted:
            log.info("%s prices %s -> %s", route["name"], current, wanted)
            updater(route["id"], wanted[0], wanted[1])
            updated += 1
    return checked, updated


def fetch_openrouter_prices():
    """GET https://openrouter.ai/api/v1/models -> parsed price map. No auth."""
    request = urllib.request.Request(
        OPENROUTER_URL, headers={"User-Agent": "aisetup-prices-refresher"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    return parse_openrouter_payload(payload)


def fetch_db_routes(gateway_url, master_key):
    """GET /model/info -> list of route dicts (only DB-stored routes).

    Coerces input_cost_per_token / output_cost_per_token to float when present so the
    tick() equality check is comparing floats-to-floats. (LiteLLM normally returns
    these as JSON numbers; the cast defends against a future stringly-typed response.)
    """
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
        if not model_info.get("id"):
            log.warning("DB route %r has no id; skipping", entry.get("model_name"))
            continue
        params = entry.get("litellm_params") or {}
        routes.append({
            "id": model_info.get("id"),
            "name": entry.get("model_name"),
            "model": params.get("model", ""),
            "input": _maybe_float(params.get("input_cost_per_token")),
            "output": _maybe_float(params.get("output_cost_per_token")),
        })
    return routes


def _maybe_float(value):
    """Return float(value) if value is not None, else None. Defensive cast."""
    return None if value is None else float(value)


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
