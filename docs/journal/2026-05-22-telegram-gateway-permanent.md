# 2026-05-22 — Making the Telegram bot permanent (Hermes messaging gateway)

The Telegram chatbot has existed for a few days — a Hermes **messaging gateway**
(`hermes gateway`) with the Telegram platform connected, so you can talk to the
agent (with full tool access) from your phone. But it was only ever run **by hand
in the foreground**: `hermes gateway run` under a shell, stopped with Ctrl-C
(`SIGINT`) when the terminal closed. The logs tell that story plainly — the
2026-05-19 session started under `bash` and exited on a planned SIGINT a few
minutes later. So "listening" was not automatic: the bot only answered while a
terminal happened to be holding it open.

Goal here: make it a real always-on service. Four decisions, each with a rejected
alternative.

## Decision 1: user systemd service, not a system service

`hermes gateway install` offers a **user** service (`--user`, the default) or a
boot-time **system** service (`sudo … --system`). We took the user service.

Why: the bot *is* the user. It reads `~/.hermes/config.yaml`, `auth.json`, the
session DB, skills, kanban, the `.env` with the bot token — all owned by
`dertechie`, mode 600. A system service runs as root and would either not find
that state or need `User=`/`$HOME` overrides bolted on, and there is no reason to
run a network-listening agent with tool access as root. The user service runs as
the same identity that owns every byte of state it touches. Rejected the system
service as more privilege for no benefit.

The one thing a user service lacks — surviving when no session is logged in —
is handled by Decision 2.

## Decision 2: linger (the installer enabled it automatically)

A user service normally runs only while the user has an active login session;
systemd tears down the user manager on last logout. `loginctl enable-linger`
flips a system flag (`/var/lib/systemd/linger/dertechie`) that keeps the user
manager alive across logout and from boot-before-login. `hermes gateway install`
**did this itself** ("Enabling linger so the gateway survives SSH logout").

Worth being precise about what linger does *not* do: it does not run anything as
root (the process stays `dertechie`), and it does not wake a sleeping machine —
see Decision 4. Verified `Linger=yes` after install.

## Decision 3: long polling, not webhooks (this is why LAN-only works)

Hermes' Telegram adapter defaults to **long polling**: the bot calls *out* to
Telegram asking "any messages?". Setting `TELEGRAM_WEBHOOK_URL` would switch it to
webhook mode, where Telegram pushes *in* to a public URL. We left it on polling.

This is the quiet reason the whole thing works on a LAN-only box with no public
exposure, no tunnel, no port-forward: polling needs only **outbound** HTTPS to
Telegram, which the workstation already has. A webhook would need an inbound
public endpoint — the opposite of this setup's posture. Confirmed in the live
log: `[Telegram] Connected to Telegram (polling mode)`.

## Decision 4: keep the workstation always-on; do **not** move the bot to the Mac

The real limiter on "message it anytime" is not login state — linger covers that —
it is **sleep**. A suspended machine can't be woken by a Telegram message: polling
is outbound (a queued message has nothing to push), and Wake-on-LAN needs a magic
packet *on the LAN* that Telegram, out on the internet, can't send. While the box
sleeps, the bot is simply offline; messages queue on Telegram's side (~24 h) and
get answered when the machine next wakes on its own.

Two ways to close that gap were on the table:

- **Move the gateway to the Mac M2 Max** — architecturally the "right" box (it's
  the headless, no-sleep, always-on server, `pmset disablesleep 1`). **Rejected:**
  the bot's entire value is local access to *this* workstation's data, files, and
  tools. Hosting it on the Mac severs exactly that. The state also all lives in
  `~/.hermes` here — it'd be a migration, not a move.
- **Keep the workstation always-on** — chosen. It's a desktop (KDE, no lid), so
  the only things that suspend it are KDE's power manager or a manual suspend.
  Mask the sleep targets system-wide so suspend is impossible
  (`systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target`),
  and set KDE → Power Management → "Suspend session: Never". Screen blanking still
  works and doesn't affect the bot. Cost is idle power, accepted on purpose.

This mirrors the existing split: the Mac is the no-sleep inference box, but the
*agent* — and now its messaging front door — stays on the workstation where its
data is.

## Verified

Under the new `hermes-gateway.service` (systemd `--user`), 2026-05-22 17:27:16:

```
Connecting to telegram...
[Telegram] Connected to Telegram (polling mode)
✓ telegram connected
Gateway running with 1 platform(s)
```

Service `active (running)`, `enabled` (auto-start on boot), `Linger=yes`. Operate
it from the runbook (§ "Telegram bot / Hermes messaging gateway (Arch)").
