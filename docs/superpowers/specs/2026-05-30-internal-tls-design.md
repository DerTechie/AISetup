# Internal TLS for `.lan.dertechie.de` Services — Design Spec

- **Date:** 2026-05-30
- **Status:** Approved (design phase) — implementation pending
- **Owner:** DerTechie
- **Tracking issue:** Forgejo `dertechie/AISetup` #1
- **Relationship to other specs:** Cross-cutting; supersedes the per-service TLS approach implicit in [`2026-05-25-self-hosted-forgejo-design.md`](2026-05-25-self-hosted-forgejo-design.md) § 5 ("Access"). The Forgejo install today reaches clients via `https://git.home` with an mkcert-signed cert — this spec replaces that pattern for Forgejo and every future `.home` service.

## 1. Purpose & goals

Every internal service we put behind nginx proxy manager (NPM) should serve real HTTPS, every device on the LAN — current and future — should trust the cert with zero manual setup on the device, and cert rotation should not be a thing I have to remember.

The trigger for this spec was the 2026-05-30 cutover from `http://10.63.0.2:30142` to `https://git.home` for Forgejo. NPM only offered "Let's Encrypt" or "None" for certs; `.home` is a private TLD Let's Encrypt cannot sign; so I generated a leaf cert via mkcert's local CA, installed mkcert's rootCA into the Arch workstation's system trust store, and uploaded the leaf to NPM as a Custom cert. That works, but it's a per-host pattern: every new device that wants to reach a `.home` service has to install the rootCA, and every new `.home` service would repeat that ceremony.

This spec picks one path for internal TLS and documents it as a NAS-wide pattern — not bolted on per service.

**Success criteria:**

1. A new internal service can be onboarded in <10 minutes with no DNS edit and no cert work — add an NPM proxy host pointing at the new container, pick the existing wildcard cert from a dropdown, save.
2. A fresh device on the LAN (laptop, phone, visitor) opens `https://<service>.lan.dertechie.de` with a green padlock and **zero profile or cert install**. This is the test that the local-CA distribution problem actually went away, not that it merely got automated.
3. No `.home` references remain in active config (NPM proxy hosts, Pi-hole local DNS, fj client state, git remotes, README, runbook), except `fritzbox.home` as a special case.
4. NPM has completed at least one automatic cert renewal end-to-end without human intervention — proof that the renewal flow works, not just the first-issue.

**Non-goals (explicitly):**

- mTLS / client cert auth. Not needed at this scale.
- Per-service certs. Wildcard is the explicit pick; the compartmentalization tradeoff is accepted.
- HSTS preload submission. Requires public DNS exposure, which we are deliberately not doing.
- A local CA in any form. mkcert / step-ca were considered and rejected in § 3; the mkcert install from 2026-05-30 gets retired in § 6.
- A second TLS terminator on another box. NPM remains the single terminator even when services split across hosts (see § 5).
- Automated rotation of the OVH API token. It is one long-lived secret; if it needs rotation that is a manual runbook step.

## 2. Decision

Carve `lan.dertechie.de` out of the public DNS zone for `dertechie.de`. Resolve `*.lan.dertechie.de` on the LAN side only (Pi-hole / dnsmasq wildcard pointing at NPM). Let NPM hold a Let's Encrypt wildcard cert issued via **DNS-01 against OVH**, with auto-renewal.

No public A records are published. The internet-visible footprint of this is exactly the transient `_acme-challenge.lan.dertechie.de` TXT record during renewal, plus an optional CAA record pinning issuance to Let's Encrypt.

## 3. Why this and not the alternatives

Four options were considered. The headline trade-off is "do we need to distribute a custom root CA to every client" — the answer is what differs.

| Option | CA distribution to clients | Cert rotation | New device onboarding | One-time setup |
|---|---|---|---|---|
| **A.** mkcert + manual distribution | Required, per device | Manual every ~2y | ~10 min/device, every time | ~0 (status quo) |
| **B.** mkcert + bootstrap URL + mobileconfig | Required, but one-shot via hosted URL | Manual every ~2y | ~5 min/device | ~30 min |
| **C.** *(this spec)* `*.lan.dertechie.de` + Let's Encrypt via DNS-01 | **None — public CA already trusted everywhere** | **Automatic via NPM/certbot, ~60d** | **None** | ~1.5–2.5 h |
| **D.** step-ca with ACME on `*.home` | Still required (ACME automates leaves, not client trust) | Automatic | Same as A | ~half a day (new service, monitoring) |

Picking **C** because:

- The thing the migration is paying for is *not* automation of leaves — it's elimination of the rootCA-on-every-client step. Only C does that.
- The build-in-public angle: someone reading the journal can replicate this with their own domain. They cannot replicate a custom CA without taking on that operational baggage themselves.
- OVH (the registrar where `dertechie.de` lives) has a working DNS API and a supported certbot plugin, so DNS-01 is not a fight.
- Pi-hole handles LAN DNS already, and dnsmasq supports wildcard rewrites via a one-line config file. The "where do new names get added?" question stays a single line, forever.

**A** is the status quo and is fine for a single operator with one device. It does not survive the second device.

**B** is the right answer if we cannot get DNS-01 working (e.g., the registrar API is hostile, or there's no internet-side renewal path). It pays once-per-device-forever instead of once-per-renewal-forever.

**D** automates the wrong half of the problem. Auto-rotating leaves is cheap; distributing the rootCA is the cost, and step-ca doesn't help with that.

### Why `*.lan.dertechie.de` and not `*.home` / `*.lan` / `*.internal`

- `.home` and `.lan` are informal private TLDs. They work, but Let's Encrypt will never issue for them — that's the whole reason the local CA was needed in the first place.
- `.internal` was reserved by ICANN in mid-2024 for private use, but is still a private TLD: same constraint.
- A subdomain of a domain I own (`lan.dertechie.de`) is what makes Let's Encrypt possible. It costs nothing extra — I already pay for `dertechie.de` — and the subdomain never resolves publicly.

### Why wildcard and not a SAN list

- Adding a new service should never trigger a cert re-issue. With a SAN list, every `*.lan.dertechie.de/<new>` needs the cert regenerated and re-attached in NPM. With a wildcard, the cert is bought once.
- Compartmentalization tradeoff (a leaked key affects every internal service until rotation) is accepted: solo operator, one box, one trust boundary, no plausible threat model where per-service compartmentalization changes the blast radius.

## 4. Architecture

```
        ┌──────────────────────────────────────────────────────────────┐
        │  OVH DNS zone for dertechie.de  (PUBLIC, internet-visible)   │
        │  • _acme-challenge.lan.dertechie.de TXT  — transient, mins   │
        │  • CAA record pinning issuance to Let's Encrypt              │
        │  • NO A records for *.lan.dertechie.de  — never published    │
        └────────────────────▲─────────────────────────────────────────┘
                             │ DNS-01 challenge, every ~60d
                             │ (OVH API token, scoped)
              ┌──────────────┴────────────────┐
              │  certbot inside NPM           │
              └──────────────┬────────────────┘
                             │
                             ▼
        ┌──────────────────────────────────────────────────────────────┐
        │  Nginx Proxy Manager  (NAS, 10.63.0.2 :443)                  │
        │  • Holds *.lan.dertechie.de wildcard leaf + key              │
        │  • Proxy hosts (cert dropdown picks the wildcard for each):  │
        │      git.lan.dertechie.de       → ix-forgejo:3000            │
        │      <next>.lan.dertechie.de    → <upstream>                 │
        └────────────────────▲─────────────────────────────────────────┘
                             │ HTTPS (LE-trusted), plain LAN
                             │
        ┌────────────────────┴─────────────────────────────────────────┐
        │  Pi-hole on the NAS  (dnsmasq)                               │
        │  • /etc/dnsmasq.d/02-lan-dertechie.conf:                     │
        │      address=/lan.dertechie.de/10.63.0.2                     │
        │  • ONE rule covers every current + future *.lan.dertechie.de │
        │  • fritzbox.home stays as a special A record (→ 10.63.0.1)   │
        │  • All other .home entries get retired after cutover         │
        └────────────────────▲─────────────────────────────────────────┘
                             │
              ┌──────────────┴────────────────┐
              │  Clients on the LAN           │
              │  (Arch, phone, future devs)   │
              │  — trust LE root, zero setup  │
              └───────────────────────────────┘
```

### Trust boundary

- **Private, never leaves the NAS:** the LE leaf cert's private key and the OVH API token.
- **Private, but on more than one host:** nothing. There is no local CA root to copy around. That is the entire point.
- **Public, but transient:** the `_acme-challenge.lan.dertechie.de` TXT record during renewals (minutes-long window). The CAA record on `dertechie.de` is also public but does not reveal subdomain structure.
- **Public, structural:** the fact that `lan.dertechie.de` exists as a name (visible only via the TXT record and the LE certificate-transparency log; no IPs are published).

### Service-side topology

Services behind NPM stay exactly as they are: plain HTTP upstreams. The only per-service change is that any `ROOT_URL`-equivalent setting flips from `<service>.home` to `<service>.lan.dertechie.de`. For Forgejo specifically, that's the `[server] ROOT_URL` setting (exposed in the TrueNAS chart wizard env).

## 5. What stays put when services split across hosts

If a service later moves to another box (10.63.0.3, a Raspberry Pi, whatever), **Pi-hole does not change** and **the cert does not change**. NPM's proxy host for that service gets its Forward Hostname/IP field edited to the new upstream, save, done. NPM is the single TLS terminator for the LAN; new boxes are upstreams, not new terminators.

The only case where Pi-hole would gain a more-specific override is if a service explicitly wants to bypass NPM (e.g., it runs its own reverse proxy with its own cert). That is out of scope for this spec — adding more TLS terminators is a different decision.

## 6. Migration plan

Five phases. Each has a stop-condition; the budget is honest, not optimistic.

| Phase | What | Budget | Stop-condition |
|---|---|---|---|
| 3a | OVH API token + optional CAA record | 15 min | If OVH won't scope the token narrowly enough, accept a broader scope and journal it |
| 3b | Pi-hole wildcard | 10 min | None — this is one config line |
| 3c | NPM requests the wildcard cert via DNS-01 | 15–25 min | If DNS-01 doesn't complete in 30 min, stop and journal. Don't chase it live. |
| 3d | Cut over `git` as the canary | 15 min | After this works end-to-end (browser, fj, git push) the recipe is proven |
| 3e | Cleanup of the `.home` setup | 15 min, batched | After each migrated service has lived on `.lan.dertechie.de` for ≥7 days |

**Total honest budget:** ~75 min for the minimum viable cutover (just git). Add ~5 min per additional service to migrate. Cleanup batched separately once the verification window has passed.

### 6a. OVH prep

1. OVH manager → "Create application & token", scoped to write `_acme-challenge.*` TXT records under `dertechie.de` only (or zone-level write if scoping isn't available — journal whichever applies).
2. Capture the four values certbot needs: application key, application secret, consumer key, endpoint. Stored in NPM in 6c, nowhere else.
3. Add a CAA record on `dertechie.de`: `0 issue "letsencrypt.org"`. This pins issuance — even if the OVH credentials leak, no other CA is allowed to issue for the zone.

### 6b. Pi-hole wildcard

Pi-hole v6 differs from v5 in three ways that affect this step. All three surfaced during the cutover — see [the Phase 6b journal entry](../../journal/2026-05-30-internal-tls-phase6b-pihole-wildcard.md).

1. Locate Pi-hole's persisted `/etc/dnsmasq.d/` directory (TrueNAS app or Dockge volume mount). On this NAS: `/mnt/nvme/apps/pihole/dnsmasq/`.
2. Create `02-lan-dertechie.conf` with one line: `address=/lan.dertechie.de/10.63.0.2`.
3. Make the host directory readable by FTL. FTL drops to UID 1000 (`pihole` user) inside the container at startup; the TrueNAS dataset default is `drwxrwx--- root:root` which UID 1000 cannot traverse. `chmod 755` on the directory fixes it.
4. Enable custom `/etc/dnsmasq.d/` loading. Pi-hole v6 ignores the directory by default (changed from v5): `sudo docker exec <container> pihole-FTL --config misc.etc_dnsmasq_d true`. Or set `FTLCONF_misc_etc_dnsmasq_d: 'true'` in the container env if the deployment surface allows.
5. Restart the container to apply both step 4 (TOML re-read) and the new file. `pihole reloaddns` (v6's replacement for `restartdns`) re-reads dnsmasq files only once `etc_dnsmasq_d` is already true; for the first-time setup a full restart is cleaner.
6. Verify: `dig +short anything-made-up.lan.dertechie.de @10.63.0.2` returns `10.63.0.2`. `dig +short fritzbox.home @10.63.0.2` still returns `10.63.0.1`.

### 6c. NPM gets the wildcard cert

The token scope set in 6a needs an extra rule for this step — the certbot-dns-ovh plugin calls `GET /domain/zone/` (trailing slash, zone-discovery) before it touches any record, and the 6a scope didn't cover that path. OVH access rules are byte-literal: `/foo` and `/foo/` are distinct rules, and `*` does not match the empty string. See [the Phase 6c journal entry](../../journal/2026-05-30-internal-tls-phase6c-npm-cert.md).

1. **Rotate the CK** to include the zone-discovery rule. Don't use `createToken` (it makes a *new* application each call); use `POST /1.0/auth/credential` with the existing AK to mint a new CK against the same app:

   ```bash
   curl -sS -H "X-Ovh-Application: $OVH_AK" -H "Content-Type: application/json" \
     -d '{"accessRules":[
       {"method":"GET","path":"/domain/zone/"},
       {"method":"GET","path":"/domain/zone/dertechie.de/*"},
       {"method":"POST","path":"/domain/zone/dertechie.de/*"},
       {"method":"PUT","path":"/domain/zone/dertechie.de/*"},
       {"method":"DELETE","path":"/domain/zone/dertechie.de/*"}
     ]}' \
     https://eu.api.ovh.com/1.0/auth/credential
   ```

   Open the returned `validationUrl` in a browser while logged into OVH to activate the new CK. AK and AS stay unchanged.

2. NPM → SSL Certificates → Add SSL Certificate → Let's Encrypt.
3. Domain Names: `*.lan.dertechie.de` and `lan.dertechie.de` (apex too — cheap future-proofing).
4. DNS Challenge → OVH → paste credentials (use the rotated CK from step 1, keep AK + AS from 6a, endpoint `ovh-eu`).
5. Save. NPM/certbot writes a TXT, LE validates, cert lands within ~1 min.
6. If this fails, read the URL in the 403 message byte-by-byte before assuming credentials are wrong — most likely a path-shape mismatch in the access rules.

### 6d. Cut over `git` as the canary

Parallel transition — the old name doesn't disappear yet.

1. **NPM:** edit the Forgejo proxy host. *Add* `git.lan.dertechie.de` as an additional domain (keep `git.home` for now). Attach the new wildcard cert. Save. Both names now work, both serving valid TLS.
2. **Forgejo `ROOT_URL`:** flip from `https://git.home/` to `https://git.lan.dertechie.de/` in the TrueNAS chart wizard env. Restart Forgejo. Generated URLs in the UI start pointing at the new name; the old name keeps resolving via NPM until 6e.
3. **fj on the workstation:**
   - Add `git.lan.dertechie.de <existing-UUID>` to `~/.config/forgejo-cli/client_ids` (same OAuth app registration on the Forgejo side — the host string is just a routing key for fj).
   - `fj -H git.lan.dertechie.de auth login` → browser OAuth → fresh token under the new host.
   - Add alias `git.lan.dertechie.de:30143 → git.lan.dertechie.de` to `~/.local/share/forgejo-cli/keys.json` aliases.
4. **Git remote:** `git remote set-url origin ssh://git@git.lan.dertechie.de:30143/dertechie/AISetup.git`.
5. **Verify** with the checks in § 7.

### 6e. Cleanup, after the verification window

Per migrated service, once it's been on the new name for ≥7 days:

- NPM: remove the `<service>.home` domain from the proxy host.
- Pi-hole UI: delete the `<service>.home` local DNS record.

Workstation (Arch), once `git` has fully moved:

- Remove the `10.63.0.2:30142 ...` line from `~/.config/forgejo-cli/client_ids`.
- Remove the `git.home` host entry and the `10.63.0.2:30143 → git.home` / `git.home:30143 → git.home` aliases from `~/.local/share/forgejo-cli/keys.json`.
- `mkcert -uninstall` to remove the local rootCA from the system trust store. Harmless to leave; cleaner to remove.
- NPM: delete the mkcert Custom cert once no proxy host references it.

`fritzbox.home` stays. It's the router, not a service behind NPM, and the migration buys nothing for it.

`joplin.home` is the decommissioned entry from issue #4 — it drops out as part of that issue, not this one.

## 7. Verification (the done-check)

### Per-phase, while migrating

| Phase | Check | Expected |
|---|---|---|
| 6a OVH | OVH API token "Test" call | 200 |
| 6b Pi-hole | `dig +short anything.lan.dertechie.de @10.63.0.2` | `10.63.0.2` |
| 6b Pi-hole | `dig +short fritzbox.home @10.63.0.2` | `10.63.0.1` |
| 6c NPM | SSL Certificates page shows the wildcard | Expires ~90d out, "Renews automatically" tag |
| 6c NPM | `openssl s_client -connect git.lan.dertechie.de:443 -servername git.lan.dertechie.de </dev/null \| openssl x509 -noout -issuer` | `issuer=...Let's Encrypt...` (current LE intermediate) |
| 6d git | `curl -sI https://git.lan.dertechie.de/api/v1/version` from a fresh device, no custom CA | `HTTP/2 200`, no cert warning |
| 6d git | `fj -R origin issue search --state open` on the workstation | Shows the open issues |
| 6d git | `git pull` and `git push` via the new SSH URL | Succeeds |

### No public footprint check

```sh
dig +short git.lan.dertechie.de @1.1.1.1
dig +short lan.dertechie.de @1.1.1.1
```

Both return nothing (NXDOMAIN). The internet never resolves these. The only public artefact during the renewal window is a transient `_acme-challenge.lan.dertechie.de` TXT.

### End-to-end "spec is done"

1. A new fictional service `<new>.lan.dertechie.de` can be onboarded in <10 minutes: NPM proxy host added, wildcard cert picked from the dropdown, done. No Pi-hole edit, no cert work.
2. A fresh device (phone, visitor laptop) opens `https://git.lan.dertechie.de` on the LAN with a green padlock. **Zero profile install, zero cert trust step.** This is the actual proof that the local-CA distribution problem went away.
3. `.home` references gone from active config except `fritzbox.home`. The mkcert rootCA is no longer in the system trust store. The mkcert Custom cert is no longer in NPM.
4. NPM has completed at least one automatic renewal of the wildcard cert without human intervention.

## 8. Operational notes

### When the OVH token rotates

If the OVH API token is rotated or expires, NPM's next renewal attempt fails silently in the background. Symptoms surface ~14 days before cert expiry if monitoring (see § 9) is in place; without monitoring, the first symptom is "every service breaks at once" on cert expiry day.

Recovery: regenerate the token in OVH manager, update the four values in NPM's SSL Certificate config for the wildcard, trigger a manual renewal. Estimated MTTR: 15 min if the runbook entry exists, 45+ if it doesn't.

### When DNS-01 stops working

Either OVH is down, the token is wrong, the certbot OVH plugin has regressed, or LE rate-limited us. Diagnose in NPM's log file for that cert; the certbot subprocess output shows whether the failure is at OVH (TXT write didn't land) or at LE (challenge validation failed).

### CT log visibility

The wildcard cert appears in public Certificate Transparency logs (this is a Let's Encrypt requirement, not an OVH or NPM choice). That means the string `lan.dertechie.de` is searchable on crt.sh and similar. No IPs, no subdomain names within the wildcard — just the wildcard pattern itself. Accepted.

## 9. Deferred — and what triggers each

| Deferred | Trigger |
|---|---|
| **Cert renewal monitoring** (healthchecks.io ping or Grafana panel for `<14d to expiry`) | First thing to file after this ships. Failure mode without it is severe: silent ~60d cert death → every service breaks at once. |
| **Public LAN access** (VPS + WG tunnel) | First real customer or external collaborator. Already deferred in [`2026-05-25-self-hosted-forgejo-design.md`](2026-05-25-self-hosted-forgejo-design.md) § 9; this spec does not change that. |
| **Migration of services beyond `git`** | Each service migrates when I next touch its NPM proxy host. No bulk migration; no forcing function. |
| **`BuildInPublic` repo on the new domain** | Already issue #3 — that issue adopts this convention when it lands. |
| **Document the new domain in the `fj`-capture convention** | Ops repo, cross-cutting, separate concern. Will file as a follow-on issue. |

## 10. Repo artifact

Files this spec ends up touching during implementation:

- `nas/npm/` — if we keep NPM config under version control (currently it lives in NPM's own DB; reconsider whether to export a snapshot here).
- `nas/pihole/dnsmasq.d/02-lan-dertechie.conf` — the one wildcard config file, version-controlled.
- `docs/runbook.md` — section added: "Internal TLS — how to add a new service, how to recover a failed renewal, how to rotate the OVH token."
- `README.md` — overview line updated to mention the `.lan.dertechie.de` convention.
- `docs/journal/2026-05-30-internal-tls-cutover.md` — to be written when the cutover happens.

## 11. Follow-on issues (after this ships)

1. **Add cert renewal monitoring.** Healthchecks.io ping or Grafana panel for `<14d to expiry`. Label `infra`.
2. **Document the new domain in the fj-capture convention** (ops repo).
3. Anything else surfaced during implementation that doesn't fit this spec.
