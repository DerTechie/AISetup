# 2026-05-21 — Why local was "wayyy too slow": auxiliary calls evicting the KV cache

## Context

The `private` route (local `qwen3.6:27b` on the Mac M2 Max) was unusable for agentic work —
roughly two minutes per turn even for trivial messages. We set out to optimize it, starting
(per the [design spec](../superpowers/specs/2026-05-21-local-speed-optimization-design.md))
with a benchmark + diagnosis before changing anything.

## What we measured

- Generation is memory-bandwidth bound at a steady ~11 tok/s; cold prompt ingest ~128 tok/s,
  so the 16.4k-token agent context costs ~128 s before the first output token.
- Ollama keeps a KV cache of a **stable prompt prefix**: re-sending an identical prefix
  ingested 14.6× faster (225 s → 15 s).

## The hypothesis that was wrong

Initial theory: Hermes injects something dynamic near the **front** of the prompt each turn
(a timestamp, a rotating system-reminder), which would invalidate the prefix cache and force
a full re-ingest every turn. A synthetic probe confirmed the *mechanism* — changing one token
at the front of a stable prefix did force a full re-ingest.

But the hypothesis about Hermes was **wrong**, and capturing real traffic proved it.

## What the captured payloads actually showed

We enabled `OLLAMA_DEBUG_LOG_REQUESTS` and captured real `private`-route requests. (Aside:
the macOS Ollama app ignores `launchctl setenv`, so we briefly replaced it with a manual
`ollama serve` to get request-body logging, then restored the app.)

Three `/api/chat` bodies for two user turns:
1. Turn 1 — agent prompt, **18,031-char system message**.
2. A **251-char** call: *"Generate a short, descriptive title…"* — Hermes' **title generation**.
3. Turn 2 — agent prompt, **system message byte-identical to turn 1** (same SHA), and the whole
   request was exactly turn 1 **plus appended messages**. A perfect prefix extension.

So the agent prompt never changed. The cache *should* have hit. The culprit was call #2: the
title-generation auxiliary call runs on the **same single-slot** local model (`num_parallel=1`),
and it overwrote turn 1's cached 18k prefix. Turn 2 then found the slot holding the title-gen
prompt and had to re-ingest all 18k cold.

**The buster wasn't prompt mutation — it was cache eviction by an interleaved utility call.**

## The fix and what was rejected

Routed Hermes' text auxiliary tasks (`title_generation`, `compression`, `triage_specifier`,
`profile_describer`, `curator`) to the fast cloud `main` route, so they never touch — and never
evict — the local model's cache. This also matches the project's existing philosophy: cloud is
the utility brain, local is the heavy/private specialist.

Rejected alternatives:
- **`OLLAMA_NUM_PARALLEL=2`** (give aux calls a second slot): rejected — Ollama splits the
  context window across slots, dropping each to 16k, below the 18k agent prompt.
- **A second small local model (`qwen3:4b`) for aux**: preserves on-device privacy and avoids
  thrash, but more setup; deferred. (We'd revisit this if private-route privacy becomes a hard
  requirement — GDPR is currently deferred.)
- **Disabling aux features on private**: simplest but loses auto-titles/compression.

## Result

Real `private` turns measured before vs after:
- Subsequent turn: **~133 s → 15.9 s** (~8.4×).
- First turn of a new session: **8.3 s** — the 18k prefix now survives across sessions because
  nothing evicts it.

The one-time cold ingest after a model reload is still slow — measured at ~1m24s (84 s) for
the first agent turn; that's the next target (WS2: `num_batch` and KV-cache tuning). But the
day-to-day "every turn is 2 minutes" pain is gone.

## Lesson

The benchmark-first discipline paid off twice: the synthetic probe proved the *mechanism*, but
only **capturing real traffic** revealed the actual *cause*. Had we acted on the plausible
front-mutation hypothesis, we'd have spent effort stabilizing a prompt that was already stable
and never found the title-generation evictor.
