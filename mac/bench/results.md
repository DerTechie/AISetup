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

## After WS1 (prefix-cache fix) — 2026-05-21
Root cause was NOT prompt front-mutation (initial hypothesis). The agent system prompt is
byte-identical across turns (18,031 chars) and each turn is a pure prefix-extension. The
real evictor: Hermes' `title_generation` auxiliary call ran on the same single-slot local
model between turns and overwrote the cached 18k prefix, forcing a full cold re-ingest every
turn. Fix: routed text auxiliary tasks (`title_generation`, `compression`, `triage_specifier`,
`profile_describer`, `curator`) to the cloud `main` route in `~/.hermes/config.yaml`.

Measured on real Hermes `private` turns (wall-clock per /api/chat):
| Metric | Before | After |
|---|---|---|
| Subsequent agent turn (turn 2) | ~133 s (2m13s) | **15.9 s** (~8.4× faster) |
| First turn of a new session | ~145 s | **8.3 s** (18k prefix survived from prior session) |
| Title-gen call landing on local model | yes (evicts cache) | **no** (now cloud) |
| One-time cold ingest after model (re)load | ~128 s | ~128 s (unchanged — WS2 target) |
