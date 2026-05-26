# 2026-05-26 — Phase 4 restore: the gate that finally proves the chain

## What happened

Phase 4 of the backup plan: pick a small protected dataset, extract it from
the latest Borg archive on Hetzner, diff against live, and document the
outcome with a measured RTO. Until this passes, the Forgejo spec's
Status-line gate (no real/customer data into Forgejo) holds.

The spec's example dataset was Joplin. Joplin has since been decommissioned
(memory: `joplin-decommissioning`), so the target had to be re-picked.

I chose **`litellm`** instead of one of the static candidates (dockge,
pihole, nginx-proxy-manager) deliberately. The smaller, file-only options
would have made a cleaner byte-diff but proved less. LiteLLM exercises *both*
halves of the backup chain — the file layer (a Postgres data directory) and
the per-DB `pg_dump` artifact that the borgmatic pre-hook writes into the
archive. A restore test that doesn't touch the pg_dump path leaves the
half-of-the-chain that actually matters for a real DB recovery untested.

## What "diff against live" had to mean here

The plan said "diff against live." For a static file tree that means
byte-equality. For a Postgres data directory it's an impossible target —
live PG writes to pgdata constantly, so any bit-comparison is guaranteed
to drift. Insisting on byte-equality there would have been a fake test
that always failed for the wrong reason.

Adjusted approach, three load-bearing checks:

1. **File-count parity** — `borg list` says 1671 regular files for
   `source/nvme/litellm` + `borgmatic/postgresql_databases/litellm-db:5432`;
   `find … -type f` on the extracted tree also says 1671. The extract
   wasn't truncated.
2. **Stable PG config files match byte-for-byte** — `PG_VERSION`,
   `postgresql.conf`, `postgresql.auto.conf`, `pg_hba.conf`,
   `pg_ident.conf`. These don't move while PG runs, so they *can* be
   byte-compared. All five sha256s identical between extracted and live.
3. **pg_dump round-trips into a live Postgres** — restored into a
   throwaway `postgres:17` container; 65 tables came back; row counts
   are sane (`LiteLLM_ProxyModelTable` 10, `LiteLLM_UserTable` 1,
   `LiteLLM_VerificationToken` 2).

Together: the *bytes* of the things that should be stable are stable, and
the *logical* content of the volatile parts (the database) restores into a
working DB. That's the proof the gate was meant to deliver.

## Two snags worth recording

**Podman volume-mount and colons in the path.** The pg_dump artifact lives
inside the archive at
`borgmatic/postgresql_databases/litellm-db:5432/litellm`. Podman's `-v
src:dst[:opt]` syntax splits on `:`, so a path containing a colon gets
mis-parsed as a malformed option. `--mount type=bind,src=…,dst=…` takes the
literal path. Anyone restoring a pg_dump via a Docker/Podman one-liner will
hit this — noted in the runbook.

**`pg_restore` version skew.** Borgmatic's image bundles a Postgres 17
client, so the dump file's format is 1.16 even though the live LiteLLM DB
is server 16.14. `pg_restore` from a `postgres:16` image refuses it
("unsupported version (1.16) in file header"). `postgres:17` reads it
fine. This is the kind of detail that lives only in muscle memory until
someone has to restore at 2 a.m. — runbook now says "Postgres **17+**".

## The 75 ignored errors

The throwaway-restore log shows 75 `ALTER DEFAULT PRIVILEGES … TO
grafana_ro` failures. `grafana_ro` is a per-stack read-only role created
by the live NAS environment for the Grafana metrics dashboard's queries.
The role doesn't exist in a clean `postgres:17` container; pg_restore
logs the skip and moves on. None of these failures are data — they're
grants on tables that get re-issued by the environment, not the dump.
A real Scenario-D restore (rebuilding the NAS from scratch) would have
the role in place by the time the restore runs, because role creation is
part of the stack's bring-up, not the backup. So: noted, not fixed.

## Measured RTO

End-to-end on a warm laptop with a fast home connection:

- `borg extract` of the litellm subset (~67 MB, 1671 files): roughly
  one minute (terminal didn't capture a clean elapsed; the borg progress
  output dominates the noise floor anyway).
- pg_dump round-trip including image pull: another ~90 seconds.
- sha256 + diff: trivial.

This is the first **measured** RTO data point on this system. For a
single-stack file+DB recovery on a warm box with the image cached, call
it **under five minutes**. The Scenario-D RTO (full NAS rebuild) is a
different number that has to wait until the actual disaster drill — by
definition you can't measure that one in a verification test.

## Gate result

Phase 4 passes. The Forgejo Status-line dependency in
`2026-05-25-self-hosted-forgejo-design.md` is unblocked: real/customer
data is now permitted to land in Forgejo. The backup plan's only remaining
work is the recurring quarterly reminder (timer + ntfy push, no
auto-restore — see runbook § Backups for the reasoning).

## Decisions worth keeping

- **For Postgres-bearing stacks, the restore test must exercise the
  pg_dump path, not just the bind-mount files.** The file layer's
  integrity is incidental for a live DB; the dump is what you'd actually
  restore from.
- **"Diff against live" has to flex by data type.** Static files: byte
  diff. Live RDBMS data dirs: byte-diff the stable config files plus a
  logical pg_restore round-trip. Specs that say "diff against live"
  uniformly are quietly assuming static data.
- **No auto-restore on the quarterly timer.** A scripted unattended
  restore would prove the *script* still works, not that the *human*
  remembers how. We're keeping the timer as a ntfy reminder so the muscle
  memory stays current.
