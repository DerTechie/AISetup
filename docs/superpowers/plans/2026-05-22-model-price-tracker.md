# Model Price Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A zero-dependency Python tool that snapshots current OpenRouter model prices to a committed CSV, fetches Artificial Analysis quality scores to a second CSV, and joins them (via a hand-curated id-map) into a price-vs-quality CSV — git carries the price history.

**Architecture:** A `pricing/` Python package. Each data source has a *pure* `parse_*()` (transform, unit-tested against fixtures) split from a *thin* `fetch()` (HTTP). `csvio` does deterministic, atomic CSV writes. `join` merges prices + scores by id-map. A `pricetrack.py` CLI wires it together (`fetch-prices | fetch-scores | suggest-map | join | all`).

**Tech Stack:** Python 3.14 stdlib only at runtime (`urllib`, `json`, `csv`, `decimal`, `difflib`, `argparse`). `pytest` for tests, installed in a local `pricing/.venv` (dev-only; runtime stays dependency-free).

**Spec:** `docs/superpowers/specs/2026-05-22-openrouter-price-tracker-design.md`

---

## File Structure

```
pricing/
  __init__.py
  pricetrack.py              # CLI: python -m pricing.pricetrack <cmd>
  csvio.py                   # fmt_mtok(), write_csv() (sort + atomic temp-rename)
  join.py                    # build_price_vs_quality() (pure)
  sources/
    __init__.py
    openrouter.py            # parse_models() (pure) + fetch() (thin)
    artificialanalysis.py    # parse_aa() (pure) + fetch() (thin, needs API key)
  data/
    openrouter-prices.csv          # full snapshot, committed
    scores-artificialanalysis.csv  # long format, committed
    model-id-map.csv               # curated: openrouter_id,aa_slug
    price-vs-quality.csv           # derived join, committed
  tests/
    __init__.py
    fixtures/
      openrouter_sample.json
      artificialanalysis_sample.json
    test_csvio.py
    test_openrouter.py
    test_artificialanalysis.py
    test_join.py
  requirements-dev.txt       # pytest
  README.md
```

All commands below are run **from the repo root** `/home/dertechie/Organizations/DerTechie/AISetup`. Tests run via `pricing/.venv/bin/pytest`.

---

## Task 0: Scaffold package and dev venv

**Files:**
- Create: `pricing/__init__.py`, `pricing/sources/__init__.py`, `pricing/tests/__init__.py`
- Create: `pricing/requirements-dev.txt`
- Modify: `.gitignore`

- [ ] **Step 1: Create package directories and empty package markers**

```bash
mkdir -p pricing/sources pricing/data pricing/tests/fixtures
touch pricing/__init__.py pricing/sources/__init__.py pricing/tests/__init__.py
```

- [ ] **Step 2: Create dev requirements file**

Create `pricing/requirements-dev.txt`:

```
pytest
```

- [ ] **Step 3: Ignore local virtualenvs**

Add this block to the end of `.gitignore`:

```
# Local virtualenvs
.venv/
```

- [ ] **Step 4: Create the dev venv and install pytest**

```bash
python3 -m venv pricing/.venv
pricing/.venv/bin/pip install -q -r pricing/requirements-dev.txt
pricing/.venv/bin/pytest --version
```

Expected: prints a `pytest 8.x` version line.

- [ ] **Step 5: Commit**

```bash
git add pricing/__init__.py pricing/sources/__init__.py pricing/tests/__init__.py pricing/requirements-dev.txt .gitignore
git commit -m "chore(pricing): scaffold package and dev venv"
```

---

## Task 1: `csvio` — number formatting + deterministic atomic write

**Files:**
- Create: `pricing/csvio.py`
- Test: `pricing/tests/test_csvio.py`

`fmt_mtok` converts an OpenRouter per-token USD string to USD-per-million-tokens, losslessly (Decimal), with no scientific notation and no trailing-zero noise. A missing value (`None`) becomes `""`; an explicit `"0"` (genuinely free) stays `"0"`.

- [ ] **Step 1: Write the failing tests**

Create `pricing/tests/test_csvio.py`:

```python
import csv

from pricing.csvio import fmt_mtok, write_csv


def test_fmt_mtok_converts_per_token_to_per_million():
    # 0.0000025 USD/token -> 2.5 USD per 1M tokens
    assert fmt_mtok("0.0000025") == "2.5"


def test_fmt_mtok_keeps_explicit_zero():
    assert fmt_mtok("0") == "0"


def test_fmt_mtok_missing_is_blank():
    assert fmt_mtok(None) == ""


def test_fmt_mtok_no_scientific_notation_for_small_values():
    # 0.00000018 USD/token -> 0.18 per 1M, must not render as 1.8E-1
    out = fmt_mtok("0.00000018")
    assert out == "0.18"
    assert "E" not in out and "e" not in out


def test_write_csv_sorts_and_is_readable(tmp_path):
    path = tmp_path / "out.csv"
    rows = [{"id": "b", "v": "2"}, {"id": "a", "v": "1"}]
    write_csv(str(path), ["id", "v"], rows, sort_key=lambda r: r["id"])
    with open(path, newline="") as f:
        got = list(csv.DictReader(f))
    assert [r["id"] for r in got] == ["a", "b"]


def test_write_csv_atomic_leaves_no_tmp(tmp_path):
    path = tmp_path / "out.csv"
    write_csv(str(path), ["id"], [{"id": "x"}], sort_key=lambda r: r["id"])
    leftovers = [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pricing/.venv/bin/pytest pricing/tests/test_csvio.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pricing.csvio'`.

- [ ] **Step 3: Implement `csvio.py`**

Create `pricing/csvio.py`:

```python
"""Deterministic, atomic CSV writing and price-unit formatting."""
import csv
import os
import tempfile
from decimal import Decimal

_PER_MILLION = Decimal(1_000_000)


def fmt_mtok(per_token):
    """OpenRouter per-token USD string -> USD per 1M tokens.

    None -> "" (field absent). "0"/"0.0" -> "0" (genuinely free).
    Lossless via Decimal; plain decimal notation, no trailing-zero noise.
    """
    if per_token is None or per_token == "":
        return ""
    value = (Decimal(str(per_token)) * _PER_MILLION).normalize()
    # `+ Decimal(0)` collapses exponents like 2.5E+1 back to plain form.
    return f"{value + Decimal(0):f}"


def write_csv(path, fieldnames, rows, sort_key):
    """Write rows sorted by sort_key, atomically (temp file + os.replace)."""
    ordered = sorted(rows, key=sort_key)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(ordered)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pricing/.venv/bin/pytest pricing/tests/test_csvio.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add pricing/csvio.py pricing/tests/test_csvio.py
git commit -m "feat(pricing): deterministic CSV writer + per-Mtok formatter"
```

---

## Task 2: OpenRouter source — `parse_models` (pure)

**Files:**
- Create: `pricing/sources/openrouter.py`
- Test: `pricing/tests/test_openrouter.py`
- Create fixture: `pricing/tests/fixtures/openrouter_sample.json`

`parse_models(payload, fetched_date)` flattens the `/models` response to one row per model. Token-priced fields (`prompt`, `completion`, `input_cache_read`, `input_cache_write`, `internal_reasoning`) are converted to per-Mtok. Native-unit fields (`image`, `audio`, `web_search`, `request`) are kept raw with a `_usd` suffix (their OpenRouter unit isn't per-token; documented in README). Missing pricing keys become `""`.

- [ ] **Step 1: Create the fixture**

Create `pricing/tests/fixtures/openrouter_sample.json` (three models: paid+cache, free, image-priced):

```json
{
  "data": [
    {
      "id": "vendor/big-model",
      "name": "Vendor: Big Model",
      "context_length": 1000000,
      "architecture": {"modality": "text->text"},
      "pricing": {
        "prompt": "0.0000025",
        "completion": "0.0000075",
        "input_cache_read": "0.0000006",
        "request": "0.01"
      }
    },
    {
      "id": "vendor/free-model",
      "name": "Vendor: Free Model",
      "context_length": 8192,
      "architecture": {"modality": "text->text"},
      "pricing": {"prompt": "0", "completion": "0"}
    },
    {
      "id": "vendor/vision-model",
      "name": "Vendor: Vision Model",
      "context_length": 200000,
      "architecture": {"modality": "text+image->text"},
      "pricing": {
        "prompt": "0.0000011",
        "completion": "0.0000044",
        "image": "0.0014"
      }
    }
  ]
}
```

- [ ] **Step 2: Write the failing tests**

Create `pricing/tests/test_openrouter.py`:

```python
import json
import pathlib

from pricing.sources.openrouter import parse_models, PRICE_FIELDNAMES

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "openrouter_sample.json"


def _rows():
    payload = json.loads(FIXTURE.read_text())
    return parse_models(payload, "2026-05-22")


def test_one_row_per_model():
    assert len(_rows()) == 3


def test_token_fields_converted_to_mtok():
    big = next(r for r in _rows() if r["id"] == "vendor/big-model")
    assert big["prompt_usd_per_mtok"] == "2.5"
    assert big["completion_usd_per_mtok"] == "7.5"
    assert big["input_cache_read_usd_per_mtok"] == "0.6"


def test_missing_pricing_fields_are_blank():
    big = next(r for r in _rows() if r["id"] == "vendor/big-model")
    assert big["image_usd"] == ""
    assert big["internal_reasoning_usd_per_mtok"] == ""


def test_free_model_keeps_zero():
    free = next(r for r in _rows() if r["id"] == "vendor/free-model")
    assert free["prompt_usd_per_mtok"] == "0"
    assert free["completion_usd_per_mtok"] == "0"


def test_native_unit_fields_kept_raw():
    big = next(r for r in _rows() if r["id"] == "vendor/big-model")
    assert big["request_usd"] == "0.01"
    vision = next(r for r in _rows() if r["id"] == "vendor/vision-model")
    assert vision["image_usd"] == "0.0014"


def test_carries_metadata_and_date():
    vision = next(r for r in _rows() if r["id"] == "vendor/vision-model")
    assert vision["fetched_date"] == "2026-05-22"
    assert vision["name"] == "Vendor: Vision Model"
    assert vision["context_length"] == 200000
    assert vision["modality"] == "text+image->text"


def test_every_row_has_all_columns():
    for row in _rows():
        assert set(row.keys()) == set(PRICE_FIELDNAMES)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pricing/.venv/bin/pytest pricing/tests/test_openrouter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pricing.sources.openrouter'`.

- [ ] **Step 4: Implement `openrouter.py`**

Create `pricing/sources/openrouter.py`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pricing/.venv/bin/pytest pricing/tests/test_openrouter.py -v`
Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add pricing/sources/openrouter.py pricing/tests/test_openrouter.py pricing/tests/fixtures/openrouter_sample.json
git commit -m "feat(pricing): OpenRouter price parser with fixture tests"
```

---

## Task 3: CLI `fetch-prices` + first live snapshot

**Files:**
- Create: `pricing/pricetrack.py`
- Output (committed data): `pricing/data/openrouter-prices.csv`

- [ ] **Step 1: Implement the CLI with the `fetch-prices` command**

Create `pricing/pricetrack.py`:

```python
"""pricetrack CLI: snapshot prices, fetch scores, suggest map, join.

Run from the repo root: python -m pricing.pricetrack <command>
"""
import argparse
import datetime
import os

from pricing import csvio
from pricing.sources import openrouter

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PRICES_CSV = os.path.join(DATA_DIR, "openrouter-prices.csv")


def _today():
    return datetime.date.today().isoformat()


def cmd_fetch_prices(_args):
    payload = openrouter.fetch()
    rows = openrouter.parse_models(payload, _today())
    csvio.write_csv(
        PRICES_CSV, openrouter.PRICE_FIELDNAMES, rows, sort_key=lambda r: r["id"]
    )
    print(f"Wrote {len(rows)} models to {PRICES_CSV}")


def build_parser():
    parser = argparse.ArgumentParser(prog="pricetrack")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch-prices", help="snapshot OpenRouter prices").set_defaults(
        func=cmd_fetch_prices
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it live against OpenRouter**

Run: `python3 -m pricing.pricetrack fetch-prices`
Expected: `Wrote 35x models to .../openrouter-prices.csv` (≈358 today).

- [ ] **Step 3: Verify the output CSV by hand**

Run: `head -3 pricing/data/openrouter-prices.csv && wc -l pricing/data/openrouter-prices.csv`
Expected: header row matching `PRICE_FIELDNAMES`, then alphabetically-sorted model rows; line count = models + 1. Confirm a known free model shows `0` and a paid model shows a sensible per-Mtok number (single-digit to low-hundreds).

- [ ] **Step 4: Commit the tool and the first snapshot**

```bash
git add pricing/pricetrack.py pricing/data/openrouter-prices.csv
git commit -m "feat(pricing): fetch-prices CLI + first OpenRouter price snapshot"
```

---

## Task 4: Artificial Analysis source — `parse_aa` (pure)

**Files:**
- Create: `pricing/sources/artificialanalysis.py`
- Test: `pricing/tests/test_artificialanalysis.py`
- Create fixture: `pricing/tests/fixtures/artificialanalysis_sample.json`

`parse_aa(payload, fetched_date)` emits **long-format** rows (one per model × benchmark) keyed on the AA `slug`. Only non-null evaluation scores are emitted. The AA response wraps models in `data`; each model has `slug` and an `evaluations` object.

- [ ] **Step 1: Create the fixture** (shape per AA `/data/llms/models` docs)

Create `pricing/tests/fixtures/artificialanalysis_sample.json`:

```json
{
  "status": 200,
  "data": [
    {
      "id": "o3-mini",
      "name": "o3-mini",
      "slug": "o3-mini",
      "evaluations": {
        "artificial_analysis_intelligence_index": 62.9,
        "artificial_analysis_coding_index": 55.8,
        "artificial_analysis_math_index": 87.2,
        "mmlu_pro": 79.0,
        "gpqa": 74.0,
        "hle": null
      }
    },
    {
      "id": "tiny-model",
      "name": "Tiny Model",
      "slug": "tiny-model",
      "evaluations": {
        "artificial_analysis_intelligence_index": 20.1,
        "artificial_analysis_coding_index": null
      }
    }
  ]
}
```

- [ ] **Step 2: Write the failing tests**

Create `pricing/tests/test_artificialanalysis.py`:

```python
import json
import pathlib

from pricing.sources.artificialanalysis import parse_aa, SCORE_FIELDNAMES

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "artificialanalysis_sample.json"


def _rows():
    payload = json.loads(FIXTURE.read_text())
    return parse_aa(payload, "2026-05-22")


def test_long_format_one_row_per_model_benchmark():
    rows = _rows()
    # o3-mini: 5 non-null of 6; tiny-model: 1 non-null of 2 => 6 rows
    assert len(rows) == 6


def test_null_scores_are_skipped():
    rows = _rows()
    assert not any(r["source_model_name"] == "o3-mini" and r["benchmark"] == "hle" for r in rows)
    assert not any(r["source_model_name"] == "tiny-model" and r["benchmark"] == "artificial_analysis_coding_index" for r in rows)


def test_score_value_and_keys():
    rows = _rows()
    intel = next(
        r for r in rows
        if r["source_model_name"] == "o3-mini"
        and r["benchmark"] == "artificial_analysis_intelligence_index"
    )
    assert intel["score"] == 62.9
    assert intel["fetched_date"] == "2026-05-22"
    assert set(intel.keys()) == set(SCORE_FIELDNAMES)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pricing/.venv/bin/pytest pricing/tests/test_artificialanalysis.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pricing.sources.artificialanalysis'`.

- [ ] **Step 4: Implement `artificialanalysis.py`**

Create `pricing/sources/artificialanalysis.py`:

```python
"""Artificial Analysis source: pure parse + thin fetch.

Data: Artificial Analysis (https://artificialanalysis.ai). Attribution required.
Set the API key in env var ARTIFICIALANALYSIS_API_KEY (free tier, 1000 req/day).
"""
import json
import os
import urllib.request

MODELS_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
API_KEY_ENV = "ARTIFICIALANALYSIS_API_KEY"

# Evaluation keys we record, in stable order.
EVAL_KEYS = [
    "artificial_analysis_intelligence_index",
    "artificial_analysis_coding_index",
    "artificial_analysis_math_index",
    "mmlu_pro", "gpqa", "hle", "livecodebench", "scicode", "math_500", "aime",
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pricing/.venv/bin/pytest pricing/tests/test_artificialanalysis.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add pricing/sources/artificialanalysis.py pricing/tests/test_artificialanalysis.py pricing/tests/fixtures/artificialanalysis_sample.json
git commit -m "feat(pricing): Artificial Analysis score parser with fixture tests"
```

---

## Task 5: CLI `fetch-scores` + first live score fetch

**Files:**
- Modify: `pricing/pricetrack.py`
- Output (committed data): `pricing/data/scores-artificialanalysis.csv`

**Prerequisite (manual, one-time):** create a free Artificial Analysis Insights account, generate an API key, and export it:
`export ARTIFICIALANALYSIS_API_KEY=<key>`. If you don't have it yet, do Steps 1–2 now and run Steps 3–4 once the key is set.

- [ ] **Step 1: Add the `fetch-scores` command to the CLI**

In `pricing/pricetrack.py`, add the import for the AA source. Change:

```python
from pricing.sources import openrouter
```

to:

```python
from pricing.sources import artificialanalysis, openrouter
```

Add the data-path constant after `PRICES_CSV`:

```python
AA_SCORES_CSV = os.path.join(DATA_DIR, "scores-artificialanalysis.csv")
```

Add the command function after `cmd_fetch_prices`:

```python
def cmd_fetch_scores(_args):
    payload = artificialanalysis.fetch()
    rows = artificialanalysis.parse_aa(payload, _today())
    csvio.write_csv(
        AA_SCORES_CSV,
        artificialanalysis.SCORE_FIELDNAMES,
        rows,
        sort_key=lambda r: (r["source_model_name"], r["benchmark"]),
    )
    print(f"Wrote {len(rows)} score rows to {AA_SCORES_CSV}")
```

Register it in `build_parser` after the `fetch-prices` line:

```python
    sub.add_parser("fetch-scores", help="fetch Artificial Analysis scores").set_defaults(
        func=cmd_fetch_scores
    )
```

- [ ] **Step 2: Verify the CLI still parses (no key needed yet)**

Run: `python3 -m pricing.pricetrack --help`
Expected: usage line lists both `fetch-prices` and `fetch-scores`.

- [ ] **Step 3: Run it live (requires the API key)**

Run: `python3 -m pricing.pricetrack fetch-scores`
Expected: `Wrote NNNN score rows to .../scores-artificialanalysis.csv`. If the key is missing, it exits with the clear `ARTIFICIALANALYSIS_API_KEY not set` message — set the key and re-run.

- [ ] **Step 4: Verify the output and commit**

Run: `head -3 pricing/data/scores-artificialanalysis.csv && cut -d, -f3 pricing/data/scores-artificialanalysis.csv | sort -u | head`
Expected: header `fetched_date,source_model_name,benchmark,score`; benchmark column shows the AA eval keys.

```bash
git add pricing/pricetrack.py pricing/data/scores-artificialanalysis.csv
git commit -m "feat(pricing): fetch-scores CLI + first Artificial Analysis snapshot"
```

---

## Task 6: `suggest-map` + curate the id-map

**Files:**
- Modify: `pricing/pricetrack.py`
- Test: add to `pricing/tests/test_join.py` (created here; join logic added in Task 7)
- Output (committed data): `pricing/data/model-id-map.csv`

`suggest_map` proposes an AA slug for each *unmapped* OpenRouter id using normalized-name fuzzy matching (`difflib`). It only prints suggestions — a human edits the map file. The map is `openrouter_id,aa_slug` (blank `aa_slug` = no known counterpart).

- [ ] **Step 1: Write the failing test for the matcher**

Create `pricing/tests/test_join.py` (the `build_price_vs_quality` tests are added in Task 7):

```python
from pricing.join import suggest_map


def test_suggest_map_matches_on_normalized_name():
    price_rows = [
        {"id": "openai/o3-mini", "name": "OpenAI: o3 Mini"},
        {"id": "vendor/unknown", "name": "Vendor: Totally Unique XYZ"},
    ]
    aa_slugs = ["o3-mini", "some-other-model"]
    suggestions = suggest_map(price_rows, aa_slugs, already_mapped=set())
    by_id = {s["openrouter_id"]: s for s in suggestions}
    assert by_id["openai/o3-mini"]["suggested_aa_slug"] == "o3-mini"


def test_suggest_map_skips_already_mapped():
    price_rows = [{"id": "openai/o3-mini", "name": "OpenAI: o3 Mini"}]
    suggestions = suggest_map(price_rows, ["o3-mini"], already_mapped={"openai/o3-mini"})
    assert suggestions == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pricing/.venv/bin/pytest pricing/tests/test_join.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pricing.join'`.

- [ ] **Step 3: Create `join.py` with `suggest_map`** (join function added in Task 7)

Create `pricing/join.py`:

```python
"""Join OpenRouter prices with Artificial Analysis scores via the id-map."""
import difflib
import re

_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(text):
    """Lowercase and strip vendor prefix + non-alphanumerics for matching."""
    text = text.split(":", 1)[-1]  # drop "Vendor: " prefix if present
    return _NORM_RE.sub("", text.lower())


def suggest_map(price_rows, aa_slugs, already_mapped):
    """Suggest an AA slug for each unmapped OpenRouter model (best-effort)."""
    norm_to_slug = {_norm(slug): slug for slug in aa_slugs}
    norm_keys = list(norm_to_slug)
    suggestions = []
    for row in price_rows:
        if row["id"] in already_mapped:
            continue
        match = difflib.get_close_matches(_norm(row["name"]), norm_keys, n=1, cutoff=0.8)
        suggestions.append({
            "openrouter_id": row["id"],
            "name": row["name"],
            "suggested_aa_slug": norm_to_slug[match[0]] if match else "",
        })
    return suggestions
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pricing/.venv/bin/pytest pricing/tests/test_join.py -v`
Expected: 2 passed.

- [ ] **Step 5: Add the `suggest-map` command to the CLI**

In `pricing/pricetrack.py`, add `csv` to the imports at the top:

```python
import argparse
import csv
import datetime
import os
```

Add the import of `join` and the map path constant (after `AA_SCORES_CSV`):

```python
from pricing import join
```
```python
ID_MAP_CSV = os.path.join(DATA_DIR, "model-id-map.csv")
```

Add two small helpers and the command (after `cmd_fetch_scores`):

```python
def _read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def cmd_suggest_map(_args):
    prices = _read_rows(PRICES_CSV)
    scores = _read_rows(AA_SCORES_CSV)
    existing = _read_rows(ID_MAP_CSV)
    aa_slugs = sorted({s["source_model_name"] for s in scores})
    mapped = {m["openrouter_id"] for m in existing if m.get("aa_slug")}
    suggestions = join.suggest_map(prices, aa_slugs, mapped)
    matched = [s for s in suggestions if s["suggested_aa_slug"]]
    print(f"{len(matched)} suggested matches (of {len(suggestions)} unmapped):")
    for s in matched:
        print(f"  {s['openrouter_id']},{s['suggested_aa_slug']}    # {s['name']}")
    print("\nReview, then add the correct lines to", ID_MAP_CSV)
```

Register it in `build_parser`:

```python
    sub.add_parser("suggest-map", help="suggest id-map entries (review by hand)").set_defaults(
        func=cmd_suggest_map
    )
```

- [ ] **Step 6: Seed and curate the id-map**

Create `pricing/data/model-id-map.csv` with just the header:

```
openrouter_id,aa_slug
```

Run: `python3 -m pricing.pricetrack suggest-map`
Then **review each suggestion by hand** and append the correct `openrouter_id,aa_slug` lines (fix wrong fuzzy matches; leave genuinely-absent models out). Keep the file sorted by `openrouter_id`. This is the deliberately-manual curation step — accuracy over coverage.

- [ ] **Step 7: Commit**

```bash
git add pricing/join.py pricing/tests/test_join.py pricing/pricetrack.py pricing/data/model-id-map.csv
git commit -m "feat(pricing): suggest-map matcher + curated id-map"
```

---

## Task 7: `join` + CLI `join`/`all` → price-vs-quality

**Files:**
- Modify: `pricing/join.py`
- Modify: `pricing/pricetrack.py`
- Add tests to: `pricing/tests/test_join.py`
- Output (committed data): `pricing/data/price-vs-quality.csv`

`build_price_vs_quality(price_rows, score_rows, map_rows)` produces one row per OpenRouter model with a 3:1 blended price and the AA intelligence index (blank when unmapped or the score is absent). Blended = `(3*prompt + 1*completion)/4`, blank if either price is blank.

- [ ] **Step 1: Add the failing tests**

Append to `pricing/tests/test_join.py`:

```python
from pricing.join import build_price_vs_quality, JOIN_FIELDNAMES


def test_build_join_blends_price_and_attaches_intelligence():
    prices = [{
        "id": "openai/o3-mini", "name": "OpenAI: o3 Mini",
        "prompt_usd_per_mtok": "1.1", "completion_usd_per_mtok": "4.4",
    }]
    scores = [{
        "source_model_name": "o3-mini",
        "benchmark": "artificial_analysis_intelligence_index", "score": "62.9",
    }]
    id_map = [{"openrouter_id": "openai/o3-mini", "aa_slug": "o3-mini"}]
    rows = build_price_vs_quality(prices, scores, id_map)
    assert len(rows) == 1
    row = rows[0]
    # (3*1.1 + 4.4)/4 = 1.925
    assert row["blended_usd_per_mtok"] == "1.925"
    assert row["aa_intelligence_index"] == "62.9"
    assert set(row.keys()) == set(JOIN_FIELDNAMES)


def test_build_join_blank_when_unmapped():
    prices = [{
        "id": "vendor/unmapped", "name": "Vendor: Unmapped",
        "prompt_usd_per_mtok": "2", "completion_usd_per_mtok": "6",
    }]
    rows = build_price_vs_quality(prices, [], [])
    assert rows[0]["aa_intelligence_index"] == ""
    assert rows[0]["blended_usd_per_mtok"] == "4"


def test_build_join_blank_blend_when_price_missing():
    prices = [{
        "id": "vendor/freeish", "name": "Vendor: Freeish",
        "prompt_usd_per_mtok": "", "completion_usd_per_mtok": "6",
    }]
    rows = build_price_vs_quality(prices, [], [])
    assert rows[0]["blended_usd_per_mtok"] == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `pricing/.venv/bin/pytest pricing/tests/test_join.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_price_vs_quality'`.

- [ ] **Step 3: Implement the join in `join.py`**

Add to the top of `pricing/join.py` (alongside the existing imports):

```python
from decimal import Decimal
```

Append to `pricing/join.py`:

```python
_INTELLIGENCE = "artificial_analysis_intelligence_index"

JOIN_FIELDNAMES = [
    "openrouter_id", "name",
    "prompt_usd_per_mtok", "completion_usd_per_mtok", "blended_usd_per_mtok",
    "aa_intelligence_index",
]


def _blend(prompt, completion):
    """3:1 input:output blended price; '' if either side is blank."""
    if not prompt or not completion:
        return ""
    value = (Decimal(prompt) * 3 + Decimal(completion)) / 4
    return f"{value.normalize() + Decimal(0):f}"


def build_price_vs_quality(price_rows, score_rows, map_rows):
    """One row per OpenRouter model: blended price + AA intelligence index."""
    o2a = {m["openrouter_id"]: m["aa_slug"] for m in map_rows if m.get("aa_slug")}
    intelligence = {
        s["source_model_name"]: s["score"]
        for s in score_rows if s["benchmark"] == _INTELLIGENCE
    }
    rows = []
    for price in price_rows:
        slug = o2a.get(price["id"], "")
        prompt = price["prompt_usd_per_mtok"]
        completion = price["completion_usd_per_mtok"]
        rows.append({
            "openrouter_id": price["id"],
            "name": price["name"],
            "prompt_usd_per_mtok": prompt,
            "completion_usd_per_mtok": completion,
            "blended_usd_per_mtok": _blend(prompt, completion),
            "aa_intelligence_index": intelligence.get(slug, "") if slug else "",
        })
    return rows
```

- [ ] **Step 4: Run to verify pass**

Run: `pricing/.venv/bin/pytest pricing/tests/test_join.py -v`
Expected: 5 passed.

- [ ] **Step 5: Add `join` and `all` commands to the CLI**

In `pricing/pricetrack.py`, add the join output path (after `ID_MAP_CSV`):

```python
PRICE_VS_QUALITY_CSV = os.path.join(DATA_DIR, "price-vs-quality.csv")
```

Add the command functions (after `cmd_suggest_map`):

```python
def cmd_join(_args):
    prices = _read_rows(PRICES_CSV)
    scores = _read_rows(AA_SCORES_CSV)
    id_map = _read_rows(ID_MAP_CSV)
    rows = join.build_price_vs_quality(prices, scores, id_map)
    csvio.write_csv(
        PRICE_VS_QUALITY_CSV, join.JOIN_FIELDNAMES, rows,
        sort_key=lambda r: r["openrouter_id"],
    )
    mapped = sum(1 for r in rows if r["aa_intelligence_index"])
    print(f"Wrote {len(rows)} rows ({mapped} with an intelligence score) to {PRICE_VS_QUALITY_CSV}")


def cmd_all(args):
    cmd_fetch_prices(args)
    cmd_fetch_scores(args)
    cmd_join(args)
```

Register both in `build_parser`:

```python
    sub.add_parser("join", help="join prices + scores via id-map").set_defaults(
        func=cmd_join
    )
    sub.add_parser("all", help="fetch-prices, fetch-scores, then join").set_defaults(
        func=cmd_all
    )
```

- [ ] **Step 6: Run the join live and verify**

Run: `python3 -m pricing.pricetrack join`
Expected: `Wrote 35x rows (NN with an intelligence score) to .../price-vs-quality.csv` — `NN` equals the number of curated map entries that also have an AA intelligence score.

Run: `head -3 pricing/data/price-vs-quality.csv`
Expected: header `openrouter_id,name,prompt_usd_per_mtok,completion_usd_per_mtok,blended_usd_per_mtok,aa_intelligence_index`, sorted rows, intelligence filled only for mapped models.

- [ ] **Step 7: Commit**

```bash
git add pricing/join.py pricing/tests/test_join.py pricing/pricetrack.py pricing/data/price-vs-quality.csv
git commit -m "feat(pricing): price-vs-quality join + join/all CLI commands"
```

---

## Task 8: Documentation (README, runbook, journal)

**Files:**
- Create: `pricing/README.md`
- Modify: `README.md`
- Modify: `docs/runbook.md`
- Create: `docs/journal/2026-05-22-model-price-tracker.md`

- [ ] **Step 1: Write `pricing/README.md`**

Create `pricing/README.md`:

```markdown
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

Quality data is from Artificial Analysis (<https://artificialanalysis.ai>) — attribution required.

## Data files

| File | What |
|---|---|
| `data/openrouter-prices.csv` | One row per model. Token prices in **USD per 1M tokens**; `*_usd` columns are OpenRouter's native per-unit (per request/image) values. Currency: USD. |
| `data/scores-artificialanalysis.csv` | Long format: one row per model × benchmark (AA `slug` as the key). |
| `data/model-id-map.csv` | Hand-curated `openrouter_id,aa_slug`. The join key between the two sources. |
| `data/price-vs-quality.csv` | Derived: blended price (`(3*prompt+1*completion)/4`) + AA intelligence index. |

## Cheapest equal-quality lookup

Sort `price-vs-quality.csv` by `blended_usd_per_mtok` among rows with
`aa_intelligence_index >= N` to find the cheapest model at a quality floor.

## Tests

```bash
pricing/.venv/bin/pytest pricing/tests -v
```

Runtime code is stdlib-only; `pytest` lives in the dev-only `pricing/.venv`.
```

- [ ] **Step 2: Add a pointer in the top-level `README.md`**

Read `README.md`, find the section listing the repo's components/tools (or the doc map), and add one bullet:

```markdown
- **`pricing/`** — OpenRouter price + Artificial Analysis quality tracker; snapshots to committed CSVs for accurate, dated cost comparisons (see [`pricing/README.md`](pricing/README.md)).
```

- [ ] **Step 3: Add an operations entry to `docs/runbook.md`**

Read `docs/runbook.md` and append a section:

```markdown
## Refreshing model prices / scores

From the repo root:

```bash
export ARTIFICIALANALYSIS_API_KEY=<key>     # for scores only
python3 -m pricing.pricetrack all           # refresh prices + scores + join
git add pricing/data && git commit -m "data(pricing): refresh snapshot $(date +%F)"
```

The commit diff is the price-change record. See `pricing/README.md` for details.
```

- [ ] **Step 4: Write the journal entry**

Create `docs/journal/2026-05-22-model-price-tracker.md` capturing the **why** and **what was rejected**:
- Why: needed accurate, dated prices without re-hitting the API each time, and a quality axis to honour "cheapest equal-quality cloud".
- Decisions: **CSV over SQLite** (tiny data; git-diffable; binary blob gives no diff and this repo treats git history as the reconstructable path). **Git as the history layer** (full-snapshot overwrite, not in-file change-log). **USD as source of truth** (no stored EUR; FX drifts). **Prices and scores as separate files joined by a curated id-map** (different sources/cadences; leaderboards don't use OpenRouter ids).
- Rejected/deferred: in-file change-log; LMArena as a second source (format fragility — deferred, the schema already accommodates it); automated cron refresh; storing AA's own prices (OpenRouter is the price truth — what we actually route through).

- [ ] **Step 5: Run the full test suite once more**

Run: `pricing/.venv/bin/pytest pricing/tests -v`
Expected: all tests pass (csvio 6, openrouter 7, artificialanalysis 3, join 5 = 21).

- [ ] **Step 6: Commit**

```bash
git add pricing/README.md README.md docs/runbook.md docs/journal/2026-05-22-model-price-tracker.md
git commit -m "docs(pricing): README, runbook, and decision journal for price tracker"
```

---

## Self-Review notes

- **Spec coverage:** prices snapshot (Tasks 2–3), AA scores (Tasks 4–5), id-map + suggest (Task 6), join/derived view (Task 7), zero-dep + atomic/deterministic writes (Task 1), USD source-of-truth & unit handling (Task 2 + README), git-as-history (full-snapshot overwrite, Task 3), docs/journal (Task 8). LMArena correctly excluded (deferred in spec).
- **Refinement vs spec:** map column is `aa_slug` (AA's stable, readable slug) rather than the spec's looser `aa_model_name`; price CSV keeps `image_usd/audio_usd/web_search_usd/request_usd` as raw native-unit values instead of guessing per-k/per-1k labels — both are faithfulness improvements, noted in the README.
- **Type consistency:** `PRICE_FIELDNAMES`, `SCORE_FIELDNAMES`, `JOIN_FIELDNAMES`, `suggest_map`, `build_price_vs_quality`, `_read_rows` names are used identically across tasks; CLI command funcs (`cmd_fetch_prices/_scores/_suggest_map/_join/_all`) are consistent.
```
