#!/usr/bin/env python3
"""Triage advisor: ask the local model whether a task should run on `local` or `deep`."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

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
        return ("Could not parse a clean recommendation from the local judge.\n"
                f"Raw judge output: {rec.reason}")
    switch = "`/model deep`" if rec.recommendation == "deep" else "`/model local`"
    signals = f" (signals: {', '.join(rec.signals)})" if rec.signals else ""
    return (f"Recommend **{rec.recommendation}** — {rec.reason}{signals}.\n"
            f"Switch with {switch}? (your call — I won't switch automatically.)")


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
        print("LITELLM_BASE_URL and LITELLM_MASTER_KEY must be set", file=sys.stderr)
        return 2
    log_path = os.path.expanduser(
        os.environ.get("TRIAGE_LOG_PATH", "~/.hermes/triage-advisor.jsonl"))
    try:
        raw = call_gateway(build_prompt(task), base_url, api_key)
    except urllib.error.URLError as exc:
        print(f"Could not reach gateway: {exc.reason}", file=sys.stderr)
        return 1
    rec = parse_recommendation(raw)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a") as f:
        f.write(format_log_line(rec, task, datetime.now(timezone.utc)) + "\n")
    print(format_output(rec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
