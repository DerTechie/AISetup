# Phase 1 — Routing in Place Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the LiteLLM gateway on the Mac so Hermes routes through one OpenAI-compatible endpoint to either the local Ollama model (`local`) or OpenRouter (`deep`), with a hard cloud budget cap and a spend dashboard.

**Architecture:** Hermes (Arch) → LiteLLM proxy (Mac `:4000`, OpenAI-compatible) → Ollama (`:11434`, local) or OpenRouter (cloud). LiteLLM + Postgres run via Docker Compose on the Mac; Postgres backs spend tracking, budget enforcement, and the `/ui` dashboard. Sanitized configs live in this repo; secrets live only in an untracked `.env` on the Mac.

**Tech Stack:** Ollama (macOS), LiteLLM proxy (Docker image `ghcr.io/berriai/litellm:main-stable`), Postgres 16, Docker Compose, OpenRouter, Hermes Agent.

---

## Prerequisites — values to fill in before starting

Confirm/collect these. They are environment-specific, not guesses:

| Value | How to get it | Used in |
|---|---|---|
| `MAC_HOST` | IP/hostname of the Mac on your LAN (e.g. `192.168.1.50`) | every task |
| Docker on the Mac | Docker Desktop or **colima** (recommended for headless). Verify `docker info` works over SSH. | Task 2 |
| `OPENROUTER_API_KEY` | Create account at openrouter.ai, add a few € credit, make a key (`sk-or-...`) | Task 3 |
| `LITELLM_MASTER_KEY` | You invent it, format `sk-...` (gateway admin/API key) | Tasks 2–5 |
| `POSTGRES_PASSWORD`, `UI_USERNAME`, `UI_PASSWORD` | You invent them | Task 2 |
| `LOCAL_MODEL_TAG` | Verify on `ollama.com/library`. **Default: `qwen2.5:32b-instruct-q4_K_M`** (~20 GB). Prefer a current agentic Qwen (e.g. a qwen3 32B / 30B-A3B tag) if available. | Task 1, 3 |
| `DEEP_MODEL` | OpenRouter model id. **Default: `deepseek/deepseek-r1`**. Verify its context limit. | Task 3 |
| Hermes config location | Confirm `~/.hermes/config.yaml` and how Hermes currently talks to Ollama | Task 5 |

**Memory note:** on a 32 GB Mac, `qwen2.5:32b` (Q4 ≈ 20 GB) + Postgres + LiteLLM + macOS leaves a tight but workable margin for one concurrent inference. If it swaps or feels slow, switch `LOCAL_MODEL_TAG` to a 30B-A3B MoE (≈18 GB, faster) or a smaller quant.

---

## Task 1: Mac prep — no-sleep + Ollama on the network + model pulled

**Files:** none in repo (Mac-side config). Verification only.

- [ ] **Step 1: SSH to the Mac and disable sleep**

```bash
ssh $MAC_HOST
sudo pmset -a sleep 0 disablesleep 1
sudo pmset -a powernap 0
```

- [ ] **Step 2: Verify sleep is disabled**

Run (on Mac): `pmset -g | grep -E 'sleep|disablesleep'`
Expected: `sleep` shows `0` and `disablesleep` shows `1`.

- [ ] **Step 3: Make Ollama listen on the network**

```bash
launchctl setenv OLLAMA_HOST "0.0.0.0:11434"
# Restart Ollama: quit the menubar app and relaunch it, OR if running via CLI:
#   pkill ollama; OLLAMA_HOST=0.0.0.0:11434 ollama serve &
```

- [ ] **Step 4: Pull the local model**

Run (on Mac): `ollama pull qwen2.5:32b-instruct-q4_K_M`
Expected: download completes; `ollama list` shows the tag.

- [ ] **Step 5: Smoke-test Ollama over the LAN (from the Arch box)**

```bash
curl -s http://$MAC_HOST:11434/api/tags | head
curl -s http://$MAC_HOST:11434/api/generate \
  -d '{"model":"qwen2.5:32b-instruct-q4_K_M","prompt":"Reply with exactly: OK","stream":false}'
```
Expected: first call lists the model; second returns JSON whose `response` contains `OK`. **If this fails, stop** — nothing downstream works until Ollama answers over the LAN.

---

## Task 2: Bring up LiteLLM + Postgres on the Mac via Docker Compose

**Files:**
- Create: `mac/docker-compose.yml`
- Create: `mac/.env.example`
- Create (on Mac only, NOT committed): `mac/.env`

- [ ] **Step 1: Write `mac/docker-compose.yml`**

```yaml
services:
  litellm-db:
    image: postgres:16
    restart: always
    environment:
      POSTGRES_USER: litellm
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: litellm
    volumes:
      - litellm-pgdata:/var/lib/postgresql/data

  litellm:
    image: ghcr.io/berriai/litellm:main-stable
    restart: always
    depends_on:
      - litellm-db
    ports:
      - "4000:4000"
    environment:
      DATABASE_URL: postgresql://litellm:${POSTGRES_PASSWORD}@litellm-db:5432/litellm
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
      OPENROUTER_API_KEY: ${OPENROUTER_API_KEY}
      UI_USERNAME: ${UI_USERNAME}
      UI_PASSWORD: ${UI_PASSWORD}
    volumes:
      - ./litellm-config.yaml:/app/config.yaml
    command: ["--config", "/app/config.yaml", "--port", "4000"]

volumes:
  litellm-pgdata:
```

- [ ] **Step 2: Write `mac/.env.example` (template, committed)**

```bash
POSTGRES_PASSWORD=change-me
LITELLM_MASTER_KEY=sk-set-a-long-random-value
OPENROUTER_API_KEY=sk-or-your-key-here
UI_USERNAME=admin
UI_PASSWORD=change-me
```

- [ ] **Step 3: On the Mac, create the real `.env`**

Copy `mac/.env.example` to `mac/.env` on the Mac and fill in real values. This file is gitignored (`.env`) and must never be committed.

- [ ] **Step 4: Verify Docker is available on the Mac**

Run (on Mac): `docker info | head`
Expected: prints server info without error. If not, install/start colima (`brew install colima && colima start`) or Docker Desktop.

(Bringing the stack up happens in Task 3 once the config file exists.)

---

## Task 3: Configure routes, caps, fallback — and verify both routes

**Files:**
- Create: `mac/litellm-config.yaml`

- [ ] **Step 1: Write `mac/litellm-config.yaml`**

> Note: the LiteLLM container reaches Ollama on the Mac host via `host.docker.internal`, not `localhost`.

```yaml
model_list:
  - model_name: local
    litellm_params:
      model: ollama_chat/qwen2.5:32b-instruct-q4_K_M
      api_base: http://host.docker.internal:11434
  - model_name: deep
    litellm_params:
      model: openrouter/deepseek/deepseek-r1
      api_key: os.environ/OPENROUTER_API_KEY
      max_tokens: 8000          # default completion ceiling per request

litellm_settings:
  drop_params: true
  num_retries: 2
  context_window_fallbacks: [{"local": ["deep"]}]   # local prompt too big -> deep
  max_budget: 100               # USD, global, hard cap (~EUR92). Adjust to taste.
  budget_duration: 30d

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
```

- [ ] **Step 2: Bring the stack up**

Run (on Mac, in `mac/`): `docker compose --env-file .env up -d`
Expected: `litellm` and `litellm-db` containers start.

- [ ] **Step 3: Verify the gateway is live**

Run (from Arch): `curl -s http://$MAC_HOST:4000/health/liveliness`
Expected: `"I'm alive!"` (or HTTP 200).

- [ ] **Step 4: Verify the `local` route**

```bash
curl -s http://$MAC_HOST:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"local","messages":[{"role":"user","content":"Reply with exactly: OK"}]}'
```
Expected: JSON with `choices[0].message.content` containing `OK`, served by the local model.

- [ ] **Step 5: Verify the `deep` route (costs a few cents)**

```bash
curl -s http://$MAC_HOST:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"deep","messages":[{"role":"user","content":"Reply with exactly: OK"}]}'
```
Expected: JSON response from DeepSeek-R1 via OpenRouter.

- [ ] **Step 6: Verify the dashboard shows both calls**

Open `http://$MAC_HOST:4000/ui` in the Arch browser, log in with `UI_USERNAME`/`UI_PASSWORD`.
Expected: the Usage/Logs view lists the two requests with model name, tokens, and spend.

---

## Task 4: Verify the budget cap blocks cloud with a clear error

**Files:** temporary edit to `mac/litellm-config.yaml` (reverted at the end).

- [ ] **Step 1: Temporarily set a near-zero budget**

In `mac/litellm-config.yaml`, change `max_budget: 100` to `max_budget: 0.00001`, then restart:
Run (on Mac, in `mac/`): `docker compose --env-file .env restart litellm`

- [ ] **Step 2: Call `deep` and confirm it is blocked**

Run the Task 3 Step 5 curl again.
Expected: an error response indicating the budget was exceeded (HTTP 400 / message mentioning "budget"). The request does **not** reach the cloud.

- [ ] **Step 3: Confirm `local` still works at the cap**

Run the Task 3 Step 4 curl again.
Expected: still returns `OK` — routine local work is unaffected by the cloud budget.

- [ ] **Step 4: Restore the real budget**

Set `max_budget` back to `100`, then `docker compose --env-file .env restart litellm`. Re-run Task 3 Step 5 to confirm `deep` works again.

- [ ] **Step 5: Commit the verified config to the repo**

```bash
git add mac/docker-compose.yml mac/litellm-config.yaml mac/.env.example
git commit -m "feat: LiteLLM gateway config with local/deep routes, budget cap, fallback"
```

---

## Task 5: Wire Hermes to the gateway

**Files:** Hermes config on the Arch box (`~/.hermes/config.yaml`) — not in this repo.

> The exact provider keys for a custom OpenAI-compatible endpoint should be confirmed against Hermes' own config docs (open item from the spec). Use the wizard where possible.

- [ ] **Step 1: Point Hermes' main model at the gateway**

Run: `hermes model` (wizard) and set the provider base URL to `http://$MAC_HOST:4000/v1`, API key = `LITELLM_MASTER_KEY`, model = `local`.
Or edit `~/.hermes/config.yaml`:

```yaml
model:
  provider: openai            # generic OpenAI-compatible provider
  default: local
  api_base: http://MAC_HOST:4000/v1
  api_key: sk-your-litellm-master-key

model_aliases:
  fast:
    provider: openai
    model: local
  deep:
    provider: openai
    model: deep
```

- [ ] **Step 2: Verify routine runs local**

Start a Hermes chat and send a short prompt.
Expected: the dashboard (`:4000/ui`) logs a request against `local`.

- [ ] **Step 3: Verify explicit cloud switch**

In the Hermes session, run `/model deep`, then send a prompt.
Expected: the dashboard logs a request against `deep` (OpenRouter), and spend increments.

- [ ] **Step 4: Switch back to local**

Run `/model fast` (or `/model local`) in the session.
Expected: subsequent prompts log against `local` again.

---

## Task 6: Write the runbook and finalize

**Files:**
- Create: `docs/runbook.md`

- [ ] **Step 1: Write `docs/runbook.md`**

```markdown
# Runbook — Phase 1 Routing

## Hosts
- Mac (`MAC_HOST`): Ollama + LiteLLM gateway. Headless, no-sleep (`pmset`).
- Arch: Hermes Agent.

## Start / stop the gateway (on the Mac, in `mac/`)
- Start:   `docker compose --env-file .env up -d`
- Stop:    `docker compose --env-file .env down`
- Restart: `docker compose --env-file .env restart litellm`
- Logs:    `docker compose --env-file .env logs -f litellm`

## Auto-start
- `restart: always` in compose + Docker starting on login keeps the gateway up.
- Ollama: set the app to launch on login. `pmset` keeps the Mac awake.

## Verify health
- `curl http://MAC_HOST:4000/health/liveliness`  -> "I'm alive!"
- Dashboard: http://MAC_HOST:4000/ui  (UI_USERNAME / UI_PASSWORD)

## Routing
- Default model = `local` (Ollama). `/model deep` -> OpenRouter. `/model fast` -> local.
- Oversized prompts auto-fall back local -> deep (context_window_fallbacks).

## Cost
- Hard cap: `max_budget` (USD) over `budget_duration` in `litellm-config.yaml`.
- At cap: `deep` is blocked with a clear error; `local` keeps working.

## Secrets
- Live ONLY in `mac/.env` on the Mac (gitignored). Never commit. `mac/.env.example` is the template.
```

- [ ] **Step 2: Commit the runbook**

```bash
git add docs/runbook.md
git commit -m "docs: add Phase 1 routing runbook"
```

- [ ] **Step 3: Push**

```bash
git push origin main
```

---

## Self-review (coverage against spec §11 Phase 1)

- **1a Mac prep (pmset, OLLAMA_HOST, pull model, smoke test):** Task 1 ✓
- **1b LiteLLM gateway (local + deep, key via env, budget + token cap, context fallback, dashboard):** Tasks 2–3 ✓ (cap verified in Task 4)
- **1c Wire Hermes (default local, `/model deep` → cloud, dashboard shows model/tokens/cost, cap blocks with clear error):** Tasks 4–5 ✓
- **Sanitized configs committed, secrets gitignored:** Task 4 Step 5, `.gitignore` already in place ✓
- **Runbook:** Task 6 ✓

**Open items carried (environment-specific, flagged in Prerequisites):** exact `LOCAL_MODEL_TAG`, `DEEP_MODEL` context limit, Hermes provider config keys for a custom OpenAI-compatible endpoint, Docker runtime choice on the Mac.
