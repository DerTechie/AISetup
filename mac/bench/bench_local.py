#!/usr/bin/env python3
"""Benchmark a local Ollama model and the gateway routes.

Usage:
  python3 bench_local.py raw   --host http://10.63.0.32:11434 --model qwen3.6:27b
  python3 bench_local.py e2e   --gw http://10.63.0.32:4000/v1 --key sk-... --route private
"""
import argparse, json, time, urllib.request
from metrics import compute_rates

FOX = "The quick brown fox jumps over the lazy dog while the engineer reviews the routing config. "


def _post(url, body, key=None, timeout=600):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    t0 = time.time()
    r = json.load(urllib.request.urlopen(req, timeout=timeout))
    return r, time.time() - t0


def raw(args):
    url = args.host.rstrip("/") + "/api/generate"
    short = "Write one paragraph explaining what a reverse proxy is."
    longp = FOX * 350 + "\n\nSummarize the repeated sentence in one short sentence."
    for label, prompt, npred in [("warm-gen", short, 256), ("long-ingest", longp, 64)]:
        body = {"model": args.model, "prompt": prompt, "stream": False,
                "options": {"num_predict": npred, "temperature": 0}}
        r, wall = _post(url, body)
        m = compute_rates(r)
        print(f"[raw/{label}] prompt={m['prompt_tokens']}@{m['prompt_tok_s']:.0f}t/s "
              f"gen={m['gen_tokens']}@{m['gen_tok_s']:.1f}t/s load={m['load_s']:.2f}s wall={wall:.2f}s")


def e2e(args):
    url = args.gw.rstrip("/") + "/chat/completions"
    body = {"model": args.route, "temperature": 0, "max_tokens": 256,
            "messages": [{"role": "user", "content": "Write one paragraph explaining what a reverse proxy is."}]}
    r, wall = _post(url, body, key=args.key, timeout=300)
    ct = r.get("usage", {}).get("completion_tokens", 0)
    print(f"[e2e/{args.route}] completion={ct} wall={wall:.2f}s -> {ct/wall:.1f} t/s end-to-end")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("raw"); pr.add_argument("--host", required=True); pr.add_argument("--model", required=True); pr.set_defaults(fn=raw)
    pe = sub.add_parser("e2e"); pe.add_argument("--gw", required=True); pe.add_argument("--key", required=True); pe.add_argument("--route", default="private"); pe.set_defaults(fn=e2e)
    a = p.parse_args(); a.fn(a)
