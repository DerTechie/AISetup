# Phase 2 — Triage Advisor Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Hermes skill that asks the local model whether the current task should run on `local` (fast/free) or `deep` (cloud), surfaces a recommendation the user confirms, and logs each call for later trust-building — never switching models itself.

**Architecture:** A stdlib-only Python script (`triage_advisor.py`) splits into pure, unit-tested functions (prompt building, response parsing, log formatting, output formatting) plus one I/O function that POSTs to the **LiteLLM gateway's `local` route** over HTTP (engine-agnostic — survives the Phase 4 MLX swap, and sidesteps the resolved §12 "can a skill invoke a specific model" item). A `SKILL.md` encodes the hybrid trigger: a manual invocation path plus a fact-keyed description so Hermes proposes it only on concrete signals (large context, explicit market/strategic/current-info research). Source lives in the repo under `hermes/skills/triage-advisor/` and is deployed to Hermes' skills dir on the Arch box, mirroring how `mac/` configs are version-controlled then copied to the Mac.

**Tech Stack:** Python 3.11+ (stdlib only: `urllib`, `json`, `dataclasses`), pytest (tests only), LiteLLM gateway (Phase 1), Hermes Agent skill system.

---

## Prerequisites — confirm/collect before starting

| Value | How to get it | Used in |
|---|---|---|
| Repo root | `/home/dertechie/Organizations/DerTechie/AISetup` | all tasks |
| `LITELLM_BASE_URL` | The gateway base URL Hermes already uses, e.g. `http://MAC_HOST:4000/v1` | Tasks 4–5, 7 |
| `LITELLM_MASTER_KEY` | The gateway master key from Phase 1 (`mac/.env` on the Mac) | Tasks 4–5, 7 |
| Hermes skills dir | Confirm where Hermes loads skills (expected `~/.hermes/skills/`). Open item from the spec. | Tasks 6–7 |
| Python + pytest | Python 3.11+ available on the Arch box | all tasks |

- [ ] **Prereq step: create the skill dir and a test venv**

```bash
mkdir -p hermes/skills/triage-advisor
cd hermes/skills/triage-advisor
python3 -m venv .venv
.venv/bin/pip install -q pytest
echo ".venv/" > .gitignore
```

Expected: `.venv/` exists; `.venv/bin/python -m pytest --version` prints a pytest version. All test/run commands below are run **from `hermes/skills/triage-advisor/`**.

---

## Task 1: Scaffold module + `Recommendation` + `build_prompt`

**Files:**
- Create: `hermes/skills/triage-advisor/triage_advisor.py`
- Test: `hermes/skills/triage-advisor/test_triage_advisor.py`

- [ ] **Step 1: Write the failing test**

In `test_triage_advisor.py`:

```python
from triage_advisor import Recommendation, build_prompt


def test_build_prompt_includes_rubric_and_task():
    msgs = build_prompt("summarize my inbox")
    assert msgs[0]["role"] == "system"
    assert "LOCAL" in msgs[0]["content"] and "DEEP" in msgs[0]["content"]
    assert msgs[1]["role"] == "user"
    assert "summarize my inbox" in msgs[1]["content"]


def test_recommendation_defaults_signals_to_empty_list():
    rec = Recommendation("local", "routine")
    assert rec.signals == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest test_triage_advisor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'triage_advisor'`.

- [ ] **Step 3: Write minimal implementation**

In `triage_advisor.py`:

```python
#!/usr/bin/env python3
"""Triage advisor: ask the local model whether a task should run on `local` or `deep`."""
from __future__ import annotations

from dataclasses import dataclass, field

JUDGE_MODEL = "local"

RUBRIC = """You are a routing advisor for an AI agent. Decide whether a task should run \
on the LOCAL model (fast, free, private; good for routine triage, summaries, drafting, \
simple Q&A) or the DEEP cloud model (stronger reasoning + large context; for heavy \
strategic/market research, tasks needing large context, or tasks needing current/web \
information).

Respond with ONLY a JSON object, no prose, in exactly this shape:
{"recommendation": "local" | "deep", "reason": "<one short sentence>", "signals": ["<signal>", ...]}"""


@dataclass
class Recommendation:
    recommendation: str  # "local" | "deep" | "unknown"
    reason: str
    signals: list[str] = field(default_factory=list)


def build_prompt(task: str) -> list[dict]:
    return [
        {"role": "system", "content": RUBRIC},
        {"role": "user", "content": f"Task to route:\n{task}"},
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest test_triage_advisor.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd ../../..   # back to repo root
git add hermes/skills/triage-advisor/triage_advisor.py hermes/skills/triage-advisor/test_triage_advisor.py hermes/skills/triage-advisor/.gitignore
git commit -m "feat(triage): scaffold judge module with rubric prompt builder"
```

---

## Task 2: `parse_recommendation` (valid + malformed → unknown)

**Files:**
- Modify: `hermes/skills/triage-advisor/triage_advisor.py`
- Test: `hermes/skills/triage-advisor/test_triage_advisor.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_triage_advisor.py`:

```python
from triage_advisor import parse_recommendation


def test_parse_valid_json():
    raw = '{"recommendation": "deep", "reason": "market analysis", "signals": ["strategic"]}'
    rec = parse_recommendation(raw)
    assert rec.recommendation == "deep"
    assert rec.reason == "market analysis"
    assert rec.signals == ["strategic"]


def test_parse_malformed_falls_back_to_unknown():
    rec = parse_recommendation("I think you should use deep, definitely")
    assert rec.recommendation == "unknown"
    assert "deep" in rec.reason


def test_parse_unexpected_recommendation_value_is_unknown():
    rec = parse_recommendation('{"recommendation": "cloud", "reason": "x"}')
    assert rec.recommendation == "unknown"
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from skill dir): `.venv/bin/python -m pytest test_triage_advisor.py -k parse -v`
Expected: FAIL with `ImportError: cannot import name 'parse_recommendation'`.

- [ ] **Step 3: Write minimal implementation**

Add `import json` at the top of `triage_advisor.py` (with the other imports), then add:

```python
def parse_recommendation(raw: str) -> Recommendation:
    try:
        data = json.loads(raw)
        rec = data["recommendation"]
        if rec not in ("local", "deep"):
            raise ValueError(f"unexpected recommendation: {rec!r}")
        return Recommendation(
            recommendation=rec,
            reason=str(data.get("reason", "")).strip(),
            signals=[str(s) for s in data.get("signals", [])],
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return Recommendation(recommendation="unknown", reason=raw.strip(), signals=[])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest test_triage_advisor.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd ../../..
git add hermes/skills/triage-advisor/triage_advisor.py hermes/skills/triage-advisor/test_triage_advisor.py
git commit -m "feat(triage): parse judge JSON, fall back to unknown on bad output"
```

---

## Task 3: `format_log_line` + `format_output`

**Files:**
- Modify: `hermes/skills/triage-advisor/triage_advisor.py`
- Test: `hermes/skills/triage-advisor/test_triage_advisor.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_triage_advisor.py`:

```python
import json
from datetime import datetime, timezone

from triage_advisor import format_log_line, format_output


def test_format_log_line_is_valid_jsonl():
    rec = Recommendation("local", "routine", ["summary"])
    when = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    data = json.loads(format_log_line(rec, "my task", when))
    assert data["recommendation"] == "local"
    assert data["task"] == "my task"
    assert data["reason"] == "routine"
    assert data["signals"] == ["summary"]
    assert data["ts"].startswith("2026-05-20")


def test_format_output_deep_suggests_model_deep():
    out = format_output(Recommendation("deep", "needs big context", ["large-context"]))
    assert "deep" in out
    assert "/model deep" in out
    assert "won't switch automatically" in out


def test_format_output_local_suggests_model_local():
    out = format_output(Recommendation("local", "routine", []))
    assert "/model local" in out


def test_format_output_unknown_shows_raw():
    out = format_output(Recommendation("unknown", "garbled text", []))
    assert "Could not parse" in out
    assert "garbled text" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest test_triage_advisor.py -k "format" -v`
Expected: FAIL with `ImportError: cannot import name 'format_log_line'`.

- [ ] **Step 3: Write minimal implementation**

No new imports are needed here (`when` is passed in by the caller). Add this to `triage_advisor.py`:

```python
def format_log_line(rec: Recommendation, task: str, when) -> str:
    return json.dumps({
        "ts": when.isoformat(),
        "task": task,
        "recommendation": rec.recommendation,
        "reason": rec.reason,
        "signals": rec.signals,
    })


def format_output(rec: Recommendation) -> str:
    if rec.recommendation == "unknown":
        return ("Could not parse a clean recommendation from the local judge.\n"
                f"Raw judge output: {rec.reason}")
    switch = "`/model deep`" if rec.recommendation == "deep" else "`/model local`"
    signals = f" (signals: {', '.join(rec.signals)})" if rec.signals else ""
    return (f"Recommend **{rec.recommendation}** — {rec.reason}{signals}.\n"
            f"Switch with {switch}? (your call — I won't switch automatically.)")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest test_triage_advisor.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
cd ../../..
git add hermes/skills/triage-advisor/triage_advisor.py hermes/skills/triage-advisor/test_triage_advisor.py
git commit -m "feat(triage): JSONL log line + human-facing recommendation output"
```

---

## Task 4: `call_gateway` (mocked) + `main` wiring

**Files:**
- Modify: `hermes/skills/triage-advisor/triage_advisor.py`
- Test: `hermes/skills/triage-advisor/test_triage_advisor.py`

- [ ] **Step 1: Write the failing test**

Append to `test_triage_advisor.py`:

```python
import triage_advisor


def test_call_gateway_posts_and_extracts_content(monkeypatch):
    captured = {}

    class FakeResp:
        def __init__(self, payload): self._p = payload
        def read(self): return json.dumps(self._p).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)
        captured["auth"] = req.headers.get("Authorization")
        return FakeResp(
            {"choices": [{"message": {"content": '{"recommendation": "local", "reason": "ok"}'}}]})

    monkeypatch.setattr(triage_advisor.urllib.request, "urlopen", fake_urlopen)
    content = triage_advisor.call_gateway(
        [{"role": "user", "content": "hi"}], "http://mac:4000/v1", "sk-test")
    assert captured["url"] == "http://mac:4000/v1/chat/completions"
    assert captured["body"]["model"] == "local"
    assert captured["auth"] == "Bearer sk-test"
    assert "local" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest test_triage_advisor.py -k call_gateway -v`
Expected: FAIL with `AttributeError: module 'triage_advisor' has no attribute 'call_gateway'` (or an import/urllib error).

- [ ] **Step 3: Write the implementation**

Add `import os`, `import sys`, `import urllib.request` to the imports in `triage_advisor.py`, then add:

```python
def call_gateway(messages: list[dict], base_url: str, api_key: str) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({"model": JUDGE_MODEL, "messages": messages}).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read())
    return payload["choices"][0]["message"]["content"]


def main(argv: list[str]) -> int:
    from datetime import datetime, timezone
    if len(argv) < 2 or not argv[1].strip():
        print('usage: triage_advisor.py "<task description>"', file=sys.stderr)
        return 2
    task = argv[1].strip()
    base_url = os.environ.get("LITELLM_BASE_URL")
    api_key = os.environ.get("LITELLM_MASTER_KEY")
    if not base_url or not api_key:
        print("LITELLM_BASE_URL and LITELLM_MASTER_KEY must be set", file=sys.stderr)
        return 2
    log_path = os.path.expanduser(
        os.environ.get("TRIAGE_LOG_PATH", "~/.hermes/triage-advisor.jsonl"))
    raw = call_gateway(build_prompt(task), base_url, api_key)
    rec = parse_recommendation(raw)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a") as f:
        f.write(format_log_line(rec, task, datetime.now(timezone.utc)) + "\n")
    print(format_output(rec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 4: Run the full suite to verify it passes**

Run: `.venv/bin/python -m pytest test_triage_advisor.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
cd ../../..
git add hermes/skills/triage-advisor/triage_advisor.py hermes/skills/triage-advisor/test_triage_advisor.py
git commit -m "feat(triage): HTTP call to gateway local route + main() CLI entrypoint"
```

---

## Task 5: Live smoke test against the gateway

**Files:** none (verification only).

- [ ] **Step 1: Export the gateway env vars**

```bash
export LITELLM_BASE_URL="http://MAC_HOST:4000/v1"   # real MAC_HOST
export LITELLM_MASTER_KEY="sk-..."                  # from mac/.env
```

- [ ] **Step 2: Run the script on a routine task**

Run (from skill dir): `.venv/bin/python triage_advisor.py "Summarize today's unread emails into 3 bullets"`
Expected: prints `Recommend **local** — ...` with a `/model local` suggestion. The gateway dashboard (`MAC_HOST:4000/ui`) shows a new `local` request.

- [ ] **Step 3: Run the script on a heavy task**

Run: `.venv/bin/python triage_advisor.py "Analyze the competitive positioning of my SaaS against three named competitors using current market data"`
Expected: prints `Recommend **deep** — ...` with a `/model deep` suggestion.

- [ ] **Step 4: Confirm the log was written**

Run: `tail -n 2 ~/.hermes/triage-advisor.jsonl`
Expected: two JSONL lines, one per call, each with `ts`, `task`, `recommendation`, `reason`, `signals`.

**If the recommendations are obviously wrong** on these clear-cut cases, that's the empirical signal (per the spec) to switch the judge to `local-think`: change `JUDGE_MODEL = "local"` to `"local-think"` and re-test. Record the observation in the journal either way.

---

## Task 6: Write `SKILL.md` and deploy to Hermes

**Files:**
- Create: `hermes/skills/triage-advisor/SKILL.md`

- [ ] **Step 1: Write `SKILL.md`**

```markdown
---
name: triage-advisor
description: Use when the user is unsure whether a task should run on the local or deep (cloud) model, OR when a task involves large context, explicit market/strategic/business research, or needs current/web information. Recommends deep vs local — the user confirms; it does NOT switch models itself.
---

# Triage Advisor

Recommend whether the current task should run on the fast **local** model or the **deep** cloud model. The LOCAL model judges; you present its recommendation; the user decides. **Never run `/model` yourself without the user's explicit go-ahead.**

## How to run

1. Write a one-line description of the task being routed.
2. Run (requires `LITELLM_BASE_URL` and `LITELLM_MASTER_KEY` in the environment — the same gateway values Hermes already uses):

   ```bash
   python3 ~/.hermes/skills/triage-advisor/triage_advisor.py "<task description>"
   ```

3. Show the script's recommendation to the user verbatim and wait for their decision. On their explicit confirmation: `/model deep` for deep, `/model local` for local.

## Rules

- **Recommend-only.** Do not switch models automatically.
- Each call is logged to `~/.hermes/triage-advisor.jsonl` automatically — you don't need to log anything.
```

- [ ] **Step 2: Deploy the skill to Hermes' skills dir on the Arch box**

```bash
# Confirm the skills dir first (Prerequisites open item); expected ~/.hermes/skills/
mkdir -p ~/.hermes/skills/triage-advisor
cp hermes/skills/triage-advisor/triage_advisor.py hermes/skills/triage-advisor/SKILL.md ~/.hermes/skills/triage-advisor/
```

Expected: both files present under `~/.hermes/skills/triage-advisor/`.

- [ ] **Step 3: Commit the skill source**

```bash
git add hermes/skills/triage-advisor/SKILL.md
git commit -m "feat(triage): add SKILL.md with hybrid (manual + fact-keyed) trigger"
```

---

## Task 7: Live Hermes verification (manual + fact-nudge)

**Files:** none (verification only).

- [ ] **Step 1: Confirm Hermes loaded the skill**

Restart/refresh Hermes so it picks up the new skill, then ask Hermes to list its available skills (or check its skills view).
Expected: `triage-advisor` appears.

- [ ] **Step 2: Verify the manual path**

In a Hermes session, ask: "Should I run a deep market analysis of my competitors locally or deep? Use the triage advisor."
Expected: Hermes runs the script and shows a `Recommend **deep** — ...` message, and does **not** switch models on its own.

- [ ] **Step 3: Verify the fact-keyed nudge**

Start a turn whose task plainly matches the description's signals (e.g. "Do a market-positioning analysis using current 2026 data") **without** mentioning the advisor.
Expected: Hermes proposes the triage advisor before doing the work. (If it does not, note it — the description wording may need tuning; this is the empirical check flagged in the spec.)

- [ ] **Step 4: Verify a routine turn does NOT trigger the nudge**

Send a routine turn (e.g. "summarize this paragraph") without mentioning the advisor.
Expected: Hermes does not propose the advisor — confirming the nudge is fact-keyed, not firing on everything.

---

## Task 8: Docs + journal + finalize

**Files:**
- Modify: `README.md`
- Modify: `docs/runbook.md`
- Create: `docs/journal/2026-05-20-phase2-triage-advisor-implementation.md`

- [ ] **Step 1: Add a Triage Advisor section to `docs/runbook.md`**

Append:

```markdown
## Triage advisor (Phase 2)
- Skill source: `hermes/skills/triage-advisor/` (repo); deployed to `~/.hermes/skills/triage-advisor/`.
- Manual use: ask Hermes to use the triage advisor, or run
  `python3 ~/.hermes/skills/triage-advisor/triage_advisor.py "<task>"`.
- Needs `LITELLM_BASE_URL` + `LITELLM_MASTER_KEY` in the environment.
- Judge runs on the `local` (no-think) route. Recommend-only — it never switches models.
- Recommendations are logged to `~/.hermes/triage-advisor.jsonl` (review before considering auto-routing).
- To deepen judgment: set `JUDGE_MODEL = "local-think"` in `triage_advisor.py` and redeploy.
```

- [ ] **Step 2: Add a one-line mention to `README.md`** under the routing/overview section noting the triage advisor exists (recommend-only, local-judge). Keep it to 1–2 sentences consistent with the surrounding prose.

- [ ] **Step 3: Write the implementation journal entry**

Create `docs/journal/2026-05-20-phase2-triage-advisor-implementation.md` recording: what was built, the live smoke-test results (which recommendations it gave on the routine vs heavy task), whether the fact-nudge fired correctly, and whether `local` no-think judgment was good enough or you switched to `local-think`. Include the **why** of any deviation.

- [ ] **Step 4: Run the full test suite one last time**

Run (from skill dir): `.venv/bin/python -m pytest test_triage_advisor.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit and push**

```bash
git add README.md docs/runbook.md docs/journal/2026-05-20-phase2-triage-advisor-implementation.md
git commit -m "docs: document triage advisor (runbook, README, journal)"
git push origin main
```

---

## Self-review (coverage against spec §11 Phase 2)

- **Recommend-only, never auto-switch:** enforced in `format_output` wording + SKILL.md rules; verified Task 7 Step 2 ✓
- **Hybrid trigger — manual spine:** SKILL.md "How to run" + Task 7 Step 2 ✓
- **Hybrid trigger — fact-keyed nudge:** SKILL.md `description` names concrete signals; verified Task 7 Steps 3–4 ✓
- **Judges via direct HTTP to gateway `local` route (sidesteps §12):** `call_gateway` (Task 4), `JUDGE_MODEL = "local"` ✓
- **No-think to start, revisit empirically:** `JUDGE_MODEL = "local"`; switch path noted in Task 5 + runbook ✓
- **Structured result (recommendation/reason/signals):** `Recommendation` + `parse_recommendation` (Tasks 1–2) ✓
- **Observability — dashboard + local log:** dashboard shows `local` calls (Task 5 Step 2); JSONL log via `format_log_line` + `main` (Tasks 3–4), confirmed Task 5 Step 4 ✓
- **Rubric from spec examples:** `RUBRIC` constant (Task 1) ✓
- **Out of scope (auto-routing, local-think upgrade):** not implemented; switch paths documented only ✓

**Open items carried (environment-specific, flagged in Prerequisites):** exact Hermes skills dir; real `MAC_HOST`/`LITELLM_MASTER_KEY` values; empirical confirmation that the fact-keyed description triggers Hermes at the right times.
