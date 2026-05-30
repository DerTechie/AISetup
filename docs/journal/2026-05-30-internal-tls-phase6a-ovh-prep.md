# 2026-05-30 — Internal TLS Phase 6a: OVH prep, narrower than narrowest

## What landed

OVH has a consumer key issued for `dertechie.de`, and a CAA record pins
issuance to Let's Encrypt. That's Phase 6a of the internal-TLS migration
([`2026-05-30-internal-tls-design.md`](../superpowers/specs/2026-05-30-internal-tls-design.md))
done. The token sits unused until NPM asks for the wildcard in 6c.

## The scope-narrowing finding

The spec § 6a wrote the scope as "`_acme-challenge.*` TXT records under
`dertechie.de` only (or zone-level write if scoping isn't available — journal
whichever applies)." OVH does not let you scope by record name. It scopes by
HTTP method plus API path. So `_acme-challenge.*`-only is not a thing OVH can
issue, no matter how narrowly you'd like to ask.

The actually-narrowest scope OVH grants for this job is:

```
GET    /domain/zone/dertechie.de/*
POST   /domain/zone/dertechie.de/*
PUT    /domain/zone/dertechie.de/*
DELETE /domain/zone/dertechie.de/*
```

That's zone-level write on `dertechie.de` and nothing else — no other domain,
no other product. It's broader than the spec's ideal but much narrower than
the default "manage your whole account" token most guides hand out. The
`createToken` URL accepts these as query params and pre-fills the form, so
the click path is "open URL, name the app, submit" rather than "remember to
narrow this in the UI later."

If OVH credentials leak, the CAA record (`0 issue "letsencrypt.org"`) caps the
blast radius from "anything in the zone, signed by any CA" to "anything in the
zone, signed by LE only." The CAA doesn't help against an attacker who has the
keys; it helps against an attacker who has the keys *and* tries to mint via a
different CA. Belt; not armour.

## The verification false alarm

First probe was `GET /domain/zone/dertechie.de` (the bare zone path) — 403.
The token was fine; the path wasn't in scope. OVH's `/path/*` rule matches
*sub-paths*, not the literal path itself. Re-probing `GET /domain/zone/dertechie.de/record`
returned 200, and that's the kind of path certbot-dns-ovh actually calls in
production.

The lesson generalizes for OVH: write verification commands against a path the
scope already covers — `/record` is a safe pick because it's both inside the
scope and on the certbot critical path. Don't probe the parent path "because
it's shorter."

## What's next

Phase 6b is the Pi-hole wildcard (`address=/lan.dertechie.de/10.63.0.2`). One
line of config, then DNS resolution starts working on the LAN. The cert isn't
in NPM yet, so HTTPS will fail with name mismatch until 6c — that's expected
and matches the spec's phase ordering.
