# n8n standup on the NAS — design

**Date:** 2026-05-22
**Status:** Design approved. Gateway round-trip already verified live (see §6).

## 1. Goal

Stand up **n8n** on the NAS (`10.63.0.2`), running and persistent, reachable on
the LAN, with the single hard requirement that it can make LLM calls **through
the LiteLLM gateway**. Nothing else. Workflows and integrations are built later
from the n8n UI — they need no spec.

This is the automation host for the setup: business automations (lead handling,
content, scheduled research, admin ops) and any "agent orchestration" (cron /
webhook triggers of LLM jobs) are all built *on top of* this foundation, in
later specs or directly in the UI. None are in scope here.

## 2. Scope

**In scope:** install n8n, persist it, make it reachable on the LAN, prove a
cloud-model round-trip through the gateway, document the install.

**Non-goals (named, not built):** any business automation; the €100-cap alert;
webhook / public-URL config; nginx-proxy-manager fronting; forcing SQLite over
the chart's default DB; relocating to the HP t640 (see §5).

## 3. Deployment

- Install the **community / TrueCharts n8n app** via the TrueNAS app wizard —
  **not** a Dockge compose stack like the other NAS services (see §5 for why
  this one diverges).
- **Database:** accept the chart's default (likely a bundled Postgres). Do not
  override to SQLite — "don't fight the chart" for a get-it-running goal.
- **Storage:** point app data at a **pool dataset** (the TrueNAS system dataset
  is read-only). A pool dataset is covered by TrueNAS snapshots/replication, so
  snapshots *are* the backup — no separate backup job.
- **Encryption key:** ensure the chart's `N8N_ENCRYPTION_KEY` is persisted and
  its location recorded. It is what makes stored credentials survive a redeploy;
  losing it orphans every saved credential.
- **Access:** LAN-only at `http://10.63.0.2:<port>` (record the port the chart
  exposes). nginx fronting / public URL stay deferred, matching Grafana's
  posture.

## 4. The one hard requirement — gateway connectivity

- n8n **and** the LiteLLM gateway both run on the NAS, so n8n reaches the
  gateway directly at `http://10.63.0.2:4000` — no cross-host hop, no firewall
  rule. (This is *easier* than first assumed: the gateway moved off the Mac onto
  the NAS in the prior phase.)
- Configure n8n's **OpenAI credential** → base URL `http://10.63.0.2:4000`,
  using the gateway master key. LiteLLM is OpenAI-compatible, so any n8n LLM
  node works against it. Future workflows then inherit gateway tracing
  (Langfuse) and the €100 budget cap **automatically** — no per-workflow setup,
  no way to escape the cap.
- **Set `max_tokens` generously in n8n LLM nodes (≥ 256).** If a route resolves
  to a GPT-5-family model and `max_tokens < 16`, the upstream returns a 400 and
  the gateway **silently falls back** to the local model (`main → private`).
  This bit the first n8n test call (landed on local qwen instead of cloud). See
  the decision journal for the full lesson.

## 5. Two intentional deviations (recorded in the journal)

1. **NAS over the HP t640.** The Langfuse-observability phase deliberately put
   n8n on the t640, reasoning that NAS RAM should be left for ClickHouse. We
   reverse that to get started faster on one host; n8n's footprint is small.
   Accepted tradeoff: if NAS RAM gets tight against Langfuse/ClickHouse, n8n is
   the easy thing to relocate to the t640 later.
2. **Native community app over Dockge.** Every other NAS service is a committed
   `docker-compose.yml` pasted into Dockge. n8n uses the TrueNAS community app
   instead — simpler install/updates. Caveat: TrueCharts has a history of
   catalog churn/deprecation, so the documented fallback is a TrueNAS
   **Custom App** with a committed compose YAML.

## 6. Verification (the done-check)

Done is **not** "the app is up." Done is a verified cloud round-trip:

1. A throwaway n8n workflow makes one completion call through the gateway.
2. That call returns a completion **from the intended cloud model** (not a
   silent fallback — check the trace's `Model` field).
3. The call appears in **Langfuse** / `LiteLLM_SpendLogs`.

**Current state:** a live n8n call already round-tripped through the gateway
today (Langfuse trace tagged `User-Agent: n8n`). Connectivity is proven; what
remains is the documentation artifact, pinning the encryption key, confirming
storage sits on a snapshot-covered dataset, and the journal entry.

## 7. Repo artifact

- `nas/n8n/README.md` — a documented **install spec**, not a compose file:
  app/train, exposed port, storage dataset, encryption-key location, the
  gateway-credential step, the `max_tokens` note, the verification steps, and
  the Custom-App-compose fallback.
- **No `docker-compose.yml`** for this service — its config lives in the TrueNAS
  app wizard.

## 8. Follow-on (out of this spec)

- **Model-route review** (parked, decided separately): add a *tool-use
  reliability* data point to `pricing/data/price-vs-quality.csv`, then re-pick
  `main` and `deep`. Today's data already shows both current cloud routes are
  dominated (gpt-5 `deep` scores *below* its own gemini fallback), but the
  every-turn `main` driver must be chosen on agentic/tool-use reliability and
  latency, which the AA Intelligence Index does not capture.
