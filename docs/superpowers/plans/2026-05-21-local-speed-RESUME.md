# RESUME — Local-speed optimization (state as of 2026-05-21 ~05:30)

Pick up here after a context clear. Read this top-to-bottom, then continue at **"NEXT ACTION"**.

> **Tomorrow's job (2026-05-22):** run the **`n_ubatch` sweep via llama.cpp** described under
> NEXT ACTION. Everything needed is in this file. Owner already decided: do the llama.cpp test,
> **drop MLX (WS4)**, keep WS3 in reserve.

## Where we are

Executing the local-speed optimization for the `private` route (local `qwen3.6:27b` on the Mac
M2 Max `10.63.0.32`). Method = Approach A (diagnose → tune → model bake-off), subagent-driven
for coded tasks, direct execution for live ops.

- **Spec:** `docs/superpowers/specs/2026-05-21-local-speed-optimization-design.md`
- **Plan:** `docs/superpowers/plans/2026-05-21-local-speed-optimization.md`
- **Results log:** `mac/bench/results.md`
- **Journal:** `docs/journal/2026-05-21-prefix-cache-aux-eviction.md`
- **Runbook section:** `docs/runbook.md` → "Prefix-cache hygiene"
- **Bench harness (committed, Phase 0 DONE):** `mac/bench/` — `metrics.py`(+test), `bench_local.py`
  (`raw`/`e2e`), `cache_probe.py`.

### DONE
- **Phase 0** (benchmark harness): complete, two-stage reviewed, committed.
- **WS1 (prefix-cache fix): COMPLETE & VERIFIED.** Root cause was NOT prompt mutation — the agent
  system prompt is byte-identical across turns. The real cause: Hermes' `title_generation` (and
  other text aux) calls ran on the single-slot local model **between** turns and evicted the cached
  18k prefix, forcing full cold re-ingest every turn. Fix: routed `title_generation`, `compression`,
  `triage_specifier`, `profile_describer`, `curator` to the cloud `main` route in
  `~/.hermes/config.yaml` (under `auxiliary:`, set `provider: custom`, `model: main`,
  `base_url: http://10.63.0.32:4000/v1`, `api_key: <gateway master key>`).
  - **Result:** subsequent local turn **133s → 15.9s (~8.4×)**; first turn when prefix resident 8.3s.
  - Config backup: `~/.hermes/config.yaml.bak-20260521-034951`.

### DONE — WS2 (cold-ingest & memory tuning): COMPLETE, negative result
Measured both knobs by taking over the Mac Ollama (`OLLAMA_HOST=0.0.0.0` serve). **No speedup.**
Full numbers in `mac/bench/results.md` → "After WS2"; story in
`docs/journal/2026-05-21-ws2-cold-ingest-memory-tuning.md`.
- **WS2a — num_batch sweep:** 512/1024/2048 → 138/140/140 tok/s. Flat; not a lever. (The earlier
  `ConnectionRefused` was NOT an outage — `open -a Ollama` binds **loopback-only**, so the
  workstation couldn't reach `:11434`. Taking over with `OLLAMA_HOST=0.0.0.0` fixed reachability.)
- **WS2b — KV quant `q8_0`:** ingest ~131 tok/s (≈ baseline), generation 11.1 tok/s (unchanged),
  KV 5.7→4.7 GiB, total VRAM 24.4→21.8 GiB (~2.6 GiB freed), quality coherent. **Not persisted** —
  its only value is headroom for WS3, and persisting needs a managed `ollama serve` (the app
  ignores the env var). App restored; production back on f16/loopback and verified healthy.

## Key constraints / gotchas learned
- **The macOS Ollama app IGNORES `launchctl setenv`** (it curates its own env). So
  `OLLAMA_DEBUG_LOG_REQUESTS`, `OLLAMA_KV_CACHE_TYPE`, etc. do NOT take effect via launchctl.
  To control env, quit the app and run the bundled binary directly:
  `OLLAMA_DEBUG_LOG_REQUESTS=true OLLAMA_KEEP_ALIVE=-1 /Applications/Ollama.app/Contents/Resources/ollama serve`
  Restore afterward with `open -a Ollama`. (We did this during WS1 capture, then restored the app.)
- `num_batch` CAN be set per-request via `options.num_batch` to `/api/generate` (changing it
  reloads the runner) — so the num_batch sweep does NOT require taking over the server, only a
  reachable Ollama.
- **Cannot run two 27B Ollama instances** (32GB unified can't hold two ~17GB models) — WS2 env
  experiments must use the single instance (take it over, don't run a parallel one).
- `OLLAMA_NUM_PARALLEL=2` is NOT a fix for the cache thrash: it splits the 32k context to 16k/slot,
  below the 18k agent prompt.
- Flash attention is ALREADY ON (new engine auto-enables it; env var unset is misleading).

## Baseline numbers (qwen3.6:27b, Mac M2 Max, Ollama 0.24.0)
- Generation ~11 tok/s (raw), ~10 e2e — memory-bandwidth bound, the next big lever (WS3/WS4).
- Cold ingest ~128 tok/s synthetic; real cold first agent turn ≈ **1m24s**.
- Cache-hit ingest ~1870 tok/s (14.6×).
- Gateway: `http://10.63.0.32:4000/v1`; key in `~/.hermes/config.yaml` `model.api_key`.
  Health: `curl http://10.63.0.32:4000/health/liveliness`.

## Web research (2026-05-21) — what it changed

Did a web sweep on "what improves *ingest* on M2 Max" and "does MLX help ingest". Two outcomes:

1. **MLX (WS4) is DROPPED for ingest on this hardware.** MLX's win is *decode*, not prefill —
   and for our exact case (M2 Max, 27B, ~16k prefix) MLX would likely make ingest *worse*:
   - MLX prefill is **slower** than llama.cpp+FlashAttention at long context (measured 49s vs 38s
     at 8.5k; effective throughput collapses to ~3 tok/s when prefill dominates).
   - **M2 has no native bf16** → MLX (ships bf16) falls back to software emulation *during prefill*,
     a penalty M3+/M5 don't have. Hits us directly.
   - MLX's decode edge **shrinks to ~zero at 27B** (both runtimes hit the bandwidth ceiling).
   - **Prompt caching is broken/unreliable** in MLX implementations → would kill the WS1 win.
   - Ollama's own MLX backend gains are **M5-only** (Neural Accelerators), need **>32 GB** (we have
     exactly 32 → excluded), and target one specific model. N/A to us.
   - Sources: famstack.dev mlx-vs-gguf, groundy.com mlx-vs-llamacpp, ollama.com/blog/mlx.

2. **WS2a "num_batch is flat" is a likely FALSE NEGATIVE.** llama.cpp has TWO batch knobs:
   - `n_batch` (`-b`, logical buffer cap) ← this is what Ollama's `num_batch` maps to (what I swept).
   - **`n_ubatch` (`-ub`, physical micro-batch)** ← THIS governs prefill matmul parallelism in the
     Metal kernels, and is the "dominant lever" the literature cites for 2–3× prefill speedups.
   I never actually moved `n_ubatch`, and Ollama's **new engine** (`--ollama-engine`) may not expose
   it at all. So "can ingest go faster?" is **still open**, on a knob I didn't reach.
   - Caveats keeping expectations honest: (a) `n_ubatch` behaves *unpredictably* per backend/quant —
     one report saw Qwen3.5-27B peak at `ub=64` and crater at 128 (on AMD ROCm, NOT Metal); (b) at
     27B the ceiling may simply be memory bandwidth regardless. So this may yield nothing.
   - Sources: medium @michael.hannecke tuning-llama-cpp, vijay.eu llm-inference-internals,
     llama.cpp discussion #6328, insights.marvin-42.com (the ub=64 Qwen case).

## NEXT ACTION (2026-05-22) — `n_ubatch` sweep via llama.cpp

Goal: settle whether cold ingest can beat Ollama's ~140 tok/s by tuning the *physical* micro-batch.
Run llama.cpp directly (Ollama's new engine won't let us set `-ub`). Contained benchmark, no prod
change. **Coordinate first:** llama.cpp + Ollama can't both hold the 24 GB model in 32 GB — unload
Ollama before benching.

**Step 0 — pause & free memory.** Ask owner to pause Hermes. Then either `ssh 10.63.0.32 'ollama
stop qwen3.6:27b'` (unloads from VRAM, app keeps serving) or quit the app entirely.

**Step 1 — get llama.cpp on the Mac.** `ssh 10.63.0.32 'brew install llama.cpp'` (provides
`llama-bench`, `llama-cli`, `llama-server`). Verify: `llama-bench --help | head`.

**Step 2 — locate the GGUF.** We can point llama.cpp straight at Ollama's blob (it *is* a GGUF).
From the runner cmdline the blob is:
`/Users/dertechie/.ollama/models/blobs/sha256-83c54730a5fea8a0958598c01617c1419c431e93b33bacf980b49a420c798926`
Confirm tomorrow with `ollama show --modelfile qwen3.6:27b | grep FROM` (path can change on re-pull).

**Step 3 — sweep `-ub` on prompt processing** (no generation, FA on, all layers on GPU):
```
GGUF=/Users/dertechie/.ollama/models/blobs/sha256-83c54730a5fea8a0958598c01617c1419c431e93b33bacf980b49a420c798926
llama-bench -m "$GGUF" -p 8192 -n 0 -fa 1 -ngl 99 -b 2048 -ub 64,128,256,512,1024,2048
```
- `-p 8192` = prefill 8192 tokens (bump to 16384 to match the real ~16k agent prefix if time allows).
- `-n 0` = skip decode; we only care about `pp` (prompt-processing) tok/s here.
- `-b 2048` must be ≥ the largest `-ub`. Read the `pp` tok/s column per `-ub`.
- Baseline to beat: **~140 tok/s** (Ollama, ub effectively 512). Also sanity-check `-ub 512` ≈ 140.

**Step 4 — decide.**
- *If some `-ub` clearly wins* (say ≥1.5×): the lever is real. Next question = can Ollama use it?
  Check `ollama show`/Modelfile params and the new engine for a ubatch/`num_ubatch` knob. If Ollama
  can't set it, the path becomes serving `private` via **`llama-server`** (OpenAI-compat) behind the
  gateway — bigger change, and **must verify prompt-cache still works** (the WS1 win) before adopting.
- *If nothing beats ~140*: confirms 27B prefill is bandwidth-bound here; the WS2a conclusion stands
  (now properly tested). Close the ingest line; the only remaining lever is WS3.

**Step 5 — restore & record.** Restore Ollama (`open -a Ollama` if quit; `ollama stop` is auto-undone
on next request). Add an "ingest n_ubatch sweep" block to `mac/bench/results.md`, update this RESUME,
and correct/confirm the WS2a finding in the journal. Commit.

### WS3 (kept in reserve, after ingest is settled)
Speculative decoding with the already-pulled `qwen3:4b` draft for the ~11 tok/s **generation** floor
(the real day-to-day pain; bandwidth-bound, untouched by anything above). The q8_0 ~2.6 GiB headroom
would feed it. Persisting q8_0 needs a managed `ollama serve`/LaunchAgent (app ignores the env var).

### Takeover/restore cheat-sheet
- Reachable serve: `OLLAMA_HOST=0.0.0.0:11434 OLLAMA_KEEP_ALIVE=-1 [OLLAMA_KV_CACHE_TYPE=q8_0] \
  nohup /Applications/Ollama.app/Contents/Resources/ollama serve >/tmp/oll.log 2>&1 &` after
  `osascript -e 'quit app "Ollama"'` + `pkill -f "Ollama.app/Contents/MacOS/Ollama"`.
- Restore steady state: `pkill -f "ollama serve"; open -a Ollama` (binds loopback, f16 KV).
- Quality spot-check must pass `think:false` (prod runs `reasoning_effort: none`), else `response`
  is empty (all tokens go to the thinking field).
- Use `TERM=xterm-256color ssh 10.63.0.32` (runbook) to avoid a garbled terminal.

## Task list
WS2 complete. Tomorrow: the `n_ubatch` llama.cpp sweep above. WS3 not yet broken into tasks.
