# 2026-05-22 — Langfuse v3 observability on the NAS

## The decision

Self-hosted **Langfuse v3** goes onto the NAS, wired to the LiteLLM gateway via
an async `success_callback: ["langfuse"]`. This is Phase 3 of the build, and
intentionally comes after the gateway is stable: observability is a diagnostic
layer, and there's nothing to observe until requests are actually flowing.

## Why now, and why not later

The LiteLLM spend dashboard has been good enough for aggregate cost tracking, but
it has a structural limit: it only shows totals and summaries — there is no
way to drill into a single request and see what prompt went in, what came back,
and what that exchange actually cost. That gap matters in two concrete ways.

First, for the **triage advisor**: one of the key feedback loops I wanted to
build is "was the routing recommendation right?" That question is fundamentally
prompt-level, not aggregate. If the advisor said "use `private`, this is routine"
and the result was bad, I want to pull up that exact exchange and see why. A chart
can't do that.

Second, for the **talk**: a concrete, real trace — this request, this model, this
latency, this cost — is a far better story beat than an aggregate spending graph.
The design document from 2026-05-20 flagged Langfuse as Phase 3; the reason it
goes in now rather than later is that the earlier it runs, the more real traces
accumulate, and the better the talk material.

## Why Langfuse and not the alternatives

This is where the decision took time, because several options looked plausible on
first read.

**Arize Phoenix** was the lightest-weight contender — roughly one container, quick
to start, and it does LLM observability. I dropped it because its framing is
ML-evaluation flavored: A/B evals, embedding drift, model quality metrics. That's
genuinely useful if you're iterating on model quality, but the use-case here is
cost-bounded routing visibility for a small founder setup, and the Phoenix UI
doesn't expose that story as clearly. It would have worked technically; it just
wouldn't have made the talk.

**Grafana-only** was the most tempting architectural choice because LiteLLM
already exposes Prometheus metrics and I already have Grafana experience. Beautiful
aggregate dashboards — latency percentiles, spend by model, request rates — are
absolutely within reach. But Grafana fundamentally cannot show an individual
prompt/response. That's not a configuration gap; it's a category mismatch.
Prometheus metrics are aggregated by design. The per-request drill-down I need
doesn't exist in that stack, full stop. That disqualified it as the sole
observability solution, though a future deferred Grafana dashboard for spend
aggregates still makes sense.

**Helicone** is a proxy itself — it sits in the request path and records traffic.
Stacking it with LiteLLM means two proxies, two configurations, and two points of
failure in the hot path. LiteLLM already *is* the proxy; the right pattern is to
push observability data from LiteLLM async, not to chain another proxy before or
after it.

**LangSmith and Datadog cloud** are both polished and would have been easy to set
up. The problem is content: full prompt/response leaving the LAN is not acceptable
when the `private` route exists precisely because some queries shouldn't touch
external infrastructure. Self-hosting wasn't just a preference here — it was the
deciding constraint. Those options were dropped the moment I confirmed they require
content to leave the LAN.

**Langfuse** is the only option that combines per-request drill-down, native
LiteLLM callback integration, cost dashboards, and a recognizable level of polish —
and can run entirely on local hardware. The LiteLLM side is a single env-var
change (`success_callback: ["langfuse"]`); the async fire-and-forget pattern means
it adds no latency to the hot path.

## The footprint surprise

The original design notes from 2026-05-20 implied Langfuse was a modest addition.
In practice, **Langfuse v3 is a mandatory 6-container stack**: web, worker,
Postgres, ClickHouse, Redis, and MinIO (or an S3-compatible store). ClickHouse,
Redis, and an object store became required in v3; the old Postgres-only deployment
is end-of-life.

That's more infrastructure than I expected to accept. The reason I accepted it
anyway: single-user traffic runs far below the load Langfuse was designed for, and
the NAS has enough headroom. The one concrete mitigation is a **2 GB cap on
ClickHouse** — configured in the compose file — to protect the other NAS services
(immich, jellyfin, paperless) from an analytics store that could otherwise grow
unbounded if left to its defaults.

## Why Langfuse stays on the NAS while n8n goes to the t640

The next phase after observability is automation via n8n. It will not share a host
with Langfuse, and this is intentional.

Langfuse is RAM-hungry: ClickHouse wants memory and the stack as a whole is not
lightweight. It belongs on the NAS, which has 32 GB RAM, persistent volumes, and
the same backup regime that already covers immich and paperless. Data custody for
prompt logs belongs there too.

n8n is effectively the opposite profile: light on CPU and RAM, heavy on I/O and
network calls, tolerates an occasional restart fine. It fits the HP t640 thin
client: weak CPU, low RAM, but always-on and cheap. There's no value in putting n8n
on the NAS when it would just consume RAM that ClickHouse could use.

There's also a structural benefit: because **the LiteLLM gateway is the single
chokepoint for all LLM calls**, any future LLM usage from n8n is traced and
budget-capped automatically the moment n8n is pointed at the gateway. n8n needs no
Langfuse integration of its own, and can't accidentally escape the €100 cap. That
architecture pays for itself.

## Privacy

Self-hosting is the reason the cloud observability tools were non-starters, but
it's worth saying explicitly: the `private` route exists because some queries
should not leave the LAN. An observability tool that phones home with prompt
content would undermine the purpose of that route entirely. Langfuse on the NAS
means the full prompt/response for every model — including the privacy-motivated
local path — stays on local infrastructure. That was a hard requirement, not a
preference.

## Claude Code is a side-case that can't proxy through the gateway

A natural question: can Claude Code's own activity be traced through Langfuse via
the LiteLLM gateway? In short, no — not via the gateway.

The Claude Code subscription authenticates via OAuth, not an Anthropic API key.
That model of authentication can't be proxied through `ANTHROPIC_BASE_URL`; only
key-based API access is routable. So Claude Code won't appear in LiteLLM or
Langfuse traces from the gateway.

However, Claude Code has its own **native OpenTelemetry export** that works on the
subscription. It can report estimated cost and exact token counts and ship traces
directly to Langfuse's OTLP endpoint — bypassing the gateway entirely, which is
fine since Claude Code isn't LiteLLM traffic anyway. The "subscription vs. API
spend" comparison (a useful metric for the talk) is a deferred Grafana dashboard
idea: combine the OTEL-derived subscription spend with LiteLLM's Prometheus output
and show the full picture side by side.

## Status

**Repo artifacts are done**: the Langfuse v3 compose stack and env template are in
`nas/langfuse/`, the gateway's `success_callback` wiring is in
`nas/litellm-config.yaml`, and docs are updated. **The live cutover is not yet
done and has not been verified.** Completing it requires placing the stack
directory on a NAS pool path (TrueNAS's system dataset is read-only), starting the
stack in Dockge, confirming all six containers reach a healthy state, and running
an end-to-end trace round-trip — a real model call that produces a visible trace in
the Langfuse web UI. That final step is pending.
