# Grafana Usage Metrics on the NAS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a Grafana stack on the NAS that visualizes (a) gateway usage/cost over time read directly from the data LiteLLM already persists to its Postgres (`LiteLLM_SpendLogs`), and (b) Claude Code subscription token/cost usage shipped from the Arch workstation via OpenTelemetry, so a real historical baseline starts accruing now.

**Architecture:** A new Dockge compose stack at `nas/metrics/` with three LAN-only containers — an OpenTelemetry collector (receives Claude Code OTLP, re-exposes it as Prometheus metrics), Prometheus (scrapes the collector, 1-year retention), and Grafana (the dashboards). Gateway data needs **no new collection**: Grafana joins the gateway stack's existing Docker network as `external` and queries `litellm-db` through a dedicated **read-only** Postgres role. Grafana, Prometheus, and the collector's exporter are all provisioned as code (no hand-clicking). Grafana publishes on host `:3001` (Langfuse owns `:3000`); the collector publishes OTLP `:4318` so Claude Code on Arch can push.

**Tech Stack:** Docker Compose (Dockge on TrueNAS), Grafana, Prometheus, OpenTelemetry Collector (contrib), LiteLLM's Postgres (`LiteLLM_SpendLogs`), Claude Code OTel export.

**Reference spec:** [`../specs/2026-05-22-grafana-usage-metrics-design.md`](../specs/2026-05-22-grafana-usage-metrics-design.md)

**Note on task style:** This is an infrastructure plan, not application code, so "tests" are concrete verification commands with expected output. Repo-artifact tasks (1–9, 13–15) are done in the working copy and committed. Live tasks (10–12) require placing files on a NAS pool path, creating a DB role, and starting the stack from Dockge by hand (TrueNAS system dataset is read-only — same constraint as the other NAS stacks); verification runs via `curl`/`psql` from the Arch workstation against the NAS LAN IP `10.63.0.2`.

---

## Verified facts (confirmed against current docs during planning — do not re-derive)

- **`LiteLLM_SpendLogs` columns** (from `schema.prisma`, BerriAI/litellm `main`): `request_id`, `call_type`, `api_key`, `spend` (Float, **USD**), `total_tokens`, `prompt_tokens`, `completion_tokens`, `startTime`, `endTime`, `request_duration_ms` (Int, nullable), `model`, `model_group` (**this is the route name**: `main`/`private`/`aux-local`/`deep`/`deep-fallback`), `custom_llm_provider`, `user`, `status`. Spend logging is **on by default** when a DB is configured (only disabled by `disable_spend_logs: True`).
- **Route → category** (from `nas/litellm-config.yaml`): local/€0 = `private` (Mac), `aux-local` (Arch); cloud = `main` ($50 cap), `deep` ($35), `deep-fallback` ($15). LiteLLM tracks **USD**; the $50+$35+$15 = **$100** budget total is the cap the gateway enforces (≈ the €100 target from the spec — the dashboard shows USD against $100).
- **Claude Code OTel** (from code.claude.com/docs/en/monitoring-usage): metrics `claude_code.cost.usage` (unit **USD**), `claude_code.token.usage` (unit **tokens**, attrs `type` ∈ {input,output,cacheRead,cacheCreation}, `model`, `query_source` ∈ {main,subagent,auxiliary}), `claude_code.session.count` (count). **Default metrics temporality is `delta`** — must set `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative` so Prometheus counters behave. HTTP/protobuf OTLP is port `4318`.
- **Collector → Prometheus naming:** the collector's `prometheus` exporter appends the unit and `_total` to monotonic sums. Expected series: `claude_code_cost_usage_USD_total`, `claude_code_token_usage_tokens_total`, `claude_code_session_count_total`. **These exact names are confirmed at deploy time (Task 12, Step 2) and the dashboard JSON adjusted if the collector version differs.**
- **Cross-stack network:** the gateway compose (`nas/docker-compose.yml`) declares no `networks:`, so Docker Compose creates a default network named `<dockge-stack-dir>_default` (commonly `litellm_default`). `litellm-db` is reachable by that service name on that network. The exact name is discovered at deploy time (Task 10, Step 1).

---

## File Structure

| File | Responsibility |
|---|---|
| `nas/metrics/otel-collector-config.yaml` (create) | OTLP receiver (`:4317` gRPC, `:4318` HTTP) → `prometheus` exporter on `:8889`. |
| `nas/metrics/prometheus.yml` (create) | Scrape the collector's `:8889`; 30s interval. |
| `nas/metrics/provisioning/datasources/datasources.yaml` (create) | Prometheus + read-only LiteLLM Postgres datasources, fixed UIDs. |
| `nas/metrics/provisioning/dashboards/dashboards.yaml` (create) | Dashboard provider pointing Grafana at the JSON dir. |
| `nas/metrics/provisioning/dashboards/gateway.json` (create) | Gateway spend/usage dashboard (Postgres `LiteLLM_SpendLogs`). |
| `nas/metrics/provisioning/dashboards/claude-code.json` (create) | Claude Code tokens/cost dashboard (Prometheus) + the subscription-vs-API money-shot panel. |
| `nas/metrics/docker-compose.yml` (create) | The 3-service stack: collector, prometheus, grafana; external gateway network; retention flag; ports. |
| `nas/metrics/.env.example` (create) | `GRAFANA_ADMIN_PASSWORD`, `GRAFANA_DB_RO_PASSWORD`, `GATEWAY_NETWORK`. |
| `nas/metrics/sql/grafana_ro.sql` (create) | One-time read-only Postgres role for Grafana. |
| `nas/metrics/README.md` (create) | Deploy + operate the metrics stack; the Arch-side CC env. |
| `arch/claude-code-otel.sh` (create) | Sourceable shell snippet that sets the Claude Code OTel env (Arch side). |
| `arch/README.md` (modify) | Document sourcing the CC OTel snippet. |
| `README.md` (modify) | Observability = Langfuse (traces) **+** Grafana (usage/cost metrics). |
| `docs/runbook.md` (modify) | Operate/verify Grafana, both datasources, the CC env, the cross-stack network + RO role facts. |
| `docs/journal/2026-05-22-grafana-usage-metrics.md` (create) | Decision story for the talk. |

---

## Task 1: OpenTelemetry collector config

**Files:**
- Create: `nas/metrics/otel-collector-config.yaml`

- [ ] **Step 1: Write the collector config**

```yaml
# Receives Claude Code OTLP metrics (from Arch) and re-exposes them as Prometheus
# metrics for Prometheus to scrape. The collector decouples the bursty Claude Code
# CLI from Prometheus's pull model and is the single egress that could later also
# forward Claude Code traces to Langfuse.
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch: {}

exporters:
  prometheus:
    endpoint: 0.0.0.0:8889
    # Promote OTLP resource attributes (service.name, host, etc.) to metric labels.
    resource_to_telemetry_conversion:
      enabled: true

service:
  telemetry:
    logs:
      level: info
  pipelines:
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [prometheus]
```

- [ ] **Step 2: Sanity-check the YAML parses**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('nas/metrics/otel-collector-config.yaml')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add nas/metrics/otel-collector-config.yaml
git commit -m "feat(metrics): otel collector config (OTLP in -> Prometheus exporter)"
```

---

## Task 2: Prometheus config

**Files:**
- Create: `nas/metrics/prometheus.yml`

- [ ] **Step 1: Write the Prometheus config**

```yaml
# Scrapes the collector's Prometheus exporter. Retention is set on the command
# line in docker-compose.yml (--storage.tsdb.retention.time=1y) for talk-length history.
global:
  scrape_interval: 30s
  scrape_timeout: 10s

scrape_configs:
  - job_name: claude-code
    static_configs:
      - targets: ['otel-collector:8889']
```

- [ ] **Step 2: Sanity-check the YAML parses**

Run: `python3 -c "import yaml; yaml.safe_load(open('nas/metrics/prometheus.yml')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add nas/metrics/prometheus.yml
git commit -m "feat(metrics): prometheus scrape config for the collector"
```

---

## Task 3: Grafana datasource provisioning

**Files:**
- Create: `nas/metrics/provisioning/datasources/datasources.yaml`

- [ ] **Step 1: Write the datasources file**

Grafana expands `$ENV` references in provisioning files, so the read-only DB password comes from the stack `.env` without being committed. Fixed `uid`s let the dashboard JSON reference these datasources reliably.

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    uid: prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true

  - name: LiteLLM Postgres (read-only)
    uid: litellm-pg
    type: postgres
    access: proxy
    # Reached over the gateway stack's external Docker network (joined in docker-compose.yml).
    url: litellm-db:5432
    database: litellm
    user: grafana_ro
    jsonData:
      sslmode: disable
      postgresVersion: 1600
    secureJsonData:
      password: $GRAFANA_DB_RO_PASSWORD
```

- [ ] **Step 2: Sanity-check the YAML parses**

Run: `python3 -c "import yaml; yaml.safe_load(open('nas/metrics/provisioning/datasources/datasources.yaml')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add nas/metrics/provisioning/datasources/datasources.yaml
git commit -m "feat(metrics): grafana datasources (Prometheus + read-only LiteLLM Postgres)"
```

---

## Task 4: Grafana dashboard provider

**Files:**
- Create: `nas/metrics/provisioning/dashboards/dashboards.yaml`

- [ ] **Step 1: Write the dashboard provider**

```yaml
apiVersion: 1

providers:
  - name: aisetup
    orgId: 1
    folder: AISetup
    type: file
    disableDeletion: false
    editable: true
    allowUiUpdates: true
    options:
      # Grafana loads every *.json in this dir as a dashboard. The compose file
      # mounts ./provisioning/dashboards here read-only.
      path: /etc/grafana/provisioning/dashboards
      foldersFromFilesStructure: false
```

- [ ] **Step 2: Sanity-check the YAML parses**

Run: `python3 -c "import yaml; yaml.safe_load(open('nas/metrics/provisioning/dashboards/dashboards.yaml')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add nas/metrics/provisioning/dashboards/dashboards.yaml
git commit -m "feat(metrics): grafana dashboard provider"
```

---

## Task 5: Gateway dashboard JSON (Postgres `LiteLLM_SpendLogs`)

**Files:**
- Create: `nas/metrics/provisioning/dashboards/gateway.json`

- [ ] **Step 1: Write the gateway dashboard**

Five panels covering spec §7's gateway requirements. SQL targets the verified `LiteLLM_SpendLogs` columns. `$__timeGroupAlias`/`$__timeFilter` are Grafana's Postgres macros. The `spend` stat panel thresholds at 100 (the $100 budget total).

```json
{
  "title": "Gateway Usage & Cost",
  "uid": "gateway-usage",
  "schemaVersion": 39,
  "editable": true,
  "time": { "from": "now-30d", "to": "now" },
  "timezone": "browser",
  "panels": [
    {
      "id": 1,
      "type": "stat",
      "title": "30-day spend (USD) vs $100 cap",
      "gridPos": { "h": 6, "w": 8, "x": 0, "y": 0 },
      "datasource": { "type": "postgres", "uid": "litellm-pg" },
      "fieldConfig": {
        "defaults": {
          "unit": "currencyUSD",
          "max": 100,
          "thresholds": {
            "mode": "absolute",
            "steps": [
              { "color": "green", "value": null },
              { "color": "yellow", "value": 70 },
              { "color": "red", "value": 90 }
            ]
          }
        }
      },
      "options": { "colorMode": "value", "graphMode": "none", "reduceOptions": { "calcs": ["lastNotNull"] } },
      "targets": [
        {
          "refId": "A",
          "format": "table",
          "rawQuery": true,
          "rawSql": "SELECT COALESCE(SUM(spend),0) AS spend_usd FROM \"LiteLLM_SpendLogs\" WHERE \"startTime\" > NOW() - INTERVAL '30 days';"
        }
      ]
    },
    {
      "id": 2,
      "type": "timeseries",
      "title": "Spend over time (USD)",
      "gridPos": { "h": 8, "w": 16, "x": 8, "y": 0 },
      "datasource": { "type": "postgres", "uid": "litellm-pg" },
      "fieldConfig": { "defaults": { "unit": "currencyUSD" } },
      "targets": [
        {
          "refId": "A",
          "format": "time_series",
          "rawQuery": true,
          "rawSql": "SELECT $__timeGroupAlias(\"startTime\",'1h'), \"model_group\" AS metric, SUM(spend) AS spend FROM \"LiteLLM_SpendLogs\" WHERE $__timeFilter(\"startTime\") GROUP BY 1, \"model_group\" ORDER BY 1;"
        }
      ]
    },
    {
      "id": 3,
      "type": "table",
      "title": "By route (30d): spend, requests, tokens",
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 8 },
      "datasource": { "type": "postgres", "uid": "litellm-pg" },
      "targets": [
        {
          "refId": "A",
          "format": "table",
          "rawQuery": true,
          "rawSql": "SELECT \"model_group\" AS route, ROUND(SUM(spend)::numeric,4) AS spend_usd, COUNT(*) AS requests, SUM(total_tokens) AS tokens FROM \"LiteLLM_SpendLogs\" WHERE \"startTime\" > NOW() - INTERVAL '30 days' GROUP BY 1 ORDER BY spend_usd DESC;"
        }
      ]
    },
    {
      "id": 4,
      "type": "piechart",
      "title": "Local vs cloud requests (30d)",
      "gridPos": { "h": 8, "w": 6, "x": 12, "y": 8 },
      "datasource": { "type": "postgres", "uid": "litellm-pg" },
      "targets": [
        {
          "refId": "A",
          "format": "table",
          "rawQuery": true,
          "rawSql": "SELECT CASE WHEN \"model_group\" IN ('private','aux-local') THEN 'local' ELSE 'cloud' END AS category, COUNT(*) AS requests FROM \"LiteLLM_SpendLogs\" WHERE \"startTime\" > NOW() - INTERVAL '30 days' GROUP BY 1;"
        }
      ]
    },
    {
      "id": 5,
      "type": "timeseries",
      "title": "Avg latency by route (ms)",
      "gridPos": { "h": 8, "w": 6, "x": 18, "y": 8 },
      "datasource": { "type": "postgres", "uid": "litellm-pg" },
      "fieldConfig": { "defaults": { "unit": "ms" } },
      "targets": [
        {
          "refId": "A",
          "format": "time_series",
          "rawQuery": true,
          "rawSql": "SELECT $__timeGroupAlias(\"startTime\",'1h'), \"model_group\" AS metric, AVG(\"request_duration_ms\") AS avg_ms FROM \"LiteLLM_SpendLogs\" WHERE $__timeFilter(\"startTime\") AND \"request_duration_ms\" IS NOT NULL GROUP BY 1, \"model_group\" ORDER BY 1;"
        }
      ]
    }
  ]
}
```

- [ ] **Step 2: Validate the JSON parses**

Run: `python3 -c "import json; json.load(open('nas/metrics/provisioning/dashboards/gateway.json')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add nas/metrics/provisioning/dashboards/gateway.json
git commit -m "feat(metrics): gateway dashboard (spend/requests/tokens/latency by route)"
```

---

## Task 6: Claude Code dashboard JSON (Prometheus) + the money-shot panel

**Files:**
- Create: `nas/metrics/provisioning/dashboards/claude-code.json`

- [ ] **Step 1: Write the Claude Code dashboard**

PromQL uses the expected series names (confirmed/adjusted in Task 12). Panel 5 is the talk money-shot: a **Mixed** datasource panel with one Prometheus target (Claude Code estimated subscription $) and one Postgres target (gateway actual API $).

```json
{
  "title": "Claude Code Usage (subscription)",
  "uid": "claude-code-usage",
  "schemaVersion": 39,
  "editable": true,
  "time": { "from": "now-7d", "to": "now" },
  "timezone": "browser",
  "panels": [
    {
      "id": 1,
      "type": "stat",
      "title": "Estimated cost to date (USD)",
      "gridPos": { "h": 6, "w": 6, "x": 0, "y": 0 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "fieldConfig": { "defaults": { "unit": "currencyUSD" } },
      "options": { "colorMode": "value", "graphMode": "area", "reduceOptions": { "calcs": ["lastNotNull"] } },
      "targets": [
        { "refId": "A", "expr": "sum(claude_code_cost_usage_USD_total)" }
      ]
    },
    {
      "id": 2,
      "type": "stat",
      "title": "Sessions",
      "gridPos": { "h": 6, "w": 6, "x": 6, "y": 0 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "options": { "colorMode": "value", "graphMode": "none", "reduceOptions": { "calcs": ["lastNotNull"] } },
      "targets": [
        { "refId": "A", "expr": "sum(claude_code_session_count_total)" }
      ]
    },
    {
      "id": 3,
      "type": "timeseries",
      "title": "Tokens over time by type",
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 0 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [
        { "refId": "A", "legendFormat": "{{type}}", "expr": "sum by (type) (increase(claude_code_token_usage_tokens_total[$__rate_interval]))" }
      ]
    },
    {
      "id": 4,
      "type": "timeseries",
      "title": "Estimated cost over time by model (USD)",
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 8 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "fieldConfig": { "defaults": { "unit": "currencyUSD" } },
      "targets": [
        { "refId": "A", "legendFormat": "{{model}}", "expr": "sum by (model) (increase(claude_code_cost_usage_USD_total[$__rate_interval]))" }
      ]
    },
    {
      "id": 5,
      "type": "timeseries",
      "title": "Money-shot: Claude Code estimated-$ (subscription) vs gateway actual-$ (API)",
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 8 },
      "datasource": { "type": "datasource", "uid": "-- Mixed --" },
      "fieldConfig": { "defaults": { "unit": "currencyUSD" } },
      "targets": [
        {
          "refId": "A",
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "legendFormat": "Claude Code estimated $ (subscription)",
          "expr": "sum(increase(claude_code_cost_usage_USD_total[$__rate_interval]))"
        },
        {
          "refId": "B",
          "datasource": { "type": "postgres", "uid": "litellm-pg" },
          "format": "time_series",
          "rawQuery": true,
          "rawSql": "SELECT $__timeGroupAlias(\"startTime\",'1h'), SUM(spend) AS \"Gateway actual $ (API)\" FROM \"LiteLLM_SpendLogs\" WHERE $__timeFilter(\"startTime\") GROUP BY 1 ORDER BY 1;"
        }
      ]
    }
  ]
}
```

- [ ] **Step 2: Validate the JSON parses**

Run: `python3 -c "import json; json.load(open('nas/metrics/provisioning/dashboards/claude-code.json')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add nas/metrics/provisioning/dashboards/claude-code.json
git commit -m "feat(metrics): Claude Code dashboard + subscription-vs-API money-shot"
```

---

## Task 7: The metrics stack compose file

**Files:**
- Create: `nas/metrics/docker-compose.yml`

- [ ] **Step 1: Write the compose file**

Grafana joins the gateway stack's external network (named in `.env` as `GATEWAY_NETWORK`) so it can reach `litellm-db`; it is also on the internal `metrics` network with Prometheus. The collector publishes OTLP `:4318` to the LAN for Claude Code; Prometheus `:9090` and the collector's `:8889` stay internal. Image tags are pinned — `docker compose pull` (Task 10) fails loudly if a tag must be bumped to current stable.

```yaml
# Grafana usage/cost metrics stack. Deploy as its own Dockge stack on a NAS pool
# path (10.63.0.2). LAN-only. Three services: otel-collector + prometheus + grafana.
# Grafana also attaches to the gateway stack's external network to read LiteLLM_SpendLogs.
services:
  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.152.0
    restart: always
    command: ["--config=/etc/otel-collector-config.yaml"]
    volumes:
      - ./otel-collector-config.yaml:/etc/otel-collector-config.yaml:ro
    ports:
      - "4318:4318"          # OTLP/HTTP published to the LAN (Claude Code on Arch pushes here)
    networks:
      - metrics

  prometheus:
    image: prom/prometheus:v3.1.0
    restart: always
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.retention.time=1y"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus-data:/prometheus
    networks:
      - metrics                # :9090 stays internal (no ports: published)

  grafana:
    image: grafana/grafana:12.4.3
    restart: always
    depends_on:
      - prometheus
    ports:
      - "3001:3000"            # LAN UI: http://10.63.0.2:3001 (Langfuse owns host :3000)
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD}
      GF_USERS_ALLOW_SIGN_UP: "false"
      GRAFANA_DB_RO_PASSWORD: ${GRAFANA_DB_RO_PASSWORD}   # consumed by the provisioned Postgres datasource
    volumes:
      - ./provisioning:/etc/grafana/provisioning:ro
      - grafana-data:/var/lib/grafana
    networks:
      - metrics
      - gateway               # reach litellm-db on the gateway stack's network

networks:
  metrics:
  gateway:
    external: true
    name: ${GATEWAY_NETWORK}  # e.g. litellm_default — confirm with `docker network ls` (Task 10)

volumes:
  prometheus-data:
  grafana-data:
```

- [ ] **Step 2: Sanity-check the YAML parses**

Run: `python3 -c "import yaml; yaml.safe_load(open('nas/metrics/docker-compose.yml')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add nas/metrics/docker-compose.yml
git commit -m "feat(metrics): 3-service stack (collector + prometheus + grafana)"
```

---

## Task 8: Stack `.env.example`

**Files:**
- Create: `nas/metrics/.env.example`

- [ ] **Step 1: Write the env template**

```bash
# Copy to .env in the Dockge stack dir on the NAS and fill in real values.
# .env is gitignored — never commit it.

# Grafana admin login (user: admin).
GRAFANA_ADMIN_PASSWORD=change-me

# Password for the read-only Postgres role Grafana uses to read LiteLLM_SpendLogs.
# MUST equal the password set in sql/grafana_ro.sql when the role is created.
GRAFANA_DB_RO_PASSWORD=change-me

# The gateway (litellm) Dockge stack's external Docker network, so Grafana can
# reach litellm-db. Discover on the NAS with:  docker network ls | grep litellm
# Usually <stack-dir>_default, e.g. litellm_default.
GATEWAY_NETWORK=litellm_default
```

- [ ] **Step 2: Confirm `.env` is gitignored**

Run: `git check-ignore nas/metrics/.env || grep -nE '^\.env' .gitignore`
Expected: either `git check-ignore` prints the path, or the grep shows the existing `.env` rule (`.gitignore` already has `.env` + `.env.*` with `!.env.example`).

- [ ] **Step 3: Commit**

```bash
git add nas/metrics/.env.example
git commit -m "feat(metrics): env template (grafana admin, RO db pw, gateway network)"
```

---

## Task 9: Read-only Postgres role SQL

**Files:**
- Create: `nas/metrics/sql/grafana_ro.sql`

- [ ] **Step 1: Write the role SQL**

```sql
-- One-time: create a read-only role for Grafana to query LiteLLM_SpendLogs.
-- A dashboard can then never write to the gateway's database.
-- Run against the RUNNING gateway Postgres (see README "Deploy" step 2).
-- Replace 'change-me' with the same value as GRAFANA_DB_RO_PASSWORD in the metrics .env.

CREATE ROLE grafana_ro WITH LOGIN PASSWORD 'change-me';
GRANT CONNECT ON DATABASE litellm TO grafana_ro;
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_ro;
-- Future tables (e.g. after a LiteLLM upgrade adds one) are readable too:
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana_ro;
```

- [ ] **Step 2: Commit**

```bash
git add nas/metrics/sql/grafana_ro.sql
git commit -m "feat(metrics): read-only Postgres role SQL for Grafana"
```

---

## Task 10: Deploy the stack on the NAS + verify the gateway (Postgres) path (live)

**Files:** none (live action on the NAS + verification from Arch).

- [ ] **Step 1: Discover the gateway's external network name (on the NAS)**

Run (in the NAS shell or Dockge terminal): `docker network ls | grep -i litellm`
Expected: a network like `litellm_default`. Note the exact name — it goes in the metrics `.env` `GATEWAY_NETWORK`. Also confirm `litellm-db` is on it: `docker network inspect <name> --format '{{range .Containers}}{{.Name}} {{end}}'` should list a `litellm-db` container.

- [ ] **Step 2: Create the read-only Postgres role (on the NAS)**

In the gateway Postgres container, run the SQL from `nas/metrics/sql/grafana_ro.sql` with the real password substituted:
```bash
docker exec -i <litellm-db-container> psql -U litellm -d litellm <<'SQL'
CREATE ROLE grafana_ro WITH LOGIN PASSWORD 'YOUR-RO-PASSWORD';
GRANT CONNECT ON DATABASE litellm TO grafana_ro;
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana_ro;
SQL
```
Expected: `CREATE ROLE` then `GRANT`/`ALTER` lines, no error. (Find the container name with `docker ps --format '{{.Names}}' | grep litellm-db`.)

- [ ] **Step 3: Verify the RO role can read but not write (on the NAS)**

```bash
docker exec -i <litellm-db-container> psql "postgresql://grafana_ro:YOUR-RO-PASSWORD@localhost:5432/litellm" \
  -c 'SELECT COUNT(*) FROM "LiteLLM_SpendLogs";' \
  -c 'INSERT INTO "LiteLLM_SpendLogs"(request_id,call_type,api_key,startTime,endTime) VALUES (1,1,1,now(),now());'
```
Expected: the COUNT returns a number (proves SELECT works and SpendLogs is populated); the INSERT **fails** with `permission denied for table LiteLLM_SpendLogs` (proves read-only). If COUNT is 0, generate gateway traffic first (the Langfuse round-trip `curl` from the gateway runbook works).

- [ ] **Step 4: Place and start the stack (in Dockge)**

Create a stack named `metrics` on a pool path. Place `docker-compose.yml`, `prometheus.yml`, `otel-collector-config.yaml`, the `provisioning/` tree, and a filled `.env` (from `.env.example`: set `GRAFANA_ADMIN_PASSWORD`, `GRAFANA_DB_RO_PASSWORD` = the role password from Step 2, `GATEWAY_NETWORK` = the name from Step 1). Then run `docker compose pull` (bump any tag that 404s to the current stable shown by the registry) and start the stack.

- [ ] **Step 5: Verify Grafana is up (from Arch)**

Run: `curl -s -o /dev/null -w "%{http_code}\n" http://10.63.0.2:3001/api/health`
Expected: `200`

- [ ] **Step 6: Verify both datasources test green (from Arch)**

Run (substitute the admin password):
```bash
for uid in prometheus litellm-pg; do
  echo -n "$uid: "
  curl -s -u admin:YOUR-ADMIN-PW "http://10.63.0.2:3001/api/datasources/uid/$uid/health" | head -c 120; echo
done
```
Expected: both report `"status":"OK"`. The `litellm-pg` OK proves the cross-stack network join + the read-only role both work.

- [ ] **Step 7: Verify the gateway dashboard renders real spend (manual)**

Open `http://10.63.0.2:3001` (login `admin` / admin password) → Dashboards → AISetup → **Gateway Usage & Cost**. Confirm the "By route (30d)" table and "Spend over time" panel show **real** rows from `LiteLLM_SpendLogs` (not "No data").
Expected: real route rows (`main`/`private`/etc.) appear — proves the read-only Postgres path end to end (spec §9.2).

---

## Task 11: Claude Code OTel env snippet (Arch side)

**Files:**
- Create: `arch/claude-code-otel.sh`
- Modify: `arch/README.md`

- [ ] **Step 1: Write the sourceable env snippet**

```bash
# Claude Code -> NAS OTel collector (usage/cost metrics for Grafana).
# Source this from your shell profile so every Claude Code session reports:
#   echo '. ~/path/to/AISetup/arch/claude-code-otel.sh' >> ~/.bashrc
# Works on the Max subscription, independent of the LiteLLM gateway.
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://10.63.0.2:4318
# Claude Code defaults to delta temporality; Prometheus needs cumulative counters.
export OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative
# Shorten the export interval (default 60s) so a single session shows up quickly.
export OTEL_METRIC_EXPORT_INTERVAL=10000
```

- [ ] **Step 2: Verify the snippet is valid shell**

Run: `bash -n arch/claude-code-otel.sh && echo ok`
Expected: `ok`

- [ ] **Step 3: Document it in the Arch README**

Read `arch/README.md` first (`grep -n -i "ollama\|claude\|otel\|##" arch/README.md`), then add a short subsection titled "Claude Code usage metrics (OTel → NAS)" that says: source `claude-code-otel.sh` from the shell profile to ship `claude_code.token.usage` / `claude_code.cost.usage` to the NAS collector at `10.63.0.2:4318`; this is the Max-subscription usage that the Grafana "Claude Code" dashboard charts; it is independent of the gateway (the subscription can't be proxied through LiteLLM). Match the file's existing heading/format style.

- [ ] **Step 4: Commit**

```bash
git add arch/claude-code-otel.sh arch/README.md
git commit -m "feat(arch): Claude Code OTel env snippet -> NAS collector"
```

---

## Task 12: Verify the Claude Code (Prometheus) path end to end (live)

**Files:** none (live action on Arch + verification).

- [ ] **Step 1: Run one Claude Code session with telemetry on (on Arch)**

```bash
source arch/claude-code-otel.sh
claude -p "say hello"
```
Expected: a normal Claude Code reply. With `OTEL_METRIC_EXPORT_INTERVAL=10000`, metrics flush ~10s after the session.

- [ ] **Step 2: Confirm the collector is exposing `claude_code_*` series and CONFIRM THEIR EXACT NAMES (from Arch)**

The collector's `:8889` is internal; query it from inside the stack network:
```bash
ssh nas 'docker exec metrics-prometheus-1 wget -qO- http://otel-collector:8889/metrics | grep claude_code'
```
(Adjust the container name via `docker ps`.) Expected: lines for `claude_code_cost_usage_USD_total`, `claude_code_token_usage_tokens_total`, `claude_code_session_count_total`.
**If the actual names differ** (collector version may suffix units differently), update the `expr` strings in `nas/metrics/provisioning/dashboards/claude-code.json` to match, re-commit, and re-sync the file to the NAS.

- [ ] **Step 3: Confirm Prometheus scraped them (from Arch)**

Prometheus `:9090` is internal; query via Grafana's datasource proxy:
```bash
curl -s -u admin:YOUR-ADMIN-PW \
  "http://10.63.0.2:3001/api/datasources/proxy/uid/prometheus/api/v1/label/__name__/values" \
  | tr ',' '\n' | grep claude_code
```
Expected: the `claude_code_*` series names listed (proves OTLP → collector → Prometheus).

- [ ] **Step 4: Confirm the Claude Code dashboard populates (manual)**

Open `http://10.63.0.2:3001` → Dashboards → AISetup → **Claude Code Usage**. Confirm "Tokens over time by type", "Sessions", and "Estimated cost to date" show data, and the money-shot panel plots the Claude Code series (the gateway series may be flat if no recent gateway traffic).
Expected: panels populate — proves the full OTLP → collector → Prometheus → Grafana path (spec §9.3).

---

## Task 13: Update the root README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Find the observability description**

Run: `grep -n -i "langfuse\|observ\|grafana\|dashboard\|:3000\|:4000/ui" README.md`
Expected: locate the observability sentence(s) (currently Langfuse for traces).

- [ ] **Step 2: Update the observability text**

Edit so observability now reads as two halves: **Langfuse** (`:3000`) for per-request traces, **+ Grafana** (`http://10.63.0.2:3001`) for time-series usage/cost — gateway spend vs the $100 cap and local-vs-cloud split read from `LiteLLM_SpendLogs`, plus Claude Code subscription token/cost via OTel. Keep the surrounding style/length.

- [ ] **Step 3: Verify no stale "Grafana later/deferred" wording remains**

Run: `grep -n -i "grafana" README.md`
Expected: every hit describes Grafana as live/current; none say "later", "deferred", or "planned".

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(readme): add Grafana usage/cost metrics to observability"
```

---

## Task 14: Update the runbook

**Files:**
- Modify: `docs/runbook.md`

- [ ] **Step 1: Find the right section**

Run: `grep -n -i "observ\|langfuse\|grafana\|verify\|##" docs/runbook.md`
Expected: locate the observability/verify section to extend.

- [ ] **Step 2: Add a "Grafana usage metrics" subsection**

Add operational content as concrete commands:
- Health: `curl -s -o /dev/null -w "%{http_code}\n" http://10.63.0.2:3001/api/health` → `200`.
- Datasource health (both green): the Step-6 loop from Task 10.
- The two data paths: **gateway** = Grafana reads `LiteLLM_SpendLogs` via the read-only `grafana_ro` role over the gateway stack's **external** Docker network (`GATEWAY_NETWORK` in the metrics `.env`); **Claude Code** = Arch sources `arch/claude-code-otel.sh` → collector `:4318` → Prometheus → Grafana.
- The CC env facts: temporality must be `cumulative`; metrics are `claude_code_cost_usage_USD_total` / `claude_code_token_usage_tokens_total` / `claude_code_session_count_total`.
- Failure modes: Grafana/Prometheus down does **not** affect the gateway or Langfuse (separate stacks); if the gateway dashboard shows "No data", check the `grafana_ro` role + `GATEWAY_NETWORK`; if the Claude Code dashboard is empty, confirm the env is sourced and the collector `:4318` is reachable from Arch.

Match the runbook's existing heading/command-block style.

- [ ] **Step 3: Commit**

```bash
git add docs/runbook.md
git commit -m "docs(runbook): operate + verify Grafana and both data sources"
```

---

## Task 15: Decision journal entry

**Files:**
- Create: `docs/journal/2026-05-22-grafana-usage-metrics.md`

- [ ] **Step 1: Write the journal entry**

Write a dated entry (Obsidian-friendly) capturing the **why and what was rejected**, not just the what:
- **Decision:** Grafana on the NAS now (not later), so a real usage/cost baseline accrues before the n8n phase and the talk. Two data sources: gateway cost from `LiteLLM_SpendLogs` (Postgres) + Claude Code subscription usage via OTel/Prometheus.
- **The key finding:** LiteLLM's Prometheus `/metrics` endpoint is **Enterprise (paid)** — the earlier "LiteLLM emits Prometheus for free" assumption was wrong. So gateway data is read straight from the Postgres SpendLogs LiteLLM **already writes** (free, and the *actual* billing record). Prometheus is introduced **only** for Claude Code, which has no other home.
- **Why the OTel collector** (vs Claude Code → Prometheus OTLP receiver directly): a bursty CLI emitting counters that reset per session is rough against Prometheus's pull model; the collector normalizes temporality (Claude Code defaults to **delta**, set to **cumulative** for us), decouples the CLI from Prometheus, and is the single egress that can later also forward Claude Code **traces** to Langfuse. (Anthropic's own monitoring docs use a collector.)
- **Rejected:** LiteLLM Enterprise Prometheus (recurring cost for a solo setup); Grafana → Langfuse ClickHouse for gateway cost (couples to Langfuse's internal schema; SpendLogs is purpose-built); collector-less direct OTLP→Prometheus (temporality/burst pain).
- **Cross-stack access:** Grafana joins the gateway stack's `external` Docker network and queries `litellm-db` by service name through a dedicated **read-only** `grafana_ro` role — a dashboard can never write to the billing DB, and the live gateway is untouched.
- **The money-shot:** a single mixed-datasource panel plots Claude Code estimated-$ (flat-rate Max subscription) against gateway actual-$ (API) — the "what would this have cost on pay-as-you-go" comparison, now accruing history.
- **Deferred (noted not built):** Claude Code *traces* to Langfuse via the same collector; n8n; nginx-proxy-manager fronting Grafana; alerting.

- [ ] **Step 2: Commit**

```bash
git add docs/journal/2026-05-22-grafana-usage-metrics.md
git commit -m "docs(journal): why Grafana usage metrics on the NAS (the SpendLogs path)"
```

---

## Self-Review Notes

- **Spec coverage:** §3 components (collector/prometheus/grafana, ports, networks) → Tasks 1,2,7,10; §3 read-only role + external network → Tasks 9,10; §4 Claude Code env → Task 11; §5 provisioning-as-code + `.env`/secrets + retention → Tasks 3,4,7,8; §6 LAN-only exposure (only `:3001` + `:4318` published) → Task 7; §7 both dashboards incl. money-shot → Tasks 5,6; §8 deploy steps → Task 10; §9 verification (gateway real spend §9.2, CC end-to-end §9.3) → Tasks 10,12; §10 docs → Tasks 11,13,14,15. All spec sections map to a task.
- **Open items from spec §11 resolved during planning:** CC env var names + metric names (verified vs current docs, see "Verified facts"); `LiteLLM_SpendLogs` columns + `model_group` as route (verified vs `schema.prisma`); image tags pinned (Task 7, gated by `docker compose pull` in Task 10); collector exporter = `prometheus` scrape (Task 1). The two genuinely deploy-time unknowns — the **exact external network name** and the **exact Prometheus series names** — are explicit discover/confirm-and-adjust steps (Task 10 Step 1, Task 12 Step 2), not placeholders.
- **Type/name consistency:** datasource UIDs `prometheus` + `litellm-pg` are defined in Task 3 and referenced identically in the dashboard JSON (Tasks 5,6); `GRAFANA_DB_RO_PASSWORD` flows `.env` (Task 8) → compose env (Task 7) → `$GRAFANA_DB_RO_PASSWORD` in the datasource (Task 3) → the role password (Task 9/10); `GATEWAY_NETWORK` flows `.env` (Task 8) → `external` network (Task 7) ← discovered name (Task 10). Series names `claude_code_*_total` are used identically in Task 6 and verified in Task 12.
- **Deferred items stay deferred:** no CC-traces-to-Langfuse, n8n, NPM, or alerting tasks — only noted as future in the journal (Task 15), per spec non-goals.
