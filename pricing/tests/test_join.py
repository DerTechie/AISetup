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
