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
