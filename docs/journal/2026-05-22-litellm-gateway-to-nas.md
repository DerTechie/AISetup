# 2026-05-22 — Moving the LiteLLM gateway from the Mac to the NAS

## The decision

The LiteLLM gateway (and its Postgres) move off the Mac and onto the **NAS**
(Ugreen DXP8800 Plus, TrueNAS, `10.63.0.2`), deployed as a **Dockge** compose
stack. The Mac becomes a pure inference appliance: it runs only the `private`
model in Ollama. Routes, caps, and the OpenAI-compatible surface are unchanged.

## Why (and what almost stopped it)

The starting instinct — "Langfuse is going on the NAS, so the gateway should
too" — was right for the wrong reason. Co-locating with future observability is a
**weak** argument: Langfuse ingestion is async/fire-and-forget and tolerates a LAN
hop fine. So that alone wouldn't justify touching a working, live system.

The real case that held up:

- **The gateway is an *app*, not a *model thing*.** It needs network reach to
  Ollama, not co-residence with it. On this setup, apps live in Docker on the NAS
  behind nginx-proxy-manager (immich, jellyfin, paperless, pihole). The Mac is the
  exception only because it has the unified memory to hold the model. By that
  logic the gateway belongs with the other services.
- **The hardware is more than enough.** The DXP8800 Plus is x86 with 32 GB RAM and
  already runs Docker; a Python proxy next to Jellyfin transcoding is a rounding
  error. The "can the NAS handle it" objection died on inspection.
- **Custody.** The OpenRouter key and €100 budget state fall under the NAS's
  backup regime rather than living on a headless Mac.

### Objections we worked through (not hand-waved)

- **"It adds a DNS dependency on pihole."** Checked the live config: every backend
  is addressed by **raw IP** (`10.63.0.32`, `10.63.0.29`) and Hermes' `base_url`
  was a raw IP too. pihole was **never** in the AI data path. Concern dropped.
- **"The NAS reboots more, blipping the gateway."** Wrong: app updates on the NAS
  are *container* restarts, not host reboots. Only nginx-proxy-manager and pihole
  could blip it — and the gateway doesn't need to sit behind NPM (Hermes hits
  `10.63.0.2:4000` directly on the LAN), so NPM is out of the path entirely.

## What we knowingly gave up

With the gateway on the Mac, the gateway↔model link was **loopback** — it could
not fail independently of the box. On the NAS it becomes a real network hop
(switch, cable, NIC) that *can* fail on its own, and the dominant `private` path
now requires **two** boxes (NAS + Mac) up instead of one. We accepted this: the
NAS is as always-on as the Mac, and the consolidation/custody benefits outweigh
one extra LAN hop. Latency is a non-issue — the LAN round trip is sub-millisecond
against an ~11 tok/s local model.

## What the move actually forced (the surgical part)

Porting the compose was trivial; the breakage is at the **edges**, because the
two local backends were configured to trust the *Mac* as the caller:

1. **`private` → Mac Ollama.** The Mac config used
   `api_base: http://host.docker.internal:11434` — which only resolves to the Mac
   because LiteLLM ran *on* the Mac under Docker Desktop. On the NAS that points at
   the NAS itself. Changed to the Mac LAN IP `http://10.63.0.32:11434`. And the
   Mac's Ollama was **`127.0.0.1`-only** (verified by `lsof` over SSH:
   `TCP 127.0.0.1:11434 (LISTEN)`), so it must now bind `0.0.0.0` and be fenced to
   the NAS — the same exposure pattern Arch already uses for `aux-local`.
2. **`aux-local` → Arch Ollama.** Arch's `ollama_guard` allowed
   `{127.0.0.1, 10.63.0.32}`. The gateway is the only caller and it moved, so the
   guard now allows `{127.0.0.1, 10.63.0.2}`. Forget this and `aux-local` fails
   with `APIConnectionError ... 10.63.0.29:11434`.
3. **Budget counters reset.** The €100 cap state lives in the stack's Postgres
   volume; a fresh NAS stack starts the 30-day windows at €0. Accepted as a clean
   slate rather than migrating the old volume.

## Status

Repo artifacts done: `nas/` stack + config, Arch guard re-scoped, docs synced. The
**live cutover is unverified** (the stack dir must be placed on a NAS pool path by
hand — TrueNAS's system dataset is read-only). The old `mac/` stack is retained
for rollback until the NAS gateway passes the runbook health checks, then retired.
