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


def test_format_output_local_suggests_model_fast():
    out = format_output(Recommendation("local", "routine", []))
    assert "/model fast" in out


def test_format_output_unknown_shows_raw():
    out = format_output(Recommendation("unknown", "garbled text", []))
    assert "Could not parse" in out
    assert "garbled text" in out


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
