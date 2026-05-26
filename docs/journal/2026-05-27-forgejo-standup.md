# 2026-05-27 — Forgejo standup: sovereignty over default opt-ins

## What happened

Stood up a self-hosted Forgejo on the NAS as the canonical source for the
AISetup repo. GitHub becomes a read-only push-mirror shopfront. The plan was
linear by the time I reached it (design spec
[`2026-05-25-self-hosted-forgejo-design.md`](../superpowers/specs/2026-05-25-self-hosted-forgejo-design.md)
was already approved, backup Phase 4 had passed so the "no real data" gate
was open). The work was: pre-create datasets, wire snapshots and replication
and Borg, install the TrueNAS Community Forgejo app, migrate the AISetup
repo, set the push mirror, lock down the GitHub side.

End state: local, Forgejo, and GitHub all at the same `HEAD`. Mirror lag on
the cutover commit was about five seconds.

What's worth recording is not the steps; the spec covers those. What's worth
recording is *why this was worth doing*, what I rejected, and what the
install surfaced that the spec didn't predict.

## The why: an option, not an exit

This isn't a GitHub protest. The mirror still ships there, and that's
deliberate. Build-in-public depends on people being able to read the code
in the place they already are, and that's GitHub for almost everyone.

What's changing is *where the source of truth lives*. Up to today, GitHub
was both the canonical home and the public shopfront. From today, GitHub is
only the shopfront. The canonical home is a Forgejo instance I control,
reachable over Wireguard, sitting on the same NAS as everything else in
this project.

That gives me one specific thing: an option for the day the terms change. I
don't know what that day looks like yet. License shifts, ToS changes, AI
training carve-outs, archive opt-ins, who knows. Owning the source-of-truth
means the answer to "what happens then" is "the GitHub mirror stops, I
publish wherever else I want, no migration cost." Without this, the answer
is "I rebuild git history and re-link every reference."

I keep wanting to call this a *defensive* move, but that overstates it. It's
just inventory. The same way you keep a backup of your data because storage
can fail without warning. You don't expect it to. You'd just rather not be
in the wrong position when it does.

## Forgejo over Gitea: a governance choice, not a feature one

Day to day, Forgejo and Gitea are still feature-equivalent and
API-compatible. The hard fork happened in 2024, the divergence is recent
and future-facing. If I cared only about today's feature set, either was
fine.

I cared about who's steering each of them.

- **Gitea** is governed by **Gitea Ltd**, the for-profit that took over the
  upstream in Oct 2022 without community consultation. Picking it would
  trade Microsoft for a smaller version of the same shape: a single
  for-profit corporate owner controlling the project's direction.
- **Forgejo** is governed by **Codeberg e.V.**, a non-profit. Its
  governance is structurally designed to make the 2022-Gitea scenario
  harder to repeat. The relicensing to GPL-3.0+ blocks future hostile
  re-licensing in a way MIT doesn't.

If the motivation for this whole move is "reduce dependency on a single
corporate owner," picking Gitea would have been internally inconsistent. So:
Forgejo. The license has no bearing on what I store in it. Closed code is
fine in Forgejo same as it is in GitLab CE or GitHub Enterprise Server.

## What surprised me during install

Two things the spec didn't anticipate.

### 1. The chart's LFS path adds a `git/` segment

Upstream Forgejo's default is `[lfs] PATH = ${APP_DATA_PATH}/lfs`. The
TrueNAS Community chart sets it to `${APP_DATA_PATH}/git/lfs`. Same
two-character difference that breaks everything if you blindly mount the
HDD volume at the upstream-default path.

The spec § 4 explicitly said *"verify the exact container path in the
chart's app.ini at install time rather than trusting the default
blindly."* I half-remembered this and gave the install instructions with
the upstream path. The first LFS push test would have silently routed
objects to the NVMe `data` volume; the HDD dataset would have stayed
empty. The spec saved me from itself.

The catch is in the live `app.ini` after install, and the verification
that catches it is two lines in the container's mount table:

```
sudo docker inspect ix-forgejo-forgejo-1 --format \
  '{{range .Mounts}}{{println .Destination "  <-  " .Source}}{{end}}' | grep lfs
```

If that single line doesn't match the `app.ini`'s `[lfs] PATH`, fix it
before pushing anything LFS-tracked.

The lesson generalizes: when a chart's defaults differ from the upstream
defaults, the chart wins. Don't trust upstream documentation as a proxy
for the chart's behavior.

### 2. GitHub auto-enrolled the repo into the Archive Program

I went to disable Issues, Wiki, Discussions, Projects on the GitHub side
(the standard "shopfront" lockdown). In the same settings page, in the
Features list, "Preserve this repository" was checked. Including the repo
in the GitHub Archive Program. I never enabled that. The default was on.

This is exactly the pattern that motivates the migration. Not malice, but
a platform owner deciding what my repo participates in, on my behalf, until
I notice and untick a box. The list of things I'd want to opt out of by
default but couldn't see to opt out of grows over time.

So this got unticked alongside the others, and the journal-worthy note is
*the discovery itself*. If the migration hadn't put me on that settings
page, the box would still be checked.

### 3. GitHub now exposes a PR-creator restriction

The design spec § 6 wrote off PR disabling as impossible: *"GitHub doesn't
let you disable PRs without archiving the repo (which would also block the
mirror push)."* That's no longer fully true.

The GitHub repo settings now have a "Pull request permissions" block with a
*"Creation allowed by:"* dropdown. Set it to "Users with write access" and
nobody outside the collaborator list can open a PR on the mirror.

It doesn't replace the `.github/pull_request_template.md` redirect. Belt
and suspenders. If GitHub ever loosens the restriction, the template
still works.

Source-rigor lesson, again: vendor-platform claims age. The spec was three
days old and already wrong on this point. Re-check before quoting them in
implementation work.

## What I deliberately didn't build

The spec § 9 has a deferred list. The triggers matter as much as the
items:

| Deferred | Trigger |
|---|---|
| Public access (Option B: EU VPS + WG tunnel + reverse proxy) | First customer onboarding |
| Forgejo Actions runner | First repo that actually needs CI |
| ActivityPub federation | If contributors materialize from other Forgejo instances |
| OAuth providers | A second human user |
| Customer DPA / GDPR processor docs | First customer onboarding |

None of these change the install. Option B is a layer in front; the rest
are toggles on the existing instance. The point of deferring is that "no
real customers yet" is the right time to keep the surface area small. The
moment a real customer appears, the threat model changes and these stop
being deferrals and start being requirements. Triggers, not dates.

## Open

The pg_dump hook for Forgejo's Postgres is committed but commented in
`nas/borgmatic/borgmatic.yaml`. Container name and PG creds are known now
(`ix-forgejo-postgres-1`, user/db from the wizard), so wiring it up is a
follow-up task. Until then, the Forgejo Postgres is covered file-only via
`/source/nvme/forgejo/postgres_data` plus the ZFS snapshot at 03:00. That's
the same posture ClickHouse has had since Phase 4 of the backup plan, and
it's good enough as a holding pattern, not as a destination.

The next concrete thing is: enable the pg hook, then bring a second repo
(BuildInPublic) into Forgejo with its own push mirror. Both small.

## Takeaway

At least for this kind of single-owner project, the cost of self-hosting
the forge is small and the option-value of owning the source-of-truth is
real. The migration paid for itself in the same afternoon I did it, the
moment I noticed the Archive Program flag. I'm not claiming the same
trade-off holds for a team with CI pipelines and external contributors;
that's a different shape and probably a different call. For solo / public
build / future-customer-uncertain, the math works.
