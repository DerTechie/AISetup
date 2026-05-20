# 2026-05-20 — Choosing the `deep` cloud model: GPT-5 + Gemini fallback

## Context / where this started

`deep` was provisionally `deepseek/deepseek-r1` (an open item in the spec: "verify the OpenRouter model"). On first real use through Hermes, `/model deep` failed with **HTTP 403 `NOT_ENOUGH_BALANCE`** from provider **Novita** — and kept failing across ~5 retries over many minutes, so not a transient blip. This forced the decision early.

## The debugging (how we isolated it)

The instinct was "top up OpenRouter" — but the account screenshot showed **$9.98**, so that was wrong. Applied systematic debugging: the error metadata said the failure came from a *specific upstream provider* (Novita, `is_byok:false`), and OpenRouter's *own* insufficient-credits signal is a **402**, not a 403 — so this was a provider-relayed error, not an account gate. To prove it, we forced provider routing with two direct OpenRouter curls (bypassing the gateway entirely):

- **Force Azure** → success, and `"cost":0.00030888, "is_byok":false` — **OpenRouter charged the account fine.** So balance/key/gateway/setup are all healthy.
- **Force Novita** → reproduced the exact `NOT_ENOUGH_BALANCE` 403 in isolation.

**Verdict: a Novita-side outage**, not us. Clean isolation by switching one variable (the provider). This is the lesson in miniature: don't guess ("did something break?"), isolate at the component boundary.

## What we decided and why

- **Don't just patch around Novita — pick a `deep` model that no single provider can take down.** DeepSeek-R1 has only **two** providers on OpenRouter; the cheap one (Novita) was broken, and the reliable one (Azure) caps output at **4,096 tokens** — too tight for a reasoning model (in the test, R1 spent the whole budget on reasoning and returned *no* answer). Thin, fragile, ill-fitting. **Rejected.**
- **`deep` = GPT-5 (`openrouter/openai/gpt-5`).** Frontier reasoning, **dual first-party providers (OpenAI + Azure, ~99.8–99.9%)** so it's redundant by itself, 400K context, 128K max output, and — surprisingly — the *cheapest* frontier tier (~$1.25/$10 per M ≈ **$0.08/research-call**). The reasoning for going frontier at all: `deep` is rare, explicitly-invoked, and hard-capped, so quality (the entire reason to leave local) costs little in absolute terms and can't run away. First-party serving also structurally avoids the flaky cheap third-parties that just bit us.
- **`deep-fallback` = Gemini 3.1 Pro Preview (`openrouter/google/gemini-3.1-pro-preview`).** Wired via LiteLLM `fallbacks: [{"deep": ["deep-fallback"]}]`. A *different vendor* (Google, 1M context) so even a total OpenAI+Azure GPT-5 outage is covered. This is the actual "solid state": a single first-party model is reliable but still one vendor; cross-vendor fallback is real redundancy.
- **Split the budget to keep the €100 ceiling literally true:** `deep` `max_budget: 75` + `deep-fallback` `max_budget: 25`. Two independent caps that **sum to ≤100 USD (~€92)**, so total cloud spend still can't exceed the ceiling even with the fallback engaged. €75 is ample for light deep use.

## What we rejected / considered

- **Staying on DeepSeek-R1 + pinning Azure** (`ignore: ["Novita"]`): unblocks, but Azure's 4,096 output cap cripples a reasoning model, and it's still a 2-provider model. A stopgap, not a solid state.
- **Open model for `deep` generally:** cheaper, but for the *rare, hard, already-capped* deep query, frontier quality is worth the few extra dollars/month — a mediocre cloud answer defeats the purpose of escalating.
- **No fallback (single model + cap):** simpler, but leaves a vendor outage = no deep. The whole trigger for this work was a provider outage, so redundancy won.

## State / pending

- Config written in `mac/litellm-config.yaml` (slugs + context limits verified against OpenRouter's API). **Not yet deployed/tested live** — needs `docker compose --env-file .env restart litellm` on the Mac, then a `/model deep` test call to confirm GPT-5 responds and the dashboard logs it.
- Open follow-up (with GDPR): no-log / data-collection provider routing for cloud.
