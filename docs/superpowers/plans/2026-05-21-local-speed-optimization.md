# Local-Speed Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the local `private` route (qwen3.6:27b on the Mac M2 Max) fast enough for real agentic work, primarily by fixing prompt-prefix-cache busting and tuning ingest, then evaluating speculative decoding, an MLX backend, and faster models.

**Architecture:** First build a small committed benchmark harness so every change is measured the same way. Then work the [design spec](../specs/2026-05-21-local-speed-optimization-design.md) workstreams in priority order, each gated: re-benchmark after each and stop when local is fast enough. The biggest expected win (WS1) is making Hermes' prompt prefix byte-stable across turns so Ollama's KV prefix cache hits (measured 14.6× ingest speedup).

**Tech Stack:** Python 3 (stdlib only — `urllib`, `json`), Ollama 0.24 HTTP API on the Mac (`http://10.63.0.32:11434`), LiteLLM gateway (`http://10.63.0.32:4000`), Hermes Agent config (`~/.hermes/config.yaml`), `mlx-lm` (later), `pytest` for the one unit-testable module.

**Conventions used below:**
- `MACH=http://10.63.0.32:11434` (Mac Ollama), `GW=http://10.63.0.32:4000/v1` (gateway).
- Gateway key: `KEY=$(python3 -c "import yaml;print(yaml.safe_load(open('$HOME/.hermes/config.yaml'))['model']['api_key'])")`.
- Mac SSH: `TERM=xterm-256color ssh 10.63.0.32` (per runbook).
- Repo root: `/home/dertechie/Organizations/DerTechie/AISetup`.
- All measurement results are appended to `mac/bench/results.md` (committed) — the running record is part of the project's reconstructable history.

---

## Phase 0 — Benchmark harness (committed, measured baseline)

### Task 1: Pure rate-computation module

**Files:**
- Create: `mac/bench/metrics.py`
- Test: `mac/bench/test_metrics.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mac/bench && python3 -m pytest test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'metrics'`

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mac/bench && python3 -m pytest test_metrics.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add mac/bench/metrics.py mac/bench/test_metrics.py
git commit -m "feat(bench): pure Ollama timing -> rates parser with tests"
```

### Task 2: Local benchmark CLI

**Files:**
- Create: `mac/bench/bench_local.py`

- [ ] **Step 1: Write the script**

```python
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
```

- [ ] **Step 2: Smoke-run raw mode**

Run: `cd mac/bench && python3 bench_local.py raw --host http://10.63.0.32:11434 --model qwen3.6:27b`
Expected: two lines; `warm-gen` shows `gen=...@~11t/s`, `long-ingest` shows `prompt=...@~130t/s`.

- [ ] **Step 3: Smoke-run e2e mode**

Run: `cd mac/bench && KEY=$(python3 -c "import yaml;print(yaml.safe_load(open('$HOME/.hermes/config.yaml'))['model']['api_key'])") && python3 bench_local.py e2e --gw http://10.63.0.32:4000/v1 --key "$KEY" --route private`
Expected: one line, `~10 t/s end-to-end`.

- [ ] **Step 4: Commit**

```bash
git add mac/bench/bench_local.py
git commit -m "feat(bench): local raw + gateway e2e benchmark CLI"
```

### Task 3: Prefix-cache probe

**Files:**
- Create: `mac/bench/cache_probe.py`

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""Demonstrate Ollama prefix-cache behaviour over three turns.

turn1: cold full ingest
turn2: same prefix, grown conversation  -> should be a cache hit (fast)
turn3: same as turn2 but ONE token changed at the FRONT -> full re-ingest (slow)
"""
import json, time, urllib.request
HOST = "http://10.63.0.32:11434/api/chat"
MODEL = "qwen3.6:27b"
HEADER = "You are Hermes, an agentic assistant. Session started 2026-05-21T02:15:00Z."
BODY = ("\nTOOL: read_file(path) reads a file. TOOL: edit(path,old,new) edits. "
        "Follow the routing rules carefully and call tools precisely. ") * 900


def chat(messages, label):
    body = {"model": MODEL, "messages": messages, "stream": False,
            "options": {"num_predict": 8, "temperature": 0}}
    req = urllib.request.Request(HOST, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=600))
    pe = r.get("prompt_eval_count", 0)
    ped = r.get("prompt_eval_duration", 1) / 1e9
    print(f"[{label}] prompt_tokens={pe} ingest={ped:.2f}s -> {pe/ped:.0f} t/s")


sys1 = {"role": "system", "content": HEADER + BODY}
m = [sys1, {"role": "user", "content": "Task A: list two files."}]
chat(m, "turn1 cold")
m = m + [{"role": "assistant", "content": "OK, files: a.py, b.py."},
         {"role": "user", "content": "Task B: now read a.py."}]
chat(m, "turn2 grow, prefix STABLE")
sys2 = {"role": "system", "content": HEADER.replace("02:15:00", "02:31:00") + BODY}
chat([sys2] + m[1:], "turn3 grow, FRONT changed")
```

- [ ] **Step 2: Run it (takes a few minutes — two cold ingests)**

Run: `cd mac/bench && python3 cache_probe.py`
Expected: turn1 slow (~200s+), turn2 fast (~15s), turn3 slow again (~200s+) — confirms the cache-busting mechanism.

- [ ] **Step 3: Commit**

```bash
git add mac/bench/cache_probe.py
git commit -m "feat(bench): prefix-cache probe (stable vs front-mutated prefix)"
```

### Task 4: Record the baseline

**Files:**
- Create: `mac/bench/results.md`

- [ ] **Step 1: Create the results log with the baseline already measured (2026-05-21)**

```markdown
# Local-speed benchmark results

Model: qwen3.6:27b Q4_K_M on Mac M2 Max (32GB), Ollama 0.24.0, num_parallel=1, batch=512, KV f16, flash-attn ON.

## Baseline — 2026-05-21 (before optimization)
| Metric | Value |
|---|---|
| Generation (raw) | 11.2 tok/s |
| Generation (e2e via gateway) | ~10 tok/s |
| Cold ingest | ~128 tok/s (16.4k ctx ≈ 128s to first token) |
| Cache-hit ingest (stable prefix) | ~1870 tok/s (14.6× faster) |
| Front-mutated prefix | full re-ingest (~128 tok/s) — cache busted |

## After WS<n> — <date>
| Metric | Before | After |
|---|---|---|
| ... | ... | ... |
```

- [ ] **Step 2: Commit**

```bash
git add mac/bench/results.md
git commit -m "docs(bench): record local-speed baseline numbers"
```

---

## Phase 1 — WS1: Prefix-cache fix (highest leverage)

**Gate:** if after this phase a real Hermes agentic turn on `private` ingests the 16.4k prefix in ~15s instead of ~128s, local may already be usable — re-evaluate before doing WS2+.

### Task 5: Capture real Hermes request payloads

**Files:**
- Modify (temporary): Mac Ollama launch env on `10.63.0.32`

- [ ] **Step 1: Enable Ollama request logging on the Mac**

```bash
TERM=xterm-256color ssh 10.63.0.32 'launchctl setenv OLLAMA_DEBUG_LOG_REQUESTS true; \
  osascript -e "quit app \"Ollama\"" 2>/dev/null; sleep 3; open -a Ollama; sleep 8; \
  curl -s http://localhost:11434/api/ps >/dev/null && echo restarted'
```
Expected: `restarted`. (Note: this reloads the model — the prefix cache is now cold, which is fine for capture.)

- [ ] **Step 2: Run two consecutive real Hermes turns on the `private` route**

In a Hermes session: `/model private`, then send a first short message, wait for the reply, then send a second short message. (Real Hermes context — this is the payload we need to inspect.)

- [ ] **Step 3: Pull the two request bodies from the Ollama log**

```bash
TERM=xterm-256color ssh 10.63.0.32 'grep -a "POST /api/chat" -A2 ~/.ollama/logs/server.log | tail -40' > /tmp/hermes_turns.txt
TERM=xterm-256color ssh 10.63.0.32 'python3 - <<PY
import json,glob
# Extract the last two request payloads ollama logged (field "messages")
# Adjust the grep/parse to the actual 0.24 log format observed in step 1.
PY'
```
Expected: two JSON request bodies saved locally for diffing. If 0.24 does not log full bodies, fall back to LiteLLM: set `litellm_settings: { json_logs: true }` and `set_verbose: true` in `mac/litellm-config.yaml`, `restart litellm`, and read bodies from `docker compose logs litellm`.

- [ ] **Step 4: Commit the captured evidence (sanitized)**

```bash
mkdir -p docs/journal/evidence
cp /tmp/hermes_turns.txt docs/journal/evidence/2026-05-21-hermes-private-2turns.txt
# Sanitize: remove any secrets/personal content before committing.
git add docs/journal/evidence/2026-05-21-hermes-private-2turns.txt
git commit -m "docs(evidence): captured Hermes private-route payloads (2 turns)"
```

### Task 6: Identify the cache-busting element

- [ ] **Step 1: Diff the leading bytes of the two payloads**

```bash
python3 - <<'PY'
import json
turns = json.load(open("/tmp/hermes_two_turns.json"))  # [{messages:[...]}, {messages:[...]}]
a = json.dumps(turns[0]["messages"][0])  # system message, turn 1
b = json.dumps(turns[1]["messages"][0])  # system message, turn 2
# Find first differing character index
i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), -1)
print("first diff at char", i)
print("context:", a[max(0,i-60):i+60], "||", b[max(0,i-60):i+60])
PY
```
Expected output: either "first diff at char -1" (prefix is stable — cache should hit, so the slowness is elsewhere, likely model reloads) **or** a diff early in the system message — that is the culprit.

- [ ] **Step 2: Map the culprit to a Hermes config knob.** Likely candidates, in order:
  - A timestamp / "current date" injected into the system prompt → `display.timestamps`, or an injected runtime context block.
  - An **ephemeral system** reminder regenerated each turn → `display.ephemeral_system_ttl: 0`.
  - **Memory recall** injected near the front and rotating each turn → `memory.*` (`memory_enabled`, `nudge_interval`).
  - **Context compression** rewriting protected-region boundaries → `compression.*` (`protect_first_n: 3`).

- [ ] **Step 3: Write findings into the journal**

```bash
# docs/journal/2026-05-21-prefix-cache-diagnosis.md — record WHERE the prefix diverges,
# which knob owns it, and WHY this caused the ~2min/turn symptom. (Decision-log style.)
git add docs/journal/2026-05-21-prefix-cache-diagnosis.md
git commit -m "docs(journal): pin the Hermes prefix-cache-busting element"
```

### Task 7: Apply the prefix-stabilizing fix

**Files:**
- Modify: `~/.hermes/config.yaml` (back up first)

- [ ] **Step 1: Back up the config**

```bash
cp ~/.hermes/config.yaml ~/.hermes/config.yaml.bak-$(date +%Y%m%d-%H%M%S)
ls -la ~/.hermes/config.yaml.bak-*
```
Expected: a fresh `.bak-*` file listed.

- [ ] **Step 2: Apply the fix matching the Task 6 culprit (do exactly one, the one identified):**
  - **Timestamp at front:** if `display.timestamps` (or equivalent) injects a clock into the system prompt, set it `false` so the prefix stops changing.
  - **Ephemeral system reminder:** raise `display.ephemeral_system_ttl` from `0` so the reminder is not regenerated every turn (e.g. a value that spans a working session), OR confirm it is appended at the tail (after the stable prefix) — tail content does not bust the cache.
  - **Memory recall churn:** if memory is injected near the front, move it after the stable system+tools block, or reduce `memory.nudge_interval` churn so the recalled block is stable across consecutive turns.
  - **Compression boundary churn:** ensure `compression.protect_first_n` keeps the full system+tools prefix intact (raise if the prefix spans more than 3 messages).

- [ ] **Step 3: Restart Hermes (or reload config) so the change takes effect.** Follow the Hermes restart procedure; confirm the session starts cleanly with `/model private`.

- [ ] **Step 4: Commit a runbook note about the knob (the config file itself is not in git)**

```bash
# Add a "Prefix-cache hygiene" subsection to docs/runbook.md describing the knob + why.
git add docs/runbook.md
git commit -m "docs(runbook): prefix-cache hygiene for the private route"
```

### Task 8: Make the warm model permanent

- [ ] **Step 1: Confirm `keep_alive: -1` is set for the private model in the gateway**

```bash
grep -n "keep_alive" mac/litellm-config.yaml
```
Expected: `keep_alive: -1` on the `private`/Ollama model block. If missing, add it and `docker compose --env-file .env restart litellm` on the Mac.

- [ ] **Step 2: Verify the model stays resident**

```bash
curl -s http://10.63.0.32:11434/api/ps | python3 -c "import sys,json;m=json.load(sys.stdin)['models'][0];print('resident, expires', m['expires_at'])"
```
Expected: `resident, expires` a far-future date (years out) — confirms it won't reload and wipe the cache.

### Task 9: Re-measure and gate

- [ ] **Step 1: Disable the debug logging from Task 5**

```bash
TERM=xterm-256color ssh 10.63.0.32 'launchctl unsetenv OLLAMA_DEBUG_LOG_REQUESTS; osascript -e "quit app \"Ollama\"" 2>/dev/null; sleep 3; open -a Ollama; sleep 8; echo done'
```
Expected: `done`.

- [ ] **Step 2: Run two real Hermes turns on `private` and time the second turn's ingest** (via the Ollama log timing or wall-clock to first token). Expected: second turn ingests the 16.4k prefix in ~15s, not ~128s.

- [ ] **Step 3: Record results and decide the gate**

```bash
# Append an "After WS1" row to mac/bench/results.md with before/after ingest numbers.
git add mac/bench/results.md
git commit -m "docs(bench): WS1 prefix-cache fix results"
```
Gate: if a real `private` turn is now usable (owner-judged), pause and ask the owner whether to continue to WS2+. Otherwise proceed.

---

## Phase 2 — WS2: Cold-ingest & memory tuning

### Task 10: Raise the prompt batch size

- [ ] **Step 1: Set a larger num_batch on the Mac and reload**

```bash
TERM=xterm-256color ssh 10.63.0.32 'launchctl setenv OLLAMA_NUM_BATCH 1024; osascript -e "quit app \"Ollama\"" 2>/dev/null; sleep 3; open -a Ollama; sleep 8; echo set'
```
Expected: `set`. (If Ollama 0.24 takes batch via the model load options instead of env, set `num_batch` in the gateway model `options` in `mac/litellm-config.yaml` and `restart litellm`.)

- [ ] **Step 2: Re-benchmark cold ingest**

Run: `cd mac/bench && python3 bench_local.py raw --host http://10.63.0.32:11434 --model qwen3.6:27b`
Expected: `long-ingest` prompt tok/s higher than the ~130 baseline (or unchanged — record either way).

- [ ] **Step 3: Try 2048 if 1024 helped and memory allows**

Repeat Step 1 with `OLLAMA_NUM_BATCH 2048`, re-benchmark. Watch for memory pressure / failed load in `~/.ollama/logs/server.log`.

### Task 11: Quantize the KV cache

- [ ] **Step 1: Set q8_0 KV cache and reload**

```bash
TERM=xterm-256color ssh 10.63.0.32 'launchctl setenv OLLAMA_KV_CACHE_TYPE q8_0; osascript -e "quit app \"Ollama\"" 2>/dev/null; sleep 3; open -a Ollama; sleep 8; echo set'
```

- [ ] **Step 2: Verify KV cache size dropped and quality is acceptable**

```bash
TERM=xterm-256color ssh 10.63.0.32 'grep -a "kv cache" ~/.ollama/logs/server.log | tail -1'
```
Expected: KV cache size ~3 GiB (down from 5.7 GiB). Then run a real Hermes task on `private` and confirm no obvious quality regression (owner spot-check).

- [ ] **Step 3: Record results**

```bash
# Append "After WS2" row to mac/bench/results.md (batch + KV cache effects, freed memory).
git add mac/bench/results.md
git commit -m "docs(bench): WS2 ingest+memory tuning results"
```
Gate: decide whether generation speed (still ~11 tok/s) needs WS3.

---

## Phase 3 — WS3: Speculative decoding (conditional)

> Run only if generation throughput is still the bottleneck after WS1–WS2.

### Task 12: Configure the draft model

- [ ] **Step 1: Confirm Ollama 0.24 speculative-decoding support and the flag name**

```bash
TERM=xterm-256color ssh 10.63.0.32 'ollama run qwen3.6:27b --help 2>&1 | grep -i -E "draft|specul" || echo "no CLI flag — check /api/generate options or Modelfile"'
```
Expected: either a draft-model flag/option name, or confirmation it must be set via API options / Modelfile. (If Ollama lacks it, defer speculative decoding to the MLX backend in WS4, which supports draft models in `mlx_lm`.)

- [ ] **Step 2: Enable `qwen3:4b` as the draft model** for the `private` model (via the mechanism found in Step 1 — model `options` in `mac/litellm-config.yaml`, or a Modelfile `PARAMETER`). `restart litellm` / reload as needed.

- [ ] **Step 3: Benchmark generation with and without the draft model**

Run: `cd mac/bench && python3 bench_local.py raw --host http://10.63.0.32:11434 --model qwen3.6:27b`
Expected: `warm-gen` gen tok/s higher than ~11 if draft acceptance is good. Record net tok/s.

- [ ] **Step 4: Record results**

```bash
git add mac/bench/results.md
git commit -m "docs(bench): WS3 speculative-decoding results"
```

---

## Phase 4 — WS4: MLX backend evaluation (conditional)

> Run if Ollama-path speed is still insufficient, or to see if MLX simply beats it.

### Task 13: Stand up an MLX server on the Mac

- [ ] **Step 1: Install mlx-lm and obtain/convert the model**

```bash
TERM=xterm-256color ssh 10.63.0.32 'pip3 install --user -U mlx-lm 2>&1 | tail -2'
# Prefer a prebuilt MLX quant of the model if available; otherwise convert:
TERM=xterm-256color ssh 10.63.0.32 'python3 -m mlx_lm.convert --help 2>&1 | head -5'
```
Expected: mlx-lm installed; convert tool available.

- [ ] **Step 2: Serve it (OpenAI-compatible) on a spare port**

```bash
TERM=xterm-256color ssh 10.63.0.32 'python3 -m mlx_lm.server --model <mlx-model-path> --port 8081 >~/mlx_server.log 2>&1 &'; sleep 10
TERM=xterm-256color ssh 10.63.0.32 'curl -s http://localhost:8081/v1/models'
```
Expected: a models list — the MLX server is up and OpenAI-compatible.

### Task 14: Benchmark MLX vs tuned Ollama

- [ ] **Step 1: Time an ingest-heavy + generation request against the MLX server**

```bash
TERM=xterm-256color ssh 10.63.0.32 'python3 - <<PY
import json,urllib.request,time
body=json.dumps({"model":"local","messages":[{"role":"user","content":"Write one paragraph explaining what a reverse proxy is."}],"max_tokens":256,"temperature":0}).encode()
req=urllib.request.Request("http://localhost:8081/v1/chat/completions",data=body,headers={"Content-Type":"application/json"})
t0=time.time();r=json.load(urllib.request.urlopen(req,timeout=300));w=time.time()-t0
print("completion",r["usage"]["completion_tokens"],"wall",round(w,2),"->",round(r["usage"]["completion_tokens"]/w,1),"t/s")
PY'
```
Expected: a generation tok/s number to compare against the ~11 (Ollama) baseline.

- [ ] **Step 2: Verify prompt-cache support** (so the WS1 win survives): re-send an identical large prefix twice and confirm the second is faster. Use `mlx_lm.server`'s prompt-cache option if separate.

- [ ] **Step 3: Record the head-to-head**

```bash
# Append "MLX vs Ollama" comparison to mac/bench/results.md.
git add mac/bench/results.md
git commit -m "docs(bench): MLX backend vs tuned Ollama comparison"
```

### Task 15: Repoint the route if MLX wins

- [ ] **Step 1:** If MLX is clearly faster and stable, add it as the `private` backend in `mac/litellm-config.yaml` (point at `http://host.docker.internal:8081/v1` or the Mac IP), keeping Ollama as a fallback. `restart litellm`.
- [ ] **Step 2:** Validate `private` end-to-end through the gateway: `cd mac/bench && python3 bench_local.py e2e --gw http://10.63.0.32:4000/v1 --key "$KEY" --route private`. Expected: faster than baseline e2e.
- [ ] **Step 3:** Make the MLX server start on login (launchd plist) and document in the runbook. Commit the runbook + litellm-config changes.

---

## Phase 5 — WS5: Model bake-off (conditional)

> Run only if local is still too slow after the backend is chosen.

### Task 16: Benchmark candidate models on the winning backend

- [ ] **Step 1: Pull the shortlist** (all must fit ~24 GB; MoE models give the biggest generation-speed win because few params are active per token):
  - `qwen3-30b-a3b` (MoE, ~3B active → fast generation, ~18 GB Q4) — top speed candidate.
  - `gpt-oss-20b` (MoE, fast).
  - `mistral-small` (~24B dense) — quality reference.
  - `qwen3.6:27b` — current baseline.

```bash
TERM=xterm-256color ssh 10.63.0.32 'ollama pull qwen3-30b-a3b'   # repeat per candidate (adjust exact tags)
```

- [ ] **Step 2: Benchmark each** with `bench_local.py raw` (swap `--model`) and append a comparison table to `mac/bench/results.md`. Expected: MoE candidates show notably higher gen tok/s.

### Task 17: Quality-gate the top candidates on real Hermes tasks

- [ ] **Step 1:** For the 2–3 fastest candidates, point `private` at each in turn (`mac/litellm-config.yaml`, `restart litellm`).
- [ ] **Step 2:** Run a fixed set of **real Hermes tasks** on `private` and judge by hand: (a) a tool-calling task (must emit valid tool calls reliably), (b) an email-triage task, (c) a small coding edit. Record pass/fail + notes per candidate.
- [ ] **Step 3:** Decide. Swap the `private` backend only if a candidate clearly wins on speed without losing agentic quality. Commit the `litellm-config.yaml` change + a journal entry recording the decision and what was rejected.

---

## Phase 6 — Documentation & wrap-up

### Task 18: Sync docs and journal

- [ ] **Step 1:** Update `docs/runbook.md`: the final `private` backend, tuning env vars set on the Mac, prefix-cache hygiene, and how to re-run `mac/bench/`.
- [ ] **Step 2:** Update `README.md` if the `private` route's backend/model changed.
- [ ] **Step 3:** Write a `docs/journal/2026-05-21-local-speed-optimization.md` entry: the baseline, the prefix-cache root cause, what each workstream bought (with numbers), and what was rejected — the talk narrative.
- [ ] **Step 4: Commit**

```bash
git add README.md docs/runbook.md docs/journal/
git commit -m "docs: local-speed optimization outcomes + journal"
```

---

## Self-review notes (for the executor)

- **Spec coverage:** WS1→Phase1, WS2→Phase2, WS3→Phase3, WS4→Phase4, WS5→Phase5, §7 docs→Phase6, §2 baseline→Task4. All success-criteria metrics (§5) are produced by `bench_local.py` / `cache_probe.py` and logged in `results.md`.
- **Conditional phases:** Phases 3–5 are gated — do not run them if an earlier gate already makes `private` usable (owner decides).
- **Reversibility:** every Mac env change is `launchctl unsetenv ... && restart`; every Hermes change is guarded by a `.bak-*`; every route change is a `litellm-config.yaml` edit + `restart litellm`.
- **Exact tags:** model tags in Phase 5 (`qwen3-30b-a3b`, `gpt-oss-20b`, `mistral-small`) must be confirmed against `ollama` / MLX registries at run time; adjust to the exact available tag.
