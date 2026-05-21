# mac/bench/metrics.py
"""Parse Ollama timing fields into human rates. Pure, no I/O."""


def _rate(count, duration_ns):
    secs = duration_ns / 1e9
    return (count / secs) if secs > 0 else 0.0


def compute_rates(timing):
    pe = timing.get("prompt_eval_count", 0)
    ec = timing.get("eval_count", 0)
    return {
        "prompt_tokens": pe,
        "gen_tokens": ec,
        "prompt_tok_s": _rate(pe, timing.get("prompt_eval_duration", 0)),
        "gen_tok_s": _rate(ec, timing.get("eval_duration", 0)),
        "load_s": timing.get("load_duration", 0) / 1e9,
    }
