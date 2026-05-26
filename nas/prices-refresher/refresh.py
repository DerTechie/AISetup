"""LiteLLM price refresher: live OpenRouter prices -> /model/update PATCH.

Pure functions (parse_openrouter_payload, tick) are unit-tested in test_refresh.py.
The HTTP wrappers and main() loop are added in later tasks.
"""
import logging


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
