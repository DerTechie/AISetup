#!/usr/bin/env python3
"""Seed the LiteLLM gateway's DB-stored model routes from nas/models.seed.json.

Routes live in Postgres (store_model_in_db: true), so they survive restarts and are
editable in the UI. This script bootstraps a fresh DB (or restores it) from the
git-tracked seed. It is NOT a continuous source of truth — live UI edits can drift
from the seed file; that drift is the accepted trade-off of running all-DB.

Usage (needs the gateway reachable + the master key):
    LITELLM_MASTER_KEY=sk-... python3 nas/seed_models.py [--force] [--dry-run]
    GATEWAY_URL=http://10.63.0.2:4000 (default)

  --force    : delete any existing DB route of the same name, then re-create it
               (use after editing models.seed.json to push the change).
  --dry-run  : print what would happen, change nothing.

Idempotent without --force: a route already present in the DB is left untouched.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://10.63.0.2:4000").rstrip("/")
SEED_FILE = os.path.join(os.path.dirname(__file__), "models.seed.json")


def _request(method, path, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{GATEWAY_URL}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


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


def resolve_env_refs(params):
    """Expand 'os.environ/VAR' values to the real secret at seed time.

    DB-stored models do NOT resolve os.environ/ references at call time the way
    config.yaml models do — they need the literal credential (LiteLLM encrypts it in
    the DB under LITELLM_SALT_KEY). The seed file keeps the os.environ/ ref so it stays
    git-clean; we expand it here from the shell that runs the seed. So run this where the
    secret is present (e.g. on the NAS with the stack .env sourced: `set -a; . .env; set +a`).
    """
    resolved = dict(params)
    for field, value in params.items():
        if isinstance(value, str) and value.startswith("os.environ/"):
            var = value.split("/", 1)[1]
            secret = os.environ.get(var)
            if not secret:
                sys.exit(f"{value} referenced but ${var} is not set in this shell. "
                         "Run the seed where the secret is available.")
            resolved[field] = secret
    return resolved


def existing_db_models(key):
    """Return {model_name: model_id} for routes already stored in the DB."""
    info = _request("GET", "/model/info", key)
    out = {}
    for m in info.get("data", []):
        mi = m.get("model_info", {}) or {}
        if mi.get("db_model"):
            out[m["model_name"]] = mi.get("id")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="replace existing DB routes")
    parser.add_argument("--dry-run", action="store_true", help="print actions, change nothing")
    args = parser.parse_args()

    key = os.environ.get("LITELLM_MASTER_KEY")
    if not key:
        sys.exit("LITELLM_MASTER_KEY not set (the gateway master key).")

    with open(SEED_FILE) as handle:
        seed = json.load(handle)

    try:
        present = existing_db_models(key)
    except urllib.error.HTTPError as err:
        sys.exit(f"Cannot read {GATEWAY_URL}/model/info ({err.code}). "
                 "Is store_model_in_db enabled and the key correct?")

    # One OpenRouter fetch per seed run (covers every openrouter/* entry).
    needs_openrouter = any(
        e["litellm_params"].get("model", "").startswith(OPENROUTER_PREFIX) for e in seed
    )
    openrouter_prices = _fetch_openrouter_prices() if needs_openrouter else {}

    for entry in seed:
        name = entry["model_name"]
        if name in present:
            if not args.force:
                print(f"= {name}: already in DB, skipping (use --force to replace)")
                continue
            print(f"~ {name}: deleting existing DB route {present[name]}")
            if not args.dry_run:
                _request("POST", "/model/delete", key, {"id": present[name]})
        print(f"+ {name}: creating -> {entry['litellm_params']['model']}")
        if not args.dry_run:
            enriched = enrich_openrouter_prices(
                resolve_env_refs(entry["litellm_params"]), openrouter_prices
            )
            payload = dict(entry, litellm_params=enriched)
            _request("POST", "/model/new", key, payload)

    print("\nVerify:  curl -s -H \"Authorization: Bearer $LITELLM_MASTER_KEY\" "
          f"{GATEWAY_URL}/model/info | python3 -m json.tool")


if __name__ == "__main__":
    main()
