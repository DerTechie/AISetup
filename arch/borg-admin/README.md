# arch/borg-admin — admin-side Borg ops

Companion to [`nas/borgmatic/`](../../nas/borgmatic/). Lives on the **Arch
workstation only.** Holds the unrestricted admin SSH key the NAS never sees,
so prune / deep check / restore can't be done by an attacker who owns the NAS.

**Design spec:** [`../../docs/superpowers/specs/2026-05-25-nas-backup-strategy-design.md`](../../docs/superpowers/specs/2026-05-25-nas-backup-strategy-design.md)

## Install path (user-systemd, no root)

```bash
mkdir -p ~/.config/borg-admin ~/.config/systemd/user ~/.local/bin

cp borgmatic.yaml ~/.config/borg-admin/borgmatic.yaml
cp borgmatic-{prune,check}.{service,timer} ~/.config/systemd/user/

# Passphrase helper (1Password CLI primary, env var fallback):
cp borg-get-passphrase ~/.local/bin/borg-get-passphrase
chmod 755 ~/.local/bin/borg-get-passphrase

# Env file the units read (mode 0600). BORG_PASSPHRASE pulled from 1Password
# at install time so systemd timer runs work even without GUI session.
# NTFY_TOPIC is a random suffix unique to this install — write the chosen
# value down so the phone subscribes to the same topic.
NTFY=aisetup-backup-$(openssl rand -hex 8)
cat > ~/.config/borg-admin/env <<EOF
BORG_PASSPHRASE=$(op read "op://Der Techie/Borg passphrase - AISetup repo/password")
NTFY_TOPIC=$NTFY
EOF
chmod 600 ~/.config/borg-admin/env ~/.config/borg-admin/borgmatic.yaml
echo "ntfy topic: $NTFY"

systemctl --user daemon-reload
systemctl --user enable --now borgmatic-prune.timer borgmatic-check.timer
systemctl --user list-timers borgmatic-*
```

## Interactive wrapper (optional but nice)

Borgmatic config uses `${NTFY_TOPIC}` substitution, which fails when the
variable isn't in the shell env. Systemd timers load it via `EnvironmentFile=`;
for interactive use, wrap with a tiny shim that sources the env file:

```bash
cat > ~/.local/bin/borgmatic-admin <<'WRAPPER'
#!/bin/bash
set -a
. ~/.config/borg-admin/env
set +a
exec borgmatic --config ~/.config/borg-admin/borgmatic.yaml "$@"
WRAPPER
chmod 755 ~/.local/bin/borgmatic-admin
```

Then `borgmatic-admin list` / `borgmatic-admin info` etc. just work.

## Keys (NOT committed)

- `~/.ssh/borg-admin-arch` (mode 0600) — the admin key.
- `~/.ssh/borg-admin-arch.pub` — its public key, already installed on the Storage Box.

Escrow per spec §8 Phase 3 step 3: 1Password (primary) + printed paper (passphrase only) + LUKS USB offsite.

## One-shot commands

```bash
# List archives:
borgmatic-admin list

# Browse contents of an archive (first-class mount action):
borgmatic-admin mount --archive ARCHIVE --mount-point /mnt/borg-restore

# Dry-run prune:
borgmatic-admin --dry-run prune

# Force a check now:
systemctl --user start borgmatic-check.service && journalctl --user -u borgmatic-check.service -f
```
