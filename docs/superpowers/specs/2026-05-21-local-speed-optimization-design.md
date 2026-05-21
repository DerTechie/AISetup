# Local-Speed Optimization — Design Spec

- **Date:** 2026-05-21
- **Status:** Approved (design phase) — implementation not started
- **Owner:** DerTechie
- **Relates to:** Executes the "reclaim the default" / "MLX/engine work makes local fast enough" path left open in [`2026-05-20-model-route-roles-redesign.md`](2026-05-20-model-route-roles-redesign.md) §2 and §1.2. Explains the "~2 minutes for even the simplest task" symptom recorded there. Does **not** change route roles; it makes the `private` route fast.

## 1. Why this work

The `private` route (local `qwen3.6:27b` on the Mac M2 Max) is too slow for agentic use. The earlier redesign demoted it from default brain to a privacy/€0 specialist precisely because of this. The user now wants local to be fast enough to do real agentic work again — **maximize raw speed, and especially fix ingest.**

The trigger symptom: with ~16.4k tokens of context at the start of a turn, the model is "wayyy too slow."

## 2. What we measured (baseline, 2026-05-21)

Environment: Ollama 0.24.0 (new engine), `qwen3.6:27b` Q4_K_M (27.8B), 24.4 GB resident in VRAM on a 32 GB M2 Max, 32k context, `keep_alive:-1`. `OLLAMA_NUM_PARALLEL=1`, `BatchSize=512`, KV cache **f16** (5.7 GiB @ 32k), **FlashAttention already ON** (auto-enabled by the new engine; the env var is unset but the load request shows `FlashAttention:Enabled` — so flash attention is *not* an available lever).

| Metric | Value |
|---|---|
| Generation throughput | **11.2 tok/s** raw (~10 tok/s end-to-end via gateway). Steady; memory-bandwidth bound. |
| Cold prompt ingest | **~128 tok/s** → a 16.4k context ≈ **~128 s before the first output token** |
| Cloud `main` (GPT-5-mini), for contrast | ~35 tok/s end-to-end |
| Gateway (LiteLLM) overhead | ~1 tok/s — negligible; the model is the bottleneck |

### 2.1 Key finding — prefix-cache busting (root cause of the symptom)

Ollama caches the KV state of a stable prompt **prefix**. Measured on a ~29k-token prompt:

| Turn | Ingest time | Speed |
|---|---|---|
| 1 — cold | 225 s | 128 tok/s |
| 2 — grow conversation, prefix **byte-stable** | **15.4 s** | 1,870 tok/s (**14.6× faster**) |
| 3 — grow conversation, **one token changed at the front** (a timestamp) | 223.6 s | 129 tok/s (full re-ingest) |

**A single mutated token near the front of the prompt invalidates the whole cache and forces a full re-ingest.** Therefore: if Hermes injects dynamic content (current date/time, a rotating system-reminder) near the *front* of its prompt each turn, every turn pays the full cold ingest (~128 s for 16.4k). This is the most likely explanation for the "~2 minutes for the simplest task" symptom.

Two contributing factors also seen in the logs:
- The model reloaded ~5 times in one afternoon (every ~25 min). **Each reload wipes the prefix cache.** `keep_alive:-1` is set now, but was not always in effect.
- KV cache is full f16 (5.7 GiB) — memory headroom is tight on 32 GB, which constrains batch size and any second (draft) model.

## 3. Goals & non-goals

**Goals:** lower steady-state per-turn ingest, lower cold ingest, raise generation throughput, and reach a realistic end-to-end agentic turn time that makes local usable. Each measured against the §2 baseline.

**Non-goals:** changing route roles (`main` stays the default brain); GDPR/Presidio work; touching cloud routes. This spec only makes `private` fast.

## 4. Workstreams (priority order; each is gated — stop when fast enough)

Sequenced cheapest/highest-leverage first. After each workstream we re-benchmark and decide go/no-go on the next.

### WS1 — Prefix-cache fix (highest leverage, zero quality cost)
- **Confirm the bug on real traffic:** capture an actual Hermes → gateway → Ollama request payload across two consecutive turns (via LiteLLM request logging or `OLLAMA_DEBUG_LOG_REQUESTS=1`) and diff the leading bytes to establish whether the prefix is byte-stable.
- **Fix:** if Hermes front-mutates, move dynamic content (date/time, system-reminders) to the *tail* of the prompt — after the stable system+tools prefix — or stabilize/remove it, so the large prefix stays byte-identical across turns.
- **Harden caching:** ensure `keep_alive:-1` is permanent so reloads stop wiping the cache.
- **Expected win:** every turn drops from ~128 s to ~15 s ingest. This alone may resolve the symptom.

### WS2 — Cold-ingest & memory tuning (small quality risk)
- Raise `num_batch` (512 → 1024 / 2048) and re-benchmark cold ingest tok/s.
- Test `OLLAMA_KV_CACHE_TYPE=q8_0` (5.7 GiB → ~3 GiB), which also frees headroom for WS3.
- Matters because the first turn and any genuine cache miss still pay cold ingest.

### WS3 — Generation speedup via speculative decoding
- Use `qwen3:4b` (already pulled) as a draft model for the 27B; measure draft acceptance rate and net generation tok/s.
- Goal: lift the 11 tok/s floor with no quality change. Requires the memory headroom freed in WS2.

### WS4 — MLX backend evaluation (Mac-native lever)
- On Apple Silicon, `mlx-lm` (served via `mlx_lm.server`, OpenAI-compatible) can beat Ollama's GGUF/llama.cpp path on generation and sometimes ingest, using MLX-optimized quants.
- Convert/obtain `qwen3.6:27b` in MLX format (`mlx_lm.convert` if needed), serve it, and benchmark ingest + generation head-to-head against the tuned Ollama config.
- Verify it preserves what we rely on: OpenAI-compatible API the gateway can route to, and **prompt-cache support** (so the WS1 win survives).
- Runs **alongside** Ollama; the gateway points `private` at whichever wins. Fully reversible.

### WS5 — Model bake-off (only if still too slow)
- Shortlist models that fit ~24 GB; benchmark on the winning backend (Ollama-tuned or MLX).
- **Quality gate = real Hermes tasks judged by hand** (tool-calling, email triage, a coding edit) — not a synthetic suite. Swap only if a candidate clearly wins on speed without losing agentic quality. (History: 4B inadequate, 14B mediocre — the bar is real.)

## 5. Success criteria

Re-run the §2 probes after each workstream. Concrete targets (to be confirmed with the owner before WS1 implementation):

| Metric | Baseline | Target |
|---|---|---|
| Steady-state per-turn ingest (cache-hit path, 16.4k prefix) | ~128 s (if cache busts) | ≤ ~15 s |
| Cold ingest | ~128 tok/s | meaningfully higher (set after WS2) |
| Generation | 11 tok/s | meaningfully higher (set after WS3/WS4) |
| Realistic end-to-end agentic turn | 40–60 s+ | usable (set with owner) |

"Usable" is owner-judged on real Hermes tasks, consistent with the WS5 quality gate.

## 6. Risks & rollback

- **WS1 Hermes prompt change** is the only change touching agent behavior. Back up `~/.hermes/config.yaml` first (runbook already keeps `.bak-*`). Risk: reordering prompt content could affect model behavior — validate on real tasks.
- **WS2 server tuning** is env-var changes; revert by unsetting and restarting Ollama. `q8_0` KV cache is a small quality risk — measure.
- **WS3 speculative decoding** is additive; disable to revert. Constrained by memory headroom.
- **WS4 MLX** runs alongside Ollama; the gateway route is the only switch. Ollama stays as fallback.
- **WS5 model swap** is just repointing the route; fully reversible.

## 7. Documentation

Update `README.md`, `docs/runbook.md`, and add a `docs/journal/` entry per the project's "capture decisions" rule as workstreams land. The journal should record what each lever bought (the numbers) and what was rejected — this is part of the talk narrative.
