# Langfuse Observability on the NAS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up self-hosted Langfuse v3 on the NAS and wire the LiteLLM gateway to it so every gateway LLM request is captured as a per-request trace (prompt, response, tokens, cost), with all content staying on the LAN.

**Architecture:** A new, independent Dockge compose stack at `nas/langfuse/` runs Langfuse v3's six required services (web, worker, Postgres, ClickHouse, Redis, MinIO). Langfuse resources (org/project/user/API keys) are provisioned headlessly via `LANGFUSE_INIT_*` env vars so keys are declared in `.env`. The existing LiteLLM stack gains an async `success_callback: ["langfuse"]` plus the Langfuse host/keys, so traces flow gateway → Langfuse fire-and-forget.

**Tech Stack:** Docker Compose (Dockge on TrueNAS), Langfuse v3, ClickHouse, Redis, MinIO, Postgres 16, LiteLLM (`main-stable`).

**Reference spec:** [`../specs/2026-05-22-langfuse-observability-design.md`](../specs/2026-05-22-langfuse-observability-design.md)

**Note on task style:** This is an infrastructure plan, not application code, so "tests" are concrete verification commands with expected output. Repo-artifact tasks (1–3, 6–8) are done in the working copy and committed. Live-deploy tasks (4, 5) require placing files on a NAS pool path and starting the stack from Dockge by hand (TrueNAS system dataset is read-only — same constraint as the LiteLLM stack); verification is then run via `curl` from the Arch workstation against the NAS LAN IP `10.63.0.2`.

---

## File Structure

| File | Responsibility |
|---|---|
| `nas/langfuse/docker-compose.yml` (create) | The 6-service Langfuse v3 stack, resource caps, health checks, headless init. |
| `nas/langfuse/.env.example` (create) | Template for all Langfuse stack secrets (gitignored once filled). |
| `nas/langfuse/README.md` (create) | Deploy + operate the Langfuse stack. |
| `nas/litellm-config.yaml` (modify) | Add `success_callback: ["langfuse"]`. |
| `nas/docker-compose.yml` (modify) | Add Langfuse host/keys to the litellm service env. |
| `nas/.env.example` (modify) | Add `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`. |
| `README.md` (modify) | Observability is now Langfuse; LiteLLM dashboard demoted to fallback view. |
| `docs/runbook.md` (modify) | Operate/verify Langfuse + the callback wiring + trace round-trip check. |
| `docs/journal/2026-05-22-langfuse-observability.md` (create) | Decision story for the talk. |

---

## Task 1: Langfuse stack compose file

**Files:**
- Create: `nas/langfuse/docker-compose.yml`

- [ ] **Step 1: Write the compose file**

```yaml
# Langfuse v3 — self-hosted observability for the LiteLLM gateway.
# Deploy as its own Dockge stack on a NAS pool path (10.63.0.2). LAN-only.
# Six services: web + worker + postgres + clickhouse + redis + minio.
services:
  langfuse-web:
    image: langfuse/langfuse:3
    restart: always
    depends_on: &langfuse-depends-on
      postgres: { condition: service_healthy }
      clickhouse: { condition: service_healthy }
      redis: { condition: service_healthy }
      minio: { condition: service_healthy }
    ports:
      - "3000:3000"        # LAN UI/API: http://10.63.0.2:3000
    environment: &langfuse-env
      DATABASE_URL: postgresql://langfuse:${POSTGRES_PASSWORD}@postgres:5432/langfuse
      NEXTAUTH_URL: http://10.63.0.2:3000
      NEXTAUTH_SECRET: ${NEXTAUTH_SECRET}
      SALT: ${SALT}
      ENCRYPTION_KEY: ${ENCRYPTION_KEY}
      TELEMETRY_ENABLED: "false"
      CLICKHOUSE_MIGRATION_URL: clickhouse://clickhouse:9000
      CLICKHOUSE_URL: http://clickhouse:8123
      CLICKHOUSE_USER: clickhouse
      CLICKHOUSE_PASSWORD: ${CLICKHOUSE_PASSWORD}
      CLICKHOUSE_CLUSTER_ENABLED: "false"
      REDIS_HOST: redis
      REDIS_PORT: "6379"
      REDIS_AUTH: ${REDIS_AUTH}
      LANGFUSE_S3_EVENT_UPLOAD_BUCKET: langfuse
      LANGFUSE_S3_EVENT_UPLOAD_REGION: auto
      LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID: ${MINIO_ROOT_USER}
      LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY: ${MINIO_ROOT_PASSWORD}
      LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT: http://minio:9000
      LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE: "true"
      LANGFUSE_S3_EVENT_UPLOAD_PREFIX: events/
      LANGFUSE_S3_MEDIA_UPLOAD_BUCKET: langfuse
      LANGFUSE_S3_MEDIA_UPLOAD_REGION: auto
      LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID: ${MINIO_ROOT_USER}
      LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY: ${MINIO_ROOT_PASSWORD}
      LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT: http://minio:9000
      LANGFUSE_S3_MEDIA_UPLOAD_FORCE_PATH_STYLE: "true"
      LANGFUSE_S3_MEDIA_UPLOAD_PREFIX: media/
      # Headless init: provision org/project/user/keys on first boot from .env.
      LANGFUSE_INIT_ORG_ID: ${LANGFUSE_INIT_ORG_ID}
      LANGFUSE_INIT_ORG_NAME: ${LANGFUSE_INIT_ORG_NAME}
      LANGFUSE_INIT_PROJECT_ID: ${LANGFUSE_INIT_PROJECT_ID}
      LANGFUSE_INIT_PROJECT_NAME: ${LANGFUSE_INIT_PROJECT_NAME}
      LANGFUSE_INIT_PROJECT_PUBLIC_KEY: ${LANGFUSE_INIT_PROJECT_PUBLIC_KEY}
      LANGFUSE_INIT_PROJECT_SECRET_KEY: ${LANGFUSE_INIT_PROJECT_SECRET_KEY}
      LANGFUSE_INIT_USER_EMAIL: ${LANGFUSE_INIT_USER_EMAIL}
      LANGFUSE_INIT_USER_NAME: ${LANGFUSE_INIT_USER_NAME}
      LANGFUSE_INIT_USER_PASSWORD: ${LANGFUSE_INIT_USER_PASSWORD}

  langfuse-worker:
    image: langfuse/langfuse-worker:3
    restart: always
    depends_on: *langfuse-depends-on
    environment: *langfuse-env

  postgres:
    image: postgres:16
    restart: always
    environment:
      POSTGRES_USER: langfuse
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: langfuse
    volumes:
      - langfuse-pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U langfuse"]
      interval: 5s
      timeout: 5s
      retries: 10

  clickhouse:
    image: clickhouse/clickhouse-server
    restart: always
    user: "101:101"
    mem_limit: 2g                       # cap the RAM-heavy service (NAS contention guard)
    environment:
      CLICKHOUSE_DB: default
      CLICKHOUSE_USER: clickhouse
      CLICKHOUSE_PASSWORD: ${CLICKHOUSE_PASSWORD}
    ulimits:
      nofile: { soft: 262144, hard: 262144 }
    volumes:
      - langfuse-clickhouse-data:/var/lib/clickhouse
      - langfuse-clickhouse-logs:/var/log/clickhouse-server
    healthcheck:
      test: wget --no-verbose --tries=1 --spider http://localhost:8123/ping || exit 1
      interval: 5s
      timeout: 5s
      retries: 10
      start_period: 10s

  redis:
    image: redis:7
    restart: always
    mem_limit: 512m
    command: --requirepass ${REDIS_AUTH}
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_AUTH}", "ping"]
      interval: 3s
      timeout: 10s
      retries: 10

  minio:
    image: minio/minio
    restart: always
    mem_limit: 512m
    entrypoint: sh
    command: -c 'mkdir -p /data/langfuse && minio server --address ":9000" --console-address ":9001" /data'
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    volumes:
      - langfuse-minio-data:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s
      timeout: 5s
      retries: 10

volumes:
  langfuse-pgdata:
  langfuse-clickhouse-data:
  langfuse-clickhouse-logs:
  langfuse-minio-data:
```

- [ ] **Step 2: Validate the compose syntax**

Run: `docker compose -f nas/langfuse/docker-compose.yml config -q`
Expected: no output, exit code 0 (env vars unset is fine — `config` only checks structure). If `docker` is unavailable locally, skip and rely on the Dockge editor's parse on paste.

- [ ] **Step 3: Commit**

```bash
git add nas/langfuse/docker-compose.yml
git commit -m "feat(langfuse): v3 self-host compose stack for the NAS"
```

---

## Task 2: Langfuse stack `.env.example`

**Files:**
- Create: `nas/langfuse/.env.example`

- [ ] **Step 1: Write the env template**

```bash
# Copy to .env in the Dockge stack dir on the NAS and fill in real values.
# .env is gitignored — never commit it.
#
# Generate the three crypto values with:  openssl rand -hex 32
# Generate the project keys (any unique strings; keep the pk-lf-/sk-lf- prefix):
#   echo "pk-lf-$(uuidgen)"   and   echo "sk-lf-$(uuidgen)"

# --- Datastore credentials ---
POSTGRES_PASSWORD=change-me
CLICKHOUSE_PASSWORD=change-me
REDIS_AUTH=change-me
MINIO_ROOT_USER=langfuse
MINIO_ROOT_PASSWORD=change-me

# --- Langfuse crypto (each: openssl rand -hex 32) ---
NEXTAUTH_SECRET=change-me-32-byte-hex
SALT=change-me-32-byte-hex
ENCRYPTION_KEY=change-me-64-hex-chars-exactly

# --- Headless init: org / project / first user / API keys ---
LANGFUSE_INIT_ORG_ID=aisetup
LANGFUSE_INIT_ORG_NAME=AISetup
LANGFUSE_INIT_PROJECT_ID=gateway
LANGFUSE_INIT_PROJECT_NAME=LiteLLM Gateway
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=pk-lf-change-me
LANGFUSE_INIT_PROJECT_SECRET_KEY=sk-lf-change-me
LANGFUSE_INIT_USER_EMAIL=info@dertechie.de
LANGFUSE_INIT_USER_NAME=DerTechie
LANGFUSE_INIT_USER_PASSWORD=change-me
```

- [ ] **Step 2: Confirm `.env` is gitignored**

Run: `git check-ignore nas/langfuse/.env || grep -nE '(^|/)\.env$|\*\*?/?\.env' .gitignore`
Expected: either `git check-ignore` prints the path (already ignored), or the grep shows an existing `.env` rule. If neither, add `*.env` to `.gitignore` in this step and re-run.

- [ ] **Step 3: Commit**

```bash
git add nas/langfuse/.env.example
git commit -m "feat(langfuse): env template (secrets, crypto, headless init)"
```

---

## Task 3: Langfuse stack README

**Files:**
- Create: `nas/langfuse/README.md`

- [ ] **Step 1: Write the README**

````markdown
# Langfuse (NAS)

Self-hosted **Langfuse v3** — per-request tracing for everything that goes through
the LiteLLM gateway. Deployed as its own **Dockge** compose stack on the NAS
(Ugreen DXP8800, TrueNAS, `10.63.0.2`), separate from the gateway stack so each
restarts/updates independently. LAN-only. See the decision journal:
[`../../docs/journal/2026-05-22-langfuse-observability.md`](../../docs/journal/2026-05-22-langfuse-observability.md).

- **UI / API:** `http://10.63.0.2:3000` (open from the Arch browser).
- Six services: `langfuse-web`, `langfuse-worker`, `postgres`, `clickhouse`,
  `redis`, `minio`. ClickHouse is capped at 2 GB RAM.

## Files

| File | Purpose |
|---|---|
| `docker-compose.yml` | The 6-service stack (paste into Dockge). |
| `.env.example` | Template for the stack `.env` (gitignored once filled). |

## Deploy (in Dockge)

1. Create a stack dir on a **pool path** (TrueNAS system dataset is read-only).
   Place `docker-compose.yml` and a filled `.env` (from `.env.example`) in it.
2. Fill `.env`: datastore passwords; the three crypto values
   (`openssl rand -hex 32` each); and the headless-init block — the
   `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` / `_SECRET_KEY` you set here are the keys the
   gateway uses to send traces.
3. Start the stack from Dockge. First boot runs DB migrations + headless init
   (provisions org/project/user/keys) — give it a minute.
4. Verify (see [`../../docs/runbook.md`](../../docs/runbook.md) § "Verify Langfuse").

## Wiring the gateway

Langfuse only sees traffic the gateway forwards to it. The LiteLLM stack carries
`success_callback: ["langfuse"]` plus `LANGFUSE_HOST` + the public/secret keys;
those keys must match this stack's `LANGFUSE_INIT_PROJECT_*_KEY` values. See the
gateway stack: [`../README.md`](../README.md).
````

- [ ] **Step 2: Commit**

```bash
git add nas/langfuse/README.md
git commit -m "docs(langfuse): stack README (deploy + gateway wiring)"
```

---

## Task 4: Deploy the Langfuse stack on the NAS (live)

**Files:** none (live action on the NAS + verification from Arch).

- [ ] **Step 1: Place and start the stack (manual, on the NAS)**

In Dockge, create a stack named `langfuse` on a pool path, paste
`nas/langfuse/docker-compose.yml`, add a filled `.env` (from `.env.example` —
fill every `change-me`), and start it. First boot runs migrations + headless init.

- [ ] **Step 2: Verify the stack is alive (from Arch)**

Run: `curl -s -o /dev/null -w "%{http_code}\n" http://10.63.0.2:3000/api/public/health`
Expected: `200`

- [ ] **Step 3: Verify the UI loads and login works (manual)**

Open `http://10.63.0.2:3000` in the Arch browser; log in with
`LANGFUSE_INIT_USER_EMAIL` / `LANGFUSE_INIT_USER_PASSWORD`. Confirm the
`LiteLLM Gateway` project exists.
Expected: dashboard loads, project present (proves headless init ran).

- [ ] **Step 4: Verify the API keys are live (from Arch)**

Run (substitute the keys you set in `.env`):
```bash
curl -s -u "pk-lf-YOURKEY:sk-lf-YOURKEY" http://10.63.0.2:3000/api/public/projects | head -c 200; echo
```
Expected: JSON listing the `LiteLLM Gateway` project (not a 401). This confirms the keys the gateway will use are valid.

---

## Task 5: Wire LiteLLM → Langfuse and verify the trace round-trip

**Files:**
- Modify: `nas/litellm-config.yaml`
- Modify: `nas/docker-compose.yml`
- Modify: `nas/.env.example`

- [ ] **Step 1: Add the Langfuse success callback to the gateway config**

In `nas/litellm-config.yaml`, under `litellm_settings:` (which currently ends with the `fallbacks:` line), add the callback. The block becomes:

```yaml
litellm_settings:
  drop_params: true
  num_retries: 2
  # main hits its budget cap -> degrade to private (slow local brain), never die.
  # deep vendor outage/error -> deep-fallback (different vendor).
  # No context_window_fallbacks: the triage advisor decides overflow targets (per redesign spec).
  fallbacks: [{"main": ["private"]}, {"deep": ["deep-fallback"]}]
  # Per-request traces -> Langfuse on the NAS (async, fire-and-forget; never blocks a request).
  success_callback: ["langfuse"]
```

- [ ] **Step 2: Pass the Langfuse host + keys into the litellm container**

In `nas/docker-compose.yml`, add three lines to the `litellm:` service's `environment:` block (after the existing `UI_PASSWORD` line):

```yaml
      UI_PASSWORD: ${UI_PASSWORD}
      LANGFUSE_HOST: ${LANGFUSE_HOST}
      LANGFUSE_PUBLIC_KEY: ${LANGFUSE_PUBLIC_KEY}
      LANGFUSE_SECRET_KEY: ${LANGFUSE_SECRET_KEY}
```

- [ ] **Step 3: Add the Langfuse vars to the gateway env template**

In `nas/.env.example`, append:

```bash
# Langfuse callback target (must match the Langfuse stack's INIT project keys).
LANGFUSE_HOST=http://10.63.0.2:3000
LANGFUSE_PUBLIC_KEY=pk-lf-change-me
LANGFUSE_SECRET_KEY=sk-lf-change-me
```

- [ ] **Step 4: Commit the repo artifacts**

```bash
git add nas/litellm-config.yaml nas/docker-compose.yml nas/.env.example
git commit -m "feat(gateway): ship per-request traces to Langfuse (success_callback)"
```

- [ ] **Step 5: Apply on the NAS (manual)**

Update the `.env` of the **litellm** Dockge stack with the three `LANGFUSE_*` values (keys must match the Langfuse stack's `LANGFUSE_INIT_PROJECT_*_KEY`), update its `docker-compose.yml` + `litellm-config.yaml` from this commit, and restart the litellm stack from Dockge.

- [ ] **Step 6: Fire a real request through the gateway (from Arch)**

Run (substitute the gateway master key from the litellm stack `.env`):
```bash
curl -s http://10.63.0.2:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"private","messages":[{"role":"user","content":"Langfuse trace round-trip test — reply with the single word OK."}]}'
```
Expected: a normal chat completion JSON with a `choices[0].message.content`. (`private` is the free local route — no cost, content stays on the LAN.)

- [ ] **Step 7: Confirm the trace landed in Langfuse (from Arch)**

Wait ~10s for async ingestion, then run (substitute the Langfuse keys):
```bash
curl -s -u "pk-lf-YOURKEY:sk-lf-YOURKEY" \
  "http://10.63.0.2:3000/api/public/traces?limit=1" | head -c 400; echo
```
Expected: JSON with a non-empty `data` array — the most recent trace, showing the request just sent (model `private`, the prompt, the response). This trace round-trip is the **acceptance criterion** for the whole feature; containers-up alone is not sufficient.

- [ ] **Step 8: Confirm in the UI (manual)**

Open `http://10.63.0.2:3000` → Tracing → confirm the request appears with prompt, response, token counts, and latency.
Expected: the trace is visible and drillable.

---

## Task 6: Update the root README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read the current observability description**

Run: `grep -n -i "langfuse\|observ\|dashboard\|:4000/ui" README.md`
Expected: locate the observability sentence(s) describing the LiteLLM dashboard as current and Langfuse as "later".

- [ ] **Step 2: Update the observability text**

Edit the located lines so observability now reads as: **Langfuse v3 self-hosted on the NAS** provides per-request traces (prompt, response, tokens, cost) at `http://10.63.0.2:3000`; the LiteLLM dashboard (`:4000/ui`) remains as a quick spend/latency fallback view. Keep the surrounding style and length consistent with the existing overview.

- [ ] **Step 3: Verify no stale "Langfuse later/deferred" wording remains**

Run: `grep -n -i "langfuse" README.md`
Expected: every hit describes Langfuse as live/current; none say "later", "deferred", or "planned".

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(readme): observability is now Langfuse (live), dashboard demoted"
```

---

## Task 7: Update the runbook

**Files:**
- Modify: `docs/runbook.md`

- [ ] **Step 1: Find the right section**

Run: `grep -n -i "observ\|langfuse\|verify health\|dashboard" docs/runbook.md`
Expected: locate the observability / verify section to extend (or the end of the ops sections if none exists).

- [ ] **Step 2: Add a "Verify Langfuse" + "Trace wiring" subsection**

Add operational content covering, as concrete commands:
- Health: `curl -s -o /dev/null -w "%{http_code}\n" http://10.63.0.2:3000/api/public/health` → `200`.
- Recent traces (substitute keys): `curl -s -u "pk-lf-…:sk-lf-…" "http://10.63.0.2:3000/api/public/traces?limit=1"` → non-empty `data`.
- The wiring fact: traces flow only because the litellm stack has `success_callback: ["langfuse"]` + `LANGFUSE_HOST`/`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`, and those keys must equal the Langfuse stack's `LANGFUSE_INIT_PROJECT_*_KEY`.
- Failure mode: if traces stop, Langfuse being down does **not** break the gateway (async fire-and-forget) — check the Langfuse stack health independently.

Match the runbook's existing heading style and command-block formatting.

- [ ] **Step 3: Commit**

```bash
git add docs/runbook.md
git commit -m "docs(runbook): operate + verify Langfuse and the trace wiring"
```

---

## Task 8: Decision journal entry

**Files:**
- Create: `docs/journal/2026-05-22-langfuse-observability.md`

- [ ] **Step 1: Write the journal entry**

Write a dated entry (Obsidian-friendly markdown) capturing the **why and what was rejected**, not just the what:
- **Decision:** Langfuse v3, self-hosted on the NAS, wired to the gateway via async `success_callback`.
- **Why Langfuse over alternatives:** it is the only option giving per-request prompt/response drill-down + cost dashboards + native LiteLLM integration + recognizable polish for talks. Rejected: **Phoenix** (lighter but less business/cost-flavored), **Grafana-only** (aggregate metrics, never individual prompt content), **Helicone** (itself a proxy — overlaps LiteLLM's role), **LangSmith/Datadog cloud** (content leaves the LAN).
- **The footprint surprise:** v3 is now a mandatory 6-container stack (ClickHouse/Redis/S3); the old Postgres-only Langfuse is EOL. Accepted because single-user load runs far under the published production minimums and ClickHouse is capped at 2 GB.
- **Why it stays on the NAS while n8n (next phase) goes to the t640:** opposite hardware/workload profiles — Langfuse/ClickHouse is RAM-hungry (wants the NAS's memory + backups), n8n is light and I/O-bound (fine on the weak-CPU, low-RAM thin client). The gateway being the single LLM chokepoint means n8n's future LLM calls will be traced + budget-capped for free.
- **Privacy:** self-hosting is the decisive reason — full prompt/response content (incl. the `private` local route) stays on the LAN.
- **Claude Code side-research:** the Max subscription **cannot** be proxied through LiteLLM (subscription/OAuth auth isn't routable via `ANTHROPIC_BASE_URL`; proxying needs an API key). So Claude Code won't appear via the gateway — but its native OpenTelemetry export can ship traces **directly** to Langfuse's OTLP endpoint later, and its `claude_code.cost.usage` (estimated $ on a flat-rate subscription) is the basis for a future "subscription vs API" comparison, which is a metric better suited to a deferred Grafana dashboard.

- [ ] **Step 2: Commit**

```bash
git add docs/journal/2026-05-22-langfuse-observability.md
git commit -m "docs(journal): why Langfuse v3 on the NAS (Phase 3 decision)"
```

---

## Self-Review Notes

- **Spec coverage:** §3 components → Task 1; §4 config/secrets/headless-init/wiring → Tasks 2 & 5; §5 resource caps → Task 1 (`mem_limit` on clickhouse/redis/minio); §6 LAN-only exposure → Task 1 (only `:3000` published) + Task 4; §7 deploy → Task 4; §8 verification (incl. the trace round-trip acceptance test) → Tasks 4 & 5; §9 docs → Tasks 3, 6, 7, 8. All spec sections map to a task.
- **Deferred items stay deferred:** no Grafana, Claude Code OTLP wiring, n8n, or NPM tasks — only noted in the journal (Task 8) as future, per the spec's non-goals.
- **Key-consistency:** the public/secret keys are set once in the Langfuse stack (`LANGFUSE_INIT_PROJECT_PUBLIC_KEY`/`_SECRET_KEY`, Task 2) and must equal the gateway's `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` (Task 5) — this equality is called out in Tasks 3, 5, and 7.
