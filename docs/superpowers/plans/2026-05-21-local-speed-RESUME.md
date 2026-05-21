# RESUME — Local-speed optimization (state as of 2026-05-21 ~04:00)

Pick up here after a context clear. Read this top-to-bottom, then continue at **"NEXT ACTION"**.

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

## NEXT ACTION — GATE WS3 WITH THE OWNER
WS1 and WS2 are done. The warm-turn pain is fixed (~16 s); the ~11 tok/s **generation** floor and
the one-time ~128 s cold ingest are unmoved and are bandwidth-bound — no server knob touches them.
The remaining real levers both need an owner decision:
1. **WS3 — speculative decoding** with the already-pulled `qwen3:4b` as a draft model. This is the
   most promising generation-speed lever and is what the q8_0 ~2.6 GiB headroom would feed. If we
   commit to WS3, also persist `q8_0` (requires switching the Mac from the Ollama *app* to a
   managed `ollama serve` / LaunchAgent, since the app ignores `OLLAMA_KV_CACHE_TYPE`).
2. **WS4 — MLX backend** evaluation (`mlx-lm` server) as an alternative path that can beat the
   GGUF/llama.cpp generation speed. Must preserve OpenAI-compat API + prompt-cache (the WS1 win).
3. **WS5 — model bake-off** only if still too slow, with a hand-judged quality gate on real tasks.
Pick the lever (likely WS3 first) with the owner before resuming.

### Takeover/restore cheat-sheet (for WS3)
- Reachable serve: `OLLAMA_HOST=0.0.0.0:11434 OLLAMA_KEEP_ALIVE=-1 [OLLAMA_KV_CACHE_TYPE=q8_0] \
  nohup /Applications/Ollama.app/Contents/Resources/ollama serve >/tmp/oll.log 2>&1 &` after
  `osascript -e 'quit app "Ollama"'` + `pkill -f "Ollama.app/Contents/MacOS/Ollama"`.
- Restore steady state: `pkill -f "ollama serve"; open -a Ollama` (binds loopback, f16 KV).
- Quality spot-check must pass `think:false` (prod runs `reasoning_effort: none`), else `response`
  is empty (all tokens go to the thinking field).

## Task list
WS2 tasks complete. WS3 not yet broken into tasks — create them after the owner picks the lever.
