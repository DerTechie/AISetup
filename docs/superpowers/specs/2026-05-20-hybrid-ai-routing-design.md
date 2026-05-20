# Hybrid Local/Cloud AI Agent Setup — Design Spec

- **Date:** 2026-05-20
- **Status:** Approved (design phase) — implementation not started
- **Owner:** DerTechie

## 1. Purpose & Goals

Optimize a local AI agent (Hermes Agent) setup so that it:

- Offloads model inference from the Arch Linux workstation (AMD Radeon RX 7900 XTX) to a headless Mac M2 Max (32 GB), keeping the workstation free of inference load and freezes.
- Runs routine tasks (email triage, "alert me on a real business inquiry", daily summaries) **locally** — free and private.
- Routes heavy business research (market analysis, strategic positioning, project-vs-market analysis) to a **capable cloud model** when local quality is insufficient.
- Makes routing **reliable**, cloud cost **hard-bounded**, and the model choice **observable**.
- Persists the build path so it can be reconstructed later for Obsidian notes and a future talk.

**Non-goals (deferred):** GDPR / Microsoft Presidio anonymization. Revisit once there is a real customer or genuinely sensitive third-party data in play. Treat masking as risk-reduction, never as a compliance guarantee.

## 2. Components

| Component | Location | Role |
|---|---|---|
| Hermes Agent | Arch workstation | The agent brain: skills, memory, email gateway. Single main model pointed at the gateway. |
| LiteLLM Proxy | Mac M2 Max (`:4000`) | OpenAI-compatible gateway. Routes by model name, enforces budget + token caps, logs every call. |
| Ollama | Mac M2 Max (`:11434`) | Runs **one** ~24 GB agentic model locally. |
| OpenRouter | Cloud | Deep reasoning / large context for business research. |
| LiteLLM dashboard | Mac (`:4000/ui`) | Initial observability — model chosen, tokens, cost, latency. |
| Langfuse | NAS (Docker), later | OpenTelemetry-based observability upgrade (per-request traces). |

## 3. Key constraints & findings

- **Hermes uses ONE main model per profile**, fixed at startup. There is **no native automatic task-difficulty routing** (open, unimplemented feature request [NousResearch/hermes-agent#4461](https://github.com/NousResearch/hermes-agent/issues/4461)). Ways to use another model: `/model` switch + aliases, subagent delegation, auxiliary task slots (side-tasks only). **Therefore the routing decision lives in the gateway and in explicit user/agent choices — not in Hermes auto-detecting difficulty.**
- **Hermes connects via an OpenAI-compatible endpoint.** Both Ollama and OpenRouter also speak OpenAI format, so the gateway is a thin router, not a protocol translator.
- **32 GB unified memory** realistically runs **one** ~24 GB agentic model; macOS overhead + KV cache consume the rest. No room for a second resident "router" model — so no local LLM-classifier on the hot path.
- **Model tier matters more than the README implied.** A 4B model was inadequate in prior testing; 14B is mediocre for agentic/tool use; the realistic, capable ceiling on this hardware is the **~24 GB agentic Qwen tier** (Ollama lists a `qwen3.6`-class model here — confirm exact tag at setup).

## 4. Architecture

```mermaid
graph TD
    subgraph Arch[Arch Workstation - 7900 XTX, freed up]
        H[Hermes Agent<br/>single main model]
    end
    subgraph Mac[Mac M2 Max - headless, no-sleep]
        L[LiteLLM Proxy :4000<br/>OpenAI-compatible<br/>routing + budget cap + logging]
        O[Ollama :11434<br/>one ~24GB agentic model]
        L -->|model: local| O
    end
    subgraph Cloud[Cloud]
        OR[OpenRouter<br/>deep reasoning / large context]
    end
    NAS[(NAS - Langfuse, later)]

    H -->|OpenAI /v1| L
    L -->|model: deep| OR
    L -. logs/traces .-> NAS
```

## 5. Routing design

Cost is bounded independently (Section 6), so routing is chosen for **reliability**, not to police spend. Layers:

1. **Default = local.** Routine + email triage run on the local model. Automatic, €0.
2. **Capability auto-fallback.** LiteLLM `context_window_fallbacks`: if a prompt is too large for the local model, it routes to cloud automatically. Reliable because it is a capability fact, not a difficulty guess.
3. **Triage advisor (Hermes skill).** When unsure, ask "deep or local, and why?" The **local model** judges and **recommends**; the user confirms. Start in *recommend / local-judge* mode; reversible to auto-route once its judgment is trusted (observe via the dashboard).
4. **Explicit cloud.** `/model deep` alias, or a **cloud-pinned research subagent** Hermes delegates the heavy step to (which also scopes cloud spend to that step rather than a whole session).
5. **Backstop.** Hard budget cap + per-request token cap (Section 6).

**Rejected:** gateway guessing difficulty from raw prompt text (unreliable — the exact cause of "dumb local answer"); pure manual selection with no cap (human over-biases to cloud → cost).

## 6. Cost control

- LiteLLM `max_budget` = **€100/month** (plus optional daily sub-cap), with `budget_duration`, and a per-request `max_tokens`.
- **Behavior at cap:** cloud requests are **blocked with a clear error**; routine continues locally. Do **not** silently downgrade a deep task to the local model — a visible error is better than a confidently wrong cheap answer.

## 7. Observability

- **Now:** LiteLLM built-in logging + spend dashboard at `mac:4000/ui`, opened from the Arch browser. Shows model chosen, token counts, cost, latency per request.
- **Later:** Langfuse self-hosted on the **NAS** (always-on Docker host, no contention with the Mac's model memory). LiteLLM ships traces via callback. Langfuse is built on the OpenTelemetry GenAI semantic conventions — the de-facto standard (stable early 2026).
- Routing observability is **separate from Obsidian.** Obsidian holds project docs + the journal only, never runtime logs.

## 8. Model choice

- **Local:** `qwen3.6:27b` (Q4_K_M, ~17 GB; official Ollama tag, native tools + thinking, benchmark-leading for its size). One model only. Thinking-by-default; pass `think: false` for fast triage. Q4 only — higher quants are too tight alongside the Docker stack.
- **Cloud (OpenRouter):** a strong reasoning / large-context model for research (e.g. DeepSeek-R1 or a Qwen-72B-class model). Pick by required context window + quality; verify the model's real context limit (not all support 200k).

## 9. Error handling

- **Cloud failure** (timeout / 5xx / rate limit): clear error to Hermes, logged. No silent downgrade of a deep task to local.
- **Local failure:** surfaced error.
- **Budget cap hit:** cloud blocked with a clear message; local routine unaffected.

## 10. Persistence & documentation

- **git repo** (initialized) — history is part of the reconstructable path.
- `README.md` — English overview / front page.
- `docs/journal/` — dated decision entries; the WHY + story for the talk; Obsidian-friendly markdown.
- `docs/runbook.md` — operational reference (created during implementation).
- `docs/superpowers/specs/` — design specs (this file).
- `CLAUDE.md` — working conventions.

## 11. Phasing (implementation order)

The **routing layer (LiteLLM) is the centerpiece and the first milestone** — local *and* cloud routing through the gateway, end to end. Local-model quality on the real inbox is validated once routing is up, not as a gating step before it.

1. **Routing in place (core milestone).**
   - **1a — Mac prep:** disable sleep (`pmset`), set `OLLAMA_HOST`, pull the ~24 GB model; smoke-test that Ollama answers over the network.
   - **1b — LiteLLM gateway:** define `local` (Ollama) and `deep` (OpenRouter) models/aliases; OpenRouter key via env var; **€100/mo budget cap** + per-request token cap; `context_window_fallbacks`; dashboard at `:4000/ui`.
   - **1c — Wire Hermes:** point Hermes' main model at LiteLLM (default `local`); verify `/model deep` reaches cloud and the dashboard shows model / tokens / cost; confirm the cap blocks cloud with a clear error.
2. **Triage advisor skill** (recommend / local-judge).
3. **Langfuse on the NAS.**
4. **(Deferred)** Presidio anonymization + GDPR hardening.

## 12. Open items to verify during implementation

- ~~Exact Ollama tag for the local model.~~ **Resolved: `qwen3.6:27b` (Q4_K_M).**
- Hermes model-alias / subagent config specifics for the `deep` route, and which API Hermes currently uses.
- Whether a Hermes skill can invoke a specific model for the triage advisor.
- OpenRouter chosen model + real context limit + provider/no-log settings (revisit in Phase 6).

## 13. Success criteria

- Workstation GPU stays free during agent use (no freezes).
- Email triage runs locally, free, and is good enough on the real inbox.
- Heavy research reachable via an explicit route, with visible model choice + cost.
- Monthly cloud spend **cannot** exceed €100.
- Every significant decision captured in `docs/journal/`.
