# 2026-05-26 — Borg append-only negative test: the rc 0 trap

## What I was trying to verify

Task 14 of the backup plan: prove the NAS writer key cannot remove data from
the Hetzner repo. The threat model is "attacker roots the NAS and steals the
writer key" — append-only should make the worst case "they can write garbage,
they cannot destroy backups". I wanted a live demonstration, not just a
config audit.

The structural side was easy:

```
command="borg serve --append-only --restrict-to-path /home/borg-repo",
no-port-forwarding,no-X11-forwarding,no-pty ssh-ed25519 ... borg-writer@nas-aisetup
```

That line, pulled fresh from the Storage Box via SFTP with the admin key, is
the security claim. Confirmed by sshing in with the writer key from inside
the borgmatic container and trying to `echo test` — got
`Borg 1.2.9: Got connection close before repository was opened.` instead.
The forced command runs; the server starts in append-only mode.

The live test is where I lost the plot.

## Three tests that said "yes" without proving anything

**Attempt 1: `borg compact` from the writer.** Returned `rc 0`. My first
read: append-only is broken. Wrong read. Compact only does anything when
there are tombstoned segments. We had none. It returned rc 0 because it had
literally no work to do — the append-only enforcement was never tested.

**Attempt 2: create a 1 MB random throwaway archive, delete it, then
compact.** Reasoning: now there IS a tombstone, so compact has work, so
append-only should refuse. Returned `rc 0` again. Closer this time but
still inconclusive: in borg 1.x, compact has a default `--threshold 10`,
meaning a segment must be ≥10% orphaned to be considered for rewrite. A
1 MB orphan inside a ~500 MB segment that's otherwise 99.8% live chunks
falls below threshold, so compact silently skips it. Still no append-only
test fired.

**Attempt 3: same as 2 but with `--threshold 0`.** Now compact has work AND
is told to act on any orphan, however small. Returned `rc 0` a third time.
At this point I was ready to conclude append-only was broken.

It wasn't. I was wrong about what `rc 0` means.

## The trap

Borg's append-only enforcement in 1.2.x (the server-side version on the
Hetzner box) is *soft*: when a writer issues compact, the server accepts
the RPC and silently logs the would-be deletions to a pending-delete log
instead of executing them. The client gets a clean `rc 0`. The repo is
unchanged. No error surfaces.

This is documented behavior — the design intent is that an admin can later
review and act on the pending deletes for forensic recovery — but the docs
phrase it as "only allow appending to repository segment files", which I
mentally translated to "deletion RPCs are refused". Newer borg (≥1.4 docs)
spells it out more strictly ("compact ... will be denied"), but the
**server's** behavior is what matters, and the server is 1.2.9.

The practical consequence: you cannot prove append-only works by watching
the writer's exit code. The writer's view always says success. You have to
look at whether anything was actually removed.

## The test that actually proved it

Run compact from the **admin** key (no append-only), which DOES execute
deletes, and watch the bytes-freed report. If the prior writer compacts
had really deleted the throwaway, admin compact would have nothing to do.
If they hadn't, admin compact would surface the still-pending 1 MB orphan
and free it.

```
$ borg --verbose --show-rc compact --threshold 0 \
    --rsh 'ssh -p 23 -i ~/.ssh/borg-admin-arch' \
    ssh://u600186@.../borg-repo
Remote: compaction freed about 1.09 MB repository space.
terminating with success status, rc 0
```

That 1.09 MB is the throwaway. After three writer-side compact attempts
that reported rc 0, the data was still sitting on the Storage Box,
unfreed. The append-only enforcement worked. The writer cannot remove
data, even when its own compact command claims success.

Real archive `nas-aisetup-2026-05-26T15:02:07` listed and intact after
the whole sequence.

## What I'd do differently

The single most useful change to the plan would be: never trust a `rc 0`
from a writer-side destructive op as evidence of either success or
refusal. Always cross-check with the admin side — either by listing what
the repo actually contains, or by running an authoritative compact that
shows real bytes freed (or zero, if the writer had really succeeded).

The version split (client 1.4.4 in the container, server 1.2.9 on
Hetzner) cost me an attempt because I read the 1.4 docs and assumed
strict refusal. For any server-side semantic question, the server's
version is the one that matters; the client's is just the wire format.

The structural test (SFTP the authorized_keys, confirm the
forced-command line, confirm the writer key in the container matches by
fingerprint) was the highest-value evidence the whole time. The live
test only added value because it caught my misunderstanding of what
"works" looks like for borg 1.2 append-only. Future negative tests:
lead with the structural audit, then design the live test to look at
**state change** (repo size, segment files, admin-side compaction
yield), not at the writer's exit code.

## What this means operationally

The writer key on the NAS borgmatic container can:

- Create new archives. (Verified, that's the daily job.)
- Issue `borg delete <archive>`: succeeds in the manifest, archive
  disappears from `borg list`, but the chunks remain on disk.
- Issue `borg compact`: silently no-op'd by the server (`rc 0` is a lie).

The writer key cannot, even by trying:

- Actually free segment files. (Just proved.)
- Wipe the repository. (Implied — same RPC path, same refusal.)
- Modify segment files in place. (Same.)

Admin key (on Arch, `~/.ssh/borg-admin-arch`, unrestricted) is the only
path that can compact, prune, recover from tombstoned-by-writer
mischief, or destroy the repo. That key lives only on Arch + escrow
(1Password vault item `borg-admin-arch (private key - AISetup BX21)`),
not on the NAS.

## State / pending

- Task 14 of `docs/superpowers/plans/2026-05-25-nas-backup-strategy.md`
  is **DONE**. Repo is back to a single archive (real one), no
  tombstones, no leftover throwaway segments.
- Task 15 (ntfy.sh + healthchecks.io wiring) and Task 17 (Phase 4 test
  restore) still pending.
- The "writer compact returns rc 0 but does nothing" finding is a
  durable operational fact, not specific to this test — captured in
  memory as a separate entry so future debug sessions don't fall into
  the same trap.
