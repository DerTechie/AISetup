# Mac side

The Mac M2 Max (headless, no-sleep, `10.63.0.32`) is a pure **inference
appliance**: it runs only the `private` model in Ollama. The LiteLLM gateway
moved to the NAS — see [`../docs/journal/2026-05-22-litellm-gateway-to-nas.md`](../docs/journal/2026-05-22-litellm-gateway-to-nas.md).

## Files

| File | Installed to | Purpose |
|---|---|---|
| `com.ollama.serve.plist` | `~/Library/LaunchAgents/` | Managed `ollama serve` on `0.0.0.0:11434` (LAN), in the `gui/$UID` domain so it keeps Metal GPU access. Replaces the menubar app's server on this headless box. |
| `pf-ollama-guard.conf` | `/etc/pf-ollama-guard.conf` | pf ruleset fencing `:11434` to localhost + the NAS (`10.63.0.2`). |
| `com.ollama.pfguard.plist` | `/Library/LaunchDaemons/` | Enable pf + load the guard at boot (root). |

The gateway stack that used to live here (`docker-compose.yml`, `litellm-config.yaml`,
`.env.example`) was retired on 2026-05-22 when the gateway moved to the NAS; it
lives in [`../nas/`](../nas/) now (and in git history if you need the old form).

## Ollama on the LAN (LaunchAgent)

```bash
cp mac/com.ollama.serve.plist ~/Library/LaunchAgents/
plutil -lint ~/Library/LaunchAgents/com.ollama.serve.plist        # -> OK
pkill -f "Ollama.app/Contents/MacOS/Ollama"                       # quit menubar app
pkill -f "Contents/Resources/ollama serve"                        # and its server
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ollama.serve.plist
```

Then **System Settings → General → Login Items → turn OFF "Ollama"** so the
menubar app does not relaunch on reboot and fight the agent for `:11434`.

Verify: `lsof -nP -iTCP:11434 -sTCP:LISTEN` → `*:11434`;
`/Applications/Ollama.app/Contents/Resources/ollama ps` → `100% GPU`.

## Firewall fence (pf)

The Arch box runs no general firewall and uses a surgical nftables guard; the
Mac is the same idea with pf. This Mac uses the Application Firewall, not pf, so
loading a self-contained default-pass ruleset is acceptable.

```bash
sudo install -m644 mac/pf-ollama-guard.conf /etc/pf-ollama-guard.conf
sudo install -m644 -o root -g wheel mac/com.ollama.pfguard.plist \
     /Library/LaunchDaemons/com.ollama.pfguard.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/com.ollama.pfguard.plist
sudo pfctl -E -f /etc/pf-ollama-guard.conf        # apply now (no reboot)
```

Verify:

```bash
sudo pfctl -s rules | grep 11434          # the block-drop rule is present
sudo pfctl -s info  | head -1             # Status: Enabled
# From a NON-allowed LAN host, :11434 should now time out; from the NAS it works.
```

> **Caveat:** `pfctl -f` replaces the active ruleset. This is safe because the
> Mac doesn't otherwise use pf. If you later enable a pf consumer (e.g. Internet
> Sharing), move these rules into a named anchor referenced from `/etc/pf.conf`
> instead of loading them as the main ruleset.
