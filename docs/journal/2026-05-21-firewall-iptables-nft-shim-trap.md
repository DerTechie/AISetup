# 2026-05-21 — The "failed" iptables command that silently broke the gateway

## Context

Moving Hermes' aux tasks onto the Arch GPU (see
[the aux-models journal](2026-05-21-local-aux-models-on-arch.md)) needed one
thing the rest of the work didn't: the Arch Ollama had to listen on the LAN so
the Mac LiteLLM gateway could reach it (`OLLAMA_HOST=0.0.0.0:11434`). Binding to
the LAN means exposing an **unauthenticated** API, so the plan was bind + a
firewall rule scoping `:11434` to localhost and the Mac.

## The trap

The first attempt used the familiar `iptables` syntax:

```
sudo iptables -I INPUT -p tcp --dport 11434 -j DROP
# Warning: Extension tcp revision 0 not supported, missing kernel module?
```

That warning *looks* like the command failed. It did not. On modern Arch,
`/usr/bin/iptables` is a symlink to `xtables-nft-multi` — the **nftables shim**.
The `tcp`-match extension couldn't load (no legacy `xt_tcpudp` module), so the
fancy match was dropped, but the shim still wrote a real nftables rule:

```
table ip filter {
  chain INPUT {
    tcp dport 11434 counter packets 202 bytes 12256 drop   # blocks ALL of :11434
  }
}
```

An unconditional drop on the port, from everyone. And in nftables, **a `drop` in
any base chain is final** — so this silently overrode the carefully-scoped
`ollama_guard` table we added afterwards. Every gateway round trip failed with
`Cannot connect to host 10.63.0.29:11434`.

## The misdiagnosis (and the recovery)

Two wrong turns, both from reasoning instead of looking:

1. "The warning means it failed" — so I assumed no rule existed.
2. When the Mac was still blocked after fixing `ollama_guard`, I assumed a
   **pre-existing default-deny firewall**. Plausible (the box had 84 live
   `nf_tables` refs), but wrong.

The fix was to stop guessing and **dump ground truth**: `sudo nft list ruleset`.
It showed exactly three things — libvirt's dynamic NAT tables (benign), our
correct `ollama_guard`, and the stray `ip filter` drop from the "failed"
iptables command. One `sudo nft delete table ip filter` and the round trip
worked: `aux-local` replied in ~1 s at €0.

## What the box actually runs

Worth recording, because it shaped the persistence decision: the workstation
runs **no general firewall**. `nftables`/`iptables` services are all disabled;
`ufw`/`firewalld` aren't installed; `/etc/nftables.conf` is the untouched Arch
skeleton (a default-deny template) but is never loaded. The only live rules are
libvirt's. So we did **not** enable a whole firewall — we persisted just the
surgical `ollama_guard` table via a boot-time oneshot unit (see
[`arch/`](../../arch/)), fencing the one service we exposed and changing nothing
else.

## Lessons

- On a hybrid iptables/nftables system, a **"failed" `iptables` command can
  still mutate the ruleset.** Prefer `nft` directly; when something is blocked,
  read `nft list ruleset` before theorising.
- `accept` is not a trump card. In nftables, any base chain's `drop` wins over
  another chain's `accept` at the same hook — a scoped guard can't rescue a
  packet an unconditional drop already killed.
- Verify the platform before designing the fix: "stock Arch, didn't change
  anything" turned out to mean *no firewall at all*, which made a surgical
  per-service guard the right call over a global default-deny ruleset.
