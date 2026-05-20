#!/usr/bin/env python3
"""Triage advisor: ask the `main` model whether a task should run on `main`, `private`, or `deep`."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

JUDGE_MODEL = "main"

RUBRIC = """You are a routing advisor for an AI agent. The agent fetches data and runs \
tools (email, web, files) by itself, regardless of which model it uses. So do NOT route a \
task to a different model just because it involves emails, files, web pages, or \
current/external data. Routing depends on how much reasoning the task needs and whether it \
must stay on-device.

Recommend MAIN (the fast default cloud model) for routine work: triage, summarizing, \
drafting, extracting, classifying, and simple Q&A — even when the task operates on external \
or current data. MAIN is the default; prefer it unless a clear reason below applies.

Recommend DEEP (stronger cloud reasoning) only when the task needs heavy multi-step \
reasoning or synthesis — strategic or market analysis, comparing many sources, or drawing \
non-obvious conclusions — i.e. when MAIN's answer quality would likely be insufficient.

Recommend PRIVATE (the on-device local model — free but slow) only when the task handles \
sensitive data that should not leave the machine, or is high-volume bulk work where zero \
cost matters and the slowness is acceptable.

Respond with ONLY a JSON object, no prose, in exactly this shape:
{"recommendation": "main" | "deep" | "private", "reason": "<one short sentence>", "signals": ["<signal>", ...]}"""


@dataclass
class Recommendation:
    recommendation: str  # "main" | "private" | "deep" | "unknown"
    reason: str
    signals: list[str] = field(default_factory=list)


def build_prompt(task: str) -> list[dict]:
    return [
        {"role": "system", "content": RUBRIC},
        {"role": "user", "content": f"Task to route:\n{task}"},
    ]


def parse_recommendation(raw: str) -> Recommendation:
    try:
        data = json.loads(raw)
        rec = data["recommendation"]
        if rec not in ("main", "private", "deep"):
            raise ValueError(f"unexpected recommendation: {rec!r}")
        return Recommendation(
            recommendation=rec,
            reason=str(data.get("reason", "")).strip(),
            signals=[str(s) for s in data.get("signals", [])],
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return Recommendation(recommendation="unknown", reason=raw.strip(), signals=[])


def format_log_line(rec: Recommendation, task: str, when: datetime) -> str:
    return json.dumps({
        "ts": when.isoformat(),
        "task": task,
        "recommendation": rec.recommendation,
        "reason": rec.reason,
        "signals": rec.signals,
    })


def format_output(rec: Recommendation) -> str:
    if rec.recommendation == "unknown":
        return ("Could not parse a clean recommendation from the judge.\n"
                f"Raw judge output: {rec.reason}")
    signals = f" (signals: {', '.join(rec.signals)})" if rec.signals else ""
    if rec.recommendation == "main":
        action = "Stay on `main` (the default) — no switch needed"
    else:
        action = f"Switch with `/model {rec.recommendation}`?"
    return (f"Recommend **{rec.recommendation}** — {rec.reason}{signals}.\n"
            f"{action} (your call — I won't switch automatically.)")


def read_hermes_config_creds(config_path: str) -> tuple[str | None, str | None]:
    """Read model.base_url and model.api_key from a Hermes config.yaml.

    Minimal parser for the known Hermes config shape (avoids a PyYAML
    dependency, since the skill runs under a bare `python3`). Returns
    (None, None) if the file is missing or the keys aren't found.
    """
    try:
        with open(os.path.expanduser(config_path)) as f:
            lines = f.readlines()
    except OSError:
        return None, None
    base_url = api_key = None
    in_model = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line[:1] not in (" ", "\t"):  # a top-level key (no indentation)
            in_model = stripped.rstrip(":") == "model"
            continue
        if in_model and stripped.startswith("base_url:"):
            base_url = stripped.split(":", 1)[1].strip().strip("\"'")
        elif in_model and stripped.startswith("api_key:"):
            api_key = stripped.split(":", 1)[1].strip().strip("\"'")
    return base_url, api_key


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
    if len(argv) < 2 or not argv[1].strip():
        print('usage: triage_advisor.py "<task description>"', file=sys.stderr)
        return 2
    task = argv[1].strip()
    base_url = os.environ.get("LITELLM_BASE_URL")
    api_key = os.environ.get("LITELLM_MASTER_KEY")
    if not base_url or not api_key:
        config_path = os.environ.get("HERMES_CONFIG_PATH", "~/.hermes/config.yaml")
        cfg_base, cfg_key = read_hermes_config_creds(config_path)
        base_url = base_url or cfg_base
        api_key = api_key or cfg_key
    if not base_url or not api_key:
        print("No gateway creds: set LITELLM_BASE_URL + LITELLM_MASTER_KEY, or "
              "configure model.base_url + model.api_key in ~/.hermes/config.yaml",
              file=sys.stderr)
        return 2
    log_path = os.path.expanduser(
        os.environ.get("TRIAGE_LOG_PATH", "~/.hermes/triage-advisor.jsonl"))
    try:
        raw = call_gateway(build_prompt(task), base_url, api_key)
    except urllib.error.URLError as exc:
        print(f"Could not reach gateway: {exc.reason}", file=sys.stderr)
        return 1
    except TimeoutError:
        print("Could not reach gateway: timed out (is the gateway reachable?)",
              file=sys.stderr)
        return 1
    rec = parse_recommendation(raw)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a") as f:
        f.write(format_log_line(rec, task, datetime.now(timezone.utc)) + "\n")
    print(format_output(rec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
