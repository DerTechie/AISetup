# Self-hosted Forgejo on the NAS — design

**Date:** 2026-05-25
**Status:** Design approved. Solo-mode only; public-facing access deferred (Option B — see §9). **Depends on the [NAS-wide backup spec](2026-05-25-nas-backup-strategy-design.md)**: install can proceed in parallel with backup Phases 0–3, but **real / customer data must not land in Forgejo until backup Phase 4 (verified test restore) passes** — Phase 4 is the honest gate, not earlier phases.

## 1. Goal

Stand up a **self-hosted Forgejo** instance on the NAS as the canonical home for
this repo (and other DerTechie repos), with **GitHub kept as a read-only mirror
shopfront** for the public ones. Solo use only: reached over the existing
Wireguard tunnel, no public surface, no customer access. The point is to own
the source-of-truth — GitHub becomes a derived artifact, not the primary.

## 2. Scope

**In scope:** install Forgejo + Postgres on the NAS, persist on a ZFS dataset,
configure a backup target, migrate this repo and one or two others in, set up
per-repo push mirrors to the existing GitHub remotes, document the install.

**Non-goals (named, not built):**

- Any public-facing access (TLS via Cloudflare Tunnel, a Hetzner VPS reverse
  proxy with Wireguard back to the NAS, port-forwarding, DDNS). Deferred until
  the first customer onboarding — see §9.
- Forgejo Actions runner. Added when the first repo actually needs CI.
- ActivityPub federation (experimental in Forgejo).
- OAuth providers (GitHub login, etc.). Local accounts + 2FA are enough for
  solo use.
- GitHub webhook back-channel (PR/issue events syncing into Forgejo). The
  GitHub mirror is a one-way shopfront; contributions are redirected to the
  Forgejo source.
- Anything customer-related: DPA templates, tenant isolation review, GDPR
  processor docs. Belongs in its own spec when customers actually arrive.

## 3. Why Forgejo (and not Gitea)

Forgejo is a hard fork of Gitea, stewarded by the **Codeberg e.V. non-profit**,
relicensed to **GPL-3.0+**. Both pieces matter for the motivation behind this
project:

- The driver for leaving GitHub is strategic — sovereignty, future-proofing,
  reducing dependency on a single corporate owner. Picking Gitea would swap
  Microsoft for **Gitea Ltd** (the for-profit that took over the upstream in
  Oct 2022 without community consultation) — same problem shape, smaller scale.
  Forgejo's governance is structurally designed to make that scenario harder.
- The GPL-3.0+ relicensing blocks future hostile re-licensing in a way MIT
  doesn't. For a "whatever comes next" bet, the stronger license is the
  internally consistent choice.
- Day-to-day Forgejo and Gitea are still feature-equivalent and API-compatible;
  the divergence is recent (hard fork ~2024) and future-facing.

The license has no bearing on hosted repo contents — the forge's license
governs only its own source code, not what you store in it. Hosting closed /
proprietary code in Forgejo is fine (same as GitLab CE, GitHub Enterprise
Server, etc.).

## 4. Deployment

Installed as a **TrueNAS App**, not as a Dockge compose stack. This follows the
same call already made for n8n — and the reasoning is sharper here: the next
step up from "managed by TrueNAS" is "standalone server with Ansible / proper
config management." Dockge sits in the unrewarding middle. For a service we'll
update regularly and snapshot heavily, the TrueNAS-managed tier earns its
keep on day-to-day ops; anything beyond that is a different conversation about
moving off the NAS entirely.

- **App source:** the **Forgejo** app from the official TrueNAS catalog,
  **Community train** (`apps.truenas.com/catalog/forgejo_community/`).
  Maintainer is `dev@truenas.com` — iXsystems-maintained, not third-party.
  Community train means lower-QA than Stable but same maintainers; equivalent
  trust profile to the rest of the NAS stack. Custom-App-with-compose stays
  as the documented fallback path only if the catalog app develops a real
  blocker, which it shows no sign of.
- **App version at design time:** Forgejo `15.0.2-rootless` (chart `1.2.21`,
  last updated 2026-05-15). Pinned at install; upgrades go through the app
  UI.
- **Database:** PostgreSQL 18 (the chart's default; 17 is also offered).
  SQLite is technically enough for solo use, but Postgres matches the rest of
  the NAS stack and is what the chart wires up out of the box.
- **Storage: host path, not ixVolume.** Both `data` (Forgejo repos /
  attachments) and `postgres_data` point at pre-created datasets. This
  matches the official TrueNAS recommendation ("Host Path… is the
  recommended setting for on-disk storage in a production deployment");
  ixVolume is for test/throwaway installs and is documented to complicate
  backup, snapshot, and replication tasks (data lands in the hidden
  `.ix-apps` dataset and is also a one-way choice — can't be converted later
  without restore-from-backup). The cost is one extra pre-install step per
  dataset; worth it.
- **Pool placement: NVMe for the hot data, HDD for LFS.**
  - **NVMe pool** holds Forgejo data + Postgres — same pool the other NAS
    apps (n8n, Langfuse, LiteLLM, Grafana) use, keeps operational
    consistency, and Postgres / git pushes both benefit directly from the
    latency.
  - **HDD pool** holds Git LFS objects, split off from `data/` via a
    separate mount (see below). The LFS use case is game-dev assets
    (textures, models, audio, builds), which grow into many GB / eventually
    TB and are mostly sequential / cold — the wrong workload for NVMe.
    Splitting LFS at install time avoids a painful later migration.
  - **HDD pool also serves as the local ZFS replication target** for the
    NVMe Forgejo datasets (see §7), so it earns its keep on both fronts.
- **Datasets to create before install** (using whatever per-pool app naming
  convention the NAS already uses for the other apps — match n8n's layout):
  - `<nvme-pool>/…/forgejo/` — parent on NVMe; snapshot policy attaches
    here and children inherit.
  - `<nvme-pool>/…/forgejo/data` — Forgejo data path. UID/GID `568:568`
    (the chart's `apps` user).
  - `<nvme-pool>/…/forgejo/postgres_data` — Postgres data path. UID/GID
    `999:999` (`netdata` host user / `docker` group, per the chart's
    `run_as_context`).
  - `<hdd-pool>/…/forgejo/lfs` — LFS object store. UID/GID `568:568`.
- **Wizard config for storage:**
  - **Forgejo Data Storage:** Host Path → the NVMe `forgejo/data` dataset,
    Enable ACL with chart defaults.
  - **Postgres Data Storage:** Host Path → the NVMe `forgejo/postgres_data`
    dataset, Enable ACL, and turn on **Automatic Permissions** so the chart
    `chown`s for you on first run.
  - **Additional Storage** (one entry, for LFS): Host Path → the HDD
    `forgejo/lfs` dataset; mount path inside the container set to Forgejo's
    LFS storage path. Forgejo's default is under the data volume
    (`<data>/lfs`), so this mount is nested on top of the data mount — Docker
    handles that, the LFS mount takes precedence for that subpath. **Verify
    the exact container path in the chart's app.ini at install time** rather
    than trusting the default blindly; if it differs, override with an
    `additional_envs` entry (`FORGEJO__lfs__PATH=<container-path>`) so
    Forgejo writes LFS where we mounted it.
  - Confirm LFS is enabled (`FORGEJO__server__LFS_START_SERVER=true`) —
    on by default in current Forgejo, but worth checking.
- **Post-install storage verification:** push a small LFS-tracked file from
  a test repo. Confirm the object lands on the HDD dataset
  (`ls <hdd-pool>/…/forgejo/lfs/...` shows the object), **not** on the NVMe
  `data` dataset. If it ends up on NVMe, the LFS mount or env var is wrong
  and needs fixing before we put any real game assets on it.
- **Ports:** LAN-only on `10.63.0.2`. Chart defaults are WebUI `30142` and
  SSH `30143` — accept those unless they collide with something else on the
  NAS. Bound to the LAN-only host IP (not `0.0.0.0`) via the chart's "Host
  IPs" field, matching the posture of the other NAS services.
- **Root URL:** set to the Wireguard-reachable LAN URL,
  `http://10.63.0.2:30142` (or whatever port we end up using). This is what
  Forgejo embeds in clone URLs, mail links, and webhook callbacks — getting
  it right at install avoids a confusing day-two reconfig.
- **TLS:** none on the LAN. Wireguard already provides transport encryption;
  terminating TLS internally would add cert management for no threat-model
  benefit.
- **Secrets handling:** Forgejo's instance secrets (`SECRET_KEY`,
  `INTERNAL_TOKEN`, `LFS_JWT_SECRET`) are generated on first start and stored
  inside the data volume's `app.ini`, so they're covered by the data-volume
  backup (§7) — no separate "remember this key or lose stored credentials"
  step like n8n's `N8N_ENCRYPTION_KEY`. The two values that **do** need to go
  into the password manager are the Postgres password (set in the wizard)
  and the initial admin login.
- **Admin user:** one local account (Mike) with a strong password and 2FA.

## 5. Access

- **Solo mode is Wireguard-only.** Reachable at `http://10.63.0.2:<port>` from
  any device on the Wireguard mesh — same pattern as Grafana, Langfuse,
  LiteLLM dashboard, n8n today. Phone, Arch workstation, laptop all already
  have Wireguard.
- **No public DNS record.** A future public hostname (e.g. `git.dertechie.de`)
  is part of the deferred public-access work, not this spec.

## 6. Mirroring strategy

Forgejo ships **push mirrors** as a per-repo feature. No external tooling, no
n8n workflow, no CI job — the forge pushes outbound to GitHub on every commit.
Push mirrors only need outbound HTTPS to `github.com` from the NAS, which is
the default. **No inbound public access is required for mirroring to work.**

Per repo:

- **Mode:** push mirror, Forgejo → GitHub, on every commit + a daily safety
  re-sync.
- **GitHub auth:** a GitHub PAT scoped to the mirror repos, stored in Forgejo's
  credentials store (encrypted with the instance secret).
- **Existing GitHub repo on the other side:** keep the same `DerTechie/...`
  org layout. GitHub doesn't let you disable PRs without archiving the repo
  (which would also block the mirror push), so the shopfront is built by:
  disabling **issues, wiki, projects, and discussions** in repo settings;
  adding a pinned top-of-README banner that points to the Forgejo source;
  and an `ISSUE_TEMPLATE` / `PULL_REQUEST_TEMPLATE` that redirects any
  drive-by contributor back to Forgejo. Linux kernel's `torvalds/linux` is
  the reference pattern — same posture: technically open, socially closed.

For migrating existing GitHub repos in:

- Use Forgejo's **Migrate Repository** UI: choose "Git" with the GitHub URL +
  PAT, then "Mirror" to seed history, then convert to a normal repo with a
  push mirror configured back to the same GitHub remote. This preserves
  branches, tags, issues (where present), wiki, and releases.
- For repos with no GitHub history worth preserving, a plain `git remote set-url`
  + push to Forgejo is faster and equivalent.

Which repos get mirrored to GitHub is per-repo:

- **Public DerTechie repos** (this one, BuildInPublic, etc.) → push-mirrored.
- **Private/internal repos** → Forgejo only, never mirrored.

## 7. Backup

**Backup is a NAS-wide concern, not a per-service one.** Designing it inside
this spec would mean Forgejo gets a strategy while n8n, Langfuse, LiteLLM,
and Grafana don't — and the NAS has no backup machinery in place today
either way. Pulled out to a separate spec (§11) so it can be done once and
cover every service consistently.

What this spec contributes are the **Forgejo-specific inputs that the
NAS-wide backup spec must accommodate:**

- **Datasets to cover:** the NVMe `forgejo/` parent (Forgejo data +
  Postgres) and the HDD `forgejo/lfs` dataset. Storage is on host-path
  datasets (§4), so standard ZFS snapshot/replication tooling applies —
  none of the hidden `.ix-apps` snapshot gymnastics that ixVolume-backed
  apps force.
- **Postgres needs a logical dump alongside the data-volume snapshot.** A
  ZFS snapshot of `postgres_data` is restorable but a `pg_dump` (or
  equivalent) is much cheaper insurance against partial corruption.
- **LFS will dominate offsite size and cost.** Game-dev binary assets grow
  fast; the offsite destination needs to be chosen with that in mind, and
  the LFS dataset may warrant a lower snapshot cadence than `data/` since
  LFS objects change less often than commits.
- **Different cadences are fine.** `data/` and `postgres_data` are hot,
  small, change every push — frequent snapshots, short retention.
  `lfs/` is large and cold — sparser snapshots, longer retention.
- **The GitHub mirror is not a backup.** It covers public repos only, git
  history only — no issues, no wiki, no LFS, no settings, and only the
  subset of repos that are mirrored at all.

Forgejo's instance secrets live inside the data volume (§4), so they ride
along with whatever covers `forgejo/data` — no separate handling needed.

**Hard pre-req:** at least Tier 1 of the NAS-wide backup spec must be in
place before any real or customer data lands in Forgejo. The install itself
can proceed before then (migrating this repo, which is also on GitHub, is
safe to do early), but the moment something exists only in Forgejo, backup
is on the critical path.

## 8. Verification (the done-check)

Done is **not** "Forgejo is up." Done is:

1. This repo lives on Forgejo under the `dertechie` account as
   `dertechie/AISetup`, reachable over Wireguard.
2. A push to Forgejo `main` triggers a successful mirror push to the
   `DerTechie/AISetup` GitHub repo within a minute; the commit is visible on
   both sides.
3. The GitHub side has issues/PRs/wiki disabled with a redirect note in the
   README pointing to the Forgejo source.
4. A ZFS snapshot of the Forgejo dataset exists, and `restic` has completed
   one successful backup to the off-NAS target.
5. Local clone works from the Arch workstation against the Forgejo remote
   (HTTPS or SSH over the Wireguard tunnel).

Until all five are true the migration is incomplete and the local working
remote stays on GitHub.

## 9. Deferred — and what triggers each

| Item | Trigger |
|---|---|
| Public access (Option B: EU VPS + Wireguard + reverse proxy) | First customer onboarding |
| Forgejo Actions runner | First repo that actually needs CI |
| ActivityPub federation | If/when contributors materialize from other Forgejo instances |
| OAuth providers | If/when a user other than Mike needs to log in |
| Customer DPA / GDPR processor docs | First customer onboarding |
| Cross-instance migration plan (e.g. away from Forgejo entirely) | Only if Forgejo itself develops a governance problem |

The deployment in §4–7 does not need to change to support any of these — Option B
specifically adds a layer in front, the rest are toggles on the existing
instance.

## 10. Repo artifact

- `nas/forgejo/README.md` — a documented **install spec**, not a Dockge
  compose stack (same pattern as `nas/n8n/`): which app catalog / Custom App
  path was used, exposed port, dataset layout under `forgejo/`, secrets
  location, the mirror-setup recipe, the GitHub shopfront checklist
  (disabled settings + README banner + templates), the verification steps
  from §8, and — if the Custom App path was needed — the committed Forgejo
  `docker-compose.yml`.
- An entry in `docs/runbook.md` under a new "Forgejo (self-hosted forge)"
  section: how to reach it, how to add a new repo + push mirror, how to
  rotate the GitHub PAT, where backups land.

## 11. Follow-on (out of this spec)

- **NAS-wide backup strategy** — separate spec, **urgent**. Covers every NAS
  service uniformly (Forgejo, n8n, Langfuse, LiteLLM, Grafana), defines the
  snapshot / replication / offsite tiers, and is a hard pre-req for putting
  real data into Forgejo (§7). Lifted out of this spec when we caught
  backup being designed per-service instead of cross-cutting.
- **Journal entry** capturing the *why* of this decision — the GitHub trust
  arc, A+B+C motivations, Forgejo-vs-Gitea governance reasoning, the
  decision to defer public access, and the lift-out of backup into its own
  spec. Feeds the talk narrative.
- **Public access design** (Option B) — separate spec when the first
  customer is on the horizon. EU VPS + Wireguard tunnel + reverse proxy, no
  change to the NAS-side Forgejo deployment.
