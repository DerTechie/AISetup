# CLAUDE.md

> **Inherits the general BuildInPublic rules:** @/home/dertechie/Organizations/DerTechie/BuildInPublic/CLAUDE.md
> Project-specific rules below override or extend them.

## What this project is

**AISetup** is the design and build of a hybrid local/cloud AI agent setup: **Hermes Agent** (on an Arch Linux workstation) talks to a **LiteLLM gateway** on a headless **Mac M2 Max**, which routes to a **local Ollama model** for routine work and to **OpenRouter** for heavy business research. Goals: keep the workstation free, run routine tasks locally for free/privately, reach cloud reasoning only when needed, with hard cost bounds and full observability.

Authoritative design: [`docs/superpowers/specs/2026-05-20-hybrid-ai-routing-design.md`](docs/superpowers/specs/2026-05-20-hybrid-ai-routing-design.md).

## How we work

- **Capture decisions.** Every significant decision gets a dated entry in `docs/journal/` recording the **why** and **what was rejected** — not just the what. This feeds Obsidian notes and a future talk, so the story matters as much as the outcome.
- **Keep docs in sync.** When the system changes, update `README.md` (overview) and `docs/runbook.md` (operations) in the same change.
- **All docs in English.**
- **Verify before claiming done.** Run the actual command and show output before saying something works. No success claims without evidence.
- **Commit meaningful units.** This repo's git history is part of the reconstructable path.

## Doc map

- `README.md` — overview / front page
- `docs/superpowers/specs/` — design specs
- `docs/journal/` — dated decision log (the story for the talk)
- `docs/runbook.md` — how to operate the system (created during implementation)
- `CLAUDE.md` — this file

## Architecture (summary)

- **Mac M2 Max** (headless, no-sleep): Ollama with **one** ~24 GB agentic model + LiteLLM proxy.
- **LiteLLM** (`:4000`): OpenAI-compatible gateway — routing by model name, **€100/month** hard budget cap, per-request token cap, logging.
- **Hermes** (Arch): single main model → LiteLLM. Routine defaults **local**; cloud via `/model deep` or a research subagent. A **triage advisor** skill (local judge, recommend-mode) helps decide when unsure.
- **Observability:** LiteLLM dashboard (`:4000/ui`) now → **Langfuse on the NAS** later.
- **Deferred:** Presidio / GDPR hardening.

## Key constraints (don't relearn these)

- Hermes runs **one model per profile**; no native auto difficulty-routing ([#4461](https://github.com/NousResearch/hermes-agent/issues/4461)). Routing lives in the gateway + explicit choices.
- 32 GB unified memory ⇒ **one** resident ~24 GB model; no second router model on the hot path.
- 4B was inadequate; 14B mediocre for agentic use; ~24 GB tier is the realistic target.
