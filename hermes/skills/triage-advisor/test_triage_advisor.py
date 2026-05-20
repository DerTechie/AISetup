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
