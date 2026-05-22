# 2026-05-22 — The headline cost panel lied: instant vs range queries

## What happened

The "Estimated API cost (30d)" headline read **$12.7** while the "Usage by model"
table *on the same dashboard, same time range* summed to **$17.9**. Two panels,
one metric, two answers. The question was whether to trust Anthropic's number.

## The two-layer verification (and the trap)

First I reconciled the **metric** itself: pulled `claude_code_cost_usage_USD_total`
per `session_id` and recomputed each session's cost from its own token counts at
current Opus 4.7 list prices ($5 in / $25 out / $0.50 cacheRead / $6.25 5-min
cacheCreation). It matched to ~0.1% — the biggest session was $12.2213 reported vs
$12.2235 computed. The metric is accurate.

I then **prematurely concluded "trust the dashboard."** That was wrong, and the
pushback ("you verify too fast") re-opened it. Verifying the *counter* is not the
same as verifying the *panel that renders it*.

## The real bug: range query where an instant query was needed

The three top-row **stat** panels (cost, tokens, sessions) had targets with no
`"instant": true`. Grafana therefore ran them as **range queries** — evaluating
`sum(max_over_time(metric[30d]))` at every step-aligned timestamp across 30 days,
then reducing with `lastNotNull`. Against our **bursty one-series-per-session**
data shape, that expression is *erratic per step*: adjacent steps swing between
~$0.1 and ~$17.4. `lastNotNull` grabs whatever the final (often stale, low) step
happens to be — $12.7 in the screenshot. The **instant** evaluation of the exact
same expression was stable at **$18.0**, matching the per-session sum and the
`instant:true` table panels below.

So the same data-shape lesson that earlier killed `increase()` in favour of
`max_over_time()` bit again one level up: `max_over_time` is correct, but it must
be evaluated **once at now (instant)**, not swept across a range and reduced.

## Fix

Added `"instant": true` to the three stat-panel targets in
`nas/metrics/provisioning/dashboards/claude-code.json` (ids 1–3). The break-even and
trend panels stay range queries — they are legitimately over-time. Takes effect on
the next Grafana dashboard provisioning reload on the NAS.

## What was rejected

- **"Anthropic under-reports by 24%."** Disproved — the metric is exact to list price.
- **Recomputing the cost panel from the token panel.** They are independent counters;
  the original $12.70-vs-$16.65 confusion came from pairing one session's cost with
  ~30-day aggregate tokens. Only reconcile within a single `session_id`.
- **Changing the reduction (`lastNotNull` → `last`/`mean`).** Doesn't fix it; the
  range evaluation itself is the wrong tool. `instant: true` is the fix.
