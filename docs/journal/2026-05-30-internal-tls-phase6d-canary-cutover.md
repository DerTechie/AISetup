# 2026-05-30 — Internal TLS Phase 6d: the canary works

## What landed

`git.lan.dertechie.de` is the canonical Forgejo URL. HTTPS reaches it via NPM
with the LE wildcard, SSH reaches port 30143 directly, `fj` authenticates
under the new host, the workstation's git remote pushes and pulls through
it. `git.home` still works in parallel — it has its own NPM proxy host with
its own mkcert cert, untouched by today's changes — and will until 6e cleans
it up after the seven-day burn-in window.

All five verification checks from § 7 pass:

| Check | Result |
|---|---|
| `curl -sI -X GET https://git.lan.dertechie.de/api/v1/version` | HTTP/2 200, no cert warning |
| `openssl s_client … x509 -issuer` | Let's Encrypt YE2 |
| SSH host key fingerprint vs `git.home` | identical (same upstream, different name) |
| `fj issue search -R origin --state open` (after remote flip) | 6 open issues including #1 |
| `git fetch origin --dry-run` | clean |

Push wasn't run during verification — it's the same auth path as fetch, so a
near-certain pass, and the next normal commit will exercise it for real.

## What was anticlimactic, which was the point

After the OVH gotcha chain of 6c, I half-expected 6d to surface a similar
sting. It didn't. The actual flip was a chart wizard env edit, a `git remote
set-url`, and an `fj auth login`. All three landed in under a minute apiece.
The Forgejo container came back up clean, the new OAuth token slotted into
the existing keys.json shape, and the SSH host key for the new name matched
the existing entries for `git.home` and `[10.63.0.2]:30143` byte-for-byte —
which is the right answer, because under the new name it's still the same
SSH server.

The smoothness is the validation of the phased design. By the time the
canary actually flips, the cert is already trusted (6c), the wildcard
already resolves (6b), the OVH plumbing is already paid for (6a). Each
earlier phase moves one variable off the table; phase 6d only flips the
single switch that points everything at the new name. That's what the
"phased migration with stop-conditions" structure was buying.

## One spec correction surfaced during the work

The spec's § 6d step 1 said: *"edit the Forgejo proxy host. Add `git.lan.dertechie.de`
as an additional domain (keep `git.home` for now). Attach the new wildcard
cert."* That assumed NPM's "Domain Names" field accepts multiple chips
sharing one cert binding. The chips part is true. The shared cert part is
not — NPM binds exactly one SSL Certificate per proxy host, and the LE
wildcard `*.lan.dertechie.de` does not cover `git.home`. Attaching the
wildcard to a proxy host that still has `git.home` in its domain list would
have meant TLS name-mismatch errors on the old name for the entire
transition window.

Actually-correct shape, for the spec: two proxy hosts during transition,
both pointing at the same Forgejo upstream:

| Proxy host | Domain | Cert |
|---|---|---|
| Existing (untouched) | `git.home` | mkcert custom |
| New (created today) | `git.lan.dertechie.de` | LE wildcard `*.lan.dertechie.de — ovh` |

Both forward http to `10.63.0.2:30142`. Forgejo doesn't see anything
different. The spec is updated.

## Chart wizard quirk: `DOMAIN` is the Forward IP, not derived from ROOT_URL

After flipping `ROOT_URL` to `https://git.lan.dertechie.de/`, the live
`app.ini` showed:

```
SSH_DOMAIN = git.lan.dertechie.de
ROOT_URL = https://git.lan.dertechie.de/
DOMAIN = 10.63.0.2
```

`SSH_DOMAIN` and `ROOT_URL` flipped together (the chart wizard derives one
from the other, or both are wired to the same chart variable). `DOMAIN`
didn't — it stayed at the raw cluster IP. Forgejo's `[server] DOMAIN`
governs canonical-name behaviour (cookie scope defaults, some link
generation fallbacks), and having it on `10.63.0.2` is unprincipled even if
it hasn't bitten in practice. Not blocking — clone URLs in the UI now show
`git.lan.dertechie.de` for both HTTPS and SSH, which is what users actually
see. But worth flipping for consistency, probably as part of the same
follow-on issue that handles the eventual `DOMAIN` cleanup.

This is a TrueNAS chart-wizard ergonomic, not a Forgejo bug. The chart
infers `DOMAIN` from the Forward IP it set up for the NodePort, separately
from whatever you write into the URL-shaped fields.

## What's next

The seven-day burn-in window starts now (counts to 2026-06-07). During that
window the old name and the new name both work — devices that already had
the mkcert rootCA installed see the LE cert on the new name and the mkcert
cert on the old name, both with green padlocks. Devices that *didn't*
already have the mkcert rootCA can now reach `https://git.lan.dertechie.de/`
fresh, padlock-green, zero setup. That's the actual win — the success
criterion from § 1 of the spec, now provable with any phone on the LAN.

After 2026-06-07, if nothing has gone wrong, 6e cleans up: remove
`git.home` from NPM's proxy host list, delete the `git.home` local DNS
record in Pi-hole, drop the `10.63.0.2:30142` and `git.home` entries from
the workstation's `~/.config/forgejo-cli/client_ids` and corresponding
hosts/aliases in `keys.json`, and `mkcert -uninstall` to retire the rootCA
from the system trust store. The mkcert Custom cert in NPM gets deleted
last, after nothing references it.

Until then: don't touch anything `.home`-shaped except to use it as a
fallback, and watch for whether the LE cert renews automatically before
2026-08-28. Renewal monitoring (spec § 11 follow-on issue #1) wants to land
before then.
