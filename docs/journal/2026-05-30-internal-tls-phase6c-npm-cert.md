# 2026-05-30 — Internal TLS Phase 6c: OVH access rules are more literal than I thought

## What landed

`*.lan.dertechie.de` is held in NPM as an LE-signed ECDSA wildcard cert,
issuer Let's Encrypt YE2, valid through 2026-08-28. Both `*.lan.dertechie.de`
and `lan.dertechie.de` are in SANs. The cert is parked ("Not Used" in NPM)
until 6d attaches it to the Forgejo proxy host.

Phase 6c budgeted 15-25 minutes. Actual elapsed was closer to two hours,
because OVH's API scoping bit three times in a row and I kept solving a layer
deeper than the last fix. Worth writing up because the pattern was the same
each time: assume more about OVH path matching than the docs actually
guarantee.

## Three OVH gotchas, in order

### 1. `certbot-dns-ovh` calls `GET /domain/zone` for zone discovery

When you hand the plugin a name like `lan.dertechie.de` and it has to figure
out which OVH zone that subdomain lives under, it lists all zones on the
account and finds the longest match. That list endpoint is `GET /domain/zone`,
which is not under `dertechie.de` — it's the root of the zone product.

My Phase 6a scope was `GET/POST/PUT/DELETE /domain/zone/dertechie.de/*`. Tight,
"narrowest realistic" — but it covers operations *inside* the zone, not the
account-wide list call the plugin makes first. So the plugin's very first
request 403s before it ever tries to write a TXT.

Fix: add `GET /domain/zone` to the scope. Conceptually obvious in hindsight.
The 6a verification probe didn't catch it because I tested the path the spec
*wrote about* (record CRUD on dertechie.de), not the path the *plugin* uses
under the hood.

### 2. `createToken` makes a new application; `/auth/credential` rotates only the CK

When the first scope-fix attempt failed, I told myself "regenerate the CK with
the extra rule" — and reached for the URL I'd used in 6a:
`https://eu.api.ovh.com/createToken/?GET=...&POST=...`. That endpoint creates
a *new application* (new AK, new AS, new CK) every time. Two attempts in,
I had three AKs and three CKs floating around, and the value I was pasting
into NPM kept being from a different application than the one I'd just
verified at the curl prompt.

The right OVH endpoint for "same app, new CK with different rules" is
`POST /1.0/auth/credential`. It needs only the existing AK in the
`X-Ovh-Application` header, takes the new accessRules in the body, and
returns a `validationUrl` plus a pending CK. You open the URL in a browser,
confirm, the CK becomes active, and your AK/AS are untouched.

```bash
curl -sS -H "X-Ovh-Application: $OVH_AK" -H "Content-Type: application/json" \
  -d '{"accessRules":[...]}' \
  https://eu.api.ovh.com/1.0/auth/credential
```

This is the flow python-ovh's `new_consumer_key_request()` wraps. I knew this
existed; I just defaulted to the URL I already had open.

### 3. OVH access rules are byte-literal — trailing slash counts

After CK rotation #2 fixed the createToken-vs-credential confusion and gave
me a CK that worked for `GET /domain/zone` (verified 200 at curl), NPM was
*still* failing with the same 403. The error message was the giveaway, once
I actually re-read the URL it printed:

```
403 Client Error: Forbidden for url: https://eu.api.ovh.com/1.0/domain/zone/.
```

The trailing `/` in `domain/zone/`. The plugin calls with a trailing slash;
my access rule was `GET /domain/zone` without one. OVH's rule matcher does
not normalize — `/foo` and `/foo/` are different rules. Two probes from the
same CK:

| Request path | Result |
|---|---|
| `GET /domain/zone`  | 200 ✓ |
| `GET /domain/zone/` | 403 ✗ |

The fix was to issue the CK with `GET /domain/zone/` instead. The probes
flipped (the trailing-slash version is now 200, the no-slash one is 403),
which is fine — the plugin only calls with a trailing slash. Cert issued
within a minute of NPM's next save.

The OVH docs I could find talk about `*` matching multiple resources (e.g.,
`vps:apiovh:ips/*` granting `ips/edit`, `ips/delete`, `ips/get`), but never
explicitly state that `*` does not match the empty string, nor that trailing
slashes are distinct from non-trailing-slash paths. Both are true in
practice, and both bit me here.

## Final scope, for the record

The five rules attached to the working CK:

| Method | Path |
|---|---|
| GET    | `/domain/zone/` |
| GET    | `/domain/zone/dertechie.de/*` |
| POST   | `/domain/zone/dertechie.de/*` |
| PUT    | `/domain/zone/dertechie.de/*` |
| DELETE | `/domain/zone/dertechie.de/*` |

Five rules instead of four, but the surface is still tightly bounded. The CK
can list zones on the account (it has to, for the plugin's discovery step),
and can read/write records inside `dertechie.de` only. It cannot touch any
other product, any other domain, or any other zone.

## Why this took so long

The same shape as 6b's "Pi-hole v6 moved the goalposts": I had a mental
model of how the tool worked that was *almost* right, and the failures
looked like other things — wrong credentials, NPM caching, paste mistakes —
until the URL in the error message made the actual issue undeniable.

The lesson I want to remember: **when an external system 403s on a request
you believe is scoped, read the URL in the error byte-by-byte before assuming
it's a credentials problem**. Six lines of trial-and-error vs a 30-second
re-read of the exact URL the plugin built. The plugin was telling me the
right thing the whole time.

Adjacent lesson: **prefer the API endpoint that does what you mean over a
URL you already have open**. `createToken` and `auth/credential` exist for
different jobs, and reaching for the wrong one cost an hour because each
attempt looked like a new failure of the same root cause when it was
actually a fresh app I'd just made.

## What's next

Phase 6d — cut over `git` as the canary. Attach this wildcard cert to the
existing Forgejo proxy host alongside `git.home`, add `git.lan.dertechie.de`
as an additional domain, flip Forgejo's `ROOT_URL`, update fj and the git
remote on the workstation. After ≥7 days on the new name without incident,
6e cleans up the `.home` plumbing.
