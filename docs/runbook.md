# Runbook — Phase 1 Routing

How to operate the hybrid gateway. See the [design spec](superpowers/specs/2026-05-20-hybrid-ai-routing-design.md) for the why.

## Hosts

- **Mac** (`10.63.0.32`): Ollama + LiteLLM gateway (Docker Desktop). Headless, no-sleep (`pmset`).
- **Arch**: Hermes Agent.
- **NAS** (later): Langfuse observability.

## Model routes (through the gateway)

| Route | Backend | Use |
|---|---|---|
| `main` | OpenRouter `openai/gpt-5-mini` (cloud) | **Default brain.** Orchestration + routine: triage, drafting, summarizing, route decisions. Fast, every turn. |
| `private` | qwen3.6:27b on Ollama, thinking **off** | On-device work where privacy or €0 is worth the latency (slow). Also the email-triage pin. |
| `deep` | OpenRouter `openai/gpt-5` | Heavy research / strategic reasoning / large context. |
| `deep-fallback` | OpenRouter `google/gemini-3.1-pro-preview` | Reliability backstop; used only if `deep` errors. |

- **At its budget cap, `main` degrades to `private`** (slow local brain) via LiteLLM `fallbacks` — the agent stays alive and the hard cap holds.
- **No automatic context fallback:** the triage advisor decides where an oversized prompt goes (it is not hardwired).
- `keep_alive: -1` keeps the local model resident (~17 GB always in memory; warm `private` responses).

## Start / stop the gateway (on the Mac, in `~/AISetup/mac`)

```bash
docker compose --env-file .env up -d        # start
docker compose --env-file .env down         # stop
docker compose --env-file .env restart litellm   # reload after editing litellm-config.yaml
docker compose --env-file .env logs -f litellm    # logs
```

> **Gotcha:** `litellm-config.yaml` is a *mounted file*. After editing it, `up -d` does **nothing** (compose spec unchanged) — you must `restart litellm` to reload it.

## Auto-start across reboots

- compose `restart: always` + Docker Desktop set to start at login + Mac auto-login.
- Ollama: launch on login. `pmset -a sleep 0 disablesleep 1` keeps the Mac awake.

## Verify health

```bash
curl http://10.63.0.32:4000/health/liveliness                       # -> "I'm alive!"
curl http://10.63.0.32:4000/v1/models -H "Authorization: Bearer $KEY"  # -> main, private, deep, deep-fallback
```
Dashboard: `http://10.63.0.32:4000/ui` (login `UI_USERNAME` / `UI_PASSWORD`).

## Hermes (Arch)

- `~/.hermes/config.yaml`: `model.provider: custom`, `base_url: http://10.63.0.32:4000/v1`, `default: main`, `api_key: <litellm master key literal>`.
- Switch model in a session: `/model deep` (heavy research), `/model private` (on-device/private). `main` is the default — no switch needed to return to it.
- Pin email triage to `private` so sensitive inbox content stays on-device even though the brain is cloud.
- Config backups: `~/.hermes/config.yaml.bak-*`.

## Prefix-cache hygiene (keep `private` fast)

- The local model has **one KV slot** (`OLLAMA_NUM_PARALLEL=1`). Any call that lands on it with a different prompt **evicts** the cached agent prefix, forcing the next agent turn to re-ingest the full ~16k context cold (~2 min). The big lever for `private` speed is keeping that slot holding the agent's stable prefix.
- Therefore Hermes' text **auxiliary tasks** must never land on the Mac's `private` slot during a conversation. As of 2026-05-21 they run on **dedicated Arch hardware** instead (design: [`docs/superpowers/specs/2026-05-21-local-aux-models-design.md`](superpowers/specs/2026-05-21-local-aux-models-design.md)). In `~/.hermes/config.yaml` under `auxiliary:`, all set `provider: custom`, `base_url: <gateway>/v1`, `api_key: <master key>`, and route by `model`:
  - `title_generation`, `profile_describer`, `triage_specifier` → **`model: aux-local`** → gateway → local **`qwen3:4b-instruct-2507`** on the Arch **7900 XTX** (on-device, €0). Verified: the 4B produces valid, quality `triage_specifier` specs at ~74–125 tok/s. These are short, single-shot tasks — they don't summarise a whole conversation, so the summary-model context rule below doesn't apply.
  - `compression` → **`model: main`** (cloud). The summary model must have a context window **≥ the main agent model's**, and Hermes enforces a **64k hard floor** on it — else the middle turns are dropped *without* a summary (silent context loss, the top cause of degraded compaction). The 4B at 32k failed both, so compression routes to `main` (GPT-5-mini, 400k). Privacy tradeoff accepted: a compaction sends the conversation middle to the cloud brain, same as any `main` turn.
  - `curator` → **`model: private`** → Mac `qwen3.6:27b` (rare/weekly, idle-triggered; capable + on-device). Its timeout is raised to **1800 s** for the ~11 tok/s agentic loop. The cache-bust is harmless because it only runs when idle (cost: one ~84 s cold re-ingest on your next turn).
  - **Do not let these default back to `provider: auto`.** Everything still flows through LiteLLM for observability.
- **`aux-local` gateway model** (`mac/litellm-config.yaml`): `ollama_chat/qwen3:4b-instruct-2507-q4_K_M` at `http://10.63.0.29:11434` (Arch LAN IP), `input/output_cost_per_token: 0` (logged, never counts against the €100 cap), `keep_alive: 10m`. **Requires Arch Ollama to listen on the LAN:** systemd drop-in `Environment="OLLAMA_HOST=0.0.0.0:11434"`, firewall-scoped to the Mac (`10.63.0.32`). If `aux-local` calls return `APIConnectionError ... 10.63.0.29:11434`, the Arch bind/firewall is the cause.
- `keep_alive: -1` (gateway model block) keeps the 18k prefix resident across turns/sessions so subsequent turns stay cache-warm (~8–16 s).
- **Diagnosing cache busts:** capture real requests with a manual logging server — the macOS Ollama app ignores `launchctl setenv`, so quit it and run `OLLAMA_DEBUG_LOG_REQUESTS=true OLLAMA_KEEP_ALIVE=-1 /Applications/Ollama.app/Contents/Resources/ollama serve`. Bodies land in a temp `ollama-request-logs-*` dir as JSON; diff consecutive agent turns' `messages[0]` and watch for small auxiliary prompts interleaved between them. Restore the app with `open -a Ollama` when done.
- **Cold-ingest / KV tuning (WS2) — measured, not adopted:** `num_batch` (512→2048) is flat (~140 tok/s) and `OLLAMA_KV_CACHE_TYPE=q8_0` doesn't speed ingest or generation; both ingest and the ~11 tok/s generation are memory-bandwidth bound on the M2 Max. `q8_0` only *frees ~2.6 GiB VRAM* (24.4→21.8 GiB) — useful headroom for a future draft model, but the app ignores the env var, so persisting it requires running Ollama as a managed `ollama serve` (LaunchAgent). Deferred until/unless WS3 (speculative decoding) is adopted. Don't reach for these as speed fixes. See `mac/bench/results.md` → "After WS2".

## Context-length declarations (avoid silent truncation)

- **The trap:** Hermes reads each model's context window from the endpoint's `/v1/models`, but LiteLLM's `/v1/models` **omits context length**, and the gateway aliases (`main`/`private`/`deep`/`aux-local`) aren't in models.dev. So Hermes probes-down every alias to its **256k default** and budgets prompts against it. For local models that actually load far smaller (e.g. `private`/`qwen3.6:27b` was at 32k), Ollama then **silently truncates** — lost context with no error.
- **The fix lives in Hermes, not LiteLLM.** Declare the real window per model in `~/.hermes/config.yaml` under the **gateway** `custom_providers` entry as a `models:` map (matched by `base_url`, so all four aliases — including the entry-less `aux-local` — share one entry):
  ```yaml
  models:
    main:      {context_length: 400000}   # GPT-5-mini (OpenRouter)
    private:   {context_length: 65536}    # qwen3.6:27b, served at num_ctx 65536
    deep:      {context_length: 400000}   # GPT-5 (OpenRouter)
    aux-local: {context_length: 32768}    # 4B aux tasks
  ```
  This override is checked **before** the probe/cache (`get_model_context_length` step 0b), so it always wins. After changing it, blank `~/.hermes/context_length_cache.yaml` (`context_lengths: {}`) to drop stale probed values.
- **`private` must serve what it declares.** Declaring 65536 is only safe because the Mac actually loads at 64k — set via `num_ctx: 65536` in the `private` block of `mac/litellm-config.yaml`. Measured footprint: **26 GB / 100% GPU** on the 32 GB Mac with the default f16 KV cache (Qwen3's GQA keeps the KV small), ~6 GB left for the OS — no `q8_0` KV change or `iogpu.wired_limit` bump needed. Verify after a `private` request with `ollama ps` on the Mac (CONTEXT column should read `65536`). 64k is also Hermes' **minimum** for any main-agent model, which `private` is (it's the `main` budget-cap fallback).

## Cost control

- Cloud models: `main` = GPT-5-mini (`openrouter/openai/gpt-5-mini`, the default brain), `deep` = GPT-5 (`openrouter/openai/gpt-5`), `deep-fallback` = Gemini 3.1 Pro Preview (used only if `deep` errors).
- Hard cap on **cloud only**, split so the total stays ≤100 USD (~€92): `main` `max_budget: 50` + `deep` `max_budget: 35` + `deep-fallback` `max_budget: 15` / `budget_duration: 30d` in `litellm-config.yaml`. (`private` is local and intentionally uncapped, so it always works.)
- At cap: the capped cloud model is blocked with a `budget_exceeded` (429) error. **`main` then degrades to `private`** (slow but free) so the agent keeps working; `private` itself has no budget.

## Secrets

- Live ONLY in `~/AISetup/mac/.env` on the Mac (gitignored). Template: `mac/.env.example`.
- Hermes master key: literal in `~/.hermes/config.yaml` (mode 600, not in git).
- Rotate: regenerate `.env` values → `docker compose --env-file .env down -v && up -d` → update `api_key` in `~/.hermes/config.yaml`.

## Common gotchas

- **Config edit ignored** → `restart litellm` (mounted file).
- **SSH terminal garbled** (backspace wrong) → connect with `TERM=xterm-256color ssh 10.63.0.32`.
- **First call ~15 s** → cold model load; `keep_alive: -1` keeps it warm afterward.
- **401 from gateway** → Hermes `api_key` must be the literal master key (`key_env` is not honored on the main `model:` block).
- **`deep` → 403 `NOT_ENOUGH_BALANCE`** → it's an upstream *provider* error (OpenRouter's own out-of-credit is 402), **not** your key/balance. Isolate with a direct OpenRouter curl forcing `provider: {"order":["<provider>"],"allow_fallbacks":false}`. Fix is provider routing (`ignore`/`only`) or picking a model with reliable providers — this is why `deep` is GPT-5 (first-party OpenAI+Azure) with a Gemini fallback.

## Triage advisor (Phase 2)
- Skill source: `hermes/skills/triage-advisor/` (repo); deployed to `~/.hermes/skills/triage-advisor/`.
- Manual use: ask Hermes to use the triage advisor, or run
  `python3 ~/.hermes/skills/triage-advisor/triage_advisor.py "<task>"`.
- Reads gateway creds from `~/.hermes/config.yaml` (`model.base_url` + `model.api_key`) automatically; override with `LITELLM_BASE_URL` + `LITELLM_MASTER_KEY` env vars if needed.
- Judge runs on the `main` route (fast cloud) — running it on the slow `private` model would cost ~2 min per recommendation. Costs a fraction of a cent. Recommend-only — it never switches models.
- Recommends one of `main` (stay, the default), `private` (on-device), or `deep` (escalate).
- Recommendations are logged to `~/.hermes/triage-advisor.jsonl` (review before considering auto-routing).
