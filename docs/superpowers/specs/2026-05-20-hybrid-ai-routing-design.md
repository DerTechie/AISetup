# Hybrid Local/Cloud AI Agent Setup — Design Spec

- **Date:** 2026-05-20
- **Status:** Approved (design phase) — implementation not started
- **Owner:** DerTechie
- **⚠️ Superseded in part:** §5 (Routing), §6 (Cost control), §8 (Model choice), and §13 (Success criteria) are revised by [`2026-05-20-model-route-roles-redesign.md`](2026-05-20-model-route-roles-redesign.md): routes are renamed `main`/`private`/`deep`, the default brain is now a fast cloud model (GPT-5-mini) rather than local, and the budget splits three ways ($50/$35/$15). Where the two conflict, the redesign wins.

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

- Cloud budgets are scoped to the **cloud models only** (per-model `max_budget` + `budget_duration` in `litellm_params`), **not** the global `litellm_settings.max_budget`. The global budget blocks *all* requests — including the free `local` model — once total spend crosses it (verified the hard way during implementation), which we explicitly do not want. Plus a per-request `max_tokens` on each cloud model.
- **Split cap preserves the €100 ceiling:** `deep` (GPT-5) `max_budget: 75` + `deep-fallback` (Gemini) `max_budget: 25` — the two independent caps **sum to ≤100 USD (~€92)**, so total cloud spend still cannot exceed the ceiling even with the fallback. €75 is ample for light, occasional deep use (~$0.08/call).
- **Behavior at cap:** the capped cloud model is **blocked with a clear `budget_exceeded` error**; `local` (no budget) keeps working. Never silently downgrade a deep task to local — though `deep` may error-fall-back to `deep-fallback` (another *cloud* model, still capped), which is not a downgrade to local.

## 7. Observability

- **Now:** LiteLLM built-in logging + spend dashboard at `mac:4000/ui`, opened from the Arch browser. Shows model chosen, token counts, cost, latency per request.
- **Later:** Langfuse self-hosted on the **NAS** (always-on Docker host, no contention with the Mac's model memory). LiteLLM ships traces via callback. Langfuse is built on the OpenTelemetry GenAI semantic conventions — the de-facto standard (stable early 2026).
- Routing observability is **separate from Obsidian.** Obsidian holds project docs + the journal only, never runtime logs.

## 8. Model choice

- **Local:** `qwen3.6:27b` (Q4_K_M, ~17 GB; official Ollama tag, native tools + thinking, benchmark-leading for its size). One physical model exposed as two gateway routes: **`local`** (thinking OFF via `reasoning_effort: none`) for fast routine, and **`local-think`** (thinking ON) for local reasoning/synthesis (e.g. drawing conclusions from `deep`'s research). `keep_alive: -1` keeps it resident/warm. Q4 only — higher quants are too tight alongside the Docker stack.
- **Cloud (OpenRouter):** **`deep` = GPT-5** (`openrouter/openai/gpt-5`, 400K context, 128K max output, ~$1.25/$10 per M), with **`deep-fallback` = Gemini 3.1 Pro Preview** (`openrouter/google/gemini-3.1-pro-preview`, 1M context, ~$2/$12) wired via LiteLLM error-fallbacks. Rationale: `deep` is rare, explicitly-invoked, and hard-capped, so frontier quality (the whole reason to escalate) costs little in absolute terms (~$0.08/call) and stays under the cap. Frontier models are **first-party served** — GPT-5 even has dual providers (OpenAI + Azure) — which avoids the flaky cheap third-party providers that broke the earlier choice. The cross-vendor fallback gives redundancy a single first-party model can't. **Rejected: DeepSeek-R1** — only two providers on OpenRouter, the cheap one (Novita) returned a persistent `NOT_ENOUGH_BALANCE` 403 (isolated to Novita; Azure worked), and the reliable one (Azure) caps output at 4096 — too tight for a reasoning model. See journal `2026-05-20-deep-model-gpt5.md`.

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
2. **Triage advisor skill** (recommend / local-judge). A Hermes skill that, given a task, asks the local model "deep or local, and why?" and surfaces a recommendation **the user confirms** — it never switches models itself. Recommend-only; auto-routing is a later, earned change once its judgment is trusted.
   - **Trigger (hybrid):** *manual spine* — invoked deliberately whenever the user is unsure (opt-in, zero latency on normal turns); *fact-triggered nudge* — the skill's **description** names concrete conditions (large context, or explicit market/strategic/current-info research) so Hermes proposes it only then. Reliable because it is fact-keyed, not a difficulty guess (consistent with §5's "capability facts, not text-difficulty guessing").
   - **How it judges:** the skill makes a **direct HTTP call to the gateway's `local` route** (not via any Hermes-internal model-invocation mechanism) — engine-agnostic, so it survives the Phase 4 MLX swap, and it sidesteps the §12 feasibility item. Runs on `local` (no-think) to start; revisit to `local-think` only if recommendations prove weak. Returns a structured result: recommendation (`local`/`deep`), one-line reason, signals keyed on.
   - **Rubric:** recommend **deep** when the task needs large context, current/web info, or heavy strategic/market reasoning; recommend **local** for routine triage, summaries, drafting, simple Q&A. Derived from the spec's examples.
   - **Observability / trust-building:** the judge call appears in the LiteLLM dashboard as a `local` request (free); the skill also appends a one-line record (timestamp, task gist, recommendation, reason) to a small local log the user can skim to evaluate "was it right?" before considering auto-routing.
   - **Out of scope / later:** auto-routing (acting without confirmation); the `local-think` upgrade.
3. **Langfuse on the NAS.**
4. **Local inference optimization (Mac engine).** Reduce local latency by changing only the engine behind the gateway's `local` / `local-think` routes — nothing downstream is touched (the gateway isolates the engine). Empirical and persisted for the talk.
   - **Levers (prefill is the dominant cost, so decode-side levers rank low):** (1) engine bake-off — **MLX** (via LM Studio's OpenAI-compatible server, for keep-warm + ops) vs **tuned Ollama** (`OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`, bounded `num_ctx`); (2) quantization (MLX 4-bit vs Ollama Q4_K_M; test higher quants if MLX frees memory); (3) KV-cache / context tuning; (4) wired-memory limit (`sudo sysctl iogpu.wired_limit_mb=28000`); (5) speculative decoding — low priority / likely deferred (decode isn't the bottleneck and a draft model is tight on 32 GB).
   - **Method:** one standard benchmark turn (the real ~16K-token Hermes system prompt + a representative routine prompt); measure **TTFT (prefill)** and **decode tok/s**, warm vs cold, think on/off, repeated N times. Capture the **Ollama baseline first**, then run each candidate through the same harness *via the gateway* (apples-to-apples). Phase 3's Langfuse traces are the measurement substrate; results go into a dated journal entry as a table.
   - **Decision criterion:** fastest config that preserves (a) **tool/function calling** (Hermes is agentic), (b) the **thinking on/off** toggle parity that `local` vs `local-think` depends on, and (c) memory headroom alongside the Docker stack. If MLX wins, migrate by repointing the two routes; keep Ollama as instant rollback until validated. Update `runbook.md` and `README.md` then.
   - **Out of scope (separate effort):** trimming Hermes' 16K system prompt / toolsets — the other big prefill lever, but Hermes-side, not Mac engine.
5. **(Deferred)** Presidio anonymization + GDPR hardening.

## 12. Open items to verify during implementation

- ~~Exact Ollama tag for the local model.~~ **Resolved: `qwen3.6:27b` (Q4_K_M).**
- ~~Hermes `deep`-route + API specifics.~~ **Resolved:** Hermes uses an OpenAI-compatible `custom` provider (`base_url` → gateway, model `local`); auth requires a **literal `api_key`** in the `model:` block (`key_env` is *not* honored there — only for fallback/auxiliary). `/model deep` switches the model in-session and routes via the gateway.
- ~~Whether a Hermes skill can invoke a specific model for the triage advisor.~~ **Resolved (sidestepped):** the triage advisor skill calls the gateway's `local` route over HTTP directly rather than relying on Hermes-internal model invocation — engine-agnostic and independent of undocumented Hermes internals.
- ~~OpenRouter chosen model + real context limit + provider/no-log settings.~~ **Resolved:** `deep` = GPT-5 (`openrouter/openai/gpt-5`, verified 400K context / 128K output, dual first-party providers), `deep-fallback` = Gemini 3.1 Pro Preview (`openrouter/google/gemini-3.1-pro-preview`, 1M context). Chosen after the DeepSeek-R1/Novita outage; see journal `2026-05-20-deep-model-gpt5.md`. (No-log/data-collection routing still deferred with GDPR.)
- **Phase 4 (local inference optimization), verify when reached, not now:** does the MLX / LM Studio server do reliable **tool/function calling** (deal-breaker for agentic use if not)? Does it honor a **thinking on/off** toggle equivalent to `reasoning_effort: none` (may need two served instances or a chat-template flag)? MLX **keep-warm + auto-start** on headless login, matching Ollama's `keep_alive: -1` + launch-on-login.
- **Revisit model-route naming (deferred).** Route names (`local`, `local-think`, `deep`, `deep-fallback`) are named by *implementation* (where the model runs). `local` is too implementation-specific — it's slow and may be swapped for a different/faster backend, which would make the name misleading. Rename to *role/intent*-based names describing what each route is **for** (e.g. the everyday default Hermes points at), so the backend can change without the name lying. Blast radius to coordinate in one rename: `mac/litellm-config.yaml` model_names + the `context_window_fallbacks`/`fallbacks` keys; Hermes `~/.hermes/config.yaml` `default` + `model_aliases` + the `/model …` switch names; `triage_advisor.py` (`JUDGE_MODEL`, the rubric's local/deep wording, the `/model` hints in `format_output`); `SKILL.md`; `runbook.md`; `README.md`; this spec.

## 13. Success criteria

- Workstation GPU stays free during agent use (no freezes).
- Email triage runs locally, free, and is good enough on the real inbox.
- Heavy research reachable via an explicit route, with visible model choice + cost.
- Monthly cloud spend **cannot** exceed €100.
- Every significant decision captured in `docs/journal/`.
