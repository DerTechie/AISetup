"""Artificial Analysis source: pure parse + thin fetch.

Data: Artificial Analysis (https://artificialanalysis.ai). Attribution required.
Set the API key in env var ARTIFICIALANALYSIS_API_KEY (free tier, 1000 req/day).
"""
import json
import os
import urllib.request

MODELS_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
API_KEY_ENV = "ARTIFICIALANALYSIS_API_KEY"

# Evaluation keys we record, in stable order. The agentic group
# (terminalbench_hard, tau2) is the part the AA Intelligence Index does NOT
# capture: tau2 is tool-use / function-calling reliability — the signal that
# drives the every-turn `main` route pick.
EVAL_KEYS = [
    "artificial_analysis_intelligence_index",
    "artificial_analysis_coding_index",
    "artificial_analysis_math_index",
    "mmlu_pro", "gpqa", "hle", "livecodebench", "scicode", "math_500", "aime",
    "aime_25", "ifbench", "lcr", "terminalbench_hard", "tau2",
]

SCORE_FIELDNAMES = ["fetched_date", "source_model_name", "benchmark", "score"]


def parse_aa(payload, fetched_date):
    """Flatten AA models into long-format (model x benchmark) rows."""
    rows = []
    for model in payload["data"]:
        slug = model["slug"]
        evaluations = model.get("evaluations", {})
        for key in EVAL_KEYS:
            score = evaluations.get(key)
            if score is None:
                continue
            rows.append({
                "fetched_date": fetched_date,
                "source_model_name": slug,
                "benchmark": key,
                "score": score,
            })
    return rows


def fetch():
    """GET the AA model data (requires ARTIFICIALANALYSIS_API_KEY)."""
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise SystemExit(
            f"{API_KEY_ENV} not set. Create a free key at "
            "https://artificialanalysis.ai (Insights Platform) and export it."
        )
    request = urllib.request.Request(
        MODELS_URL,
        headers={"x-api-key": key, "User-Agent": "aisetup-pricetrack"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)
