# Forgejo (NAS, self-hosted forge)

Canonical source for the DerTechie repos. Solo-mode, Wireguard-only; the public
GitHub copies are read-only push mirrors. Installed as the **Forgejo Community
App** from the TrueNAS catalog (Ugreen DXP8800, TrueNAS, `10.63.0.2`), same
pattern as n8n. Design spec:
[`../../docs/superpowers/specs/2026-05-25-self-hosted-forgejo-design.md`](../../docs/superpowers/specs/2026-05-25-self-hosted-forgejo-design.md).
Standup journal:
[`../../docs/journal/2026-05-27-forgejo-standup.md`](../../docs/journal/2026-05-27-forgejo-standup.md).

- **Web UI:** `http://10.63.0.2:30142` (open from any Wireguard host).
- **SSH:** port `30143`. Clone URLs look like `ssh://git@10.63.0.2:30143/<user>/<repo>.git`.
- **Admin:** local account with 2FA; no OAuth, no public access.
- **Postgres:** chart default (PostgreSQL 18). SQLite would be enough for solo
  use but Postgres matches the rest of the NAS stack.

## Why no `docker-compose.yml`

Config lives in the TrueNAS app wizard, not a committed compose file (same call
as n8n). This README **is** the install spec. If the catalog app is ever
deprecated, the fallback is a TrueNAS **Custom App** with a committed compose
YAML; until then there's no compose file to drift.

## Dataset layout

Pre-created in the TrueNAS UI before install. **All five datasets use the
`Apps` preset** (NFSv4 ACL + passthrough), matching every other native TrueNAS
App on the NAS (n8n, pihole, NPM, jellyfin, paperless-ngx). The Phase 0
migration's "Generic-not-Apps" rule applies only to Dockge-migrated apps where
no chart fixes permissions, not here.

| Path | Pool | Purpose | UID:GID after chart's `chown` |
|---|---|---|---|
| `nvme/apps/forgejo` | NVMe | parent (anchor for snapshots + replication) | inherited |
| `nvme/apps/forgejo/data` | NVMe | Forgejo data (app.ini, repos, attachments) | 568:568 |
| `nvme/apps/forgejo/postgres_data` | NVMe | Postgres data dir | 999:999 |
| `tank/apps/forgejo` | HDD | parent for LFS snapshot scope | inherited |
| `tank/apps/forgejo/lfs` | HDD | Git LFS object store | 568:568 |

LFS is split off to HDD because it'll dominate by size as game-dev assets
land. The NVMe `data` parent stays hot; the HDD `lfs` parent is cold.

## Install (TrueNAS app wizard)

The wizard fields that matter, in order:

1. **App:** Apps → Discover → Community train → **Forgejo**. Pinned at
   `15.0.2-rootless` (chart `1.2.21`) at install time; upgrades go through
   the app UI later.
2. **Admin Username / Password / Email.** Admin password into 1Password
   "Forgejo admin (AISetup)".
3. **Network:**
   - Web Port: `30142`, SSH Port: `30143`.
   - **Host IPs: `10.63.0.2`** (not `0.0.0.0`). Matches every other NAS
     service's LAN-only posture.
   - Root URL: `http://10.63.0.2:30142`. Forgejo embeds this in clone URLs
     and mail links; setting it wrong here is painful to fix later.
4. **Forgejo Data Storage:** Host Path → `/mnt/nvme/apps/forgejo/data`,
   Enable ACL (chart defaults), Automatic Permissions on.
5. **Postgres Data Storage:** Host Path → `/mnt/nvme/apps/forgejo/postgres_data`,
   Enable ACL, Automatic Permissions on. Postgres password into 1Password
   "Forgejo Postgres (AISetup)".
6. **Additional Storage** (the LFS mount, the load-bearing detail):
   - Host Path: `/mnt/tank/apps/forgejo/lfs`.
   - Mount Path inside container: **`/var/lib/gitea/git/lfs`**. **Not** the
     wizard hint's `/data/git/lfs`. The TrueNAS Community chart's
     `APP_DATA_PATH` is `/var/lib/gitea`, and Forgejo's `[lfs] PATH` resolves
     to `${APP_DATA_PATH}/git/lfs`. If you mount anywhere else, Forgejo
     writes LFS objects to the NVMe `data` volume and the HDD dataset stays
     empty. Verify with `docker inspect ix-forgejo-forgejo-1 | grep lfs`
     before pushing anything LFS-tracked.

The chart auto-creates `data/custom/`, `data/git/`, and the Postgres tree on
first start with the right ownership; the data root itself stays
`root:root 0770`. That's fine, Forgejo only writes inside the subdirs.

## Secrets

- Forgejo instance secrets (`SECRET_KEY`, `INTERNAL_TOKEN`, `LFS_JWT_SECRET`)
  are generated on first start and live inside `data/custom/conf/app.ini`.
  They ride along with whatever backs up the data volume. No separate
  "remember this key" handling like n8n's `N8N_ENCRYPTION_KEY`.
- **1Password** entries (DerTechie vault):
  - `Forgejo admin (AISetup)`: admin login.
  - `Forgejo Postgres (AISetup)`: DB password.
  - `Forgejo push-mirror PAT (GitHub)`: the fine-grained GitHub token used
    by per-repo push mirrors. Scope: `Contents: read/write` on
    `DerTechie/AISetup` only, expires yearly.

## Mirror setup (per repo)

GitHub-side repos exist as the shopfront; Forgejo pushes outbound on every
commit.

1. **Migrate or seed Forgejo:**
   - **Existing GitHub repo:** Forgejo → "+" → New Migration → GitHub. Clone
     address only; no GitHub auth needed for public repos. **Do not** check
     "This repository will be a mirror". That makes it a pull mirror, the
     opposite of what we want.
   - **No GitHub history worth preserving:** `git remote add forgejo …` +
     push from the local clone is faster and equivalent.
2. **Add the push mirror** in Forgejo: repo Settings → Mirror Settings → Add
   Push Mirror.
   - Git Remote Repository URL: `https://github.com/DerTechie/<repo>.git`.
   - Authorization: ✅, Username: GitHub login, Password: fine-grained PAT
     scoped to that repo only (Contents read/write).
   - Sync Period: `8h0m0s` (safety re-sync).
   - **Sync When Push:** ✅ (the on-every-commit behavior).
3. **Verify** the round-trip: push to Forgejo `main`, then within a few
   seconds `git ls-remote https://github.com/DerTechie/<repo>.git` returns
   the same SHA.

## GitHub shopfront (per mirrored repo)

The GitHub copy is technically open, socially closed. Linux kernel pattern.

1. **Repo Settings → Features:** uncheck Wikis, Issues, Discussions,
   Projects. Sponsorships off. **Uncheck "Preserve this repository"** (the
   GitHub Archive Program opt-in is enabled by default without consent;
   that default is part of the migration's motivation).
2. **Pull requests:** can't be fully disabled (the mirror push needs the
   feature on), but the "Creation allowed by:" dropdown (newly exposed in
   the GitHub UI) restricts PR creation to users with write access. Set it
   to the most restrictive option. Drive-by PRs become impossible.
3. **Repo content:** the canonical README ships a pinned top blockquote
   ("canonical source: Forgejo"). `.github/pull_request_template.md`
   pre-fills any PR with a redirect note as belt-and-suspenders. Both ride
   along through the mirror; no GitHub-specific files needed beyond the
   template.

## Backup wiring

Everything Forgejo writes is already in scope as of repo commit
`8cfcafe` (Chunk A pre-install pre-wiring).

- **Snapshots:**
  `nas/snapshot-tasks/2026-05-27-forgejo-snapshot-tasks.sh` adds 5 Class M
  recursive tasks on `nvme/apps/forgejo` (hot+standard, same as
  n8n/paperless/metrics) and 3 Class B recursive tasks on
  `tank/apps/forgejo` (LFS, sparser cadence per spec §7).
- **Replication:** `nvme/apps/forgejo` → `tank/replica/nvme-apps/forgejo`,
  04:00 daily, recursive, properties-on, anchored to the Class M parent
  task. Set up in TrueNAS UI (not file-tracked).
- **Borgmatic:** two RO source mounts in `nas/borgmatic/docker-compose.yml`
  (`/source/nvme/forgejo`, `/source/tank/forgejo-lfs`) plus matching
  `source_directories` entries in `borgmatic.yaml`. Cron stays at 05:00
  daily, no per-app overrides.
- **Postgres dump hook (pending):** the `forgejo` block in
  `nas/borgmatic/borgmatic.yaml` is committed but commented. To enable, set
  `FORGEJO_PG_PASSWORD` in the live `.env` (Dockge stack dir), uncomment the
  block, redeploy. Container name `ix-forgejo-postgres-1`; DB user/name
  comes from the wizard install. Tracks the same Pattern B as n8n/paperless/
  immich (`docker exec -e PGPASSWORD …`).

## Verify (the done-check)

Done is not "the app is up". Done is, per design spec §8:

1. `dertechie/AISetup` lives on Forgejo, reachable over Wireguard.
2. Push to Forgejo `main` → GitHub `main` matches within ~10 seconds.
3. GitHub shopfront features off; PR creation restricted; banner +
   PR-template visible from GitHub.
4. ZFS snapshot of `nvme/apps/forgejo` exists; Borg archive includes the
   path on the next nightly run.
5. Local clone works from Arch over the Wireguard tunnel (SSH on `:30143`).

All five satisfied at standup; see journal.

## Out of scope (deferred, with triggers)

| Item | Trigger |
|---|---|
| Public access (Option B: EU VPS + WG + reverse proxy) | First customer onboarding |
| Forgejo Actions runner | First repo that actually needs CI |
| ActivityPub federation | Contributors materializing from other Forgejo instances |
| OAuth providers (GitHub login, etc.) | A second human user |
| Customer DPA / GDPR processor docs | First customer onboarding |
