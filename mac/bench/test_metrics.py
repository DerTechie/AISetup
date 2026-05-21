# mac/bench/test_metrics.py
from metrics import compute_rates

def test_compute_rates_from_ollama_timing():
    # Fields as returned by Ollama /api/generate (durations in nanoseconds)
    timing = {
        "prompt_eval_count": 3523,
        "prompt_eval_duration": 24_490_000_000,
        "eval_count": 128,
        "eval_duration": 11_810_000_000,
        "load_duration": 140_000_000,
    }
    r = compute_rates(timing)
    assert round(r["prompt_tok_s"], 1) == 143.9
    assert round(r["gen_tok_s"], 1) == 10.8
    assert r["prompt_tokens"] == 3523
    assert r["gen_tokens"] == 128

def test_compute_rates_handles_zero_duration():
    r = compute_rates({"eval_count": 5, "eval_duration": 0})
    assert r["gen_tok_s"] == 0.0
