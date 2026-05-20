# Runbook — Phase 1 Routing

How to operate the hybrid gateway. See the [design spec](superpowers/specs/2026-05-20-hybrid-ai-routing-design.md) for the why.

## Hosts

- **Mac** (`10.63.0.32`): Ollama + LiteLLM gateway (Docker Desktop). Headless, no-sleep (`pmset`).
- **Arch**: Hermes Agent.
- **NAS** (later): Langfuse observability.

## Model routes (through the gateway)

| Route | Backend | Use |
|---|---|---|
| `local` | qwen3.6:27b, thinking **off** (`reasoning_effort: none`) | Fast routine: triage, sorting, route decisions |
| `local-think` | qwen3.6:27b, thinking **on** | Local reasoning/synthesis (e.g. conclusions on `deep`'s research) |
| `deep` | OpenRouter `deepseek/deepseek-r1` | Heavy research / large context |

- Oversized prompts auto-fall back `local`/`local-think` → `deep` (`context_window_fallbacks`).
- `keep_alive: -1` keeps the model resident (~17 GB always in memory; warm responses).

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
curl http://10.63.0.32:4000/v1/models -H "Authorization: Bearer $KEY"  # -> local, local-think, deep
```
Dashboard: `http://10.63.0.32:4000/ui` (login `UI_USERNAME` / `UI_PASSWORD`).

## Hermes (Arch)

- `~/.hermes/config.yaml`: `model.provider: custom`, `base_url: http://10.63.0.32:4000/v1`, `default: local`, `api_key: <litellm master key literal>`.
- Switch model in a session: `/model deep`, `/model local-think`, `/model local`.
- Config backups: `~/.hermes/config.yaml.bak-*`.

## Cost control

- Hard cap on **cloud only**: `deep` model `max_budget: 100` (USD) / `budget_duration: 30d` in `litellm-config.yaml`.
- At cap: `deep` is blocked with a `budget_exceeded` (429) error; `local`/`local-think` keep working.

## Secrets

- Live ONLY in `~/AISetup/mac/.env` on the Mac (gitignored). Template: `mac/.env.example`.
- Hermes master key: literal in `~/.hermes/config.yaml` (mode 600, not in git).
- Rotate: regenerate `.env` values → `docker compose --env-file .env down -v && up -d` → update `api_key` in `~/.hermes/config.yaml`.

## Common gotchas

- **Config edit ignored** → `restart litellm` (mounted file).
- **SSH terminal garbled** (backspace wrong) → connect with `TERM=xterm-256color ssh 10.63.0.32`.
- **First call ~15 s** → cold model load; `keep_alive: -1` keeps it warm afterward.
- **401 from gateway** → Hermes `api_key` must be the literal master key (`key_env` is not honored on the main `model:` block).

## Triage advisor (Phase 2)
- Skill source: `hermes/skills/triage-advisor/` (repo); deployed to `~/.hermes/skills/triage-advisor/`.
- Manual use: ask Hermes to use the triage advisor, or run
  `python3 ~/.hermes/skills/triage-advisor/triage_advisor.py "<task>"`.
- Reads gateway creds from `~/.hermes/config.yaml` (`model.base_url` + `model.api_key`) automatically; override with `LITELLM_BASE_URL` + `LITELLM_MASTER_KEY` env vars if needed.
- Judge runs on the `local` (no-think) route. Recommend-only — it never switches models.
- Recommendations are logged to `~/.hermes/triage-advisor.jsonl` (review before considering auto-routing).
- To deepen judgment: set `JUDGE_MODEL = "local-think"` in `triage_advisor.py` and redeploy.
