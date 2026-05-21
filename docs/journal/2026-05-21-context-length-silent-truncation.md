# Context-length detection: a "cosmetic" warning that was eating context

**Date:** 2026-05-21

## What happened

The local-aux work shipped with a log line I'd written off as cosmetic:
*"Could not detect context length for model 'aux-local' … defaulting to 256,000."*
Pulling on it revealed Hermes was mis-sizing **every** model's context window — and for
the local models that meant **silently truncating prompts**.

## Why it happens (the chain)

Hermes resolves a model's context window from the endpoint's `/v1/models`. But:

- LiteLLM's `/v1/models` **omits** `context_length` (it's an OpenAI-compat surface, and the
  OpenAI spec has no such field).
- Our gateway aliases (`main`/`private`/`deep`/`aux-local`) aren't resolvable via models.dev.

So Hermes falls through to its **256k default** for all of them and budgets prompts against
256k. For `private` (`qwen3.6:27b`) — actually loaded by Ollama at **32k** — Ollama then
quietly drops everything past 32k. No error, just lost context mid-conversation. The cache
(`~/.hermes/context_length_cache.yaml`) had even persisted the phantom `262144` values.

Two more constraints fell out of reading the Hermes source/docs:

- A **main-agent model needs ≥64k** of context. `private` is the `main` budget-cap fallback,
  so it counts — and at 32k it was under the floor.
- The **compression (summary) model must have a window ≥ the main model's**, and there's a
  **hard 64k floor** on it in code (`conversation_compression.py`). Below either, the middle
  turns are dropped *without* a summary — the textbook cause of degraded compaction. We had
  `compression` on the 4B at 32k, failing both.

## Decisions, and what was rejected

- **Declare context lengths in Hermes, not LiteLLM.** Tempting to "fix it at the gateway,"
  but LiteLLM's `/v1/models` structurally can't carry per-model context — so the override
  belongs in Hermes' `custom_providers` (`models.<name>.context_length`), which it checks
  *before* any probe or cache (resolution step 0b). Verified by calling Hermes' own resolver
  against the live config rather than trusting the YAML.
- **Real windows, not guesses.** `main`/`deep` set to the true GPT-5-mini/GPT-5 window
  (**400k**) read from the OpenRouter API, not eyeballed. `private` = 65536, `aux-local` =
  32768.
- **Bump `private` to 64k — but measure first.** A declaration of 65536 is a *lie* unless the
  Mac actually serves 64k (else we'd just recreate the truncation bug, budgeting 64k against a
  32k load). The worry was VRAM on a 32 GB Mac. Measured: 64k loads at **26 GB, 100% GPU** with
  the **default f16 KV cache** — only +2 GB over 32k, because Qwen3's GQA keeps the KV cache
  small. So the planned `q8_0` KV-cache workaround and an `iogpu.wired_limit` bump (would have
  needed sudo on the Mac) were **both unnecessary**. Set via `num_ctx: 65536` in the litellm
  `private` block; verified live with `ollama ps` (CONTEXT 65536, Forever).
- **Re-route `compression` to `main` (cloud), not "make the 4B bigger".** Bumping the Arch 4B
  to 64k wouldn't satisfy "≥ main's window" when chatting on cloud `main` (400k). The correct
  invariant is summary-ctx ≥ main-ctx, which only a cloud route guarantees. The privacy cost (a
  compaction ships the conversation middle to the cloud brain) is the same exposure as any
  `main` turn, already accepted in the runbook. Kept `title_generation` / `profile_describer` /
  `triage_specifier` local — they're short single-shot tasks, not whole-conversation summaries,
  so the rule doesn't bite.

## Coverage caveat (verified live)

The override only fires on call sites that thread `custom_providers` into
`get_model_context_length` (resolution step 0b). The **budgeting** paths do — startup
compressor init (`agent_init.py` resolves the per-model override and feeds it in),
`/model` switch, and the gateway compaction-threshold recompute (`run.py:8113` reads the
`models:` map directly). Confirmed empirically: once a request starts, the progress bar
shows the correct window (65536 on `private`), i.e. the compaction budget is right.

A few **secondary/display** paths don't thread it and have no top-level `model.context_length`
to fall back on, so they still log `… defaulting to 256,000 (probe-down)` — notably the
`@`-mention budgeter (`run.py:7723`) and the `/model`-switch context display
(`model_switch.py`). Symptom: the context shown at the *moment of switching models* is briefly
wrong, then self-corrects on the next request. Cosmetic — left as-is rather than patching
installed Hermes source. (A clean fix would thread `custom_providers` into those two sites;
upstreamable.)

## Lesson

A "harmless" default-value warning was a proxy for real, invisible data loss. The empirical
check (load it, read `ollama ps`) also *saved* work — the feared VRAM ceiling wasn't real, so
two mitigations got dropped instead of built. And the inverse: a scary-looking residual
"defaulting to 256,000" log turned out to be cosmetic once the *budgeting* path was traced and
the live progress bar confirmed correct — read the path, don't fear the log line.
