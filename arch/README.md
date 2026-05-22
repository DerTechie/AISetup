# Arch workstation config

Config for the Arch Linux workstation side of the hybrid setup: the local
**auxiliary** Ollama model on the RX 7900 XTX (the `aux-local` route) and the
firewall fence around it. The gateway that calls this Ollama runs on the **NAS**
(`10.63.0.2`), so the guard scopes `:11434` to the NAS — not the Mac. See the design spec:
[`../docs/superpowers/specs/2026-05-21-local-aux-models-design.md`](../docs/superpowers/specs/2026-05-21-local-aux-models-design.md).

## Files

| File | Installed to | Purpose |
|---|---|---|
| `ollama-lan.conf` | `/etc/systemd/system/ollama.service.d/lan.conf` | Make Ollama listen on the LAN so the NAS gateway can reach it. |
| `nftables-ollama-guard.nft` | `/etc/nftables-ollama-guard.nft` | Restrict `:11434` to localhost + the NAS gateway (`10.63.0.2`). |
| `ollama-guard.service` | `/etc/systemd/system/ollama-guard.service` | Load the nft guard at boot (it is otherwise runtime-only). |
| `claude-code-otel.sh` | sourced from shell profile | Ship Claude Code OTel usage/cost metrics to the NAS collector. |

## Install

The Arch box runs **no general firewall** (stock Arch ships none enabled). We
do not impose one — `ollama_guard` is a self-contained nftables table that
fences only Ollama and leaves everything else as-is.

```bash
# 1. Ollama listens on the LAN (keeps localhost too)
sudo install -Dm644 ollama-lan.conf /etc/systemd/system/ollama.service.d/lan.conf
sudo systemctl daemon-reload && sudo systemctl restart ollama

# 2. Firewall fence around :11434, persisted via a boot-time oneshot unit
sudo install -m644 nftables-ollama-guard.nft /etc/nftables-ollama-guard.nft
sudo install -m644 ollama-guard.service /etc/systemd/system/ollama-guard.service
sudo systemctl daemon-reload && sudo systemctl enable --now ollama-guard.service
```

## Verify

```bash
# Listening on the LAN
ss -tlnp | grep 11434                      # -> *:11434

# Guard is loaded
sudo nft list table inet ollama_guard

# Mac can reach it; round trip through the gateway works at EUR0
#   (run from a host with the gateway key — see docs/runbook.md)
```

If `aux-local` calls fail with `APIConnectionError ... 10.63.0.29:11434`, check
the bind (step 1) and the guard (step 2). A stray `iptables`-created `ip filter`
table can silently re-block the port — inspect ground truth with
`sudo nft list ruleset`.

## Claude Code usage metrics (OTel → NAS)

Source `claude-code-otel.sh` from your shell profile so every Claude Code session
ships `claude_code.token.usage` / `claude_code.cost.usage` to the NAS OTel
collector at `10.63.0.2:4318`:

```bash
echo '. ~/Organizations/DerTechie/AISetup/arch/claude-code-otel.sh' >> ~/.bashrc
```

This is the Max-subscription usage charted by the Grafana "Claude Code" dashboard
(`http://10.63.0.2:3001`). It is independent of the LiteLLM gateway — the Max
subscription cannot be proxied through LiteLLM, so Claude Code spend never
appears in gateway dashboards and must be measured separately via OTel.

The snippet sets `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative`
because Claude Code defaults to `delta` temporality, which Prometheus cannot
aggregate correctly without the cumulative override.
