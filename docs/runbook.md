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
