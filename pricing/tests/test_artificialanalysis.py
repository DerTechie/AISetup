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
