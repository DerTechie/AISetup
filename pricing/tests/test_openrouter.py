import json
import pathlib

from pricing.sources.openrouter import parse_models, PRICE_FIELDNAMES

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "openrouter_sample.json"


def _rows():
    payload = json.loads(FIXTURE.read_text())
    return parse_models(payload, "2026-05-22")


def test_one_row_per_model():
    assert len(_rows()) == 3


def test_token_fields_converted_to_mtok():
    big = next(r for r in _rows() if r["id"] == "vendor/big-model")
    assert big["prompt_usd_per_mtok"] == "2.5"
    assert big["completion_usd_per_mtok"] == "7.5"
    assert big["input_cache_read_usd_per_mtok"] == "0.6"


def test_missing_pricing_fields_are_blank():
    big = next(r for r in _rows() if r["id"] == "vendor/big-model")
    assert big["image_usd"] == ""
    assert big["internal_reasoning_usd_per_mtok"] == ""


def test_free_model_keeps_zero():
    free = next(r for r in _rows() if r["id"] == "vendor/free-model")
    assert free["prompt_usd_per_mtok"] == "0"
    assert free["completion_usd_per_mtok"] == "0"


def test_native_unit_fields_kept_raw():
    big = next(r for r in _rows() if r["id"] == "vendor/big-model")
    assert big["request_usd"] == "0.01"
    vision = next(r for r in _rows() if r["id"] == "vendor/vision-model")
    assert vision["image_usd"] == "0.0014"


def test_carries_metadata_and_date():
    vision = next(r for r in _rows() if r["id"] == "vendor/vision-model")
    assert vision["fetched_date"] == "2026-05-22"
    assert vision["name"] == "Vendor: Vision Model"
    assert vision["context_length"] == 200000
    assert vision["modality"] == "text+image->text"


def test_every_row_has_all_columns():
    for row in _rows():
        assert set(row.keys()) == set(PRICE_FIELDNAMES)
