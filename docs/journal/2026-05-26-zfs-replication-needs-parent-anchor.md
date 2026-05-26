# 2026-05-26 — ZFS replication needs a parent anchor; classes belong to the app

## What happened

Six of nine TrueNAS Replication Tasks failed with the same error:

```
[EFAULT] Dataset 'nvme/apps/<x>' does not have any matching snapshots to replicate.
```

Dockge, Pihole, NPM replicated fine. Immich, LiteLLM, Langfuse, n8n, paperless-ngx,
metrics did not. Same parent-recursive replication design across all nine, so the
difference had to be in how Phase-1 snapshots were configured.

## The collision in the spec

The original `2026-05-25-nas-backup-strategy-design.md`:

- **§5.2** assigned snapshot classes **per child** (`*/pgdata` → Class A,
  `apps/n8n/data` → Class B, etc.).
- **§5.3** mandated **one Replication Task per protected NVMe parent dataset,
  recursive.**

For the three that worked, the children of the parent happened to all be Class
B (`apps/dockge/*`, `apps/pihole/*`, `apps/nginx-proxy-manager/*`) — the Class B
row uses wildcards, so the snapshot task was created **recursive at the
parent**, and the parent picked up matching snapshots as a side effect.

For the other six, Class A children (`*/pgdata`, etc.) were given their own
**per-leaf, non-recursive** snapshot tasks. The parent dataset itself had no
snapshots at all. TrueNAS recursive replication anchors at the source dataset:
no parent snapshot, no anchor, immediate failure.

## What TrueNAS actually requires

Primary source — community thread on the identical error, accepted answer:

> "It appears you created a Periodic Snapshot Task named 'auto-XXXX' for the
> root dataset 'JailData', yet you did not make it a *recursive* snapshot.
> Hence, the *child* datasets lack these atomic snapshots."

And the structural limit, from the [TrueNAS Scale replication screen
docs][trueapi-repl]:

> "The recursive model replicates from the selected source level downward, not
> selectively from child datasets upward."

There is no mode in TrueNAS where a recursive replication anchored at a parent
can find anchors only on its children. The replication and snapshot task have
to agree on `recursive` and on which dataset they apply to.

[trueapi-repl]: https://www.truenas.com/docs/scale/dataprotection/replication/replicationscreens/

## Fix

Two scripts under [`nas/snapshot-tasks/`](../../nas/snapshot-tasks/):

1. `2026-05-26-recursive-parent-migration.sh` — created 21 new recursive parent
   snapshot tasks (2 each on immich/litellm/langfuse, 5 each on
   n8n/paperless-ngx/metrics), copied retention values from the working
   Dockge/Pihole/NPM rows. `langfuse` excludes `clickhouse-logs` (regrowing log
   data, marked Excluded in §5.1).
2. `2026-05-26-cleanup-relink-and-delete.sh` — re-linked the six failing
   replications from old per-leaf task IDs to the new recursive parent IDs via
   `replication.update`, then deleted the 25 redundant per-leaf snapshot tasks.

After the first script, the old per-leaf tasks started erroring every hour
with *"cannot create snapshot ... dataset already exists"* — the new recursive
parent task beat them to the same snapshot name. So Phase 2 cleanup wasn't
optional; it was blocking ongoing health.

## The deeper insight: classes belong to the app, not parts of the app

Once snapshot tasks live on the parent, the per-child class labels in §5.2 stop
carrying information. A recursive `-A` task at `nvme/apps/n8n` puts an `-A`
snapshot on `n8n/postgres` **and** `n8n/data`, regardless of which one was
nominally "Class A". The spec's per-child taxonomy was a way of saying *"this
app has hot transactional state and standard config state"* — but TrueNAS
forces that policy to be expressed at the app level.

Restructured §5.2 to three app-level shapes:

| Class | Cadences | Retention | Apps |
|---|---|---|---|
| **A — Hot only** | hourly + daily anchor | 24h + 14d | immich, litellm, langfuse* |
| **B — Standard only** | 6h + daily + monthly | 12×6h + 30d + 6mo | dockge, pihole, nginx-proxy-manager |
| **M — Mixed (hot + standard)** | hourly + 6h + daily + monthly | A ∪ B | n8n, paperless-ngx, metrics |

\* langfuse excludes `clickhouse-logs` on the snapshot task.

ZFS copy-on-write makes the "extra coverage" on mixed parents nearly free —
hourly snapshots of slow-changing `n8n/data` cost a few KB of metadata each.

## What was rejected

- **Additive anchor task (keep per-leaf, add daily recursive parent for
  anchoring).** Replication would only carry daily granularity to the HDD
  replica; the per-leaf hourly snapshots would stay local. Violates §5.3's
  "Retention: matches source."
- **One Replication Task per protected leaf.** Works without changing snapshot
  policy, but blows up to ~25 replication tasks instead of 9, and contradicts
  §5.3 explicitly.
- **Collapsing A and B into one retention superset per parent and dropping the
  class distinction entirely.** Considered. Kept the A/B/M split because the
  two retentions encode meaningfully different recovery patterns: A = short
  crash-recovery window (the canonical postgres restore path is `pg_dump` in
  Borg, not snapshots; long retention here is waste); B = long config-rollback
  window (months of accumulated app state worth reverting to).

## What I'd do differently

The migration script should have re-linked the replications in the same pass —
`pool.snapshottask.create` returns the new task IDs, and `replication.update`
would have wired them in atomically. Splitting it into two scripts meant the
old per-leaf tasks errored hourly for a full day until the cleanup ran. Filed
under "the script that's safe to re-run is better than the script that's done
in fewer commands."
