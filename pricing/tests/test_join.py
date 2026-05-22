from pricing.join import suggest_map, build_price_vs_quality, JOIN_FIELDNAMES


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
    # (3*2 + 6)/4 = 3
    assert rows[0]["blended_usd_per_mtok"] == "3"


def test_build_join_blank_blend_when_price_missing():
    prices = [{
        "id": "vendor/freeish", "name": "Vendor: Freeish",
        "prompt_usd_per_mtok": "", "completion_usd_per_mtok": "6",
    }]
    rows = build_price_vs_quality(prices, [], [])
    assert rows[0]["blended_usd_per_mtok"] == ""


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
