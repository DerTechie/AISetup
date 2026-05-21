# 2026-05-21 — WS2: when the obvious knobs do nothing (cold-ingest & memory tuning)

## Context

After [WS1](2026-05-21-prefix-cache-aux-eviction.md) fixed the per-turn cache eviction
(~133 s → 16 s for warm turns), two costs remained for the `private` route
(local `qwen3.6:27b`, Mac M2 Max): the ~128 s **cold ingest** of the agent's ~16k prefix after
a model reload, and the ~11 tok/s **generation** floor. WS2's brief
([design spec](../superpowers/specs/2026-05-21-local-speed-optimization-design.md)) was the two
cheap, reversible server knobs: raise `num_batch`, and quantize the KV cache to `q8_0`.

## What we measured

Took over the Mac Ollama with a manual `ollama serve` (`OLLAMA_HOST=0.0.0.0`, `keep_alive=-1`),
since the macOS app ignores `launchctl setenv` for these vars. Cold ingest = a unique ~13.6k-token
prompt forcing a cache miss; generation = `think:false` (production runs `reasoning_effort: none`).

- **`num_batch` sweep (512 / 1024 / 2048):** 138 / 140 / 140 tok/s. **Flat.**
- **`OLLAMA_KV_CACHE_TYPE=q8_0`** (flash-attn on): cold ingest ~131 tok/s (~6% slower, noise),
  generation 11.1 tok/s (unchanged), KV cache **5.7 → 4.7 GiB**, total resident VRAM
  **24.4 → 21.8 GiB**. Quality spot-check coherent, no visible degradation.

## The decision and what was rejected

**Both knobs are bandwidth-bound dead ends for speed.** The M2 Max's unified memory bandwidth
limits both prompt ingest and token generation; `num_batch` (a compute-parallelism knob) can't
move a memory-bound matmul, and `q8_0` shrinks KV reads but adds dequant overhead, netting to
noise. So WS2 delivers **no speedup**.

- **Persist a higher `num_batch`** — rejected: zero benefit, just config drift.
- **Adopt `q8_0` KV now** — *deferred, not adopted.* It costs nothing in speed and frees ~2.6 GiB,
  but that headroom only pays off if we add a draft model (WS3 speculative decoding). And
  persisting it has a real operational cost: the macOS Ollama app ignores `OLLAMA_KV_CACHE_TYPE`,
  so keeping it means abandoning the app for a managed `ollama serve` (LaunchAgent). Not worth
  that trade until WS3 is a go. Gate the two decisions together.
- We **restored the app** (f16 KV, loopback) — production steady state is unchanged. No
  `litellm-config.yaml` change was made, because there was nothing worth persisting.

## Lesson

The benchmark-first discipline earned its keep again, this time by saying *stop*: the spec's two
"cheap wins" were measured to be worth nothing, so we spent no effort wiring them into production.
The honest output of a workstream can be "this lever doesn't exist here." The real bottleneck —
~11 tok/s generation — is untouched by anything in WS2; it needs a different *kind* of lever
(WS3 speculative decoding, which the q8_0 headroom would feed, or WS4's MLX backend), and that's
the next gate with the owner.
