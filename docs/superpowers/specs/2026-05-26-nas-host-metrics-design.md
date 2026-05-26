# NAS Host Metrics on Grafana — Design Spec

- **Date:** 2026-05-26
- **Status:** Approved (design phase) — implementation deferred to a later session
- **Owner:** DerTechie
- **Relationship to other specs:** Adds a third dashboard to the existing Grafana stack from [`2026-05-22-grafana-usage-metrics-design.md`](2026-05-22-grafana-usage-metrics-design.md). The Grafana + Prometheus + OTel-collector infrastructure is already deployed and live; this spec only adds **a new data source (NAS host metrics) and a new dashboard**, not new infrastructure.

## 1. Purpose & goals

Make the **"do I need more NAS RAM or a dedicated server?"** decision data-driven instead of vibes-driven. Today (2026-05-26) the NAS runs LiteLLM+pg, Langfuse+ClickHouse, Grafana+Prometheus+OTel, Borgmatic, and the TrueNAS apps (Paperless, etc.) on **32 GB DDR5**, with Forgejo about to land. We can guess the headroom; we'd rather see it on a chart over weeks.

Two questions the dashboard must answer:

1. **Is the NAS pressured right now, and how is the trend?** Host CPU, RAM (used vs ZFS ARC vs free), swap, load avg, disk I/O, network throughput — all over time, not point-in-time.
2. **If it's pressured, by what?** Per-container CPU and memory, so "Langfuse is eating 6 GB" vs "ClickHouse is the pin" is a panel, not a guess.

**Success criteria:**
- A NAS dashboard reachable on the existing Grafana (`http://10.63.0.2:3001`).
- Real time-series data for host CPU/RAM/disk/network and per-container CPU/RAM.
- ZFS ARC visibility (size, hit ratio, target) at minimum — ARC is the silent RAM consumer on a NAS and the missing piece if we only watch "used vs free".
- Total session time: target **~65–75 min** on the happy path, **hard ceiling 140 min**, with the explicit cut points in §2 triggering before the ceiling rather than after. The point is "don't repeat the last Grafana session" — if either ceiling is at risk, ship less.
- Decision + rejected options captured in `docs/journal/`.

**Non-goals (deferred, noted not built):**
- Alerting/notifications on thresholds (just dashboards; eyes-on first, alerts later).
- SMART disk health metrics (TrueNAS already alerts on these in its own UI).
- UPS / power metrics.
- Long-form capacity planning model.

## 2. Time discipline (the binding constraint)

The user has explicitly said: **no multi-hour task**. Last Grafana stand-up took hours; that was the full OTel+Prometheus+Grafana+provisioning+CC-dashboard build. **That work is done.** This spec adds only a data source and a dashboard on top.

Honest budget:

| Step | Time if smooth | Time if TrueNAS-permission friction |
|---|---|---|
| Phase 0 — investigate native options | 15 min | 15 min |
| Phase 1 — deploy collection (one of two branches) | 20–30 min | 60–90 min |
| Phase 2 — Prometheus scrape config + reload | 5 min | 10 min |
| Phase 3 — import + adjust dashboard | 15 min | 15 min |
| Phase 4 — verify | 10 min | 10 min |
| **Total** | **~65–75 min** | **~110–140 min** |

**Hard cut points** (if the budget blows up, stop here and ship a smaller dashboard now):

- After Phase 1 — if native scrape works, **stop** (Tier-1+2+3 all included for free). If we fell into the exporter branch and Tier-1 (`node_exporter`) cost > 60 min, **defer Tiers 2–3** to a third session and ship a "host only" dashboard now.
- If TrueNAS Scale's hardened defaults block `pid=host` / privileged mounts for the exporters in the exporter branch, **stop and journal it** rather than chase a workaround live — that's its own investigation.

## 3. Phase 0 — investigate TrueNAS native metrics (the branch point)

The big unknown that bifurcates the design: **does TrueNAS Scale expose system metrics in a Prometheus-scrapable format out of the box?** TrueNAS Scale's "Reporting" UI is backed by *some* metrics collector (current understanding is netdata, replacing earlier graphite/collectd, but **this must be verified at the running version, not assumed**). Netdata exposes `/api/v1/allmetrics?format=prometheus` for OpenMetrics-format scraping when enabled. If TrueNAS's bundled netdata exposes that endpoint on an interface reachable from the Dockge containers, we get host + ZFS + network + disk + per-container metrics from **one already-running source**, no new exporters.

**Phase 0 procedure (15 min, before deciding which branch to build):**

1. Identify the TrueNAS Scale version actually running on the NAS (`midclt call system.version` or in the UI).
2. Check whether `netdata` (or equivalent) is running on the host (`systemctl status netdata`, or `ss -tlnp | grep -E '19999|9100|2003'` — common ports: netdata `:19999`, node_exporter `:9100`, graphite-exporter `:2003`).
3. If a metrics endpoint is found, hit it from inside the metrics stack network: `docker exec <prometheus> wget -qO- http://<host-ip>:<port>/api/v1/allmetrics?format=prometheus | head` — verify Prometheus-format output.
4. Check TrueNAS Scale's "Advanced Settings" UI for **"Graphite Remote"** / **"Metrics export"** — TrueNAS has historically had a Graphite remote-write option; if present, that's another route (Prometheus has a graphite-exporter that accepts the carbon line protocol).
5. **Decision rule:** if Phase 0 surfaces *any* working native endpoint covering host + ZFS, take **Branch A** (native). Otherwise take **Branch B** (exporters).

This is the only "research" task in the spec; everything after Phase 0 is execution.

## 4. Architecture

### Branch A — native scrape (if Phase 0 confirms it works)

```
TrueNAS host (10.63.0.2)              Existing metrics stack (Dockge)
─────────────────────────             ───────────────────────────────
netdata (or equivalent)   ──scrape──> prometheus  ──PromQL──> grafana
:<native-port>/...?format=prometheus                          (new NAS dashboard)
```

- **New components:** none.
- **Config changes:** one new `scrape_config` in `nas/metrics/prometheus.yml`; one new dashboard imported into Grafana.
- **Cost:** ~30–45 min total.
- **Coverage in one shot:** host CPU/RAM/disk/network/load + ZFS (netdata's ZFS plugin) + per-container (netdata's cgroups plugin).

### Branch B — deploy exporters (fallback)

Three small containers deployed as a **new Dockge stack** (`nas/host-metrics/`), kept separate from the existing `nas/metrics/` stack so the new failure surface doesn't co-locate with the Grafana stack.

```
host (/, /proc, /sys mounted ro) ──> node_exporter   :9100  ─┐
ZFS (host) via /proc/spl/kstat/  ──> zfs_exporter    :9134  ─┼──> prometheus ──> grafana
docker.sock (ro) + cgroups       ──> cadvisor        :8080  ─┘
```

- **New containers:** `prom/node-exporter`, `prometheus-community/zfs_exporter` (or `pdf/zfs_exporter` — pin in plan), `gcr.io/cadvisor/cadvisor`.
- **Config changes:** three new `scrape_config`s in `prometheus.yml`; one new dashboard (assembled from imported community dashboards or built from panels).
- **Cost:** ~60–90 min total, **most of the risk is here** — exposing `pid=host` and `/`/`/proc`/`/sys` to a container on TrueNAS Scale needs the container to be privileged or have specific capabilities, and TrueNAS Scale's app sandboxing can object. Dockge gives us raw docker-compose which sidesteps the Apps system, but the host's apparmor/SELinux posture still applies.
- **Coverage:** same as Branch A (host + ZFS + per-container), assembled from three sources.

### Why this shape (rejected alternatives)

- **Use TrueNAS Scale's built-in "Reporting" UI and stop there** — rejected: lives in TrueNAS's own UI silo, doesn't sit next to the gateway/cost/Claude Code dashboards, no long retention for trend analysis. The whole reason to land this in Grafana is **one pane of glass with consistent retention** alongside the metrics the Borg/LiteLLM/CC dashboards already provide.
- **Push metrics from the host via the existing OTel collector** — rejected for now: the OTel collector is already deployed and *could* receive host metrics via its `hostmetrics` receiver, but that requires running the collector **on the host**, not as a container. The current collector runs in Docker on the NAS and only sees container-internal stats. Adding a host-mode collector is a bigger lift than a one-shot exporter. Reconsider if we ever want unified OTel ingest for everything.
- **Wire host metrics into Langfuse** — rejected: Langfuse is for traces and request-level observability, not infrastructure metrics.
- **Per-container metrics via Docker's experimental `/metrics` endpoint** instead of cAdvisor — rejected for Branch B: Docker's built-in endpoint is daemon-level (engine internals), not per-container resource usage. cAdvisor is the right tool for per-container CPU/RAM.

## 5. Tiered scope (what gets built, what gets cut)

This is the order things land in, so we can stop after any tier and have something useful.

| Tier | What | Branch A cost | Branch B cost | Cut policy |
|---|---|---|---|---|
| **T1** | Host CPU, RAM, swap, load, disk I/O, network, filesystem fill | Free (included in native scrape) | `node_exporter` + dashboard 1860 — 30–45 min | Always ship T1. This is the minimum that answers "is the NAS pressured?" |
| **T2** | ZFS ARC size + hit ratio, pool used %, scrub status | Free (included in native scrape) | `zfs_exporter` + dashboard 10995 / 17048 — +15–20 min | Cut if Branch B + T1 is already >60 min. ARC visibility is high-value but `node_exporter`'s `meminfo` already shows total RAM pressure; ZFS-specific can wait. |
| **T3** | Per-container CPU/RAM (Langfuse / ClickHouse / Postgres / Grafana / Borgmatic / Paperless etc., labeled) | Free (included in native scrape) | `cadvisor` + dashboard 14282 — +15–20 min | Cut if budget blown. **This is the highest-value tier for the original bottleneck question** — without it, T1+T2 tell you "the NAS is hot" but not "which service to offload". If we must drop T2 *or* T3, drop T2. |

**Recommendation:** ship T1+T2+T3 in one session if Phase 0 lands on Branch A (it's all one source, all free). If Branch B, ship T1+T3 in this session and defer T2 to a tiny follow-up — that gives both halves of the bottleneck answer (pressure + culprit) and skips the ZFS-specific tier that overlaps with `meminfo`.

## 6. Configuration & provisioning

Pattern matches the existing metrics stack:

- **Prometheus config** — edit `nas/metrics/prometheus.yml` to add the new scrape job(s). Reload via container restart or `curl -X POST http://prometheus:9090/-/reload` if `--web.enable-lifecycle` is on (check current flags; the existing compose doesn't set it).
- **Grafana dashboards** — add new JSON files under `nas/metrics/provisioning/dashboards/` so they provision on Grafana restart (consistent with the gateway and CC dashboards already there). Source dashboards from grafana.com by ID and prune panels we don't need before committing.
- **Branch B only — new Dockge stack** `nas/host-metrics/` with its own `docker-compose.yml` and `.env.example`. Reuse the `metrics` Docker network (set as `external: true`) so Prometheus can scrape by service name without IP plumbing.
- **Secrets** — none expected for any of these exporters; if Branch A's native endpoint requires auth, journal that finding and revisit.

## 7. Network exposure

LAN-only, no published ports needed beyond what already exists. Exporters scraped on the internal Docker network. Grafana stays on `:3001` (already published).

## 8. Verification (evidence before "done")

1. **Phase 0 result captured.** Whichever branch was taken, the journal entry includes the specific command output that justified the choice (a `curl` against the native endpoint, or the negative result that forced Branch B).
2. **Prometheus targets healthy.** `http://10.63.0.2:3001` → Prometheus data source → "Targets" page shows the new job(s) `UP`. (Or query Prometheus directly: `curl http://prometheus:9090/api/v1/targets`.)
3. **Real data on the dashboard.** NAS dashboard loads, all panels show ≥ 5 minutes of real data (not "no data"). Pick three numbers to sanity-check against `top` / `free -h` on the host: load avg, used RAM, one network interface's RX/TX rate.
4. **ZFS ARC visible (T2 or Branch A).** ARC size in GB and ARC hit ratio render and look plausible (ARC commonly ~50% of total RAM on a NAS under steady state).
5. **Per-container visible (T3 or Branch A).** Container list panel shows the expected ~10–15 containers across the gateway / langfuse / metrics / borg / TrueNAS-apps stacks with non-zero memory.

If any of 1–5 fails, the task is **not done** regardless of how much time was spent. Update the spec or journal with what blocked, don't paper over.

## 9. Documentation (same change)

- **Branch A:** append a "Scraping TrueNAS native metrics" section to `nas/metrics/README.md` documenting the endpoint, the scrape config, and how to verify.
- **Branch B:** new `nas/host-metrics/README.md` covering the three exporters and any TrueNAS-permission tricks needed.
- **Root `README.md`:** add the NAS dashboard to the observability summary.
- **`docs/runbook.md`:** add "NAS host metrics" under operate/verify.
- **Journal entry** `docs/journal/<implementation-date>-nas-host-metrics.md`: Phase 0 finding (which branch and why, with command evidence), any TrueNAS-permission gotchas, dashboard IDs imported, what was deferred.

## 10. Open items to verify during implementation

- **Phase 0** itself — this is the load-bearing unknown. Don't proceed past it until the branch choice is justified.
- Exact TrueNAS Scale version and whether its bundled metrics collector is netdata, graphite/collectd, or something newer.
- Whether the native endpoint (if present) is bound to all interfaces or just `127.0.0.1` (the latter is reachable from host-network containers but not bridge-network containers — would force `network_mode: host` on the Prometheus container, which has its own knock-on effects).
- Pin image tags for whichever exporters land (`prom/node-exporter:v1.X`, `prometheus-community/zfs_exporter:vX.Y`, `gcr.io/cadvisor/cadvisor:v0.Z`).
- Confirm `--web.enable-lifecycle` on Prometheus — if not, reload is a container restart (5 s blip on the existing dashboards, acceptable).
- Dashboard IDs are sourced from `grafana.com/grafana/dashboards/` — verify each is still maintained at implementation time and prefer the most recently updated equivalent if a listed ID has bit-rotted.

## 11. Out of scope (explicit non-decisions)

This spec **does not** decide between RAM-upgrade-vs-dedicated-server. Its sole purpose is to put data in front of that decision. The decision itself is a separate journal entry once enough time-series has accrued (suggest: 2–4 weeks of dashboard observation before deciding).
