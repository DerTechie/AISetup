"""LiteLLM price refresher: live OpenRouter prices -> /model/update PATCH.

Pure functions (parse_openrouter_payload, tick) are unit-tested in test_refresh.py.
The HTTP wrappers and main() loop are added in later tasks.
"""


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
