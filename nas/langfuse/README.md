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

## Verify
Run: `test -f nas/langfuse/README.md && head -1 nas/langfuse/README.md`
Expected: prints `# Langfuse (NAS)`

## Commit
```bash
git add nas/langfuse/README.md
git commit -m "docs(langfuse): stack README (deploy + gateway wiring)"
```

## Report back
End with a STATUS line: `STATUS: DONE`, `STATUS: DONE_WITH_CONCERNS` (list), or `STATUS: BLOCKED` (explain). Include the verify output and commit SHA.
