# 2026-05-22 — Grafana usage metrics on the NAS (the SpendLogs path)

## The decision

**Grafana goes onto the NAS now**, not after n8n, not after the talk. Two data
sources: gateway spend read directly from `LiteLLM_SpendLogs` in Postgres, and
Claude Code subscription usage via an OTel collector feeding Prometheus. This is
the quantitative layer that complements Langfuse's qualitative per-request traces:
Langfuse answers "what happened in this exchange?", Grafana answers "how much has
this cost me across time, by route and by tool?"

The reason to do it now is accrual. Every day Grafana isn't running is a day
without a data point. The talk needs a real spend history — a graph that shows the
hybrid routing working over weeks, not a synthetic demo. Starting now means the
history will exist by the time I need it.

## The key finding that changed the plan

The original design note (and the Langfuse journal entry from the same day) assumed
**LiteLLM emits Prometheus metrics for free** — a natural assumption because LiteLLM
does have a `/metrics` endpoint and Grafana+Prometheus is the obvious stack. That
assumption was wrong.

LiteLLM's `/metrics` endpoint is an **Enterprise (paid) feature**. The open-source
tier does not expose it. Discovering this midway through the plan forced a rethink:
the Prometheus path for gateway cost data is simply unavailable without a
subscription.

This turned out not to matter, because LiteLLM already writes every request to a
`LiteLLM_SpendLogs` table in its own Postgres database — and that table is the
*actual* billing record. It contains the model, route, token counts, and cost for
every request. Reading from it directly is more reliable than a Prometheus counter
(which could drift or restart), costs nothing, and needs only a read-only Postgres
role (`grafana_ro`) granted `SELECT` on that table. The gateway stack is completely
untouched; Grafana joins its external Docker network and queries `litellm-db` by
service name as a read-only observer.

Prometheus is still in the stack — but only for Claude Code, which has no Postgres
table of its own.

## Why an OTel collector for Claude Code

Claude Code has a native OTel export that works on the subscription (bypassing the
gateway entirely — the subscription auth can't be proxied). The naive path would be
to point it straight at a Prometheus OTLP receiver and skip the collector layer.
Three reasons that doesn't work well enough:

1. **Temporality mismatch.** Claude Code defaults to `delta` temporality: each
   session emits counts relative to the session start, not accumulated totals.
   Prometheus expects `cumulative` counters that only go up; left as delta, the
   graphs would sawtooth back to zero at every new session. We fix this at the
   source — `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative` in
   `arch/claude-code-otel.sh` makes Claude Code itself emit cumulative — so the
   collector doesn't need a conversion processor; it just receives clean cumulative
   metrics and re-exposes them. (This is also why the collector path matters: it is
   the natural place to normalise or transform if a future exporter can't be told to
   emit cumulative directly.)

2. **Decoupling.** A CLI tool that starts and stops repeatedly is a rough fit for
   Prometheus's pull model. The collector buffers and exposes a stable scrape target
   regardless of whether a Claude Code session is running at scrape time.

3. **Future extensibility.** The collector is the single egress point from Arch.
   Adding Claude Code *traces* to Langfuse later is one extra exporter block — the
   infrastructure already exists, pointed at a new destination. Without the
   collector, each new destination requires reconfiguring Claude Code itself.

Anthropic's own monitoring documentation uses a collector in the recommended setup,
which is a reasonable signal that this is the right layer to add.

## What was rejected and why

**LiteLLM Enterprise Prometheus.** A recurring subscription cost for a solo setup
to get one metric endpoint is not a reasonable trade. The SpendLogs path is free and
arguably more accurate.

**Grafana → Langfuse ClickHouse for gateway cost.** Langfuse's ClickHouse database
stores traces, not spend summaries — querying it for cost figures would couple to
Langfuse's internal schema, which changes between versions and isn't documented as
a stable external interface. `LiteLLM_SpendLogs` is purpose-built for this.

**Collector-less direct OTLP from Claude Code to Prometheus.** The temporality and
burst problems above. For a single metric series it might work around the edges, but
the result would be fragile and the future extensibility would be lost.

**Single Grafana panel for everything.** The two datasources (Postgres and
Prometheus) are genuinely different in kind — one is a relational billing record,
the other is a time-series counter. Mixing them in a single datasource would require
a transformation layer that adds complexity without benefit. Two datasources in one
dashboard is straightforward in Grafana and the standard pattern.

## Cross-stack access without touching the live gateway

The live gateway (LiteLLM + Postgres) is in its own Docker network. Adding Grafana
as a *reader* required:

- Creating a `grafana_ro` Postgres role with `SELECT` on `LiteLLM_SpendLogs` — a
  single `GRANT` statement, no schema changes, no gateway config changes.
- Defining a named external network in the gateway stack's compose, then having the
  metrics stack join that network (`external: true`, name set via `GATEWAY_NETWORK`
  in the metrics `.env`).

From that point Grafana can reach `litellm-db:5432` by service name as if it were
on the same compose network — which it is. The gateway service itself never sees the
connection; only the Postgres container does, and only for reads.

This pattern means the live gateway stack needs no modification to support the
metrics layer. The metrics stack is a pure add-on observer. Grafana cannot write to
the billing database by construction (the role has no `INSERT` or `UPDATE`).

## The headline: subscription break-even, not a cross-workload "vs"

The original design proposed a single mixed-datasource panel plotting Claude Code
estimated cost against gateway actual cost. On first contact with the live data we
**dropped it** — it compared two unrelated workloads (Claude Code coding sessions vs
the Hermes gateway's routes) and pitted a *fixed* subscription against *per-request*
spend, so the "vs" answered nothing. The confusion was the tell.

What the question actually is: *"if I paid pay-as-you-go API prices instead of the
flat Max subscription, what would my Claude Code usage have cost?"* `claude_code.cost.usage`
is exactly that shadow price (Anthropic's PAYG estimate for the tokens used). So the
headline became a **break-even** chart: the trailing-30-day estimated API cost as a
line, with two horizontal reference lines at the **$100 (Max 5×)** and **$200 (Max 20×)**
subscription tiers. When the line sits above $100, the last 30 days of usage already
exceed the cheaper tier's price — the subscription is earning its keep; above $200 it
beats the larger tier too. Supporting panels break token volume into all four types
(input / output / cacheRead / cacheCreation — kept separate because cache reads/writes
are priced very differently and would otherwise wreck the estimate) and show usage by
model, plus per-range tables.

A subtle data-shape lesson cost a debugging round and is worth recording. Every
Claude Code session carries a distinct `session_id` label, so each session is its
**own** Prometheus series. Two consequences bit in sequence:

1. A naïve `sum(claude_code_cost_usage_USD_total)` reads ≈0 most of the time, because
   each per-session series goes **stale** ~5 minutes after the session ends and drops
   out of the instantaneous sum.
2. The obvious fix — `increase(metric[window])` — *also* read 0. A short `claude -p`
   run exports its cumulative total essentially **once**, so Prometheus only ever sees
   that series at its final, constant value and never observes the climb from zero.
   `increase()` = last − first = 0 on a flat series. (The tell on the dashboard: cost
   and tokens showed 0 while the *session count* — which counts series, not their
   increase — correctly showed 1.)

The correct aggregation for this bursty, one-series-per-session shape is
`sum(max_over_time(metric[window]))`: take each session series' value (its final
cumulative total) and sum across all sessions in the window. This is the opposite of
the usual Prometheus counter instinct (`rate`/`increase`), and it only became obvious
against real data — a good reminder that the metric's *delivery pattern*, not just its
type, decides the query.

This break-even view starts accruing real history from the day the stack is deployed.
The longer it runs, the more convincing it is for the talk.

## Deferred — not built, only noted

- **Claude Code traces to Langfuse via the same collector.** The architecture
  supports it (one more exporter block in the collector config). Not added now
  because Langfuse already receives gateway traces and Claude Code traces would be a
  separate project to understand the data model. Revisit after the stack has been
  running.
- **n8n** on the HP t640. Next infra phase. Its LLM calls will trace and cost-cap
  automatically via the gateway.
- **nginx-proxy-manager fronting Grafana.** Grafana is LAN-only for now — direct IP
  access (`10.63.0.2:3001`) is sufficient. NPM adds a hostname and TLS; deferred
  until it's needed.
- **Alerting.** Grafana can fire alerts when spend approaches the $100 cap. Not
  configured yet; the LiteLLM hard cap is the safety net. Revisit once the dashboards
  have enough history to set sensible thresholds.

## Status

Stack committed to `nas/metrics/`. The OTel snippet for Arch is in
`arch/claude-code-otel.sh`. Both await live deployment on the NAS (a separate step
requiring manual placement of the stack dir on a pool path via Dockge, plus running
the `CREATE ROLE` one-liner against the gateway Postgres). Deployment and
verification of both data paths is tracked as the next task.
