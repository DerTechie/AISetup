# 2026-05-26 — First Borg archive: four walls, and Pattern B for pg version drift

## What happened

Task 13 of the backup plan: kick off the first archive to Hetzner. I expected
a one-shot. `borg init`, deploy the stack, let it run for an hour. Instead it
took four retries before `borg create` stayed alive past the manifest read.

Each failure exposed the next. None of them were anticipated in the plan.

## Wall 1: `PathNotAllowed` on the writer key

First attempt got past pre-hooks (all five `pg_dump`s ran), reached `borg
create`, then died:

```
borg.remote.PathNotAllowed: Repository path not allowed: /home/borg-repo
```

The writer key's `authorized_keys` line on the Storage Box was installed with
`--restrict-to-path /home/u600186/borg-repo` (Task 9 of the plan, with the
real user id substituted for the placeholder). Hetzner SSH chroots the user.
From inside the chroot, the user's home appears as `/home`, not
`/home/u600186`. So `ssh://…/./borg-repo` resolves to `/home/borg-repo` on
the server side, and the restriction never matches.

The `borg init` from Arch had worked earlier because the admin key has no
`--restrict-to-path` clause. So the repository physically lives at
`/home/borg-repo`. Only the writer key was guarding the wrong path.

Fix: pulled `authorized_keys` over SFTP with the admin key, swapped the path
to `/home/borg-repo`, uploaded back, verified byte-for-byte.

## Wall 2: pg_dump 17 vs Postgres 18

Second attempt got past Wall 1, reached `borg create`, started reading the
chunks cache, then failed at pg_dump immich:

```
pg_dump: error: aborting because of server version mismatch
pg_dump: detail: server version: 18.2 (Debian 18.2-1.pgdg12+1); pg_dump version: 17.9
```

The borgmatic-collective image ships PostgreSQL client 17. The Dockge stacks
(litellm and langfuse) run pg 16, so they were fine. Client 17 happily dumps
older servers. The three TrueNAS apps (n8n, paperless-ngx, immich) run
pg 18.x via the TrueNAS Apps catalog. pg_dump's strict version check refuses
newer-than-client.

The plan picked the borgmatic image without checking pg client versions
because everything was nominally "postgres". This kind of failure only bites
when you cross orchestrators, which is exactly the shape of this NAS.

## Wall 3 (compounded): DNS collision across networks

While reading the log for Wall 2, something didn't add up. n8n and paperless
are also pg 18. Same client, same image. They should have errored the same
way. They didn't. Only immich got reported.

The borgmatic container had joined five external networks. Three of them
(`langfuse_default`, `ix-internal-n8n-n8n-net`,
`ix-internal-paperless-ngx-paperless-net`) have a service literally named
`postgres`. Docker's embedded DNS resolves `postgres` against whichever
network responds first, and that turned out to be langfuse on pg 16. So
when borgmatic's pg_dump for "n8n" ran with `--host postgres`, it actually
connected to langfuse's pg. Wrong user, wrong db, presumably failed quickly
without surfacing as the headline error. Only immich's service is `pgvecto`
(unique), which is why it was the one that surfaced cleanly.

Two problems with one cause: the plan's hook config assumed each service
name was unique inside the container's network view. With three apps using
the docker-compose-default service name `postgres`, it wasn't.

## The fix: Pattern A or Pattern B?

The borgmatic community runs two patterns for multi-version pg dumps.

**Pattern A: derived image with newer pg client.**
`FROM ghcr.io/borgmatic-collective/borgmatic:latest`, add the pgdg apk repo,
install `postgresql-client-18`. Solves the version mismatch. Doesn't solve
the DNS collision. You'd still have to address containers by unique name
instead of by service alias.

**Pattern B: `pg_dump_command` override into the actual pg container.**
borgmatic 2.x lets you replace the pg_dump executable per database. Point it
at `docker exec -e PGPASSWORD ix-<app>-<svc>-1 pg_dump`. The dump runs inside
each app's own postgres container, so the client version always matches the
server by construction. The DNS collision evaporates because we address
containers by unique name, not by service alias.

Pattern A is the more conservative move and is what most self-hosters in my
shape do. Pattern B requires mounting `/var/run/docker.sock` into the
borgmatic container, which gives it root-equivalent access via the docker
daemon.

I picked Pattern B. Three reasons.

Mixed orchestrators (Dockge plus TrueNAS Apps) means pg versions will keep
diverging on their own schedule. A derived image needs rebuilding for every
pg major bump on any protected service. Pattern B is unaffected.

The docker socket exposure adds risk, but the container already holds the
writer SSH key for Hetzner and read-only mounts of every app data directory.
Root-equivalent on the docker socket on the same host isn't a meaningful
expansion of what gets compromised if this container is compromised.

The config reads more honestly. Each hook says which container it dumps
from. The implicit "service name happens to match across networks" coupling
goes away.

The Dockge stacks (pg 16, no collision) stayed on the native pg_dump path.
No reason to add docker-exec indirection where the plain path works.

## Wall 4: three env var naming bugs

Pattern B required two new env vars on the borgmatic container: install
docker CLI, and (separately, since I was already editing the env block) fix
the cron schedule, which had been silently falling back to the image default
of 01:00. I set what I thought the variable names were:

| What I set | What the image actually reads |
|---|---|
| `DOCKER_CLI` | `DOCKERCLI` |
| `BORGMATIC_CRON` (vixie format with user field) | `BACKUP_CRON` (schedule only) plus optional `EXTRA_CRON` |
| `BORG_CUSTOM_PACKAGES` (didn't end up needing) | `EXTRA_PKGS` |

The image is Alpine plus s6-overlay, not Debian, and its env vars follow
LinuxServer.io's conventions. The startup log was telling me this the
whole time:

```
[custom-init] Docker CLI variable not set, skipping...
```

Bracketed `[custom-init]` lines mean "I'm looking for a specific variable
and you didn't set it." Took two redeploys before I actually opened
`/etc/s6-overlay/s6-rc.d/init-custom-packages/run` and read the source of
truth.

## Wall 5: stale lock from `--force-recreate`

After fixing Walls 1 through 4, the next retry hit:

```
LockTimeout: /root/.cache/borg/<repo-id>/lock.exclusive
```

The `--force-recreate` between iterations had killed the container while
borg was mid-flight on the previous attempt. The local cache lock file
wasn't cleaned up. `borgmatic break-lock` cleared it. Borg's lock is for
concurrent-access correctness, not for graceful recovery from a SIGKILL'd
container. Operator's responsibility, not the tool's.

## Took

- Four retries between `Task 13: kick off first archive` and a `borg create`
  that actually started streaming chunks.
- The first archive kicked off at 2026-05-26T15:02:07 UTC.
- Source payload visible to me as my user: ~17 GB raw (immich photos
  dominate at 9.6 GB). Real total is larger because root-owned pg data
  dirs read as 30 bytes for `du`. Will fill in real numbers from the borg
  stats once the archive lands.

## What was rejected

- **Comment out the three failing hooks, ship a file-only first archive.**
  Tempting. The file-level capture still gives crash-recoverable pg state
  (the ZFS snapshot at 03:00 is atomic at the FS layer), and we'd be
  uploading within an hour. Rejected because deferring the problem doesn't
  shrink it, and the failure modes were instructive enough to solve with
  full context now.
- **Drop borgmatic's pg hook entirely, replace with custom `before_actions`
  scripts that do `docker exec pg_dump > file` and clean up after.** More
  flexible. Reinvents what `pg_dump_command` already gives. Pattern B is
  the half-step that preserves the rest of borgmatic's hook machinery,
  notably the ntfy / failure-path side.

## What I'd do differently

Two preventable failures hid in the plan.

Read the image's startup scripts before setting env vars from a remembered
README. `[custom-init] Docker CLI variable not set, skipping...` would have
been a 30-second answer instead of two redeploys.

Task 9 should have probed each protected service's real pg version, service
name, and network name during planning, not left them as `# adjust on first
deploy` placeholders. The placeholders were structurally correct but failed
loud in the worst place: mid-archive, after pre-hooks had already written
dump files to `/tmp`. A short pre-flight inventory script in Task 9 would
have caught Walls 2, 3, and arguably the third env-var bug in 4 before the
first redeploy. The cost would have been ten minutes of inspection; the
cost of not doing it was four retries and several restarts of a stack
holding the Hetzner writer key.

Most of the time, when a config plan says "adjust on first deploy", that's
a flag that the inspection got deferred. At least in a multi-orchestrator
setup, the inspection should be the planning, not the deployment.
