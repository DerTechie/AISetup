# Runbook — Phase 1 Routing

How to operate the hybrid gateway. See the [design spec](superpowers/specs/2026-05-20-hybrid-ai-routing-design.md) for the why.

## Hosts

- **NAS** (`10.63.0.2`): LiteLLM gateway + Postgres, as a Dockge compose stack on TrueNAS. Always-on services host (Langfuse observability later). Gateway at `http://10.63.0.2:4000`.
- **Mac** (`10.63.0.32`): Ollama only — the `private` route model. Headless, no-sleep (`pmset`). Listens on the LAN for the NAS gateway.
- **Arch** (`10.63.0.29`): Hermes Agent + the `aux-local` Ollama (4B on the 7900 XTX).

> **Gateway moved Mac → NAS (2026-05-22).** Why + the trade-offs: [`journal/2026-05-22-litellm-gateway-to-nas.md`](journal/2026-05-22-litellm-gateway-to-nas.md). The old Mac stack (`mac/`) is retained for rollback until the NAS gateway is verified live, then retired.

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

## Start / stop the gateway (on the NAS, via Dockge)

The stack lives in `nas/` (repo) → a Dockge stack dir on a NAS pool path
(the TrueNAS system dataset is read-only — use your apps/pool dataset). The
dir holds `docker-compose.yml`, `litellm-config.yaml`, and a filled `.env`.

- **Start / stop / restart:** Dockge UI buttons on the stack.
- **Logs:** Dockge stack log pane (or `docker logs -f <stack>-litellm-1` on the NAS shell).

> **Gotcha:** `litellm-config.yaml` is a *mounted file*. After editing it, a stack
> redeploy with an unchanged compose spec does **nothing** — you must **restart the
> `litellm` service** (Dockge restart, or `docker compose restart litellm`) to reload it.

## Auto-start across reboots

- compose `restart: always` → Docker (and Dockge stacks) come back with the NAS.
- **Mac:** Ollama launches on login; `pmset -a sleep 0 disablesleep 1` keeps it awake. The Mac no longer runs the gateway — only the model.

## Mac Ollama on the LAN (required by the move)

The gateway is off-box now, so the Mac's Ollama must accept connections from the
NAS. It was `127.0.0.1`-only (verified 2026-05-22: `lsof` showed `TCP 127.0.0.1:11434 (LISTEN)`).

The macOS Ollama **app ignores `OLLAMA_HOST`** and its "expose to network" toggle
is GUI-only (no console path). So on this **headless** Mac we replace the menubar
app's server with a managed `ollama serve` **LaunchAgent** — repo file
[`../mac/com.ollama.serve.plist`](../mac/com.ollama.serve.plist). Loaded into the
`gui/$UID` domain it runs inside the auto-login GUI session and **keeps Metal GPU
access** (a `LaunchDaemon` would not). Install:

```bash
cp mac/com.ollama.serve.plist ~/Library/LaunchAgents/
plutil -lint ~/Library/LaunchAgents/com.ollama.serve.plist        # -> OK
pkill -f "Ollama.app/Contents/MacOS/Ollama"                       # quit menubar app
pkill -f "Contents/Resources/ollama serve"                        # and its server
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ollama.serve.plist
```

Verify (done 2026-05-22): `lsof -nP -iTCP:11434 -sTCP:LISTEN` → `*:11434`;
`ollama ps` → `100% GPU`; from another LAN host `curl http://10.63.0.32:11434/api/tags`.

The agent sets `OLLAMA_NUM_PARALLEL=1` (the one-KV-slot prefix-cache rule) and
`OLLAMA_KEEP_ALIVE=-1` (keep `private` resident). It loads at the default 32k
until the gateway's first `private` call (which requests `num_ctx 65536`) reloads
it at 64k.

> **One manual step (GUI, not console-doable — TCC-blocked over SSH):** turn OFF
> Ollama in **System Settings → General → Login Items**, or on reboot the menubar
> app relaunches and fights the agent for `:11434`. Until then the agent is live
> but reboot persistence is not guaranteed.

**Fence it to the NAS (pf).** The API is unauthenticated, so `:11434` is scoped to
localhost + the NAS (`10.63.0.2`); other LAN sources are dropped. macOS uses `pf`,
not nftables — repo files [`../mac/pf-ollama-guard.conf`](../mac/pf-ollama-guard.conf)
(ruleset) + [`../mac/com.ollama.pfguard.plist`](../mac/com.ollama.pfguard.plist)
(boot-time `LaunchDaemon`). Install/verify in [`../mac/README.md`](../mac/README.md).
Quick check: from a non-allowed LAN host `:11434` times out; from the NAS the
`private` route still works.

## Verify health

```bash
curl http://10.63.0.2:4000/health/liveliness                       # -> "I'm alive!"
curl http://10.63.0.2:4000/v1/models -H "Authorization: Bearer $KEY"  # -> main, private, deep, deep-fallback
# End-to-end the local hops (these break first after the move):
#   private  -> Mac Ollama  (needs Mac LAN bind + pf allow 10.63.0.2)
#   aux-local-> Arch Ollama (needs ollama_guard allowing 10.63.0.2)
```
Dashboard: `http://10.63.0.2:4000/ui` (login `UI_USERNAME` / `UI_PASSWORD`).

## Hermes (Arch)

- `~/.hermes/config.yaml`: `model.provider: custom`, `base_url: http://10.63.0.2:4000/v1`, `default: main`, `api_key: <litellm master key literal>`. (Was `10.63.0.32` — repoint to the NAS after the move.)
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
- **`aux-local` gateway model** (`nas/litellm-config.yaml`): `ollama_chat/qwen3:4b-instruct-2507-q4_K_M` at `http://10.63.0.29:11434` (Arch LAN IP), `input/output_cost_per_token: 0` (logged, never counts against the €100 cap), `keep_alive: 10m`. **Requires Arch Ollama to listen on the LAN:** systemd drop-in `Environment="OLLAMA_HOST=0.0.0.0:11434"`, firewall-scoped to the **NAS** (`10.63.0.2`) — the gateway moved off the Mac, so the guard's allowed source changed (`arch/nftables-ollama-guard.nft`; re-install per `arch/README.md`). If `aux-local` calls return `APIConnectionError ... 10.63.0.29:11434`, the Arch bind/firewall is the cause.
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

- Live ONLY in the `.env` inside the **Dockge stack dir on the NAS** (gitignored). Template: `nas/.env.example`.
- Hermes master key: literal in `~/.hermes/config.yaml` (mode 600, not in git).
- Rotate: regenerate `.env` values → redeploy the stack in Dockge (`down -v` then up, which also resets budget counters) → update `api_key` in `~/.hermes/config.yaml`.

## Common gotchas

- **Config edit ignored** → `restart litellm` (mounted file).
- **`private` → `APIConnectionError ... host.docker.internal`** → leftover from the Mac-hosted gateway. On the NAS there is no `host.docker.internal`; `private`'s `api_base` must be the Mac LAN IP `http://10.63.0.32:11434` (already set in `nas/litellm-config.yaml`).
- **`private` → connection refused to `10.63.0.32:11434`** → Mac Ollama not on the LAN (see § "Mac Ollama on the LAN") or pf is blocking the NAS.
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

## Refreshing model prices / scores

From the repo root:

```bash
export ARTIFICIALANALYSIS_API_KEY=<key>     # for scores only
python3 -m pricing.pricetrack all           # refresh prices + scores + join
git add pricing/data && git commit -m "data(pricing): refresh snapshot $(date +%F)"
```

The commit diff is the price-change record. See [`pricing/README.md`](pricing/README.md) for details.
