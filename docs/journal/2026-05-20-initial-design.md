# 2026-05-20 — Initial design: hybrid local/cloud AI agent setup

## Context / where this started

I'd been asking Gemini how to optimize my local AI setup, which produced a `README.md` describing an ambitious hybrid architecture: Hermes Agent on my Arch workstation, a headless Mac M2 Max (32 GB) running Ollama + a custom FastAPI gateway that would *guess* whether each request goes local or to OpenRouter, with Microsoft Presidio masking everything for "DSGVO compliance".

Ready going in: SSH access to the Mac; Hermes already installed and running against a local Ollama (to be retired in favour of Ollama on the Mac).

This entry records what we kept, what we changed, and **why** — so it can be reconstructed for Obsidian and a talk.

## What we decided and why

- **Offload inference to the Mac, keep Hermes on Arch.** Frees the 7900 XTX, no more freezes. (Kept from the original plan — it's the sound core idea.)
- **One ~24 GB agentic model locally, not the README's two-model setup.** 32 GB unified memory only realistically holds one such model once macOS + KV cache are accounted for. A prior `qwen3:4b` test was "dumb as hell" — that's the floor, not a verdict on the approach. 14B is mediocre for agentic/tool use; the ~24 GB agentic Qwen tier is the realistic, capable ceiling here.
- **Routing decision can't live in Hermes.** Verified: Hermes runs **one model per profile**, fixed at startup, with **no native automatic difficulty-routing** (open feature request [#4461](https://github.com/NousResearch/hermes-agent/issues/4461), no implementation). So "Hermes automatically decides local vs cloud" — as I'd originally wanted — isn't possible today. The decision moves to the gateway + explicit choices.
- **LiteLLM instead of a hand-built FastAPI gateway.** LiteLLM is the de-facto open-source AI gateway: OpenAI-compatible, routes to Ollama *and* OpenRouter from one config, built-in budget caps + token caps + logging + a spend dashboard. Less custom code, more standard.
- **Routing = explicit + capability fallback, not text-guessing.** Default local; LiteLLM auto-escalates to cloud only when a prompt is too big to fit locally (a capability fact, reliable); heavy research via `/model deep` or a cloud-pinned subagent.
- **A triage advisor for "I can't always decide".** A Hermes skill where the **local** model judges "deep or local?" and recommends; I confirm. Starting in recommend/local-judge mode because deciding is itself a tiny, cheap task and I want to *observe* how good its judgment is before automating. Reversible.
- **Cost protection is a hard cap, not my willpower.** My real worry was that *I* would over-pick cloud and overspend. The fix isn't discipline — it's a LiteLLM **€100/month** hard budget cap (+ per-request token cap). At the cap, cloud is blocked with a clear error; routine continues local. This makes runaway spend structurally impossible regardless of how often I reach for cloud.
- **Observability via the OTel/Langfuse standard.** "See which model was chosen" is a runtime concern, explicitly **not** in Obsidian. Start with LiteLLM's built-in dashboard (`mac:4000/ui`, opened from Arch); upgrade to Langfuse on the **NAS** later (always-on Docker host, no contention with the Mac's model memory).
- **GDPR/Presidio deferred.** No customer / sensitive third-party data yet. Also noted: NER masking is best-effort and never a compliance guarantee — so it's "later, and treated as risk-reduction", not a checkbox.
- **All docs in English.**
- **Routing-first phasing.** The LiteLLM routing layer (local + cloud, caps, dashboard) is the first milestone — it's the interesting core of the project, not the email triage. Local-model inbox quality gets validated once routing is live, not as a gate before it.

## What we rejected

- README's two-model (router + worker) setup — memory.
- Custom FastAPI gateway — LiteLLM does it better and is standard.
- Gateway guessing difficulty from raw prompt text — unreliable; the exact cause of the "dumb local answer" failure I feared.
- Pure manual model selection with no cap — I'd over-bias to cloud and overspend.
- Presidio masking framed as "DSGVO compliance" — overconfident.

## Open questions (carry into implementation)

- Exact Ollama tag for the ~24 GB agentic Qwen.
- Which API Hermes currently uses, and the alias/subagent config for the `deep` route.
- Whether a Hermes skill can invoke a specific model for the triage advisor.
- OpenRouter model choice + real context limit + provider/no-log settings (Phase 6).
