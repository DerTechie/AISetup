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
| Subsequent agent turn (turn 2, prefix cached) | ~133 s (2m13s) | **15.9 s** (~8.4× faster) |
| First turn when prefix already resident | — | **8.3 s** (prefix survived from prior session) |
| Title-gen call landing on local model | yes (evicts cache) | **no** (now cloud) |
| First agent turn after a fully cold slot | ~2 min | **~128 s** (raw cold ingest, 16.4k ÷ ~128 tok/s) — unchanged by WS1, this is the WS2 target |

**What WS1 actually changed (clarification):** it did *not* speed up ingest. The local model has a single, sequential KV slot. The interleaved `title_generation` call was forced to run between agent turns and, being a different prompt, evicted the cached agent prefix — so every turn paid the full ~128 s cold re-ingest again. Removing aux from the slot eliminates that eviction, so the prefix stays warm and subsequent turns are ~16 s. (Observed first-turn times below 128 s, e.g. ~84 s or 8.3 s, are cases where part/all of the prefix was still resident.) The genuine cold ingest of a fully empty slot remains ~128 s — that is the only thing WS2 can move.

## After WS2 (cold-ingest & memory tuning) — 2026-05-21

**Verdict: no speedup. WS2's only deliverable is freeing ~2.6 GiB VRAM (q8_0 KV), which matters only as headroom for WS3.** Both ingest and generation are memory-bandwidth bound on the M2 Max; neither lever in WS2 moves them.

Measured by taking over the Mac Ollama (`ollama serve` via the bundled binary, `OLLAMA_HOST=0.0.0.0`, `keep_alive=-1`), since the macOS app ignores `launchctl setenv` for `num_batch`/`OLLAMA_KV_CACHE_TYPE`. Cold ingest = unique ~13.6k-token prompt forcing a cache miss; generation = `think:false` (production sets `reasoning_effort: none`). App restored afterward — steady state is back on f16 KV / loopback.

### WS2a — num_batch sweep (per-request `options.num_batch`, cold ingest)
| num_batch | cold ingest |
|---|---|
| 512 (default) | 138 tok/s |
| 1024 | 140 tok/s |
| 2048 | 140 tok/s |

Flat → Ollama's `num_batch` is **not** a lever. **Caveat (web research, 2026-05-21):** `num_batch`
maps to llama.cpp's *logical* batch `n_batch` (`-b`); the lever the literature credits with 2–3×
prefill speedups is the *physical* micro-batch `n_ubatch` (`-ub`), which I never moved (and Ollama's
new engine may not expose). So "is ingest tunable?" is **not yet settled** — pending an `-ub` sweep
run via llama.cpp directly (see `docs/superpowers/plans/2026-05-21-local-speed-RESUME.md` → NEXT
ACTION). The flat `num_batch` result itself stands.

### WS2b — KV cache quant (`OLLAMA_KV_CACHE_TYPE=q8_0`, flash-attn on)
| Metric | f16 (baseline) | q8_0 |
|---|---|---|
| Cold ingest | ~140 tok/s | ~131 tok/s (~6% slower, within noise) |
| Generation (think off) | 11.2 tok/s | 11.1 tok/s (unchanged) |
| KV cache size (32k ctx) | 5.7 GiB | **4.7 GiB** |
| Total resident VRAM | 24.4 GiB | **21.8 GiB** (~2.6 GiB freed) |
| Quality spot-check | — | coherent, no visible degradation |

q8_0 costs nothing in speed and frees ~2.6 GiB — but that headroom only pays off if we add a draft model in WS3. **Not persisted** (would require running Ollama as a managed `ollama serve`/LaunchAgent instead of the macOS app, since the app ignores the env var). Gate persistence on the WS3 go/no-go.

**Bottom line:** the real bottleneck is generation at ~11 tok/s — untouched by WS2. Per web
research (2026-05-21): **MLX (WS4) is dropped** — it slows prefill on M2 (no native bf16), its
decode edge vanishes at 27B, and it risks the prompt-cache win. One ingest question remains open —
the `n_ubatch` micro-batch (see WS2a caveat above), to be tested via llama.cpp. After that, the only
generation lever is WS3 (speculative decoding with the pulled `qwen3:4b` draft, needs the q8_0 headroom).
