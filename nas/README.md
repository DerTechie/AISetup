# NAS gateway

The **LiteLLM gateway** (and its Postgres) run here, on the Ugreen DXP8800 Plus
(TrueNAS), deployed as a **Dockge** compose stack. The gateway moved off the Mac
so the Mac is a pure inference appliance and the always-on services/observability
(Langfuse later) live together on the NAS. See the decision journal:
[`../docs/journal/2026-05-22-litellm-gateway-to-nas.md`](../docs/journal/2026-05-22-litellm-gateway-to-nas.md).

- **NAS LAN IP:** `10.63.0.2` → gateway at `http://10.63.0.2:4000`.
- Operate it from the Dockge UI. The relative `./litellm-config.yaml` mount means
  the stack dir must hold both `docker-compose.yml` and `litellm-config.yaml`.

## Files

| File | Purpose |
|---|---|
| `docker-compose.yml` | LiteLLM + Postgres stack (paste into Dockge). |
| `litellm-config.yaml` | Routes + budget caps. Mount sits next to the compose file. |
| `.env.example` | Template for the stack `.env` (gitignored once filled). |

## Two cross-host prerequisites (the move forces these)

The gateway runs on the NAS now, so the two **local** backends it calls must
accept the NAS as the source — not the Mac:

1. **Mac Ollama (`private` route)** must listen on the LAN (`0.0.0.0:11434`) and
   allow the NAS (`10.63.0.2`). It was `127.0.0.1`-only. See
   [`../docs/runbook.md`](../docs/runbook.md) § "Mac Ollama on the LAN".
2. **Arch Ollama (`aux-local` route)** firewall must allow the NAS instead of the
   Mac — `arch/nftables-ollama-guard.nft` now scopes `:11434` to
   `{127.0.0.1, 10.63.0.2}`. Re-install the guard (see [`../arch/README.md`](../arch/README.md)).

## Deploy (in Dockge)

1. Create a stack dir on a pool path (TrueNAS system dataset is read-only —
   put it under your apps/pool dataset, not `/`). Place `docker-compose.yml`,
   `litellm-config.yaml`, and a filled `.env` (from `.env.example`) in it.
2. Start the stack from Dockge.
3. Verify (see [`../docs/runbook.md`](../docs/runbook.md) § "Verify health") —
   `curl http://10.63.0.2:4000/health/liveliness` → `"I'm alive!"`.

> **Budget counters reset.** The €100 cap state lives in this stack's fresh
> Postgres volume, so the 30-day windows start at €0 here. Accepted as a clean
> slate; migrating spend state from the old Mac volume is not worth it.
