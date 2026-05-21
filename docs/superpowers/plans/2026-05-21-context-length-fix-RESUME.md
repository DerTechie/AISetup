# RESUME — context-length detection fix + Mac 64k bump + compression re-route

- **Date:** 2026-05-21
- **Status:** ✅ DONE & verified (2026-05-21, commit `bb09389`). All four moves shipped — see
  spec §10 and journal `2026-05-21-context-length-silent-truncation.md`. VRAM check passed at
  f16 (26 GB/100% GPU), so the q8_0 KV fallback was *not* needed. Kept for the build story.
- **Owner:** DerTechie

## Where we are

The **local-aux-models work is DONE and verified** (spec:
[`../specs/2026-05-20...`](../specs/2026-05-21-local-aux-models-design.md), all gates green;
`main` pushed at commit `4ca619d`). Hermes' aux tasks now run on the Arch 7900 XTX 4B via the
gateway at €0; curator → Mac 27B; firewall persisted (`ollama-guard.service` enabled). Verified
live: a real `title_generation` turn hit `aux-local`.

This RESUME is a **follow-on task** that came out of a probe-warning: Hermes mis-detects every
model's context window. It is more than cosmetic.

## The problem (4 findings)

1. **All models probe-down to 256k.** Hermes reads context length from `/v1/models`, but
   LiteLLM's `/v1/models` omits it (Hermes code: *"OpenAI-compat /v1/models correctly omits
   context_length"*), and the gateway aliases (`main`/`private`/`deep`/`aux-local`) aren't
   resolvable via models.dev. So every model defaults to **262144**.
2. **Real silent-truncation bug (local).** `~/.hermes/context_length_cache.yaml` shows
   `qwen3.6:27b` and the 4B cached at **262144**, but Ollama loads them at **32768**. Hermes
   budgets against 256k → Ollama silently truncates at 32k → lost context on `private` today.
3. **Mac model below Hermes' minimum.** Hermes docs: *"requires ≥ 64,000 tokens of context."*
   The Mac `qwen3.6:27b` is at **32k**. Bump to **65536**.
4. **Compression routing unsafe as shipped.** Hermes docs: *"the compression (summary) model
   must have a context window ≥ the main agent model's, else it silently drops the middle turns
   — the most common cause of degraded compaction quality."* We routed `compression` → the 4B
   (32k); when chatting on cloud `main` (large window) it overflows → silent context loss.

## Decisions already approved by DerTechie

- **(a) Re-route `compression` → `main` (cloud).** Correct (≥ main always), fast (snappy is the
  metric), privacy tradeoff already accepted in the runbook. Keep `title_generation` /
  `profile_describer` / `triage_specifier` on the local 4B (they don't summarize the whole
  middle, so the constraint doesn't apply).
- **(b) Bump Mac `private` to 64k — pending a VRAM check** (measure before committing).
- **Fix lives in Hermes config, NOT LiteLLM** (see Finding 1).

## Plan (execute in order)

1. **Declare `context_length` per model** in `~/.hermes/config.yaml` under `custom_providers`
   (entries already exist for main/private/deep + a "Local direct" qwen3.6:27b@127.0.0.1).
   - Verify the exact override schema first: `hermes_cli/config.py:3173`
     `get_custom_provider_context_length` reads `custom_providers[i].models.<model>.context_length`.
     Current entries use flat `model: <name>` — confirm the right YAML shape (a `models:` map vs
     a field on the entry) before editing.
   - Values: `aux-local` **32768**, `private` **65536** (after bump), `deep-fallback` **1048576**
     (1M, per config comment). `main` (GPT-5-mini) and `deep` (GPT-5): **look up the true windows
     via the OpenRouter API** — `curl https://openrouter.ai/api/v1/models` (OPENROUTER_API_KEY is
     in the Mac LiteLLM env), find `openai/gpt-5-mini` and `openai/gpt-5`. **Do not guess.**
   - Check whether `aux-local` needs its own `custom_providers` entry for the override to apply
     (it's only in the `auxiliary:` block today).
   - After setting overrides, clear/invalidate the stale `~/.hermes/context_length_cache.yaml`
     (has the 262144 entries) so Hermes re-resolves.
2. **Bump Mac `private` (qwen3.6:27b) to `num_ctx: 65536` — measure VRAM FIRST.** Currently 24 GB
   resident at 32k on a 32 GB Mac; 64k ~doubles the KV cache. Try **`q8_0` KV cache** (the WS2
   work found it frees ~2.6 GB with no speed hit). Confirm where num_ctx is actually set so the
   Mac loads at 64k — check the LiteLLM `private` block (`mac/litellm-config.yaml`) vs Hermes'
   custom-provider `num_ctx` (docs note "Ollama num_ctx + think:false quirks" in
   `plugins/model-providers/custom/`). Verify with `ollama ps` (CONTEXT column) on the Mac.
3. **Re-route `compression` → `main`** in `~/.hermes/config.yaml` `auxiliary:` section (currently
   `model: aux-local`; change to `model: main`). Leave the other three aux tasks on `aux-local`.
4. **Docs:** update runbook (aux routing table — compression now `main`; add the context_length
   notes), spec (compression re-route + why; context_length declarations + the silent-truncation
   reasoning). Write a journal entry on the context-length finding.
5. **Commit + push** (work on `main`, then `git push origin main`).

## Reference (env, files, gotchas)

- **Arch IP** 10.63.0.29; **Mac IP** 10.63.0.32; gateway key in config (`sk-59b325…`).
- **Hermes config** `~/.hermes/config.yaml` — `auxiliary:` ~L163, `custom_providers:` ~L555,
  `model:` block L1–6. Backup: `~/.hermes/config.yaml.bak-aux-2026-05-21`.
- **LiteLLM config** repo `mac/litellm-config.yaml` → deploy to Mac `~/AISetup/mac/` (scp),
  restart container: `ssh 10.63.0.32 'export PATH=/usr/local/bin:$PATH; cd ~/AISetup/mac &&
  docker compose restart litellm'`. Container `mac-litellm-1`; `docker` not on the
  non-interactive PATH (use full path or export it).
- **Hermes source** `~/.hermes/hermes-agent/`: `agent/model_metadata.py`, `hermes_cli/config.py`,
  `agent/conversation_compression.py`, `website/docs/developer-guide/context-compression-and-caching.md`,
  `website/docs/getting-started/quickstart.md` (the 64k minimum).
- **Routing evidence** `~/.hermes/logs/agent.log` — grep `agent.auxiliary_client` (shows which
  model each aux task used) and `agent.model_metadata` (probe results).
- **No passwordless sudo on Arch** — hand privileged steps to DerTechie to run via `!`.
- **SSH to Mac is passwordless** (`dertechie@Mikes-MacBook-Pro`).
