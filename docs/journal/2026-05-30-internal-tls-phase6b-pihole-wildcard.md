# 2026-05-30 — Internal TLS Phase 6b: Pi-hole v6 had moved the goalposts

## What landed

`*.lan.dertechie.de` resolves to `10.63.0.2` on the LAN. `fritzbox.home` still
points at the router. The wildcard rule lives at
`nas/pihole/dnsmasq.d/02-lan-dertechie.conf` in this repo and on the NAS at
`/mnt/nvme/apps/pihole/dnsmasq/02-lan-dertechie.conf`. Phase 6b of the
[internal-TLS migration](../superpowers/specs/2026-05-30-internal-tls-design.md)
is done.

The spec budgeted 10 minutes for "this is one config line." The actual elapsed
time was closer to 40 because Pi-hole v6 broke three v5 assumptions the spec
quietly carried. The spec is now corrected (§ 6b), but the story of how each
delta surfaced is worth keeping.

## Three Pi-hole v6 deltas the spec didn't anticipate

### 1. `pihole restartdns` is gone; replaced by `reloaddns`

The spec said `pihole restartdns`. Pi-hole v6 dropped the subcommand entirely.
`pihole reloaddns` is the replacement (cache-keeping reload of dnsmasq
configs). The error you get if you type the old one is just the help text,
not "command not found," so you might miss it if you're skimming.

Also: `pihole reloaddns` itself prints a non-fatal stderr noise line in v6.6.x —
`/opt/pihole/utils.sh: line 100: local: FTL_PID_FILE: readonly variable` — which
is a known regression in the shell wrapper, not an actual failure. The reload
*does* run, when it has anything to load. The trap is: this error pattern made
me chase ghosts for a while ("the reload is broken!") when the real problem
was upstream of the reload itself.

### 2. `/etc/dnsmasq.d/` is ignored by default in v6

This is the big one. In v5, dropping a `.conf` file into the dnsmasq.d
directory and reloading was the entire recipe. In v6, FTL does not load that
directory unless `misc.etc_dnsmasq_d = true` is set in `pihole.toml`.
([upstream docs, v5→v6 upgrade guide](https://github.com/pi-hole/docs/blob/master/docs/docker/upgrading/v5-v6.md#changed-environment-variables))

The symptom is wonderfully misleading: queries succeed (Pi-hole works fine),
but the wildcard isn't applied — Pi-hole just forwards the name upstream. In
my case that meant `anything-made-up.lan.dertechie.de` resolved to
`103.133.1.1`, the IP that public `*.dertechie.de` happens to wildcard to.
The query "worked." The rule was being ignored. This is the worst kind of
config bug, where the system behaves but not the way you wanted, and gives
you no error to grep for.

The fix is `pihole-FTL --config misc.etc_dnsmasq_d true`, then a container
restart so FTL re-reads pihole.toml. In a future deployment surface I'd
prefer this as `FTLCONF_misc_etc_dnsmasq_d=true` in the container env —
declarative, lives in the same place as the rest of the FTLCONF settings,
survives recreation without remembering to re-run the toml flip.

### 3. The host directory needs to be traversable by UID 1000

Once `etc_dnsmasq_d` was on, FTL crashed at startup:

```
dnsmasq: cannot access directory /etc/dnsmasq.d: Permission denied
CRIT: Error in dnsmasq configuration: cannot access directory /etc/dnsmasq.d: Permission denied
```

The host directory was `drwxrwx--- root:root` — the TrueNAS dataset default.
The file itself was `0644 root:root` (fine), but the directory wasn't
traversable by UID 1000, which is the `pihole` user FTL drops to inside the
container. `chmod 755` on the directory unblocks it. The dataset perms
weren't a problem in v5 because FTL never tried to read the directory.

A subtle wrinkle: a `docker exec <container> ls /etc/dnsmasq.d/` from the
host succeeds, because `docker exec` runs as root by default. So the obvious
"can I see the file from inside the container?" check passes, and looks like
the mount is fine — but it's only fine for the privileged path. The relevant
check is "can UID 1000 read this," which the FTL crash log spelled out.

## Why I didn't catch this from the spec

The spec was written 2026-05-30 morning, before I touched Pi-hole. I copied
the v5-shape `/etc/dnsmasq.d/` recipe from memory and didn't verify against
the v6 docs. The forgejo standup three days ago had exactly the same shape
of mistake: design spec referenced platform behavior that had already changed
("GitHub doesn't let you disable PRs without archiving"). That was a 3-day
gap on a documented vendor change; this was a 1-day gap on a year-old
major-version change in Pi-hole.

The lesson generalises (again): when writing a migration spec, the per-tool
recipes are the part that ages fastest, and they age silently — the design
ideas are stable, but `restartdns`-style commands and "drop a file in this
directory" patterns shift with the next major. The spec section for each
phase should explicitly cite which version of the tool it was tested on, or
be re-checked against the current docs at implementation time, not at
design time.

I'm not going to put a "tested on Pi-hole 6.x" annotation on every step of
every spec — that's overhead for a one-operator shop. But for any phase
where the recipe is *the* substance (as opposed to the decision), the cheap
move is "open the current vendor docs page for that recipe before running
it." Phase 6c, NPM gets the cert via DNS-01, is exactly such a phase.

## What's next

Phase 6c — NPM cert. With Pi-hole resolving the wildcard, the next step is
NPM acquiring the actual cert via DNS-01 against OVH, using the consumer key
from Phase 6a. Until that lands, HTTPS to `*.lan.dertechie.de` will fail with
a name mismatch (NPM still serving the mkcert cert), which is exactly the
phase ordering the spec called for.
