# Model-Route Roles & Brain Redesign — Design Spec

- **Date:** 2026-05-20
- **Status:** Approved (design phase) — implementation not started
- **Owner:** DerTechie
- **Supersedes:** Sections **§5 (Routing), §6 (Cost control), §8 (Model choice), §13 (Success criteria)** of [`2026-05-20-hybrid-ai-routing-design.md`](2026-05-20-hybrid-ai-routing-design.md), and resolves the "Revisit model-route naming" item in its §12. Where the two specs conflict, **this one wins**; the base spec will be reconciled during implementation.

## 1. Why this redesign

Two problems with the original routing design surfaced:

1. **Implementation-named routes.** Routes were named for *where the model runs* (`local`, `local-think`, `deep`, `deep-fallback`). `local` is a leaky name: it is slow and may be swapped for a different/faster backend, which would make the name a lie. Names should describe what a route is **for** (its role), so the backend can change without the name misleading.

2. **The slow local model cannot be the brain (empirical).** The base spec made `local` the default ("Default = local"). In practice the local 24 GB model takes **~2 minutes for even the simplest task**. The brain fires on *every* turn — every skill-load decision, every tool dispatch, every routine reply — and each turn re-prefills Hermes' ~16K-token system prompt. A 2-minute floor on the cheapest operation makes the whole agent unusable. This is the prefill-dominated cost the base spec deferred to "Phase 4 optimization"; it turns out to be disqualifying *now*, not later.

The fix to (2) forces an inversion of the default, which in turn makes the role-based naming from (1) natural to express.

## 2. The core decision: the brain is fast cloud, local is a specialist

**`main` (a fast cloud model) is Hermes' default brain.** The local 24 GB model demotes from "default" to a **private/bulk specialist** that the brain delegates to only when its specific virtues — privacy and €0 — are worth eating the latency.

This trades the base spec's "private + free **by default**" for "**fast by default, private on demand**." It is a conscious trade against a stated project goal (see §6, success-criteria changes), accepted because an unusable default brain defeats the whole setup.

**Rejected alternatives:**
- *Small fast local brain* (a tiny model orchestrates, delegates heavy local work to the 24 GB model). Infeasible: base spec §3 — 32 GB unified memory fits only **one** resident ~24 GB model, and a 4B-class model tested inadequate for agentic/tool use.
- *Keep local as brain, fix latency in Phase 4 first.* Defers relief that is blocking work now; Phase 4 gains are unproven. Retained only as a future "reclaim the default" path if MLX/engine work makes local fast enough.

## 3. Route taxonomy (final)

Routes are named by **role** (function in the agent), not by backend.

| Route | Role / when chosen | Backend today |
|---|---|---|
| `main` | everyday default brain — orchestration + routine work | **GPT-5-mini** (cloud) |
| `private` | on-box work where privacy or €0 is worth the latency | local 24 GB Qwen (`qwen3.6:27b`, Q4_K_M) |
| `deep` | heavy research / strategic reasoning / large context / current info | GPT-5 (`openrouter/openai/gpt-5`) |
| `deep-fallback` | reliability backstop for `deep` | Gemini 3.1 Pro Preview (`openrouter/google/gemini-3.1-pro-preview`) |

**`local-think` is dropped.** Its old job — synthesizing conclusions from `deep`'s research — now belongs to `main`, which is fast and capable. If on-box reasoning is ever needed, add `private-think` later. YAGNI until then.

**Naming scheme chosen: function-based** (`main`/`private`/`deep`). `main` is fully backend-agnostic — if Phase 4 ever makes a local model fast enough to be the brain, the name still holds. `deep` was already an intent name (depth of reasoning) and is kept. `private` replaces `local`, naming the route for *why you'd choose it* rather than where it runs. *Rejected:* workload-based (`routine`/`research`) — `routine` historically meant the local workload in the base spec, mild legacy confusion; speed-based (`fast`/`deep`) — `fast` names a property, less stable than a role name if the backend's speed changes.

## 4. The `main` model: GPT-5-mini

Chosen from the current OpenRouter landscape (verified 2026-05-20). The brain is **input-dominated** — it re-sends the ~16K system prompt + working context every turn for modest output — so input price and tool-calling reliability dominate the choice; output price and huge context matter little (large jobs escalate to `deep`).

- **GPT-5-mini** (`openrouter/openai/gpt-5-mini`): **$0.25 / $2** per M (in/out), 400K context, 128K max output, **2 first-party providers (OpenAI + Azure)**.
- **Rationale:** cheapest input tier; the exact first-party dual-provider reliability the base spec demanded after the DeepSeek/Novita outage; same family as `deep` (GPT-5) for consistent tokenization/behavior across the `main`→`deep` escalation path and one fewer vendor; purpose-built low-latency "lighter reasoning" GPT-5 — the brain role.
- **Cost sanity:** a typical turn (~20K in / 1K out) ≈ **$0.007**; ~200 turns/day ≈ **~€42/mo**, inside the `main` budget slice (§5).
- **Empirical fallback:** **Claude Haiku 4.5** (`$1 / $5`, 200K ctx, 3 providers, strongest agentic/tool reputation) if real-inbox testing shows GPT-5-mini's tool-calling is too weak. Start on GPT-5-mini; switch only on evidence.
- *Rejected for `main`:* Gemini 3 Flash (Preview — stability risk for an every-turn role); Gemini 3.1 Flash Lite (cheap + 1M context but less-proven agentic tool-calling); Gemini 3.5 Flash (released 2026-05-19; $1.50/$9 — too expensive for an every-turn brain).

## 5. Routing & escalation behavior

- **Default = `main`.** Fast, cheap, every turn. (Replaces base spec §5 "Default = local.")
- **Triage advisor is now three-way:** stay on `main` / route to `private` / escalate to `deep`. Rubric: `deep` for heavy strategic/market reasoning, large context, or current-info; `private` for privacy-sensitive or zero-cost bulk work where slowness is tolerable; otherwise `main`. Still **recommend-only** (the user confirms); never switches models itself.
- **The advisor's judge call moves from `local` to `main`.** Running the judge on the slow local model would cost ~2 min per recommendation — self-defeating. On `main` it is fast and costs a fraction of a cent. (Reverses the base spec's "judge runs free on local" choice: speed beats the trivial cost.)
- **Context overflow = advisor picks.** When a prompt exceeds the local model's context, the advisor's three-way judgment decides the target rather than the base spec's hardwired auto-fallback to cloud.
- **Email-triage privacy pin.** Email triage stays **explicitly pinned to `private`**, not `main`. The brain going cloud weakens the base spec's "email triage runs locally, private" goal; pinning the one genuinely sensitive workflow to `private` preserves it while general orchestration enjoys cloud speed.

## 6. Cost control (reworks base spec §6)

The default route now **costs money every turn**, so the base spec's "local is free, only cloud is capped" model no longer holds.

- **At budget cap, `main` degrades to `private`** (the slow local brain) rather than dying. This keeps the agent alive *and* preserves the hard €100 ceiling. **Distinct** from the rule that `deep` *tasks* never silently downgrade to local: degrading the *brain itself* to slow-but-working local is acceptable; silently answering a *deep research task* on local is not.
- **Three-way budget split** in **USD** (LiteLLM `max_budget` is USD; the hard ceiling is **$100 ≈ €92**), re-summing to exactly $100: starting guess **`main` $50 + `deep` $35 + `deep-fallback` $15**, each as a per-model `max_budget` + `budget_duration` in `litellm_params` (never the global `litellm_settings.max_budget`, which would block the free `private` route too — base spec §6 lesson). Per-request `max_tokens` on each cloud model as before. Tune the split against real usage. *(The base spec's split was `deep` $75 + `deep-fallback` $25; this redesign carves the `main` slice out of `deep`'s.)*

## 7. Migration blast radius

One coordinated rename + behavior change across:

- `mac/litellm-config.yaml` — model_names (`local`→`main`, drop `local-think`, add `main`/GPT-5-mini), the `context_window_fallbacks`/`fallbacks` keys, the three-way budget split, degrade-to-`private` fallback.
- Hermes `~/.hermes/config.yaml` — `default` (now `main`), `model_aliases`, the `/model …` switch names; the email-triage workflow pinned to `private`.
- `triage_advisor.py` — `JUDGE_MODEL` (now `main`), three-way rubric wording (`main`/`private`/`deep`), the `/model` hints in `format_output`.
- `SKILL.md` (triage advisor) — three-way rubric + names.
- `runbook.md`, `README.md` — operational + overview docs.
- Base spec `2026-05-20-hybrid-ai-routing-design.md` — reconcile §5/§6/§8/§13 to point at this redesign.

## 8. Success-criteria changes (reworks base spec §13)

- **Changed:** "Email triage runs locally, free" → **email triage runs on `private` (local, free, private) by explicit pin**; general agent orchestration runs on `main` (cloud, fast, cheap).
- **Unchanged:** workstation GPU stays free; heavy research reachable via an explicit route with visible model/cost; **monthly cloud spend cannot exceed €100** (now via the three-way split + degrade-to-`private`); every significant decision captured in `docs/journal/`.
- **New:** the default agent experience is **fast** (no 2-minute floor on routine turns).

## 9. Open items to verify during implementation

- **GPT-5-mini tool-calling quality on the real inbox.** If weak, switch `main` to Claude Haiku 4.5 (documented fallback). Decide empirically.
- **Budget split tuning.** The €50/€35/€15 split is a starting guess; adjust once the dashboard shows real `main` consumption.
- **`/model` UX for the demoted `private` route.** Confirm Hermes' `/model private` switch and the email-triage pin both reach the local route as expected.
- **Journal entry.** This redesign (the brain inversion + rename) is a significant decision; capture it in `docs/journal/` during implementation per `CLAUDE.md`, with the why + rejected alternatives (mirrored from §2/§3/§4 here).
