# 2026-05-26 — Task 15 wiring: healthchecks lands, borgmatic-side ntfy drops out

## What I was doing

Task 15 of the backup plan: wire the healthchecks.io deadman ping so a
backup that never ran (container dead, NAS off) doesn't go undetected,
and confirm the existing borgmatic ntfy hooks fire end-to-end. The plan
called for both: healthchecks for the "did anything happen at all"
signal, borgmatic ntfy for the per-run start/finish/fail pushes.

By the end of the session the ntfy side was gone and healthchecks is
the only notification path. Worth recording why.

## The healthchecks side just worked

Subscribe phone to ntfy topic, free healthchecks.io account, one check
(period 1 day, grace 2 hours), enable email + ntfy as integrations on
the check, paste the `https://hc-ping.com/<uuid>` into the NAS stack's
live `.env`, Dockge re-deploy. The `healthchecks:` block in the
borgmatic config that had been commented out is now live with `states:
[finish, fail]` (no start pings — would be noise without buying
anything). First real `borgmatic create` after re-deploy flipped the
check green with the run log attached.

The dry-run probe (`borgmatic --dry-run create`) was useful first: it
prints `Pinging Healthchecks finish (dry run; not actually pinging)`,
which confirms the env var made it into the container *and* the config
parses *and* the right state is being targeted, without an actual HTTP
call. Saves a real round-trip when you're verifying the wiring.

## Why borgmatic-side ntfy got dropped

That same real-create run pinged healthchecks fine but didn't push to
ntfy. No error in the borgmatic output. The dry-run had already shown
that ntfy doesn't log at `--verbosity 1` (borgmatic prints
would-have-pinged for healthchecks but not for ntfy at that level), so
"no output" was inconclusive — could be silent success that just didn't
reach the phone, could be silent failure.

The natural next step was to debug: bump verbosity to 2 to see ntfy
log lines, then test container egress to `ntfy.sh`, then verify the
`NTFY_TOPIC` env var made it through Dockge's re-deploy into the
recreated container. Maybe a 15-minute fix.

I didn't do it. The reason was a simpler argument that came up while
talking through the options: borgmatic-side ntfy is not actually
buying coverage that healthchecks.io's own ntfy integration doesn't
already provide.

Two layers were configured:

| Layer | Triggers on | Notifies via |
|---|---|---|
| Borgmatic ntfy hook (broken) | every backup start/finish/fail | direct push from NAS to ntfy.sh |
| Healthchecks integration | check-state transition (e.g. missed run, late finish) | ntfy push + email |

For the actual failure modes I care about (missed run, prune failed,
deep check found corruption), healthchecks is the better signal. It
fires *because something is wrong*, not on every healthy nightly. The
borgmatic ntfy hook was firing a "nightly OK" push every morning,
which is exactly the kind of notification that trains you to ignore
the channel. Worse, the broken ntfy hook would have to be debugged
twice — once on NAS and once on Arch admin — because both configs had
the block.

So the call was: drop borgmatic-side ntfy on both ends, route
everything through healthchecks. Silence = healthy, push or email =
something is wrong. Notification policy lives in one place
(healthchecks.io's check integrations) instead of split across
borgmatic configs on two hosts.

Removed: `ntfy:` block from `nas/borgmatic/borgmatic.yaml` and
`arch/borg-admin/borgmatic.yaml`. `NTFY_TOPIC` env var from
`nas/borgmatic/docker-compose.yml` + `.env.example`. Live `.env`
cleanup pending on the user side (sed one-liner, cosmetic).

## What I'd do differently in the plan

The original plan paired borgmatic ntfy with healthchecks because
that's what the borgmatic-collective docs show. It's a reasonable
default, but for a setup where the alerting fan-out is already going
through healthchecks, the second channel just duplicates the same
event with worse semantics (every success notification dilutes the
failure ones). Should have caught this at design time.

The dry-run flag's split logging behavior (healthchecks logs would-ping
at verbosity 1, ntfy doesn't) is worth remembering for any future
borgmatic config probe. `--verbosity 2` is the level where ntfy hook
calls become visible without firing for real.

## Open gap I accepted

The admin side (Arch, `arch/borg-admin/`) runs prune weekly and `check
--data` monthly. Both had the ntfy fail hook; both now don't. No
healthchecks check is wired for either. Per the decision tree:
data is still safe (prune failure = retention not applied, check
failure = corruption flagged in logs not in a push), and adding a
healthchecks check per admin action is something I can do later if a
real failure bites. The comment in `arch/borg-admin/borgmatic.yaml`
flags this for the next person to look at the file.

The reasoning is the same as the ntfy drop: don't add a notification
channel until you've felt the absence of one. The cost of a silent
prune failure is "Hetzner usage drifts above budget over weeks" — very
visible from the BX21 dashboard. The cost of a silent monthly check
failure is corruption that goes undetected for a month, which is what
the *next* check would still surface. Acceptable, with the door open
to add coverage later.

## State / pending

- Task 15 wiring committed (`df6ada2` healthchecks block, `f277e0d`
  drop ntfy). Live deploy on NAS pending: paste the updated yaml +
  compose into Dockge, optional sed cleanup of the live `.env`, then
  re-deploy. Live admin env on Arch: drop `NTFY_TOPIC=` line.
- Phase 4 (Task 17 test restore) still pending. That's the next thing,
  and the one that unblocks Forgejo's Status line for real customer
  data.
