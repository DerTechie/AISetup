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
