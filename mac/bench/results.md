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
