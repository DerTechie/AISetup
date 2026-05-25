# nas/borgmatic — offsite backup stack

Dockge stack on the NAS (`10.63.0.2`) that runs `borgmatic` nightly at 05:00 and
pushes one deduplicated archive to a Hetzner Storage Box BX21 over SSH port 23.

**Design spec:** [`../../docs/superpowers/specs/2026-05-25-nas-backup-strategy-design.md`](../../docs/superpowers/specs/2026-05-25-nas-backup-strategy-design.md)

## Hetzner Storage Box

- **Plan:** BX21 (5 TB, ~€10.50/mo)
- **Host:** `u600186.your-storagebox.de`
- **User:** `u600186`
- **Port:** **23** (Hetzner quirk — not 22)
- **Repo path:** `/home/u600186/borg-repo` → `ssh://u600186@u600186.your-storagebox.de:23/./borg-repo`

## Two-key model (ransomware defense)

| Key | Where it lives | Capability |
|---|---|---|
| `borg-writer-nas` | NAS (`/mnt/nvme/apps/borgmatic/secrets/`, mode 0600) | Create archives only. Cannot delete. Storage Box `authorized_keys` line: `command="borg serve --append-only --restrict-to-path /home/u600186/borg-repo",no-port-forwarding,no-X11-forwarding,no-pty <pub>` |
| `borg-admin-arch` | Arch (`~/.ssh/`, mode 0600) | Unrestricted. Prune, compact, deep verify, restore. Never installed on the NAS. |

**Pruning runs from Arch only.** If the NAS could prune, ransomware-on-NAS could destroy history. Don't break this rule.

## Encryption

`repokey-blake2`. Key inside the repo; passphrase in `.env` mode 0600.
**Acknowledged tradeoff:** NAS compromise → attacker reads the passphrase →
they can decrypt archive contents. Goal is *not losing* data
(ransomware-resistance), not confidentiality from NAS-root compromise. If
confidentiality ever matters more: switch to `keyfile` mode and store the key
offsite only.

## Network attachments

The container joins:
- `litellm_default` — for `pg_dump` against `litellm-db:5432`.
- `langfuse_default` — for `pg_dump` against `postgres:5432` + `clickhouse-client BACKUP` against `clickhouse:9000`.
- `n8n_default` / `paperless_*_default` / `immich_*_default` for their respective pre-hooks.

Confirm service names match `borgmatic.yaml`'s `postgresql_databases.*.hostname` before first run — TrueNAS apps sometimes pick non-obvious container names.

## Schedule

| Job | Cadence | Key |
|---|---|---|
| `borgmatic create` | daily 05:00 | writer (append-only) |
| `borgmatic check --only repository` | weekly Sun 06:00 | writer (read OK) |
| `borgmatic prune compact` | weekly Sun 09:00 | **admin (on Arch)** |
| `borgmatic check` (full + verify-data) | monthly first Sun | **admin (on Arch)** |

## Notifications

- **`ntfy.sh`** push on `start`/`finish`/`fail` to topic `${NTFY_TOPIC}`. Subscribe phone to that topic.
- **`healthchecks.io`** ping on every successful run. If no ping in 26h → email + push from healthchecks. Catches "the cron job silently stopped firing."

## Operations

See [`../../docs/runbook.md`](../../docs/runbook.md) "Backup (NAS-wide)" for: snapshot/replica/Borg inspection, all five restore scenarios, quarterly test restore, key + passphrase rotation, where the three escrow copies live.
