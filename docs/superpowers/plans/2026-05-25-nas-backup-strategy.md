# NAS-wide Backup Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a NAS-wide backup system covering every Dockge stack and dataset on the Ugreen NAS — local ZFS snapshots, NVMe→HDD replication, and append-only BorgBackup to Hetzner Storage Box — so the AISetup ecosystem (LiteLLM, Langfuse, Grafana, n8n, paperless, immich, NPM, Pi-hole, Forgejo) can survive hardware loss, ransomware, geographic disaster, and self-inflicted deletion.

**Architecture:** Four phases gated by verification. **Phase 0** migrates three Docker-named-volume Dockge stacks (LiteLLM, Grafana, Langfuse) to host-path bind mounts so backup is even *possible*. **Phase 1** configures TrueNAS Periodic Snapshot Tasks in three retention classes (hot/standard/cold). **Phase 2** sets up NVMe→HDD replication. **Phase 3** stands up a `nas/borgmatic/` Dockge stack that nightly pushes one deduplicated archive to a Hetzner BX21 Storage Box via an `append-only` SSH key — with prune/check/restore living on the Arch workstation via an `admin` key the NAS never sees. **Phase 4** proves the whole chain works by extracting from Hetzner and diffing against live.

**Tech Stack:** TrueNAS SCALE (ZFS, Periodic Snapshot Tasks, Replication Tasks), Dockge, Docker Compose, BorgBackup, Borgmatic (Postgres + ClickHouse pre-hooks), Hetzner Storage Box (BX21 / SFTP+SSH port 23), ntfy.sh, healthchecks.io, systemd timers (Arch side).

**Reference spec:** [`../specs/2026-05-25-nas-backup-strategy-design.md`](../specs/2026-05-25-nas-backup-strategy-design.md)

**Note on task style:** This is infrastructure work, not application code, so "tests" are concrete verification commands with **expected output**. Each Phase ends with a verification gate that must pass before the next phase starts. Repo artifacts (`nas/borgmatic/`, `arch/borg-admin/`) are created in the working copy and committed; live deploy happens on the NAS via Dockge and on Arch via systemd.

---

## File Structure

| File | Responsibility |
|---|---|
| `nas/docker-compose.yml` (modify) | Switch `litellm-pgdata` named volume → host-path bind mount. |
| `nas/langfuse/docker-compose.yml` (modify) | Switch four Langfuse named volumes → host-path bind mounts. |
| `nas/metrics/docker-compose.yml` (modify) | Switch `prometheus-data` + `grafana-data` named volumes → host-path bind mounts. |
| `nas/borgmatic/docker-compose.yml` (create) | Dockge stack: borgmatic container, network joins for pg_dump/clickhouse-client. |
| `nas/borgmatic/borgmatic.yaml` (create) | Source list, excludes, postgres + clickhouse hooks, repo URL, retention, ntfy + healthchecks hooks. |
| `nas/borgmatic/.env.example` (create) | Template for `BORG_PASSPHRASE`, `HEALTHCHECKS_PING_URL`, `NTFY_TOPIC`, Postgres + ClickHouse creds. |
| `nas/borgmatic/README.md` (create) | Storage Box host/port/user, two-key model, escrow pointer, network attachments, exclude rationale. |
| `arch/borg-admin/borgmatic.yaml` (create) | Arch-side config: prune retention, deep check schedule, restore helpers (no `source_directories`). |
| `arch/borg-admin/borgmatic-prune.service` (create) | systemd oneshot for `borgmatic --config ... prune compact`. |
| `arch/borg-admin/borgmatic-prune.timer` (create) | Weekly Sun 09:00. |
| `arch/borg-admin/borgmatic-check.service` (create) | systemd oneshot for `borgmatic --config ... check` (deep verify-data). |
| `arch/borg-admin/borgmatic-check.timer` (create) | Monthly first-Sun. |
| `arch/borg-admin/README.md` (create) | Admin-side ops: key location, restore commands, install path. |
| `docs/runbook.md` (modify) | New "Backup (NAS-wide)" section with snapshot/replica/Borg inspection + all 5 restore scenarios + quarterly test recipe + key/passphrase rotation. |
| `README.md` (modify) | Mention backups in the architecture summary. |
| `docs/journal/2026-05-25-nas-backup-design.md` (create) | Decision story for the talk: no-backups-today start, per-service → cross-cutting lift-out, threat ranking → append-only, Phase 0 discovery. |

---

## Phase 0 — Dockge stack migration to host-path mounts

**Blocks all subsequent phases. The named volumes currently land in `/mnt/.ix-apps/docker/volumes/`, which is snapshot/replication-hostile.**

### Task 1: Pre-flight inventory and dataset creation

**Files:** none modified — TrueNAS UI + NAS shell.

- [ ] **Step 1: Verify the seven source named volumes exist and capture sizes**

Run on the NAS (SSH or TrueNAS shell):

```bash
for v in litellm_litellm-pgdata \
         langfuse_langfuse-pgdata \
         langfuse_langfuse-clickhouse-data \
         langfuse_langfuse-clickhouse-logs \
         langfuse_langfuse-minio-data \
         metrics_grafana-data \
         metrics_prometheus-data; do
  echo "=== $v ==="
  sudo docker volume inspect "$v" --format '{{.Mountpoint}}'
  sudo du -sh "/mnt/.ix-apps/docker/volumes/$v/_data" 2>/dev/null
done
```

Expected: every line prints a `Mountpoint` under `/mnt/.ix-apps/docker/volumes/` and a size. Any missing volume name = stop and reconcile with §4.1 before migrating.

- [ ] **Step 2: Confirm `/mnt/nvme/apps/` exists and is on the NVMe pool**

```bash
sudo zfs list -o name,mountpoint,used,avail | grep -E '^nvme/apps( |$)'
df -h /mnt/nvme/apps
```

Expected: `nvme/apps` ZFS dataset is mounted at `/mnt/nvme/apps` with enough free space to hold the sum of the seven `du -sh` numbers above (plus headroom).

- [ ] **Step 3: Create the seven target host-path datasets via TrueNAS UI**

TrueNAS UI → **Datasets** → select `nvme/apps` → **Add Dataset** for each parent that does not yet exist:

| Dataset path |
|---|
| `nvme/apps/litellm/pgdata` |
| `nvme/apps/langfuse/pgdata` |
| `nvme/apps/langfuse/clickhouse-data` |
| `nvme/apps/langfuse/clickhouse-logs` |
| `nvme/apps/langfuse/minio-data` |
| `nvme/apps/metrics/grafana-data` |
| `nvme/apps/metrics/prometheus-data` |

Each as **Generic** preset, default record size, default compression. Don't set ACL — keep POSIX.

- [ ] **Step 4: Verify the seven datasets exist and are empty**

```bash
for p in litellm/pgdata \
         langfuse/pgdata langfuse/clickhouse-data langfuse/clickhouse-logs langfuse/minio-data \
         metrics/grafana-data metrics/prometheus-data; do
  echo "=== /mnt/nvme/apps/$p ==="
  sudo ls -la "/mnt/nvme/apps/$p"
done
```

Expected: each path exists, owned by `root:root`, and contains only `.` and `..`. (Ownership gets fixed per-stack in tasks 2/3/4.)

- [ ] **Step 5: Commit nothing — this task creates no repo artifact**

This is pure live setup. The compose changes in Tasks 2/3/4 are the first commits of Phase 0.

---

### Task 2: Migrate LiteLLM (one volume — warm-up)

**Files:**
- Modify: `nas/docker-compose.yml` (replace `litellm-pgdata` named volume with bind mount)

- [ ] **Step 1: Stop the LiteLLM stack from the Dockge UI**

Dockge UI → `litellm` stack → **Stop**. Wait for "Stopped" badge on both `litellm-db` and `litellm`. Confirm clean PG shutdown:

```bash
sudo docker logs litellm-litellm-db-1 --tail 20 | grep -E 'shutdown complete|received fast shutdown'
```

Expected: a `database system is shut down` line is present near the end.

- [ ] **Step 2: rsync the data into the new host path**

```bash
sudo rsync -aHAX --info=progress2 \
  /mnt/.ix-apps/docker/volumes/litellm_litellm-pgdata/_data/ \
  /mnt/nvme/apps/litellm/pgdata/
```

Expected: `rsync` finishes with 0 errors. `sudo du -sh /mnt/nvme/apps/litellm/pgdata` matches the `du` from Task 1 Step 1 within a few KiB.

- [ ] **Step 3: Force ownership on the destination (belt-and-suspenders)**

```bash
sudo chown -R 999:999 /mnt/nvme/apps/litellm/pgdata
sudo stat -c '%U:%G %a %n' /mnt/nvme/apps/litellm/pgdata | head -5
sudo stat -c '%u:%g %a %n' /mnt/nvme/apps/litellm/pgdata/PG_VERSION 2>/dev/null
```

Expected: top dir owner numeric `999:999`. The `PG_VERSION` file (if PG cluster present) also `999:999`.

- [ ] **Step 4: Edit `nas/docker-compose.yml` to bind-mount pgdata**

Replace:

```yaml
    volumes:
      - litellm-pgdata:/var/lib/postgresql/data
```

with:

```yaml
    volumes:
      - /mnt/nvme/apps/litellm/pgdata:/var/lib/postgresql/data
```

And remove the now-unused trailing block:

```yaml
volumes:
  litellm-pgdata:
```

- [ ] **Step 5: Sync the edited compose to the Dockge stack dir and start**

(Dockge stack dir lives on a NAS pool path, not in this repo — copy or apply per the existing operational pattern. The repo `nas/docker-compose.yml` is the source of truth; the deploy step copies it over.)

Dockge UI → `litellm` stack → **Update** (re-reads compose) → **Start**. Wait for both services to report "Running".

- [ ] **Step 6: Functional verify**

```bash
curl -s http://10.63.0.2:4000/health | head -20
curl -s http://10.63.0.2:4000/v1/models -H "Authorization: Bearer $LITELLM_MASTER_KEY" | grep -o 'main\|deep\|private' | sort -u
```

Expected: `/health` returns the route table (non-empty JSON, no error); `/v1/models` lists at least `main`, `deep`, `private`. UI at `http://10.63.0.2:4000/ui` loads and shows existing models.

- [ ] **Step 7: Reclaim the old volume only after verify passes**

```bash
sudo docker volume rm litellm_litellm-pgdata
sudo docker volume ls | grep litellm || echo "no litellm-named volumes left"
```

Expected: removal succeeds; the `||` line prints if the volume is gone.

- [ ] **Step 8: Commit**

```bash
git add nas/docker-compose.yml
git commit -m "feat(litellm): migrate pgdata to host-path bind mount

Backup Phase 0 — named volumes under /mnt/.ix-apps/ are
snapshot-hostile. Move to /mnt/nvme/apps/litellm/pgdata so
ZFS snapshots + replication + Borg can see it."
```

---

### Task 3: Migrate Grafana / Prometheus (two volumes)

**Files:**
- Modify: `nas/metrics/docker-compose.yml` (replace `prometheus-data` + `grafana-data` named volumes with bind mounts)

- [ ] **Step 1: Stop the metrics stack from Dockge**

Dockge UI → `metrics` stack → **Stop**. Wait for `otel-collector`, `prometheus`, and `grafana` all "Stopped".

```bash
sudo docker logs metrics-prometheus-1 --tail 5 | grep -i 'shutting down\|stopping' || true
```

Expected: a shutdown/stopping line is visible.

- [ ] **Step 2: rsync both volumes**

```bash
sudo rsync -aHAX --info=progress2 \
  /mnt/.ix-apps/docker/volumes/metrics_grafana-data/_data/ \
  /mnt/nvme/apps/metrics/grafana-data/

sudo rsync -aHAX --info=progress2 \
  /mnt/.ix-apps/docker/volumes/metrics_prometheus-data/_data/ \
  /mnt/nvme/apps/metrics/prometheus-data/
```

Expected: both finish with 0 errors. Sizes match Task 1 Step 1.

- [ ] **Step 3: Force ownership per §4.1**

```bash
sudo chown -R 472:0 /mnt/nvme/apps/metrics/grafana-data
sudo chown -R 65534:65534 /mnt/nvme/apps/metrics/prometheus-data
sudo stat -c '%u:%g %a %n' /mnt/nvme/apps/metrics/grafana-data
sudo stat -c '%u:%g %a %n' /mnt/nvme/apps/metrics/prometheus-data
```

Expected: `472:0 …` and `65534:65534 …` respectively.

- [ ] **Step 4: Edit `nas/metrics/docker-compose.yml`**

Replace the two volume references:

```yaml
      - prometheus-data:/prometheus
```

→

```yaml
      - /mnt/nvme/apps/metrics/prometheus-data:/prometheus
```

And:

```yaml
      - grafana-data:/var/lib/grafana
```

→

```yaml
      - /mnt/nvme/apps/metrics/grafana-data:/var/lib/grafana
```

Delete the trailing `volumes:` block (`prometheus-data:` / `grafana-data:`); keep the `networks:` block intact.

- [ ] **Step 5: Sync to Dockge stack dir and start**

Dockge UI → `metrics` stack → **Update** → **Start**. Wait for all three services "Running".

- [ ] **Step 6: Functional verify — Grafana UI + Prometheus continuity**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://10.63.0.2:3001/
curl -s 'http://10.63.0.2:3001/api/health' | head -5
```

Then in the Grafana UI (`http://10.63.0.2:3001`) open the spend dashboard and confirm:
- The Claude Code panels render (no "datasource error").
- A Prometheus panel with `max_over_time(...) [7d]` shows continuous data including pre-migration timestamps. (No gap → metric history survived.)

Expected: HTTP 200 from Grafana root, `{"database":"ok"}` from `/api/health`, dashboards intact, no Prometheus data gap.

- [ ] **Step 7: Reclaim old volumes**

```bash
sudo docker volume rm metrics_grafana-data metrics_prometheus-data
sudo docker volume ls | grep -E 'metrics_(grafana|prometheus)' || echo "metrics named volumes removed"
```

Expected: both removals succeed.

- [ ] **Step 8: Commit**

```bash
git add nas/metrics/docker-compose.yml
git commit -m "feat(metrics): migrate grafana + prometheus to host-path bind mounts

Backup Phase 0 continuation. Prometheus retains 1y history we
can't recreate; bind-mount lets it ride snapshots + Borg."
```

---

### Task 4: Migrate Langfuse (four volumes — ClickHouse is fragile)

**Files:**
- Modify: `nas/langfuse/docker-compose.yml` (replace four named volumes with bind mounts)

- [ ] **Step 1: Stop the Langfuse stack from Dockge**

Dockge UI → `langfuse` stack → **Stop**. Wait until **all six** services report "Stopped" (web, worker, postgres, clickhouse, redis, minio).

```bash
sudo docker logs langfuse-clickhouse-1 --tail 30 | grep -i 'graceful shutdown\|shutdown'
sudo docker logs langfuse-postgres-1   --tail 10 | grep -i 'database system is shut down'
```

Expected: ClickHouse logged a graceful shutdown; Postgres logged "database system is shut down". **If ClickHouse did not shut down cleanly, do NOT proceed — start the stack, stop it again, recheck.** ClickHouse on a dirty stop is a real recovery hazard.

- [ ] **Step 2: rsync all four volumes**

```bash
sudo rsync -aHAX --info=progress2 \
  /mnt/.ix-apps/docker/volumes/langfuse_langfuse-pgdata/_data/ \
  /mnt/nvme/apps/langfuse/pgdata/

sudo rsync -aHAX --info=progress2 \
  /mnt/.ix-apps/docker/volumes/langfuse_langfuse-clickhouse-data/_data/ \
  /mnt/nvme/apps/langfuse/clickhouse-data/

sudo rsync -aHAX --info=progress2 \
  /mnt/.ix-apps/docker/volumes/langfuse_langfuse-clickhouse-logs/_data/ \
  /mnt/nvme/apps/langfuse/clickhouse-logs/

sudo rsync -aHAX --info=progress2 \
  /mnt/.ix-apps/docker/volumes/langfuse_langfuse-minio-data/_data/ \
  /mnt/nvme/apps/langfuse/minio-data/
```

Expected: all four finish with 0 errors. Sizes match Task 1 Step 1.

- [ ] **Step 3: Force ownership per §4.1**

```bash
sudo chown -R 999:999 /mnt/nvme/apps/langfuse/pgdata
sudo chown -R 101:101 /mnt/nvme/apps/langfuse/clickhouse-data
sudo chown -R 101:101 /mnt/nvme/apps/langfuse/clickhouse-logs
sudo chown -R 0:0     /mnt/nvme/apps/langfuse/minio-data

for p in pgdata clickhouse-data clickhouse-logs minio-data; do
  sudo stat -c '%u:%g %n' "/mnt/nvme/apps/langfuse/$p"
done
```

Expected (in order): `999:999 …pgdata`, `101:101 …clickhouse-data`, `101:101 …clickhouse-logs`, `0:0 …minio-data`.

- [ ] **Step 4: Edit `nas/langfuse/docker-compose.yml`**

Replace each named-volume reference with a bind mount:

```yaml
      - langfuse-pgdata:/var/lib/postgresql/data
```
→
```yaml
      - /mnt/nvme/apps/langfuse/pgdata:/var/lib/postgresql/data
```

```yaml
      - langfuse-clickhouse-data:/var/lib/clickhouse
      - langfuse-clickhouse-logs:/var/log/clickhouse-server
```
→
```yaml
      - /mnt/nvme/apps/langfuse/clickhouse-data:/var/lib/clickhouse
      - /mnt/nvme/apps/langfuse/clickhouse-logs:/var/log/clickhouse-server
```

```yaml
      - langfuse-minio-data:/data
```
→
```yaml
      - /mnt/nvme/apps/langfuse/minio-data:/data
```

Delete the trailing top-level `volumes:` block entirely.

- [ ] **Step 5: Sync to Dockge stack dir and start**

Dockge UI → `langfuse` stack → **Update** → **Start**. Watch the log pane: ClickHouse first ("Ready for connections"), then web + worker.

- [ ] **Step 6: Functional verify — UI + pre-migration trace visible**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://10.63.0.2:3000/
curl -s 'http://10.63.0.2:3000/api/public/health' | head -5
```

Then in Langfuse UI (`http://10.63.0.2:3000`):
- Log in with the seeded user.
- Open **Traces** and confirm a trace from **before the migration** is visible (sort by oldest).
- Open a recent gateway call from LiteLLM (if any traffic happened) and confirm metadata renders.

Expected: HTTP 200 from web root; `/api/public/health` returns OK JSON; old traces present; no ClickHouse error banner.

- [ ] **Step 7: Reclaim old volumes**

```bash
sudo docker volume rm \
  langfuse_langfuse-pgdata \
  langfuse_langfuse-clickhouse-data \
  langfuse_langfuse-clickhouse-logs \
  langfuse_langfuse-minio-data
sudo docker volume ls | grep langfuse || echo "langfuse named volumes removed"
```

Expected: all four removals succeed.

- [ ] **Step 8: Commit**

```bash
git add nas/langfuse/docker-compose.yml
git commit -m "feat(langfuse): migrate all four volumes to host-path bind mounts

Backup Phase 0 final step. ClickHouse + Postgres + MinIO + CH-logs
now ride /mnt/nvme/apps/langfuse/ so they reach snapshots, replication,
and Borg. (clickhouse-logs will be excluded from Borg per spec §6.6.)"
```

---

### Task 5: Phase 0 verification gate

**Files:** none.

- [ ] **Step 1: Confirm all seven host paths populated with correct ownership**

```bash
for p in litellm/pgdata \
         langfuse/pgdata langfuse/clickhouse-data langfuse/clickhouse-logs langfuse/minio-data \
         metrics/grafana-data metrics/prometheus-data; do
  printf "%-40s " "$p"
  sudo stat -c '%u:%g size=%s' "/mnt/nvme/apps/$p"
  sudo du -sh "/mnt/nvme/apps/$p" | awk '{print "  used:", $1}'
done
```

Expected: each line shows the UID:GID from §4.1 and a non-zero size matching pre-migration.

- [ ] **Step 2: Confirm no `litellm_*`, `langfuse_*`, or `metrics_*` named volumes remain**

```bash
sudo docker volume ls --format '{{.Name}}' | grep -E '^(litellm|langfuse|metrics)_' && echo FAIL || echo OK
```

Expected: `OK`.

- [ ] **Step 3: Confirm all three stacks are running**

```bash
sudo docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'litellm|langfuse|metrics' | sort
```

Expected: every container in `Up` state, no `Restarting`.

- [ ] **Step 4: End-to-end gateway → Langfuse round-trip**

From Arch (or any LAN host):

```bash
curl -s -X POST http://10.63.0.2:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"main","messages":[{"role":"user","content":"phase0 verify ping"}],"max_tokens":20}'
```

Expected: a non-error JSON response. Then within ~30s, a new trace appears in Langfuse (`http://10.63.0.2:3000`) under Traces with the prompt `phase0 verify ping`. **If no trace appears, Phase 0 is not done — debug before continuing.**

---

## Phase 1 — Local ZFS snapshots

### Task 6: Configure Periodic Snapshot Tasks (three classes + snapshot-only)

**Files:** none modified — TrueNAS UI only. (Configs are not stored in git for Periodic Snapshot Tasks; the source of truth is TrueNAS itself, mirrored into the runbook in Task 14.)

- [ ] **Step 1: Configure Class A — Hot transactional (hourly)**

TrueNAS UI → **Data Protection** → **Periodic Snapshot Tasks** → **Add**, one per dataset below. Settings for **each**:

- **Dataset:** (per list)
- **Recursive:** **off**
- **Naming schema:** `auto-%Y-%m-%d_%H-%M-A`
- **Schedule:** hourly (`@hourly` preset, or custom `0 * * * *`)
- **Snapshot Lifetime:** **24 hours** for the hourly cadence... but you also need a daily anchor; TrueNAS has only one lifetime per task, so create two tasks per dataset:
  - **Task A-hourly:** `0 * * * *`, lifetime **24 hours**.
  - **Task A-daily:** `0 3 * * *`, lifetime **14 days**.

Datasets:

| Dataset |
|---|
| `nvme/apps/litellm/pgdata` |
| `nvme/apps/langfuse/pgdata` |
| `nvme/apps/langfuse/clickhouse-data` |
| `nvme/apps/langfuse/minio-data` |
| `nvme/apps/n8n/postgres` |
| `nvme/apps/paperless-ngx/pgdata` |
| `nvme/apps/immich/pgdata` |
| `nvme/apps/metrics/grafana-data` |
| `nvme/apps/forgejo/postgres_data` *(skip if Forgejo not yet installed — add when it is)* |

(Forgejo Postgres lands here once the Forgejo spec ships; if absent today, configure on first deploy.)

- [ ] **Step 2: Configure Class B — Standard app data (every 6 hours + daily + monthly)**

Three tasks per dataset:

- **Task B-6h:** `0 */6 * * *`, lifetime **3 days** (gives 12 × 6h snapshots).
- **Task B-daily:** `0 3 * * *`, lifetime **30 days**.
- **Task B-monthly:** `0 3 1 * *`, lifetime **6 months**.

Datasets:

| Dataset |
|---|
| `nvme/apps/dockge` *(recursive: on — covers `stacks` + `data`)* |
| `nvme/apps/n8n/data` |
| `nvme/apps/joplin` |
| `nvme/apps/pihole` *(recursive: on)* |
| `nvme/apps/nginx-proxy-manager` *(recursive: on)* |
| `nvme/apps/paperless-ngx/data` |
| `nvme/apps/forgejo/data` *(when present)* |
| `nvme/apps/metrics/prometheus-data` |

- [ ] **Step 3: Configure Class C — Cold irreplaceable (daily + monthly + yearly)**

Three tasks per dataset:

- **Task C-daily:** `0 3 * * *`, lifetime **30 days**.
- **Task C-monthly:** `0 3 1 * *`, lifetime **12 months**.
- **Task C-yearly:** `0 3 1 1 *`, lifetime **5 years**.

Datasets:

| Dataset |
|---|
| `tank/apps/immich/data` |
| `tank/apps/paperless-ngx/media` |
| `tank/apps/paperless-ngx/consume` |
| `tank/apps/paperless-ngx/trash` |
| `tank/storage` *(recursive: on)* |
| `tank/apps/forgejo/lfs` *(when present)* |

- [ ] **Step 4: Configure snapshot-only — `tank/home`**

Single task:
- **Dataset:** `tank/home` (recursive: off)
- **Naming:** `auto-%Y-%m-%d_%H-%M-snaponly`
- **Schedule:** `0 3 * * *` daily
- **Lifetime:** **14 days**

No replication, no offsite — `tank/home` is the snapshot-only tier (stale Win→Linux transfer leftover, cleanup candidate per spec §5.1).

- [ ] **Step 5: Verify task list is complete**

TrueNAS UI → Data Protection → Periodic Snapshot Tasks. Sort by Dataset. Confirm every dataset from §5.1 has the right number of tasks (Class A: 2; Class B: 3; Class C: 3; snapshot-only: 1). Cross-check against the spec.

```bash
# Optional CLI check from NAS shell:
sudo midclt call pool.snapshottask.query | jq -r '.[] | "\(.dataset)\t\(.lifetime_value)\(.lifetime_unit[0])\t\(.schedule.minute) \(.schedule.hour) \(.schedule.dom) \(.schedule.month) \(.schedule.dow)"' | sort
```

Expected: one line per task; counts match the table above.

- [ ] **Step 6: Wait 24h and re-verify snapshots fire on schedule**

After ~25 hours have elapsed:

```bash
sudo zfs list -t snapshot -o name,creation -s creation | grep auto- | tail -40
```

Expected:
- At least 24 hourly snapshots on every Class A dataset (e.g., `nvme/apps/litellm/pgdata@auto-...A`).
- At least one daily snapshot (created at 03:00) on every Class A/B/C dataset.
- Snapshot lifetimes look right (TrueNAS will auto-destroy older ones — none beyond their TTL should remain).

If any Class A dataset has fewer than ~24 hourly snapshots, debug before declaring Phase 1 done.

- [ ] **Step 7: No commit — TrueNAS state, mirrored in runbook later**

---

## Phase 2 — Local replication NVMe → HDD

### Task 7: Create replica parent + Replication Tasks

**Files:** none in repo — TrueNAS UI only. Runbook entry in Task 14.

- [ ] **Step 1: Create `tank/replica/nvme-apps` parent**

TrueNAS UI → Datasets → `tank` → Add Dataset → name `replica`, Generic preset. Then add child `nvme-apps` under `tank/replica`.

```bash
sudo zfs list -o name,mountpoint | grep '^tank/replica'
```

Expected: `tank/replica` and `tank/replica/nvme-apps` exist with mountpoints `/mnt/tank/replica` and `/mnt/tank/replica/nvme-apps`.

- [ ] **Step 2: Add one Replication Task per protected NVMe parent**

TrueNAS UI → Data Protection → Replication Tasks → Add. Configure each as **Local** (same system) replication. Per source parent below:

| Source dataset (recursive) | Destination |
|---|---|
| `nvme/apps/dockge` | `tank/replica/nvme-apps/dockge` |
| `nvme/apps/n8n` | `tank/replica/nvme-apps/n8n` |
| `nvme/apps/paperless-ngx` | `tank/replica/nvme-apps/paperless-ngx` |
| `nvme/apps/immich` | `tank/replica/nvme-apps/immich` |
| `nvme/apps/nginx-proxy-manager` | `tank/replica/nvme-apps/nginx-proxy-manager` |
| `nvme/apps/pihole` | `tank/replica/nvme-apps/pihole` |
| `nvme/apps/joplin` | `tank/replica/nvme-apps/joplin` |
| `nvme/apps/litellm` | `tank/replica/nvme-apps/litellm` |
| `nvme/apps/langfuse` | `tank/replica/nvme-apps/langfuse` |
| `nvme/apps/metrics` | `tank/replica/nvme-apps/metrics` |
| `nvme/apps/forgejo` *(when present)* | `tank/replica/nvme-apps/forgejo` |

Settings on every task:
- **Recursive:** on
- **Include snapshots created by:** the Periodic Snapshot Tasks from Phase 1 (select all that match the source parent).
- **Schedule:** **04:00 daily** (`0 4 * * *`).
- **Destination snapshot lifetime:** **Same as source** (matched retention).
- **Properties:** Yes (replicate ZFS properties).
- **Encryption:** disabled (same pool — no need).

- [ ] **Step 3: Trigger initial seed for each task manually**

Click **Run Now** on each Replication Task. The first run transfers everything; subsequent runs are incremental. Watch task status until **SUCCESS**. Initial seed for `langfuse` (ClickHouse) is the largest.

- [ ] **Step 4: Verify every protected NVMe dataset has a populated replica**

```bash
sudo zfs list -r tank/replica/nvme-apps -o name,used,refer | head -50
sudo zfs list -t snapshot -r tank/replica/nvme-apps | wc -l
```

Expected: every parent dataset above shows non-trivial `used`/`refer`; snapshot count > 0 (each source snapshot replicated through).

- [ ] **Step 5: Spot-check snapshot lists match source vs destination**

For one dataset (pick `litellm`):

```bash
sudo zfs list -t snapshot -o name,creation -s creation nvme/apps/litellm/pgdata | tail -10
sudo zfs list -t snapshot -o name,creation -s creation tank/replica/nvme-apps/litellm/pgdata | tail -10
```

Expected: the tail snapshot names + creation times match between source and destination (the daily anchor 03:00 snapshot should be on both).

- [ ] **Step 6: No commit — TrueNAS state, runbook later**

---

## Phase 3 — Offsite tier (Borg + Borgmatic → Hetzner)

### Task 8: Order BX21 and one-time Storage Box setup

**Files:** none — external provisioning.

- [ ] **Step 1: Order Hetzner Storage Box BX21 (EU location)**

Hetzner Robot → Storage Boxes → **Order BX21** (5 TB, ~€10.50/mo). Pick an EU location (Helsinki or Falkenstein — match preference, both fine).

- [ ] **Step 2: Note credentials in the password manager**

After provisioning, record in the password manager under a new entry **"Hetzner Storage Box — BX21 (AISetup offsite)"**:
- Hostname (e.g., `u123456.your-storagebox.de`)
- Username (e.g., `u123456`)
- Password
- **Port: 23** (Hetzner quirk; not 22 — getting this wrong = silent timeout)

- [ ] **Step 3: Enable SSH access on the Storage Box**

Hetzner UI → Storage Box settings → enable **SSH support** (off by default on some plans). Also enable **External reachability** if not on by default.

- [ ] **Step 4: SSH in once and record the host key fingerprint**

From any host:

```bash
ssh -p 23 u123456@u123456.your-storagebox.de
# Type "yes" at the host-key prompt. Record the fingerprint TrueNAS shows.
# Then exit immediately.
```

Expected: connection succeeds; remote shell briefly opens. Record the SHA256 host-key fingerprint in the password manager entry from Step 2.

---

### Task 9: Generate writer + admin SSH keypairs

**Files:** none in repo (private keys never committed); public keys recorded in Storage Box `authorized_keys`.

- [ ] **Step 1: Generate the writer keypair on the Arch workstation (temporary location for transport)**

```bash
mkdir -p ~/borg-keys-staging
cd ~/borg-keys-staging
ssh-keygen -t ed25519 -f borg-writer-nas -C "borg-writer@nas-aisetup" -N ""
ssh-keygen -t ed25519 -f borg-admin-arch -C "borg-admin@arch-aisetup" -N ""
ls -l
```

Expected: four files — `borg-writer-nas`, `borg-writer-nas.pub`, `borg-admin-arch`, `borg-admin-arch.pub`. No passphrase on either (passphrase protection isn't the threat model — host compromise is, and key restriction handles that).

- [ ] **Step 2: Install both public keys on the Storage Box**

```bash
ssh -p 23 u123456@u123456.your-storagebox.de mkdir -p .ssh

# Append writer key with append-only restriction:
WRITER_PUB=$(cat ~/borg-keys-staging/borg-writer-nas.pub)
ssh -p 23 u123456@u123456.your-storagebox.de \
  "echo 'command=\"borg serve --append-only --restrict-to-path /home/u123456/borg-repo\",no-port-forwarding,no-X11-forwarding,no-pty ${WRITER_PUB}' >> .ssh/authorized_keys"

# Append admin key unrestricted:
ADMIN_PUB=$(cat ~/borg-keys-staging/borg-admin-arch.pub)
ssh -p 23 u123456@u123456.your-storagebox.de \
  "echo '${ADMIN_PUB}' >> .ssh/authorized_keys"

# Verify:
ssh -p 23 u123456@u123456.your-storagebox.de "cat .ssh/authorized_keys"
```

Expected: two lines visible — the first prefixed with `command="borg serve --append-only ...`, the second a bare `ssh-ed25519 ...`.

- [ ] **Step 3: Move the admin private key into `~/.ssh/` on Arch and delete the staging copy**

```bash
mv ~/borg-keys-staging/borg-admin-arch     ~/.ssh/borg-admin-arch
mv ~/borg-keys-staging/borg-admin-arch.pub ~/.ssh/borg-admin-arch.pub
chmod 600 ~/.ssh/borg-admin-arch
chmod 644 ~/.ssh/borg-admin-arch.pub
ls -l ~/.ssh/borg-admin-arch*
```

Expected: `borg-admin-arch` mode `0600`, owned by your user.

- [ ] **Step 4: Copy the writer keypair to the NAS, then delete from staging**

```bash
# Copy to NAS (will be bind-mounted into the Borgmatic container in Task 11):
ssh root@10.63.0.2 mkdir -p /mnt/nvme/apps/borgmatic/secrets
scp ~/borg-keys-staging/borg-writer-nas{,.pub} \
  root@10.63.0.2:/mnt/nvme/apps/borgmatic/secrets/

# Lock down perms on the NAS:
ssh root@10.63.0.2 'chmod 600 /mnt/nvme/apps/borgmatic/secrets/borg-writer-nas && \
                    chmod 644 /mnt/nvme/apps/borgmatic/secrets/borg-writer-nas.pub && \
                    chown -R root:root /mnt/nvme/apps/borgmatic/secrets'

# Wipe staging:
shred -u ~/borg-keys-staging/borg-writer-nas*
rmdir ~/borg-keys-staging
ls ~/borg-keys-staging 2>&1 || echo "staging gone"
```

Expected: writer key + pub on the NAS at `/mnt/nvme/apps/borgmatic/secrets/` mode `0600`; staging dir removed.

- [ ] **Step 5: Smoke-test both keys against the Storage Box**

```bash
# From Arch — admin key unrestricted, should land in a shell:
ssh -i ~/.ssh/borg-admin-arch -p 23 u123456@u123456.your-storagebox.de "ls -la"

# From NAS — writer key restricted, should NOT open a shell:
ssh -i /mnt/nvme/apps/borgmatic/secrets/borg-writer-nas \
    -p 23 u123456@u123456.your-storagebox.de "ls -la" 2>&1 | head -5
```

Expected:
- Admin SSH from Arch shows a directory listing (writable, no restriction).
- Writer SSH from NAS errors with something like `borg serve: invalid arguments` or refuses the command — because the `command="..."` clause forces `borg serve` regardless of what you sent. **That is the desired result; it proves the restriction is active.**

---

### Task 10: Secret escrow (3-2-1 rule for unrecoverable secrets)

**Files:** none — physical/offline action. Recorded as a runbook entry in Task 14.

- [ ] **Step 1: Generate the Borg passphrase**

On Arch:

```bash
openssl rand -base64 48
```

Expected: a long random string. Save it; do **not** echo it into the shell history of a shared host.

- [ ] **Step 2: Store the passphrase + both private keys in three places**

**Three copies, two media, one offsite.** Concretely:

1. **Password manager** (primary, online). Create entries:
   - **Borg passphrase — AISetup repo** — value: the passphrase from Step 1.
   - Attach files: `~/.ssh/borg-admin-arch`, `~/.ssh/borg-admin-arch.pub`, and a copy of `borg-writer-nas` (export from NAS once, attach, do not keep an extra local copy).

2. **Printed paper, fire-safe** (secondary, offline). Print:
   - Borg passphrase (large font).
   - Storage Box hostname, port 23, username.
   - Repo URL: `ssh://u123456@u123456.your-storagebox.de:23/./borg-repo`.

   Both private keys are too long to print readably and useless without a password manager anyway — paper carries the passphrase only.

3. **Encrypted USB, offsite** (parents' house, bank deposit box, similar). Contents:
   - A copy of `borg-admin-arch` + `.pub`
   - A copy of `borg-writer-nas` + `.pub`
   - A text file with the Borg passphrase, Storage Box creds.
   Encrypt the USB with **LUKS** (`cryptsetup luksFormat /dev/sdX`); record the LUKS passphrase in the password manager.

- [ ] **Step 3: Document the escrow in the runbook**

(Runbook section is created in Task 14 — for now just **note the three locations** so they end up there. Without the locations being recorded, escrow is a fiction.)

---

### Task 11: Build `nas/borgmatic/` Dockge stack

**Files:**
- Create: `nas/borgmatic/docker-compose.yml`
- Create: `nas/borgmatic/borgmatic.yaml`
- Create: `nas/borgmatic/.env.example`
- Create: `nas/borgmatic/README.md`

- [ ] **Step 1: Create the compose file**

`nas/borgmatic/docker-compose.yml`:

```yaml
# Borgmatic — orchestrates nightly Borg archive to the Hetzner Storage Box.
# Deploy as its own Dockge stack on a NAS pool path (10.63.0.2). Container has
# read-only source mounts; the writer SSH key is mounted 0600. The container
# joins LiteLLM / Langfuse / n8n / Forgejo (where present) Docker networks so
# Borgmatic's pre-hooks can reach their Postgres + ClickHouse instances for
# native dumps.
services:
  borgmatic:
    image: ghcr.io/borgmatic-collective/borgmatic:latest
    restart: always
    hostname: nas-aisetup
    environment:
      TZ: Europe/Berlin
      # Cron line baked into the container entrypoint — daily 05:00, plus a
      # weekly Sun 06:00 repo-only check (read-OK with the append-only key).
      BORGMATIC_CRON: |
        0 5 * * *  root  borgmatic --verbosity 1 create
        0 6 * * 0  root  borgmatic --verbosity 1 check --only repository
      BORG_PASSPHRASE: ${BORG_PASSPHRASE}
      # Pre-hook DB creds (used inside borgmatic.yaml):
      LITELLM_PG_PASSWORD:  ${LITELLM_PG_PASSWORD}
      LANGFUSE_PG_PASSWORD: ${LANGFUSE_PG_PASSWORD}
      N8N_PG_PASSWORD:      ${N8N_PG_PASSWORD}
      PAPERLESS_PG_PASSWORD: ${PAPERLESS_PG_PASSWORD}
      IMMICH_PG_PASSWORD:   ${IMMICH_PG_PASSWORD}
      FORGEJO_PG_PASSWORD:  ${FORGEJO_PG_PASSWORD}     # blank until Forgejo lands
      CLICKHOUSE_PASSWORD:  ${CLICKHOUSE_PASSWORD}
      # Failure-path hooks:
      NTFY_TOPIC:          ${NTFY_TOPIC}
      HEALTHCHECKS_PING_URL: ${HEALTHCHECKS_PING_URL}
    volumes:
      # Config:
      - ./borgmatic.yaml:/etc/borgmatic/config.yaml:ro
      # Writer SSH key (host-side perms 0600, root-owned):
      - /mnt/nvme/apps/borgmatic/secrets/borg-writer-nas:/root/.ssh/id_ed25519:ro
      - /mnt/nvme/apps/borgmatic/secrets/borg-writer-nas.pub:/root/.ssh/id_ed25519.pub:ro
      # Local Borg cache + state (persistent so dedup index isn't rebuilt every run):
      - /mnt/nvme/apps/borgmatic/borg-cache:/root/.cache/borg
      - /mnt/nvme/apps/borgmatic/borg-config:/root/.config/borg
      # Source datasets (READ-ONLY — backup must never mutate live):
      - /mnt/nvme/apps/dockge:/source/nvme/dockge:ro
      - /mnt/nvme/apps/n8n:/source/nvme/n8n:ro
      - /mnt/nvme/apps/paperless-ngx:/source/nvme/paperless-ngx:ro
      - /mnt/nvme/apps/immich:/source/nvme/immich:ro
      - /mnt/nvme/apps/nginx-proxy-manager:/source/nvme/nginx-proxy-manager:ro
      - /mnt/nvme/apps/pihole:/source/nvme/pihole:ro
      - /mnt/nvme/apps/joplin:/source/nvme/joplin:ro
      - /mnt/nvme/apps/litellm:/source/nvme/litellm:ro
      - /mnt/nvme/apps/langfuse:/source/nvme/langfuse:ro
      - /mnt/nvme/apps/metrics:/source/nvme/metrics:ro
      - /mnt/nvme/apps/forgejo:/source/nvme/forgejo:ro     # may be missing pre-Forgejo
      - /mnt/tank/apps/immich/data:/source/tank/immich-data:ro
      - /mnt/tank/apps/paperless-ngx:/source/tank/paperless-ngx:ro
      - /mnt/tank/storage:/source/tank/storage:ro
      - /mnt/tank/apps/forgejo/lfs:/source/tank/forgejo-lfs:ro   # may be missing pre-Forgejo
    networks:
      - litellm_default          # for pg_dump against litellm-db
      - langfuse_default         # for pg_dump + clickhouse-client
      - n8n_default              # for pg_dump (TrueNAS-app network name TBD; fix on first deploy)
      - forgejo_default          # when present

networks:
  litellm_default:  { external: true }
  langfuse_default: { external: true }
  n8n_default:      { external: true }
  forgejo_default:  { external: true }
```

**On `BORGMATIC_CRON`:** the upstream image runs `crond` from `/etc/cron.d` by default; this env var renders into that file at container start. If the image you pull doesn't honor it, drop the env var and bake the cron line into a derived image — but the standard `borgmatic-collective` image does.

**On network names:** confirm with `sudo docker network ls` before first deploy. If a stack's network is named `<dir>_default` (Docker Compose default), the names above match. If you've overridden, edit accordingly. Comment out `forgejo_default` until Forgejo ships.

- [ ] **Step 2: Create the borgmatic config**

`nas/borgmatic/borgmatic.yaml`:

```yaml
# Borgmatic config — NAS-wide nightly archive to Hetzner BX21.
# One archive contains every protected dataset; Borg dedup makes this efficient.
# Pre-hooks dump Postgres + ClickHouse to consistent snapshots before borg create.

source_directories:
  - /source/nvme/dockge
  - /source/nvme/n8n
  - /source/nvme/paperless-ngx
  - /source/nvme/immich
  - /source/nvme/nginx-proxy-manager
  - /source/nvme/pihole
  - /source/nvme/joplin
  - /source/nvme/litellm
  - /source/nvme/langfuse
  - /source/nvme/metrics
  - /source/nvme/forgejo               # remove until Forgejo lands if mount missing
  - /source/tank/immich-data
  - /source/tank/paperless-ngx
  - /source/tank/storage
  - /source/tank/forgejo-lfs           # remove until Forgejo lands if mount missing

# Exclude regrowing logs + transient state. Glob patterns avoid PG internals
# (pg_log uses different name; pg_wal / pg_xact are not matched and stay safe).
exclude_patterns:
  - /source/nvme/langfuse/clickhouse-logs
  - '**/cache/**'
  - '**/Cache/**'
  - '**/tmp/**'
  - '**/log/**'
  - '**/logs/**'
  - '**/*.log'
  - '**/transcode-cache/**'

repositories:
  - path: ssh://u123456@u123456.your-storagebox.de:23/./borg-repo
    label: hetzner-bx21

encryption_passcommand: "echo $BORG_PASSPHRASE"

# Archive naming: hostname-timestamp lets borg list show calendar history.
archive_name_format: 'nas-aisetup-{now:%Y-%m-%dT%H:%M:%S}'

# Compression: lz4 default; switch to zstd,3 for slightly better ratio at
# negligible CPU cost on the NAS.
compression: zstd,3

# SSH options — explicit port + key inside the container.
ssh_command: ssh -p 23 -i /root/.ssh/id_ed25519 -o StrictHostKeyChecking=accept-new

# Retention is enforced from Arch (admin key). Borgmatic on the NAS does NOT
# run prune (the append-only writer key can't anyway). Documenting numbers
# here for cross-reference; arch/borg-admin/borgmatic.yaml is the active copy.
retention:
  keep_daily: 7
  keep_weekly: 4
  keep_monthly: 12
  keep_yearly: 3

# Pre-backup database dumps — Borgmatic native hooks. Each dump is included
# in the archive and removed locally after borg create.
postgresql_databases:
  - name: litellm
    hostname: litellm-db
    port: 5432
    username: litellm
    password: ${LITELLM_PG_PASSWORD}
    format: custom
  - name: langfuse
    hostname: postgres            # service name inside langfuse_default
    port: 5432
    username: langfuse
    password: ${LANGFUSE_PG_PASSWORD}
    format: custom
  - name: n8n
    hostname: n8n-postgres        # adjust to actual service name on first deploy
    port: 5432
    username: n8n
    password: ${N8N_PG_PASSWORD}
    format: custom
  - name: paperless
    hostname: paperless-db        # adjust on first deploy
    port: 5432
    username: paperless
    password: ${PAPERLESS_PG_PASSWORD}
    format: custom
  - name: immich
    hostname: immich-postgres     # adjust on first deploy
    port: 5432
    username: immich
    password: ${IMMICH_PG_PASSWORD}
    format: custom
  # Forgejo block — uncomment when Forgejo ships:
  # - name: forgejo
  #   hostname: forgejo-db
  #   port: 5432
  #   username: forgejo
  #   password: ${FORGEJO_PG_PASSWORD}
  #   format: custom

clickhouse_databases:
  - name: default
    hostname: clickhouse          # service name inside langfuse_default
    port: 9000
    username: clickhouse
    password: ${CLICKHOUSE_PASSWORD}

# MinIO needs no dump — Borg dedup handles object-store files efficiently;
# /source/nvme/langfuse/minio-data is in source_directories.

# Notification hooks: on_error -> ntfy push; on success -> healthchecks ping.
ntfy:
  topic: ${NTFY_TOPIC}
  server: https://ntfy.sh
  start:
    title: "[backup] nightly start"
    message: "borgmatic create starting on nas-aisetup"
    priority: low
  finish:
    title: "[backup] nightly OK"
    message: "borgmatic create completed; archive added"
    priority: low
  fail:
    title: "[backup] FAILED"
    message: "borgmatic FAILED on nas-aisetup — investigate"
    priority: max

healthchecks:
  ping_url: ${HEALTHCHECKS_PING_URL}
  send_logs: true
  states:
    - finish
    - fail
```

**Note on the `n8n-postgres` / `paperless-db` / `immich-postgres` hostnames:** TrueNAS-app stacks set their own service names. Confirm with `sudo docker ps --format '{{.Names}}'` and adjust to the live names on first deploy.

- [ ] **Step 3: Create `.env.example`**

`nas/borgmatic/.env.example`:

```bash
# Borgmatic stack secrets — copy to .env on the NAS Dockge dir; .env is gitignored.
# All of these must match the live service credentials.

# Long random; never changes once chosen. ESCROW REQUIRED (see runbook).
BORG_PASSPHRASE=

# Service Postgres passwords (read from each stack's .env):
LITELLM_PG_PASSWORD=
LANGFUSE_PG_PASSWORD=
N8N_PG_PASSWORD=
PAPERLESS_PG_PASSWORD=
IMMICH_PG_PASSWORD=
FORGEJO_PG_PASSWORD=

# Langfuse ClickHouse password:
CLICKHOUSE_PASSWORD=

# Notifications:
NTFY_TOPIC=aisetup-backup-CHANGE-ME-RANDOM-SUFFIX
HEALTHCHECKS_PING_URL=https://hc-ping.com/<your-uuid>
```

- [ ] **Step 4: Create `nas/borgmatic/README.md`**

````markdown
# nas/borgmatic — offsite backup stack

Dockge stack on the NAS (`10.63.0.2`) that runs `borgmatic` nightly at 05:00 and
pushes one deduplicated archive to a Hetzner Storage Box BX21 over SSH port 23.

**Design spec:** [`../../docs/superpowers/specs/2026-05-25-nas-backup-strategy-design.md`](../../docs/superpowers/specs/2026-05-25-nas-backup-strategy-design.md)

## Hetzner Storage Box

- **Plan:** BX21 (5 TB, ~€10.50/mo)
- **Host:** `u123456.your-storagebox.de`
- **User:** `u123456`
- **Port:** **23** (Hetzner quirk — not 22)
- **Repo path:** `/home/u123456/borg-repo` → `ssh://u123456@u123456.your-storagebox.de:23/./borg-repo`

## Two-key model (ransomware defense)

| Key | Where it lives | Capability |
|---|---|---|
| `borg-writer-nas` | NAS (`/mnt/nvme/apps/borgmatic/secrets/`, mode 0600) | Create archives only. Cannot delete. Storage Box `authorized_keys` line: `command="borg serve --append-only --restrict-to-path /home/u123456/borg-repo",no-port-forwarding,no-X11-forwarding,no-pty <pub>` |
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
- `n8n_default` / `paperless_*_default` / `immich_*_default` / `forgejo_default` (when present).

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
````

- [ ] **Step 5: Commit**

```bash
git add nas/borgmatic/
git commit -m "feat(borgmatic): add NAS-side offsite backup stack

Borgmatic Dockge stack: nightly 05:00 borg create to Hetzner BX21
via append-only writer key, weekly repo-only check (read OK with
append-only), pre-hooks dump Postgres + ClickHouse for consistency.
Repo state-modifying ops (prune, deep check, restore) live on Arch
under arch/borg-admin/ with the admin key the NAS never sees."
```

---

### Task 12: Build `arch/borg-admin/` (admin-side artifact)

**Files:**
- Create: `arch/borg-admin/borgmatic.yaml`
- Create: `arch/borg-admin/borgmatic-prune.service`
- Create: `arch/borg-admin/borgmatic-prune.timer`
- Create: `arch/borg-admin/borgmatic-check.service`
- Create: `arch/borg-admin/borgmatic-check.timer`
- Create: `arch/borg-admin/README.md`

- [ ] **Step 1: Create the admin-side borgmatic config**

`arch/borg-admin/borgmatic.yaml`:

```yaml
# Borgmatic config — Arch admin-side. Used for prune (weekly) and full check
# (monthly), plus ad-hoc restore. Does NOT create archives — that's the NAS
# stack's job.

repositories:
  - path: ssh://u123456@u123456.your-storagebox.de:23/./borg-repo
    label: hetzner-bx21

encryption_passcommand: "pass borg/aisetup-passphrase || echo $BORG_PASSPHRASE"

# Same retention contract as nas/borgmatic/borgmatic.yaml — but ENFORCED here.
retention:
  keep_daily: 7
  keep_weekly: 4
  keep_monthly: 12
  keep_yearly: 3

# Monthly deep check — verify-data reads & decrypts every chunk in the repo,
# catching silent corruption that the fast structural check would miss.
# Expensive: hours on a large repo and saturates upload. Worth it once a month.
checks:
  - name: repository
  - name: archives
  - name: data

# Use the admin SSH key. Port 23.
ssh_command: ssh -p 23 -i ~/.ssh/borg-admin-arch -o StrictHostKeyChecking=accept-new

# Failure notification — same ntfy topic as NAS, so a missed prune still
# pages.
ntfy:
  topic: ${NTFY_TOPIC}
  server: https://ntfy.sh
  fail:
    title: "[backup] arch admin op FAILED"
    message: "borgmatic admin op FAILED on arch"
    priority: max
```

- [ ] **Step 2: Create the prune timer + service units**

`arch/borg-admin/borgmatic-prune.service`:

```ini
[Unit]
Description=Borgmatic prune + compact (admin key, Hetzner BX21)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=%h/.config/borg-admin/env
ExecStart=/usr/bin/borgmatic --config %h/.config/borg-admin/borgmatic.yaml --verbosity 1 prune compact
```

`arch/borg-admin/borgmatic-prune.timer`:

```ini
[Unit]
Description=Weekly Borgmatic prune (Sun 09:00)

[Timer]
OnCalendar=Sun *-*-* 09:00:00
Persistent=true
Unit=borgmatic-prune.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Create the deep-check timer + service units**

`arch/borg-admin/borgmatic-check.service`:

```ini
[Unit]
Description=Borgmatic deep check (verify-data, admin key, Hetzner BX21)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=%h/.config/borg-admin/env
ExecStart=/usr/bin/borgmatic --config %h/.config/borg-admin/borgmatic.yaml --verbosity 1 check
```

`arch/borg-admin/borgmatic-check.timer`:

```ini
[Unit]
Description=Monthly Borgmatic deep check (first Sunday 10:00)

[Timer]
# systemd's "first Sunday of the month" idiom:
OnCalendar=Sun *-*-1..7 10:00:00
Persistent=true
Unit=borgmatic-check.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: Create `arch/borg-admin/README.md`**

````markdown
# arch/borg-admin — admin-side Borg ops

Companion to [`nas/borgmatic/`](../../nas/borgmatic/). Lives on the **Arch
workstation only.** Holds the unrestricted admin SSH key the NAS never sees,
so prune / deep check / restore can't be done by an attacker who owns the NAS.

**Design spec:** [`../../docs/superpowers/specs/2026-05-25-nas-backup-strategy-design.md`](../../docs/superpowers/specs/2026-05-25-nas-backup-strategy-design.md)

## Install path (user-systemd, no root)

```bash
mkdir -p ~/.config/borg-admin ~/.config/systemd/user
cp borgmatic.yaml ~/.config/borg-admin/borgmatic.yaml
cp borgmatic-{prune,check}.{service,timer} ~/.config/systemd/user/

# Env file the units read (mode 0600; NTFY_TOPIC matches NAS):
cat > ~/.config/borg-admin/env <<'EOF'
NTFY_TOPIC=aisetup-backup-<same-suffix-as-nas>
# BORG_PASSPHRASE intentionally not in env — pulled from `pass borg/aisetup-passphrase`.
EOF
chmod 600 ~/.config/borg-admin/env ~/.config/borg-admin/borgmatic.yaml

systemctl --user daemon-reload
systemctl --user enable --now borgmatic-prune.timer borgmatic-check.timer
systemctl --user list-timers borgmatic-*
```

## Keys (NOT committed)

- `~/.ssh/borg-admin-arch` (mode 0600) — the admin key.
- `~/.ssh/borg-admin-arch.pub` — its public key, already installed on the Storage Box.

Escrow per spec §8 Phase 3 step 3: password manager + printed paper + encrypted USB offsite.

## One-shot commands

```bash
# List archives:
borgmatic --config ~/.config/borg-admin/borgmatic.yaml list

# Browse contents of an archive (borgmatic's first-class mount action — honors config):
borgmatic --config ~/.config/borg-admin/borgmatic.yaml mount --archive ARCHIVE --mount-point /mnt/borg-restore

# Dry-run prune:
borgmatic --config ~/.config/borg-admin/borgmatic.yaml --dry-run prune

# Force a check now:
systemctl --user start borgmatic-check.service && journalctl --user -u borgmatic-check.service -f
```
````

- [ ] **Step 5: Commit**

```bash
git add arch/borg-admin/
git commit -m "feat(borg-admin): add Arch-side prune + check + restore artifact

Companion to nas/borgmatic/. User-systemd timers run weekly prune
(Sun 09:00) + monthly verify-data check (first Sun 10:00) using the
admin SSH key that lives only on Arch. Pruning from the NAS would
collapse the append-only ransomware defense, so it's strictly Arch."
```

---

### Task 13: Initialize the repo + first archive

**Files:** none modified — operational only.

- [ ] **Step 1: Fill `nas/borgmatic/.env` on the NAS Dockge dir from `.env.example`**

On the NAS, in the Dockge stack dir for `borgmatic`:

```bash
cp .env.example .env
# Edit with real values from the password manager + each stack's .env.
chmod 600 .env
```

Verify the file is mode `0600` and not committed: `git check-ignore .env || echo "WARNING: .env not gitignored"`.

- [ ] **Step 2: Initialize the repo from Arch (admin key, repokey-blake2 encryption)**

```bash
export BORG_PASSPHRASE='<paste from password manager>'
borg init \
  --encryption=repokey-blake2 \
  ssh://u123456@u123456.your-storagebox.de:23/./borg-repo \
  --rsh "ssh -p 23 -i ~/.ssh/borg-admin-arch"
```

Expected: command exits 0; no output (or the message "By default repositories initialized..."). A subsequent `borg info` shows an empty repo.

```bash
borg info ssh://u123456@u123456.your-storagebox.de:23/./borg-repo \
  --rsh "ssh -p 23 -i ~/.ssh/borg-admin-arch"
```

Expected: shows repo ID, encryption mode `repokey-blake2`, and 0 archives.

- [ ] **Step 3: Start the `borgmatic` stack from Dockge**

Sync the `nas/borgmatic/` files to the Dockge stack dir on the NAS. Dockge UI → **Compose** → paste/import the compose file → **Deploy**.

Wait for the `borgmatic` container to report **Up**. Watch the log pane for startup errors:

```bash
sudo docker logs $(sudo docker ps -q --filter "ancestor=ghcr.io/borgmatic-collective/borgmatic:latest") --tail 50
```

Expected: no SSH-key permission errors; no missing-network errors; `crond` starts.

- [ ] **Step 4: Run the first archive manually (multi-hour transfer)**

From the NAS:

```bash
sudo docker exec -it borgmatic-borgmatic-1 borgmatic --verbosity 2 create --stats
```

(Replace `borgmatic-borgmatic-1` with the actual container name — `docker ps`.)

Expected: pre-hooks run (`pg_dump` per database, `clickhouse-client BACKUP`), then `borg create` streams chunks to Hetzner. **This run is multi-hour** — leave it. The pre-hooks' dumps are streamed straight into the archive.

If any pre-hook fails (commonly: wrong service hostname, wrong password, network not joined), fix the underlying issue and re-run from this step.

- [ ] **Step 5: Verify the archive is visible from Arch with the admin key**

```bash
borg list ssh://u123456@u123456.your-storagebox.de:23/./borg-repo \
  --rsh "ssh -p 23 -i ~/.ssh/borg-admin-arch"
```

Expected: one line — `nas-aisetup-2026-05-25T...` (your archive). Then:

```bash
borg info ssh://u123456@u123456.your-storagebox.de:23/./borg-repo::nas-aisetup-<timestamp> \
  --rsh "ssh -p 23 -i ~/.ssh/borg-admin-arch"
```

Expected: archive stats — original size, compressed size, deduplicated size. Original size should roughly match the sum of all `du -sh` numbers on the source datasets.

---

### Task 14: Verify append-only restriction (negative test)

**Files:** none.

- [ ] **Step 1: Try to delete the test archive from the NAS — must fail**

From the NAS shell (inside the container):

```bash
sudo docker exec -it borgmatic-borgmatic-1 \
  borg delete ssh://u123456@u123456.your-storagebox.de:23/./borg-repo::nas-aisetup-<timestamp> \
  --rsh "ssh -p 23 -i /root/.ssh/id_ed25519"
```

Expected: command **fails** with a Borg error about append-only repository / not allowed. **If the delete succeeds, the append-only restriction is not configured correctly — go back to Task 9 Step 2 and fix the `authorized_keys` line before continuing.**

- [ ] **Step 2: Delete the test from Arch with the admin key — must succeed (and we won't actually do it)**

Just dry-run it to confirm the admin path works:

```bash
borg list ssh://u123456@u123456.your-storagebox.de:23/./borg-repo \
  --rsh "ssh -p 23 -i ~/.ssh/borg-admin-arch"
```

Expected: still lists the archive. **Do not actually delete it — Phase 4 restores from it.**

---

### Task 15: Schedule notifications + healthchecks deadman

**Files:** none in repo — external configuration. Captured in runbook in Task 17.

- [ ] **Step 1: Subscribe phone to the ntfy topic**

On phone: install **ntfy** app → subscribe to topic = `${NTFY_TOPIC}` value from `.env`. Test:

```bash
curl -d "phase3 test ping from arch" "https://ntfy.sh/${NTFY_TOPIC}"
```

Expected: notification appears on phone within seconds.

- [ ] **Step 2: Create the healthchecks.io check**

healthchecks.io → free tier → **Add Check**:
- **Name:** AISetup NAS backup (daily Borg)
- **Period:** 1 day
- **Grace:** 2 hours
- **Notification channels:** email + ntfy (use the same topic).

Copy the ping URL into `nas/borgmatic/.env` as `HEALTHCHECKS_PING_URL=...`.

- [ ] **Step 3: Trigger a successful run and verify both hooks fire**

```bash
sudo docker exec -it borgmatic-borgmatic-1 borgmatic --verbosity 1 create
```

Expected:
- `start` ntfy notification on phone.
- `finish` ntfy notification on phone.
- Healthchecks.io check goes **green** (last ping just now).

- [ ] **Step 4: Trigger an intentional failure — verify fail notification + healthchecks red**

Easiest fault injection: temporarily set a wrong DB password in the container env, then run create. Expected: pre-hook fails, `borgmatic` exits non-zero, **fail** ntfy fires, healthchecks goes red on next missed window.

Restore the correct password afterward.

```bash
# After fixing the password, verify a clean run again:
sudo docker exec -it borgmatic-borgmatic-1 borgmatic --verbosity 1 create
```

Expected: success, green.

---

### Task 16: Phase 3 verification gate

**Files:** none.

- [ ] **Step 1: Confirm scheduled cron line is in place inside the container**

```bash
sudo docker exec borgmatic-borgmatic-1 crontab -l
```

Expected: two lines — daily 05:00 create, weekly Sun 06:00 check.

- [ ] **Step 2: Confirm the Arch-side timers are enabled and queued**

```bash
systemctl --user list-timers borgmatic-*
```

Expected: `borgmatic-prune.timer` (next: upcoming Sunday) and `borgmatic-check.timer` (next: first Sunday of next month).

- [ ] **Step 3: Wait for the next scheduled 05:00 run and confirm it fires automatically**

The morning after Phase 3 deploy:

```bash
borg list ssh://u123456@u123456.your-storagebox.de:23/./borg-repo \
  --rsh "ssh -p 23 -i ~/.ssh/borg-admin-arch"
```

Expected: a second archive named `nas-aisetup-<today>T05:00:0X` exists. Notifications fired overnight. Healthchecks pinged.

- [ ] **Step 4: Spot-check archive content**

```bash
borg list ssh://u123456@u123456.your-storagebox.de:23/./borg-repo::<latest-archive> \
  --rsh "ssh -p 23 -i ~/.ssh/borg-admin-arch" | head -40
```

Expected: a recognizable tree of files under `/source/nvme/...` and `/source/tank/...`; database dump files (e.g., `borgmatic/postgresql_databases/litellm-db/litellm`) at the top.

---

## Phase 4 — Test restore (the gate that proves it all)

### Task 17: End-to-end restore of `joplin` to `/tmp/restore-test/`

**Files:** none in repo. Outcome recorded in runbook in Task 18.

- [ ] **Step 1: Pick the latest archive and extract `joplin` into `/tmp`**

On Arch:

```bash
mkdir -p /tmp/restore-test
cd /tmp/restore-test
ARCHIVE=$(borg list --short --last 1 \
  ssh://u123456@u123456.your-storagebox.de:23/./borg-repo \
  --rsh "ssh -p 23 -i ~/.ssh/borg-admin-arch")
echo "Restoring from archive: $ARCHIVE"

START=$(date +%s)
borg extract \
  ssh://u123456@u123456.your-storagebox.de:23/./borg-repo::$ARCHIVE \
  source/nvme/joplin \
  --rsh "ssh -p 23 -i ~/.ssh/borg-admin-arch"
END=$(date +%s)
echo "Restore took $((END - START))s"
```

Expected: extract succeeds; a `source/nvme/joplin/` tree exists under `/tmp/restore-test/`; elapsed time logged.

- [ ] **Step 2: Diff against live joplin on the NAS**

From Arch:

```bash
sudo ssh root@10.63.0.2 'cd /mnt/nvme/apps/joplin && find . -type f | sort | xargs -I{} sha256sum {}' \
  > /tmp/restore-test/live-hashes.txt

cd /tmp/restore-test/source/nvme/joplin
find . -type f | sort | xargs -I{} sha256sum {} > /tmp/restore-test/restored-hashes.txt

diff /tmp/restore-test/live-hashes.txt /tmp/restore-test/restored-hashes.txt | head -50
```

Expected: the diff is **empty or trivial** (a small handful of files changed since the 05:00 snapshot is expected; structurally identical hashes for everything stable).

If there are large unexplained differences, the chain is broken — debug before declaring Phase 4 done.

- [ ] **Step 3: Record the measured RTO**

The "Restore took Xs" number from Step 1 is your **real RTO data point for a small dataset**. If it exceeded the stated tolerance (a day or two), nothing to redesign — joplin is tiny; this is just a baseline. **Record the number** for the journal entry.

- [ ] **Step 4: Clean up**

```bash
rm -rf /tmp/restore-test
```

---

### Task 18: Runbook + journal + README updates

**Files:**
- Modify: `docs/runbook.md` (add "Backup (NAS-wide)" section)
- Modify: `README.md` (one-line mention)
- Create: `docs/journal/2026-05-25-nas-backup-design.md` (decision story)

- [ ] **Step 1: Append "Backup (NAS-wide)" section to `docs/runbook.md`**

Add a new top-level section (after any existing observability/metrics section). Content:

````markdown
## Backup (NAS-wide)

Three layers: ZFS snapshots (local), NVMe→HDD replication (local), Borg+Borgmatic to Hetzner BX21 (offsite). Design: [`superpowers/specs/2026-05-25-nas-backup-strategy-design.md`](superpowers/specs/2026-05-25-nas-backup-strategy-design.md). Stack: [`nas/borgmatic/`](../nas/borgmatic/) (NAS-side) + [`arch/borg-admin/`](../arch/borg-admin/) (Arch-side admin ops).

### Daily anchor

- **03:00** — daily ZFS snapshots fire (class B daily, class C daily, snapshot-only).
- **04:00** — NVMe → HDD replication runs against the freshly-taken snapshots.
- **05:00** — Borgmatic offsite run from the NAS container.

Hourly (class A) and 6-hour (class B 6h) snapshots run on independent cadences.

### Inspect what's there

```bash
# All snapshots, newest last:
sudo zfs list -t snapshot -o name,creation,used -s creation | tail -40

# Replicas on tank:
sudo zfs list -r tank/replica/nvme-apps

# Borg archives (from Arch, admin key):
borg list ssh://u123456@u123456.your-storagebox.de:23/./borg-repo \
  --rsh "ssh -p 23 -i ~/.ssh/borg-admin-arch"
```

### Restore scenario A — single file (snapshot)

```bash
# List snapshots of the affected dataset:
sudo zfs list -t snapshot -o name,creation -s creation -r nvme/apps/<name> | tail -20

# Copy from a snapshot directly (no rollback):
sudo cp /mnt/nvme/apps/<name>/.zfs/snapshot/<snap>/path/to/file \
        /mnt/nvme/apps/<name>/path/to/file
sudo chown <uid>:<gid> /mnt/nvme/apps/<name>/path/to/file
```

### Restore scenario B — dataset rollback or replica receive

```bash
# Stop the affected app first (Dockge UI).

# Option 1: in-place rollback (DESTROYS data newer than the snapshot):
sudo zfs rollback -r nvme/apps/<name>@<snap>

# Option 2: receive from the local HDD replica:
sudo zfs send tank/replica/nvme-apps/<name>@<snap> | \
  sudo zfs receive -F nvme/apps/<name>
```

### Restore scenario C — NVMe pool failure

After rebuilding the NVMe pool, restore each protected dataset from `tank/replica/nvme-apps/`:

```bash
for ds in dockge n8n paperless-ngx immich nginx-proxy-manager pihole joplin litellm langfuse metrics forgejo; do
  LATEST=$(sudo zfs list -t snapshot -o name -s creation tank/replica/nvme-apps/$ds | tail -1)
  sudo zfs send -R "$LATEST" | sudo zfs receive -F nvme/apps/$ds
done
```

**Data loss bound:** up to ~24h since last 04:00 replication.

### Restore scenario D — full NAS loss (Hetzner is the only copy left)

From Arch:

```bash
export BORG_PASSPHRASE='...'
borg list ssh://u123456@u123456.your-storagebox.de:23/./borg-repo \
  --rsh "ssh -p 23 -i ~/.ssh/borg-admin-arch"

ARCHIVE=nas-aisetup-<timestamp>

# Mount + selective copy (better for large archives):
mkdir -p /mnt/borg-restore
borg mount ssh://u123456@u123456.your-storagebox.de:23/./borg-repo::$ARCHIVE \
  /mnt/borg-restore --rsh "ssh -p 23 -i ~/.ssh/borg-admin-arch"

rsync -aHAX /mnt/borg-restore/source/nvme/<name>/ /mnt/nvme/apps/<name>/

# Restore Postgres dumps after stacks are back up:
pg_restore -h localhost -U <user> -d <db> /mnt/borg-restore/borgmatic/postgresql_databases/<host>/<db>
```

### Restore scenario E — ransomware

```
1. Pick an archive from BEFORE the compromise window.
2. Wipe the compromised NAS completely (factory reset / pool destroy /
   OS reinstall). Do not "clean" — trust is gone.
3. Rotate ALL secrets — passwords, API keys, Borg passphrase, Storage Box
   SSH keys, app admin creds — BEFORE restore.
4. Then restore as in Scenario D.
```

### Quarterly test restore

Repeat Task 17 of the implementation plan (extract `joplin` to `/tmp`, diff
against live). Record the date and measured RTO here.

| Date | Dataset | Measured RTO | Outcome |
|---|---|---|---|
| 2026-05-25 | joplin (Phase 4 gate) | _(fill in)_ | _(fill in)_ |

A backup not restored in 6 months is presumed broken.

### Rotate the Borg passphrase

```bash
borg key change-passphrase ssh://u123456@u123456.your-storagebox.de:23/./borg-repo \
  --rsh "ssh -p 23 -i ~/.ssh/borg-admin-arch"
```

Then update: NAS `nas/borgmatic/.env`, password manager entry, printed paper in fire-safe, USB on offsite.

### Rotate the writer SSH key

1. Generate new keypair on Arch (staging dir).
2. Append the new pub to Storage Box `authorized_keys` with the same `command="borg serve --append-only ..."` restriction.
3. Replace `/mnt/nvme/apps/borgmatic/secrets/borg-writer-nas` on the NAS.
4. Run a test `borgmatic create` to confirm.
5. Remove the **old** writer pub from `authorized_keys` on the Storage Box.

### Rotate the admin SSH key

Same procedure but admin-side: generate on Arch, append new pub to authorized_keys (no restriction), replace `~/.ssh/borg-admin-arch`, test with `borg list`, remove old pub.

### Where the escrow copies live

- **Password manager** entry "Borg passphrase — AISetup repo" — primary.
- **Printed paper** in the fire-safe — secondary.
- **Encrypted USB** at _(location)_ — offsite. LUKS passphrase in password manager.

If all three are lost along with the NAS, the offsite backup is unrecoverable.
````

- [ ] **Step 2: Add one-line mention to `README.md`**

In the architecture summary section of `README.md`, add a bullet:

```markdown
- **Backups:** ZFS snapshots + NVMe→HDD replication on the NAS, append-only Borg offsite to Hetzner BX21. Design: [`docs/superpowers/specs/2026-05-25-nas-backup-strategy-design.md`](docs/superpowers/specs/2026-05-25-nas-backup-strategy-design.md). Operations: [`docs/runbook.md`](docs/runbook.md) "Backup (NAS-wide)".
```

- [ ] **Step 3: Create the journal entry**

`docs/journal/2026-05-25-nas-backup-design.md`:

```markdown
# 2026-05-25 — NAS-wide backup strategy

## Where we started

No backup machinery existed on the NAS. Six services were running with real
data — Postgres for LiteLLM and Langfuse, a year of Prometheus history, Joplin
notes, Pi-hole config, etc. — and a single drive failure or ransomware event
would have ended the project.

## Why it became "NAS-wide" and not per-service

The first instinct was to add a Backup section to the in-flight Forgejo spec.
That section started getting big — "and we also need to back up Postgres for
LiteLLM, and ClickHouse for Langfuse, and the photos…" — and I realised this
is the same scope as auth, secrets, monitoring: a cross-cutting concern that
belongs in a NAS-wide spec, not stuffed inside a per-service one. Lifted it
out and stopped per-service backup work in Forgejo. (Memory:
`cross-cutting-not-per-service`.)

## The threat ranking drove the design

Ranking threats A–F by priority surfaced that A + B + E were all "High":

- A = NAS hardware loss
- B = ransomware
- E = geographic disaster

The intersection of those three eliminates most off-the-shelf options:
**offsite must be (a) geographically separated and (b) something the NAS can
write to but cannot delete from.** That single constraint pointed straight at
append-only Borg on Hetzner Storage Box — separate hardware, separate site,
SSH `command="borg serve --append-only ..."` restriction handles ransomware
even if NAS root is owned.

The two-key model (writer on NAS, admin on Arch) falls out naturally. Pruning
from the NAS would collapse the ransomware defense, so pruning lives on Arch.

## The Phase 0 discovery

Trying to spec the snapshot/replication design exposed that the NAS's three
Dockge stacks (LiteLLM, Grafana, Langfuse) all used Docker named volumes,
which on TrueNAS land in the hidden `/mnt/.ix-apps/` tree — snapshot-hostile,
replication-hostile. The TrueNAS docs flag this explicitly. So backup wasn't
even *possible* without first migrating those seven volumes to host-path
mounts. That's why the plan opens with Phase 0 as a hard pre-req, ahead of
any snapshot work — backup is "impossible" until the data sits where the
backup tools can see it.

## Measured RTO on the Phase 4 gate

Joplin restore from Hetzner to `/tmp` on Arch took **_(fill in)_s**. That's
the real RTO data point for a small dataset — under the stated "day or two"
tolerance with three orders of magnitude to spare. Larger restores will be
network-bound by Hetzner upstream, not by Borg, so the working assumption
remains: hours to a day for full NAS restore, minutes for individual files
from snapshots.

## What's deferred

- A second offsite provider. BX21 is single-offsite for now; revisit when
  customer data scale grows.
- Centralized secrets management (Vault/sops). Escrow procedure is in scope;
  a full secrets-management story is the next cross-cutting spec.
- NAS-wide auth/access — same cross-cutting pattern, separate spec.
- `keyfile` encryption mode (offsite key storage) — easy migration later if
  confidentiality from NAS-root compromise ever matters more than it does
  today.
```

- [ ] **Step 4: Commit**

```bash
git add docs/runbook.md README.md docs/journal/2026-05-25-nas-backup-design.md
git commit -m "docs(backup): runbook section + readme mention + journal entry

Closes Phase 4 of the NAS-wide backup spec. Runbook now carries all five
restore scenarios, quarterly test restore template, and key/passphrase
rotation procedures. Journal entry captures the per-service →
cross-cutting lift-out + the Phase 0 discovery for the talk."
```

---

## Final verification gate

- [ ] **Step 1: Spec coverage check**

Walk §3.1, §5.1, §6.6, §7.1, §10 of the spec — confirm each requirement maps to a completed task:

| Spec section | Implemented in |
|---|---|
| §3.1 threats A/B/E (offsite, geographically separated, append-only) | Task 8–11, 14 |
| §3.1 threat C (self-inflicted deletion) | Task 6 (snapshots) |
| §3.1 threat D (ZFS/TrueNAS bug) | Task 11 (Borg = non-ZFS) |
| §3.1 threat F (supply-chain) | Task 11–12 (long retention via prune config) |
| §3.3 tier classification (Protected/Snapshot-only/Excluded) | Task 6, 7, 11 (excludes) |
| §4 Dockge migration | Tasks 1–5 |
| §5.2 snapshot classes A/B/C + snapshot-only | Task 6 |
| §5.3 NVMe → HDD replication | Task 7 |
| §6.3 two-key model | Task 9 |
| §6.4 encryption (repokey-blake2) | Task 13 |
| §6.5 PG + ClickHouse pre-hooks | Task 11 (borgmatic.yaml) |
| §6.6 source list + excludes | Task 11 |
| §6.7 schedule + retention | Tasks 11, 12 |
| §7.1 all 5 restore scenarios | Task 18 (runbook) |
| §7.2 verification cadence | Tasks 12 (timers), 17 (Phase 4 gate) |
| §7.3 notifications + deadman | Task 15 |
| §8 Phase 3 step 3 escrow | Task 10 |
| §10 repo artifacts | Tasks 11, 12, 18 |

- [ ] **Step 2: Confirm Forgejo dependency**

Open `docs/superpowers/specs/2026-05-25-forgejo-design.md` (or equivalent) and confirm the Status line gating real/customer data on Phase 4 completion is still accurate. If Phase 4 passed cleanly, real customer repos can now land in Forgejo.

- [ ] **Step 3: Total estimate vs actual**

Spec said ~1.5–2 working days over ~3 calendar days. Record actual elapsed in the journal if dramatically different — useful talk material.

---

## Self-review notes

Three things to watch during execution that this plan can't fully prevent:

1. **TrueNAS-app service hostnames in `borgmatic.yaml`.** I guessed `n8n-postgres`, `paperless-db`, `immich-postgres` based on convention. Confirm with `sudo docker ps --format '{{.Names}}'` before Task 13 Step 4 — getting these wrong means pre-hooks fail silently from a service network the container can't reach. Fix path: edit `borgmatic.yaml` + `docker-compose.yml` networks block, redeploy.

2. **Docker network names** (`litellm_default`, `langfuse_default`). Compose's default is `<dir>_default`, but Dockge sometimes deploys with the stack name as prefix. `sudo docker network ls` is authoritative.

3. **The first archive will be slow** — multi-hour, possibly overnight depending on Hetzner upstream. Don't interrupt. Subsequent incrementals are minutes.
