# 2026-05-20 — Model-route roles & the brain inversion

## Decision

Renamed the gateway routes from implementation-based (`local`, `local-think`, `deep`, `deep-fallback`) to role-based names (`main`, `private`, `deep`, `deep-fallback`), and **inverted the default brain**: `main` is now a fast cloud model (GPT-5-mini), and the local 24 GB model demotes to a `private` specialist.

## Why

Two problems converged:

1. **`local` named a place, not a role.** It was slow and likely to be swapped for a faster backend, which would make the name lie. Routes should name what they are *for*.
2. **The slow local model can't be the brain (empirical).** It takes ~2 minutes for even the simplest task. The brain fires on *every* turn and re-prefills Hermes' ~16K system prompt each time, so a 2-minute floor on the cheapest operation made the whole agent unusable. The base spec's "Default = local" was wrong in practice.

Fixing (2) forced the default to a fast cloud model; that made the role-based naming from (1) natural to express. `main` is fully backend-agnostic — if Phase 4 ever makes a local model fast enough, the name still holds.

## What was rejected

- **Small fast local brain** (tiny model orchestrates, delegates to the 24 GB model): infeasible — 32 GB fits only one resident ~24 GB model, and 4B-class models tested inadequate for agentic use.
- **Keep local as brain, fix latency in Phase 4 first:** defers relief that blocks work now; Phase 4 gains unproven. Kept only as a future "reclaim the default" path.
- **`main` = Claude Haiku 4.5:** best agentic reputation but 4× the input cost; kept as the empirical fallback if GPT-5-mini's tool-calling proves weak.
- **Budget `€50/€35/€15`:** arithmetically over the $100 cap (~€109); corrected to USD `$50/$35/$15`.

## Trade accepted

"Private + free **by default**" became "**fast by default, private on demand**." Email triage is explicitly pinned to `private` so the one genuinely sensitive workflow still stays on-device.

## Cascade

Three-way triage advisor (`main`/`private`/`deep`) judged on `main` (not the slow local model); `main` degrades to `private` at its budget cap; `local-think` dropped (the fast `main` brain now does synthesis).
