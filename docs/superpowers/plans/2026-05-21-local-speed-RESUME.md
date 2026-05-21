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

### IN PROGRESS — WS2 (cold-ingest tuning)
The remaining pain is the **cold first agent turn ≈ 1m24s (~84s)** (after a model reload; the warm
case is fixed). Goal: speed cold ingest via `num_batch` and KV-cache quantization.
- **WS2a — num_batch sweep:** was running `/tmp/ws2_batch.py` (sweeps `num_batch` 512/1024/2048
  via per-request `options`, each with a unique ~16k prompt to force cold ingest, reports prompt
  tok/s). **It FAILED: `ConnectionRefused` on `10.63.0.32:11434`** — the Mac Ollama API stopped
  accepting connections (it was up right after I restored the app, then refused minutes later).
- **WS2b — KV cache quant (`OLLAMA_KV_CACHE_TYPE=q8_0`):** not started. Needs server-level env.

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

## NEXT ACTION
1. **Diagnose the Mac Ollama outage.** Run (this is the command that was interrupted):
   - check listener: `ssh 10.63.0.32 'lsof -nP -iTCP:11434 -sTCP:LISTEN'`
   - check processes: `ssh 10.63.0.32 'pgrep -fl ollama; pgrep -fl Ollama.app'`
   - `curl -s -m5 http://10.63.0.32:11434/api/ps`
   - If down: `ssh 10.63.0.32 'open -a Ollama'` (wait ~10s, model reloads cold), re-verify.
   - Use `TERM=xterm-256color ssh 10.63.0.32` per runbook.
2. Once Ollama is back: re-run the num_batch sweep: `python3 /tmp/ws2_batch.py` (recreate it if the
   /tmp file is gone — see WS2a description above; it's simple). Coordinate: ask the user to pause
   Hermes during the sweep (heavy ~16k requests evict the cache).
3. Then WS2b: take over with a manual serve setting `OLLAMA_KV_CACHE_TYPE=q8_0`, measure cold
   ingest + KV size (expect 5.7GiB→~3GiB) + spot-check quality; restore the app after.
4. Record an "After WS2" row in `mac/bench/results.md`, persist the winning `num_batch` (via the
   `private` model `options` in `mac/litellm-config.yaml`, `restart litellm`) and KV type, commit,
   update runbook.
5. After WS2, the next-biggest lever is **generation speed** (WS3 speculative decoding with the
   already-pulled `qwen3:4b` / WS4 MLX backend). Gate with the user.

## Task list (TaskList ids)
- #14 WS2a num_batch sweep (in progress — blocked on the Ollama outage)
- #15 WS2b KV cache quant test
- #16 WS2 record results + persist config
