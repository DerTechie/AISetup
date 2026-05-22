# n8n (NAS)

Workflow automation host. Unlike the other NAS services, n8n is **not** a Dockge
compose stack — it's installed as the **community / TrueCharts n8n app** through
the TrueNAS app wizard (Ugreen DXP8800, TrueNAS, `10.63.0.2`). LAN-only. See the
decision journal:
[`../../docs/journal/2026-05-22-n8n-standup.md`](../../docs/journal/2026-05-22-n8n-standup.md).

- **UI:** `http://10.63.0.2:30109` (open from the Arch browser; LAN-only).
- **Data:** on a NAS **pool dataset**, not publicly exposed. Covered by TrueNAS
  snapshots, so snapshots are the backup — no separate backup job.
- **Database:** the chart's default (bundled). Not overridden to SQLite.

## Why no `docker-compose.yml`

Config lives in the TrueNAS app wizard, not a committed file. This README *is* the
install spec. If the TrueCharts chart is ever deprecated (it has a history of
catalog churn), the fallback is a TrueNAS **Custom App** with a committed compose
YAML — at which point this dir gains a `docker-compose.yml` and rejoins the Dockge
pattern.

## Install (TrueNAS app wizard)

1. Apps → install the community/TrueCharts **n8n** app.
2. **Storage:** point app data at a pool dataset (the TrueNAS system dataset is
   read-only). This dataset must be in the snapshot/replication schedule.
3. **Encryption key:** set `N8N_ENCRYPTION_KEY` to the known personal password
   (recorded out-of-band — **never** in this repo). It must persist across
   redeploys; losing it orphans every stored credential.
4. **Port:** exposed on `30109`.

## Wiring to the gateway (the one requirement)

n8n and the LiteLLM gateway both run on the NAS, so n8n calls the gateway directly
over the LAN — no cross-host hop, no firewall rule.

1. In n8n, create an **OpenAI credential**:
   - Base URL: `http://10.63.0.2:4000`
   - API key: the gateway master key (`LITELLM_MASTER_KEY`).
2. Use that credential from any n8n LLM node. Model = a gateway route name
   (`main`, `private`, `deep`, …). Every call is then traced in Langfuse and
   counts against the €100 cap automatically — no per-workflow setup, no escape
   from the cap.
3. **Set `max_tokens` generously (≥256).** While a GPT-5-family route is in play,
   `max_tokens < 16` makes the upstream 400 and the gateway **silently falls back**
   to the local model (`main → private`). The gateway ceilings `max_tokens` at
   8000, so being generous is safe. See the journal for the full lesson.

## Verify

A round-trip is the done-check, not "the app is up":

1. A throwaway workflow makes one completion call through the gateway.
2. The completion comes from the **intended cloud model** — check the Langfuse
   trace's `Model` field is not a silent fallback.
3. The call appears in Langfuse / `LiteLLM_SpendLogs`.

Already verified live (2026-05-22): an n8n call round-tripped through the gateway,
trace tagged `User-Agent: n8n`.

## Out of scope

Business automations, the €100-cap alert, webhook/public-URL config, and nginx
fronting are all built later (in the UI or in their own specs), not here.
