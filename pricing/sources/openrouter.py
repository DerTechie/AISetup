"""OpenRouter /models source: pure parse + thin fetch."""
import json
import urllib.request

from pricing.csvio import fmt_mtok

MODELS_URL = "https://openrouter.ai/api/v1/models"

# pricing key -> column, converted from per-token to per-1M tokens
_TOKEN_FIELDS = {
    "prompt": "prompt_usd_per_mtok",
    "completion": "completion_usd_per_mtok",
    "input_cache_read": "input_cache_read_usd_per_mtok",
    "input_cache_write": "input_cache_write_usd_per_mtok",
    "internal_reasoning": "internal_reasoning_usd_per_mtok",
}
# pricing key -> column, kept in OpenRouter's native per-unit USD value
_RAW_FIELDS = {
    "image": "image_usd",
    "audio": "audio_usd",
    "web_search": "web_search_usd",
    "request": "request_usd",
}

PRICE_FIELDNAMES = [
    "fetched_date", "id", "name", "context_length", "modality",
    "prompt_usd_per_mtok", "completion_usd_per_mtok",
    "input_cache_read_usd_per_mtok", "input_cache_write_usd_per_mtok",
    "internal_reasoning_usd_per_mtok",
    "image_usd", "audio_usd", "web_search_usd", "request_usd",
]


def parse_models(payload, fetched_date):
    """Flatten the /models payload into one row per model."""
    rows = []
    for model in payload["data"]:
        pricing = model.get("pricing", {})
        row = {
            "fetched_date": fetched_date,
            "id": model["id"],
            "name": model["name"],
            "context_length": model.get("context_length", ""),
            "modality": model.get("architecture", {}).get("modality", ""),
        }
        for key, col in _TOKEN_FIELDS.items():
            row[col] = fmt_mtok(pricing.get(key))
        for key, col in _RAW_FIELDS.items():
            value = pricing.get(key)
            row[col] = "" if value is None else value
        rows.append(row)
    return rows


def fetch():
    """GET the OpenRouter models list (no auth required)."""
    request = urllib.request.Request(
        MODELS_URL, headers={"User-Agent": "aisetup-pricetrack"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)
