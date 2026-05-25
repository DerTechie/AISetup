# NAS-wide backup strategy — design

**Date:** 2026-05-25
**Status:** Design under review. **No backup machinery exists today** — this spec is urgent (the Forgejo spec depends on Phase 4 of this one before real data can land there).

## 1. Goal

Stand up a coherent, NAS-wide backup strategy covering every service and
dataset on the Ugreen NAS (`10.63.0.2`, TrueNAS SCALE), with offsite
protection on a Hetzner Storage Box. Designed once and applied uniformly,
not invented per-service. Covers the entire app inventory — LiteLLM,
Langfuse, Grafana, n8n, paperless-ngx, immich, pihole, jellyfin,
nginx-proxy-manager, Dockge itself, and Forgejo (incoming) — plus personal
data on the HDD pool.

## 2. Scope

**In scope:** dataset inventory and tier classification, the Dockge-stack
migration to host-path mounts (without which backup isn't possible), local
ZFS snapshots, local NVMe→HDD replication, offsite via BorgBackup +
Borgmatic to Hetzner Storage Box with append-only credentials, restore
procedures by failure scenario, verification cadence, notification path.

**Non-goals (named, not built):**

- Secrets management as a system (escrow procedure is in scope; a NAS-wide
  password/secret manager isn't).
- Auth/access management across NAS services.
- Monitoring of *backup* health beyond Borgmatic notifications + a deadman
  switch.
- Backup of Jellyfin or its media library (re-downloadable, excluded
  entirely per §4).
- A second offsite location beyond the Hetzner Storage Box.

## 3. Threat model and tier classification

### 3.1 Threat priorities (from the design conversation)

| Priority | Threat | Defense |
|---|---|---|
| **High** | A — NAS hardware loss (drives die / stolen / fire) | Offsite copy on different hardware. |
| **High** | B — Ransomware / malicious encryption | Append-only offsite (NAS-side credentials cannot delete). |
| **High** | E — Geographic disaster (fire, theft, flood) | Offsite that is physically off-premises. |
| **Medium** | C — Self-inflicted deletion | ZFS snapshots with sensible retention. |
| **Medium** | D — ZFS or TrueNAS bug | Offsite tool stack is non-ZFS (Borg) to break format dependency. |
| **Low** | F — Supply-chain compromise | Long historical retention naturally protects against recently-introduced bad state. |

A+B+E all high means **offsite must be (a) geographically separated and
(b) something the NAS itself can write to but cannot delete from.** That
constraint alone eliminates most options and points at append-only Borg on
Hetzner Storage Box as the only design that actually clears the bar.

### 3.2 RPO / RTO targets

- **RPO** (max acceptable data loss): mixed by sub-tier. Hot transactional
  ≤ 1h. Standard app data ≤ 24h. Cold/personal/photos ≤ 24h.
- **RTO** (max acceptable recovery time): a day or two for everything,
  including future customer data. "Cannot work for a day or two" is the
  acceptable worst case; nothing on the NAS is mission-critical enough to
  require sub-day recovery.

### 3.3 Tier classification

Three tiers, applied per dataset. Full classification in §5.1 (snapshots
and replication) and §6.1 (offsite source list).

- **Protected** — local snapshots **+** local NVMe→HDD replication (where
  source is NVMe) **+** Borg to Hetzner offsite.
- **Snapshot-only** — local snapshots, no replication, no offsite.
- **Excluded** — no backup machinery at all.

## 4. Phase 0 — Dockge stack migration (hard pre-req)

**The NAS's three Dockge stacks (LiteLLM, Langfuse, Grafana) all currently
use Docker named volumes,** which on this TrueNAS install land in
`/mnt/.ix-apps/docker/volumes/` — the hidden `.ix-apps` tree that's
snapshot/replication-hostile (documented in the Forgejo spec §4 and the
[official TrueNAS guidance][truenas-app-storage]). Backup assumes
host-path datasets; this phase fixes the gap.

[truenas-app-storage]: https://apps.truenas.com/getting-started/app-storage/

**Scope:** **only the Dockge stacks.** Every `ix-*` TrueNAS App (n8n,
paperless-ngx, immich, pihole, jellyfin, nginx-proxy-manager, dockge
itself) was already configured with host-path mounts at install time —
verified by inspecting `docker inspect` mount sources. No migration needed
for those.

### 4.1 Migration map (UIDs verified on disk)

| Source named volume | Target host path | Owner (`chown -R`) |
|---|---|---|
| `litellm_litellm-pgdata` | `/mnt/nvme/apps/litellm/pgdata` | `999:999` |
| `langfuse_langfuse-pgdata` | `/mnt/nvme/apps/langfuse/pgdata` | `999:999` |
| `langfuse_langfuse-clickhouse-data` | `/mnt/nvme/apps/langfuse/clickhouse-data` | `101:101` |
| `langfuse_langfuse-clickhouse-logs` | `/mnt/nvme/apps/langfuse/clickhouse-logs` | `101:101` |
| `langfuse_langfuse-minio-data` | `/mnt/nvme/apps/langfuse/minio-data` | `0:0` |
| `metrics_grafana-data` | `/mnt/nvme/apps/metrics/grafana-data` | `472:0` |
| `metrics_prometheus-data` | `/mnt/nvme/apps/metrics/prometheus-data` | `65534:65534` |

Targets sit under `/mnt/nvme/apps/<stack>/<volume>/` to match the existing
TrueNAS-app layout and keep Dockge's `stacks/` directory (which holds the
compose files) separate from the data.

### 4.2 Per-stack procedure

For each of the three stacks, in this order — **LiteLLM** (simplest, one
volume), then **Grafana** (two volumes), then **Langfuse** (four volumes,
ClickHouse most fragile):

1. **Stop** the stack from the Dockge UI. Clean shutdown — Postgres needs
   to checkpoint; ClickHouse needs to flush.
2. **Create** the host-path datasets above (TrueNAS UI → Datasets), one
   per child.
3. **Copy data:**
   ```bash
   sudo rsync -aHAX --info=progress2 \
     /mnt/.ix-apps/docker/volumes/<volume_name>/_data/ \
     /mnt/nvme/apps/<stack>/<child>/
   ```
   `-aHAX` preserves ownership, hard links, ACLs, xattrs (run as root).
4. **Explicitly chown** the destination to the locked-in UID/GID from
   §4.1 — belt-and-suspenders against rsync silently dropping ownership.
5. **Edit** the committed `nas/<stack>/docker-compose.yml`: replace each
   named volume reference with a bind mount, and drop the now-unused
   `volumes:` block at the bottom. Example:
   ```yaml
   volumes:
     - /mnt/nvme/apps/litellm/pgdata:/var/lib/postgresql/data
   ```
6. **Start** the stack from Dockge. Watch logs for errors.
7. **Functional verify** before declaring done:
   - LiteLLM: UI loads, `/health` returns the route table.
   - Langfuse: web UI loads, a trace from before the migration is visible.
   - Grafana: UI loads, the spend dashboard renders, Prometheus data is
     intact (`max_over_time` over last 7d shows continuity).
8. **Reclaim the old volume** only after verification:
   `sudo docker volume rm <name>`.
9. **Commit** the updated compose file with a clear message.

### 4.3 Phase 0 verification gate

All seven host-path datasets exist with the correct ownership; all three
stacks are running and serving normally; original Docker named volumes
have been removed; updated compose files are committed.

**Estimate:** ~90 minutes total across the three stacks.

## 5. Snapshots and replication (local layers)

### 5.1 Tier assignments per dataset

#### NVMe pool (`/mnt/nvme/`)

| Dataset | Tier |
|---|---|
| `apps/dockge/{stacks,data}` | Protected |
| `apps/n8n/{data,postgres}` | Protected |
| `apps/paperless-ngx/{data,pgdata}` | Protected |
| `apps/immich/pgdata` | Protected (photo metadata index) |
| `apps/nginx-proxy-manager/{data,certs}` | Protected (LE certs) |
| `apps/pihole/{dnsmasq,config}` | Protected |
| `apps/joplin` | Protected |
| `apps/litellm/pgdata` *(post-Phase 0)* | Protected |
| `apps/langfuse/pgdata` *(post-Phase 0)* | Protected |
| `apps/langfuse/clickhouse-data` *(post-Phase 0)* | Protected |
| `apps/langfuse/minio-data` *(post-Phase 0)* | Protected |
| `apps/langfuse/clickhouse-logs` *(post-Phase 0)* | **Excluded** (regrowing log files, +500 MiB/few days; also add logrotate) |
| `apps/metrics/grafana-data` *(post-Phase 0)* | Protected |
| `apps/metrics/prometheus-data` *(post-Phase 0)* | Protected (1y history; irreplaceable for talk material) |
| `apps/forgejo/{data,postgres_data}` *(post-Forgejo install)* | Protected |
| `apps/jellyfin/*` | **Excluded** (whole service; per user decision) |
| `opt`, `shares`, `smb` | **Excluded** (tiny placeholders, unused) |

#### HDD pool (`/mnt/tank/`)

| Dataset | Tier |
|---|---|
| `apps/immich/data` | Protected (**the photos** — irreplaceable) |
| `apps/paperless-ngx/{media,consume,trash}` | Protected (scanned documents) |
| `storage` | Protected (personal data dump) |
| `apps/forgejo/lfs` *(post-Forgejo install)* | Protected (game assets) |
| `home` | Snapshot-only (stale Windows→Linux transfer leftover; cleanup candidate) |
| `Jellyfin` | **Excluded** (re-downloadable media library) |

### 5.2 Snapshot policy classes

Three classes, applied per dataset based on change rate and consistency
needs. TrueNAS Periodic Snapshot Tasks, recursive where useful.

| Class | Cadence | Retention | Applies to |
|---|---|---|---|
| **A — Hot transactional** | every 1 hour | 24 hourly + 14 daily | every `*/pgdata`, `langfuse/clickhouse-data`, `langfuse/minio-data` (referenced by CH rows), `metrics/grafana-data` (sqlite-flavored grafana.db) |
| **B — Standard app data** | every 6 hours | 12×6h + 30 daily + 6 monthly | `apps/dockge/*`, `apps/n8n/data`, `apps/joplin`, `apps/pihole/*`, `apps/nginx-proxy-manager/*`, `apps/paperless-ngx/data`, `apps/forgejo/data`, `apps/metrics/prometheus-data` |
| **C — Cold irreplaceable** | daily 03:00 | 30 daily + 12 monthly + 5 yearly | `tank/apps/immich/data`, `tank/apps/paperless-ngx/{media,consume,trash}`, `tank/storage`, `tank/apps/forgejo/lfs` |
| **Snapshot-only** | daily | 14 daily | `tank/home` |

### 5.3 ZFS replication NVMe → HDD

One TrueNAS Replication Task per protected NVMe parent dataset, recursive,
destination under **`tank/replica/nvme-apps/<name>/`** — segregated from
the working `tank/apps/` so a "replicated copy" can never be confused with
"live."

- **Schedule:** daily at 04:00 (after the daily snapshot anchor at 03:00).
- **Retention:** matches source — replicated snapshots carry their TTLs.
- **Source datasets:** every NVMe parent in §5.1's Protected rows.
- **HDD-resident protected datasets** (`tank/apps/immich/data`,
  `tank/apps/paperless-ngx/*`, `tank/storage`, `tank/apps/forgejo/lfs`)
  **don't get local replication** — there's nowhere meaningfully different
  to put them on the same NAS. Their protection is snapshots + Borg
  offsite (§6).

### 5.4 The daily anchor

Three sequential events, quiet hours:

- **03:00** — daily snapshots fire (class C; class B's daily run; the
  snapshot-only daily).
- **04:00** — NVMe→HDD replication runs against the freshly-taken
  snapshots.
- **05:00** — Borgmatic offsite run (§6).

Hourly (class A) and 6-hour (class B) snapshots run on their own cadences
independent of this anchor.

## 6. Offsite tier — BorgBackup + Borgmatic to Hetzner Storage Box

### 6.1 Tool and host

- **BorgBackup** orchestrated by **Borgmatic** (YAML config, native
  pre-backup database hooks).
- Runs as a **Dockge stack** on the NAS: `nas/borgmatic/`, committed to
  git. Same operational pattern as the other Dockge stacks (post Phase 0).
- The container mounts the protected source datasets read-only, the SSH
  key as a bind-mounted secret with `0600` perms, and the `borgmatic.yaml`
  config. Joins the Docker networks of LiteLLM/Langfuse/Forgejo to reach
  their Postgres instances for `pg_dump`.
- Invocation: container's built-in cron entrypoint runs Borgmatic daily
  at 05:00.

### 6.2 Hetzner Storage Box — one-time setup

1. Order **BX21** (5 TB, ~€10.50/mo) in an EU location.
2. Note credentials: hostname like `u123456.your-storagebox.de`, username
   `u123456`, **port 23** (Hetzner quirk, not 22).
3. Enable **SSH access** in the Storage Box settings (off by default on
   some plans).
4. SSH in once to accept the host key and record its fingerprint in the
   runbook.

### 6.3 The two-key model (ransomware defense)

**Two SSH keys, never both on the same host:**

- **Writer key** — lives on the NAS inside the Borgmatic stack. Its
  public key sits in the Storage Box's `authorized_keys` with this
  restriction line:
  ```
  command="borg serve --append-only --restrict-to-path /home/u123456/borg-repo",no-port-forwarding,no-X11-forwarding,no-pty <writer-public-key>
  ```
  Server-side enforcement — even with full passphrase and root on the
  NAS, an attacker **cannot delete archives** through this key. Worst
  case: they write garbage archives that grow the repo, but history
  remains intact.
- **Admin key** — lives on the Arch workstation only. No restriction.
  Used for `borg prune`, `borg check --verify-data`, restore operations,
  and any other administrative work.

**The repo is only ever pruned from Arch, never from the NAS.** This is
load-bearing: if the NAS could prune, the ransomware defense collapses.

### 6.4 Encryption

- **Mode:** `repokey-blake2`. Key stored in the repo, protected by a
  passphrase that lives on the NAS for daily `borg create` operations.
- **Acknowledged tradeoff:** if the NAS is compromised, the attacker reads
  the passphrase and can decrypt repo contents. This is acceptable given
  threat F is low — the goal is not *losing* data (ransomware-resistance),
  not confidentiality from NAS-root compromise. If confidentiality ever
  matters more, switch to `keyfile` mode and store the key offsite.
- **Passphrase generation:** long random, stored in the password manager
  AND in the Borgmatic config on the NAS (file mode `600`).

### 6.5 Database consistency — pre-backup hooks

Borgmatic native hooks handle this:

- **PostgreSQL** (`postgresql_databases:` block) — runs `pg_dump` against
  each listed DB before `borg create`, includes the dump in the archive,
  removes it after. Targets: LiteLLM, Langfuse, n8n, paperless, immich,
  Forgejo Postgres instances.
- **ClickHouse** (`clickhouse_databases:` block) — same pattern via
  `clickhouse-client BACKUP`.
- **MinIO** — no dump; file-level backup of the data directory is
  sufficient (Borg dedup handles object store files efficiently).

### 6.6 Source list and excludes

Single daily archive containing all protected datasets — Borg dedup makes
"one big archive" cheap.

```yaml
source_directories:
  - /mnt/nvme/apps/dockge
  - /mnt/nvme/apps/n8n
  - /mnt/nvme/apps/paperless-ngx
  - /mnt/nvme/apps/immich
  - /mnt/nvme/apps/nginx-proxy-manager
  - /mnt/nvme/apps/pihole
  - /mnt/nvme/apps/joplin
  - /mnt/nvme/apps/litellm
  - /mnt/nvme/apps/langfuse
  - /mnt/nvme/apps/metrics
  - /mnt/nvme/apps/forgejo          # post-Forgejo install
  - /mnt/tank/apps/immich/data
  - /mnt/tank/apps/paperless-ngx
  - /mnt/tank/storage
  - /mnt/tank/apps/forgejo/lfs      # post-Forgejo install

exclude_patterns:
  - /mnt/nvme/apps/langfuse/clickhouse-logs
  - '**/cache/**'
  - '**/Cache/**'
  - '**/tmp/**'
  - '**/log/**'
  - '**/logs/**'
  - '**/*.log'
  - '**/transcode-cache/**'
```

**Note on excludes:** these glob patterns don't match PostgreSQL's
`pg_log` (different name) and never touch `pg_wal` or `pg_xact` — both
critical for PG recovery and safe.

### 6.7 Schedule and retention

| Job | Cadence | Where it runs | Key |
|---|---|---|---|
| `borg create` (daily archive) | daily 05:00 | NAS Borgmatic container | writer (append-only) |
| `borg check --repository-only` | weekly, Sun 06:00 | NAS Borgmatic container | writer (read OK with append-only) |
| `borg prune` (retention enforcement) | weekly | **Arch workstation** | admin (unrestricted) |
| `borg check --verify-data` (deep) | monthly, first Sun | **Arch workstation** | admin |
| Test restore (see §7) | quarterly | **Arch workstation** | admin |

**Retention** (enforced from Arch, since the NAS can't prune):

```yaml
keep_daily: 7
keep_weekly: 4
keep_monthly: 12
keep_yearly: 3
```

Standard Borg pattern. Restore any day from the last week, any week from
the last month, any month from the last year, and any year from the last
three.

### 6.8 First archive

Initial `borg create` transfers all unchanged data; subsequent runs only
transfer the diff. Expect a multi-hour initial archive depending on
upstream — leave it running overnight. One-time cost.

## 7. Restore procedures and verification

Five scenarios mapping to the threat priorities in §3.1. Each has concrete
commands; all are reproduced in the runbook for incident-time
copy-paste.

### 7.1 Restore by scenario

#### Scenario A — single-file recovery
**Source:** local ZFS snapshot. **RTO:** seconds to minutes.

```bash
sudo zfs list -t snapshot -o name,creation -s creation -r nvme/apps/<name> | tail -20
sudo ls /mnt/nvme/apps/<name>/.zfs/snapshot/<snapshot-name>/
sudo cp /mnt/nvme/apps/<name>/.zfs/snapshot/<snapshot-name>/path/to/file \
        /mnt/nvme/apps/<name>/path/to/file
sudo chown <uid>:<gid> /mnt/nvme/apps/<name>/path/to/file
```

#### Scenario B — dataset-level recovery
**Source:** local ZFS snapshot rollback OR local replica. **RTO:** minutes.

Stop the affected app first. Then either:

```bash
# Option 1: rollback (destroys any newer data)
sudo zfs rollback -r nvme/apps/<name>@<snapshot-name>

# Option 2: receive from local replica
sudo zfs send tank/replica/nvme-apps/<name>@<snapshot-name> | \
  sudo zfs receive -F nvme/apps/<name>
```

#### Scenario C — NVMe pool failure
**Source:** local replica on `tank/replica/nvme-apps/`. **RTO:** hours
(drive replacement + restore). **Data loss bound:** up to ~24h (since last
04:00 replication).

After rebuilding the NVMe pool:

```bash
for ds in dockge n8n paperless-ngx immich nginx-proxy-manager pihole joplin litellm langfuse metrics forgejo; do
  sudo zfs send -R tank/replica/nvme-apps/$ds@<latest-snapshot> | \
    sudo zfs receive -F nvme/apps/$ds
done
```

#### Scenario D — full NAS loss (fire / theft / hardware)
**Source:** Borg archive on Hetzner, via Arch (which has the admin key).
**RTO:** 1–2 days.

```bash
# On Arch:
export BORG_PASSPHRASE='...'  # from password manager
borg list ssh://u123456@u123456.your-storagebox.de:23/./borg-repo

ARCHIVE=nas-2026-05-25T05:00:00

# Option 1: full extract to staging, then rsync to rebuilt NAS
mkdir /tmp/restore && cd /tmp/restore
borg extract ssh://u123456@u123456.your-storagebox.de:23/./borg-repo::$ARCHIVE

# Option 2 (better for large archives): mount and selectively copy
borg mount ssh://u123456@u123456.your-storagebox.de:23/./borg-repo::$ARCHIVE /mnt/borg-restore
```

Restore Postgres dumps via `pg_restore` on the new NAS once stacks are up.

#### Scenario E — ransomware
**Source:** same as Scenario D, with extra steps. The append-only writer
key means historical archives are intact regardless of what the attacker
did on the NAS.

```bash
# 1. Pick an archive from BEFORE the compromise window.
# 2. Wipe the compromised NAS completely (factory reset / pool destroy /
#    OS reinstall) before any restore. Do not "clean" — trust is gone.
# 3. Rotate ALL secrets (passwords, API keys, Borg passphrase,
#    Storage Box SSH keys, app admin credentials) BEFORE restore.
# 4. Restore as in Scenario D.
```

### 7.2 Verification gate — "does this actually work"

| Cadence | Check | Where | What it proves |
|---|---|---|---|
| Every run (daily) | Borgmatic exit code + notification | NAS, push to phone | Daily archive completed. |
| Weekly Sun 06:00 | `borg check --repository-only` | NAS container | Repo structure intact. |
| Monthly first Sun | `borg check --verify-data` | Arch | Every chunk reads correctly (catches silent corruption). |
| Quarterly | **Actual test restore** to `/tmp` of a small dataset | Arch | Whole chain works end-to-end. |

Document each test restore in the runbook with date + outcome. **A backup
that hasn't been restored in 6 months is presumed broken until proven
otherwise.**

### 7.3 Notification path

A failed nightly archive that nobody sees is a backup that fails open.

- **Borgmatic `on_error` hook** → push to **ntfy.sh** (free; self-hostable
  if sovereignty matters later) on a topic only your phone subscribes to.
  Fires on `create` failure, `check` failure, or any pre-hook failure
  (pg_dump etc.).
- **Deadman switch** — `healthchecks.io` free tier (or self-hosted
  equivalent) pinged at the end of every successful Borgmatic run. If no
  ping in 26h, healthchecks fires its own alert. Catches "the cron job
  silently stopped firing."

## 8. Implementation phasing

Phases with verification gates. No skipping ahead with broken foundations.

### Phase 0 — Dockge stack migration *(blocks everything else)*
Per §4. **Verification gate:** seven host-path datasets exist with correct
ownership; all three stacks serving normally; original Docker volumes
removed; compose files committed. **Estimate:** ~90 min.

### Phase 1 — Local snapshots
Per §5.2. Configure Periodic Snapshot Tasks (classes A/B/C + snapshot-only
tier). Confirm exclusions. Wait 24h, verify snapshots fire on schedule
and counts are sane. **Estimate:** ~60 min + 24h soak.

### Phase 2 — Local replication NVMe → HDD
Per §5.3. Create `tank/replica/nvme-apps/` parent, one Replication Task
per protected NVMe parent (recursive, daily 04:00, matched retention).
Trigger initial seed manually. **Verification gate:** every protected
NVMe dataset has a populated replica; snapshot lists match.
**Estimate:** ~30 min + initial seed.

### Phase 3 — Offsite via Borg + Borgmatic
Per §6. The long one.

1. Order and provision the BX21 Storage Box.
2. Generate writer + admin SSH keypairs; install both public keys on the
   Storage Box (writer with `command="borg serve --append-only ..."`).
3. **Escrow the secrets that can't be re-derived:** Borg passphrase, both
   private keys, Storage Box username/password. **Three copies, two media,
   one offsite** — e.g., password manager (primary), printed paper in a
   fire-safe (secondary), encrypted USB at parents' or a bank deposit box
   (offsite). If all of these are lost along with the NAS, the offsite
   backup is unrecoverable.
4. `borg init --encryption=repokey-blake2 ssh://…` from Arch (admin key).
5. Build the `nas/borgmatic/` Dockge stack and commit. Stack joins
   relevant Docker networks for `pg_dump`/`clickhouse-client` access.
6. Run first archive manually with `--verbosity 2`. Multi-hour transfer
   expected.
7. Verify on Arch: `borg list`; small `borg extract` to `/tmp`.
8. Schedule the daily 05:00 timer on the NAS (Borgmatic create + check).
9. Schedule the weekly prune timer on Arch (admin key).
10. Wire notifications: ntfy.sh topic + Borgmatic `on_error` hook +
    healthchecks.io deadman switch.

**Verification gate:** first archive completes and is browsable from
Arch; second daily archive runs on schedule; an intentional pg_dump
failure triggers a phone notification within minutes; healthchecks.io
deadman is pinging. **Estimate:** ~half a day + initial transfer.

### Phase 4 — Test restore (the gate that proves it all)
Per §7.1 / §7.2. Pick a small protected dataset (e.g., `joplin`). Extract
from latest Borg archive to `/tmp/restore-test/`. Diff against live;
document outcome with date in the runbook; schedule the recurring
quarterly test.

**Verification gate:** restore worked end-to-end without manual
intervention beyond commands. Measured time becomes your actual RTO data
point. If it exceeded your stated tolerance, redesign before declaring
done. **Estimate:** ~30 min.

### Forgejo dependency
The Forgejo install can proceed in parallel with Phases 0–3 (none of them
require Forgejo). **But: real or customer data must not land in Forgejo
until Phase 4 has passed** — per the Forgejo spec's Status line.
Migrating this AISetup repo (also on GitHub) is safe to do early;
private/customer repos wait.

### Total estimate
Roughly **1.5–2 working days** spread over ~3 calendar days due to the
24h verification soaks. Longest single bit is the initial Borg transfer
to Hetzner — leave it running overnight.

## 9. Deferred / out of scope

- **A second offsite location.** Hetzner BX21 is the only offsite tier.
  If/when this feels insufficient (e.g., customer scale grows), add a
  second offsite (different provider, different geography) — separate
  spec.
- **Centralized secrets management** for NAS services (Vault, sops,
  etc.). Escrow procedure is in scope here (§8 Phase 3 step 3); a full
  secrets-management story is a future cross-cutting spec.
- **Auth/access** across NAS services (single sign-on, etc.). Cross-cutting,
  separate spec.
- **GDPR processor docs** for customer data on Forgejo. Tracked by the
  Forgejo spec's deferred list; not duplicated here.
- **Backup encryption mode upgrade** to `keyfile` (offsite key storage,
  stronger NAS-compromise confidentiality). Easy migration later if
  needed.
- **Backup monitoring beyond Borgmatic notifications.** The "watch the
  watcher" problem can become its own thing; deadman switch is enough for
  now.

## 10. Repo artifact

- `nas/borgmatic/` — committed Dockge stack with `docker-compose.yml`,
  `borgmatic.yaml`, and a `README.md` covering: Hetzner Storage Box
  hostname/port/user, the two-key model with both restriction lines,
  encryption mode, the network attachments needed for pg_dump access,
  exclude patterns, schedule, and the on_error / deadman hooks.
- `arch/borg-admin/` — Arch-side artifact: `borgmatic.yaml` for prune +
  deep check + restore, and the systemd timer units. **Admin SSH key is
  not committed** — it lives in `~/.ssh/` on the Arch workstation and is
  escrowed per §8 Phase 3 step 3. A small but load-bearing companion to
  the NAS-side stack.
- Runbook entries under a new **"Backup (NAS-wide)"** section:
  - How to inspect snapshots / replicas / Borg archives.
  - Each restore scenario from §7.1 as copy-pasteable commands with
    context.
  - How to run the quarterly test restore.
  - How to rotate keys, passphrase, and Storage Box credentials.
  - Where each escrow copy of the secrets lives.

## 11. Follow-on (out of this spec)

- **Journal entry** capturing the design path — the "no backup exists
  today" starting point, the per-service-spec → cross-cutting lift-out
  insight, the threat-priority ranking that drove the append-only choice,
  and the Phase 0 Dockge-migration discovery. Feeds the talk narrative.
- **NAS-wide secrets management** — separate spec when the escrow + ad-hoc
  password-manager pattern starts to strain.
- **NAS-wide auth/access** — separate spec; same cross-cutting pattern as
  this one.
- **Forgejo customer-data readiness** — once Phase 4 passes, the Forgejo
  spec's Status-line gate lifts and real/customer data can land there.
