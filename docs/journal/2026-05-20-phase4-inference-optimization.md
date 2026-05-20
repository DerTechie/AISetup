# 2026-05-20 — Adding Phase 4: local inference optimization (Mac engine)

## Context / where this started

Local inference is slow. The initial-design journal already measured it: a trivial turn took **~2m17s on `local` vs 6s on `deep`**, traced mostly to prefill of Hermes' ~16K-token agentic system prompt on the 27B, plus thinking generation and cold model load. Cheap wins (`reasoning_effort: none`, `keep_alive: -1`) were already applied. The open lever I'd floated in an earlier session was switching the inference engine — **MLX instead of Ollama** — which is faster on Apple Silicon. This entry records the decision to make that a real, scoped phase.

## What we decided and why

- **Optimize last, not first — as Phase 4, after Langfuse.** The decision that drove everything: the **LiteLLM gateway isolates the inference engine** from everything downstream. Hermes, the triage advisor, and Langfuse all talk to the `local` route, never to Ollama directly. So swapping Ollama → MLX later is a change to *one route's* `api_base`/`model` — nothing else reworks. Three reasons that made "last" the right call: (1) we already have a usable baseline, so Phase 2's triage advisor can be designed conservatively without the optimization done; (2) an MLX migration can rabbit-hole on model-compat and server-ops, and a functionally complete, observable system is worth more than a fast-but-incomplete one; (3) Phase 3's Langfuse gives clean before/after latency + tok/s traces — so optimizing *after* it makes the win measurable and talk-worthy automatically.
- **Scope it to the Mac engine only.** Levers: MLX-vs-tuned-Ollama bake-off, quantization, KV-cache / context tuning, wired-memory limit, and (low-priority) speculative decoding. Decode-side levers rank low because **prefill is the dominant cost**, not decode.
- **Empirical and persisted.** One standard benchmark turn (real ~16K Hermes prompt + a routine prompt); measure TTFT (prefill) and decode tok/s, warm vs cold, think on/off, repeated. Baseline on Ollama first, then each candidate through the same harness *via the gateway*. Results → a dated journal table. This matches the project's incremental + empirical working style and feeds the talk.
- **MLX is the lead candidate, but it must clear two gates.** It must do reliable **tool/function calling** (Hermes is agentic — deal-breaker if not) and honor a **thinking on/off** toggle equivalent to `reasoning_effort: none` (the `local` vs `local-think` split depends on it). If MLX wins, migrate by repointing the two routes; keep Ollama as instant rollback until validated.

## What we rejected / deferred

- **Optimizing first (before triage advisor / Langfuse).** Tempting because the slowness is annoying, but it risks yak-shaving on perf before the system works end-to-end, and the gateway means we lose nothing by waiting.
- **A broad "end-to-end latency" scope** that also trims Hermes' 16K system prompt / toolsets. That's the *other* big prefill lever, but it's Hermes-side, not Mac-engine — kept as a separate, explicitly cross-referenced effort so it isn't lost.
- **Writing the Phase 4 implementation plan now.** Premature: it hinges on open items (MLX tool-calling and thinking-toggle parity) that can only be verified when we reach the phase. The plan gets written when Phases 2–3 are done.

## Open questions (carry into Phase 4)

- Does the MLX / LM Studio OpenAI-compatible server do reliable tool/function calling?
- Does it honor a thinking on/off toggle equivalent to `reasoning_effort: none` (or do we need two served instances / a chat-template flag)?
- MLX keep-warm + auto-start on headless login, matching Ollama's `keep_alive: -1` + launch-on-login.
- Does MLX's better memory efficiency free enough headroom (alongside the Docker stack) for a higher quant or larger context?
