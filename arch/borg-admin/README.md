# arch/borg-admin — admin-side Borg ops

Companion to [`nas/borgmatic/`](../../nas/borgmatic/). Lives on the **Arch
workstation only.** Holds the unrestricted admin SSH key the NAS never sees,
so prune / deep check / restore can't be done by an attacker who owns the NAS.

**Design spec:** [`../../docs/superpowers/specs/2026-05-25-nas-backup-strategy-design.md`](../../docs/superpowers/specs/2026-05-25-nas-backup-strategy-design.md)

## Install path (user-systemd, no root)

```bash
mkdir -p ~/.config/borg-admin ~/.config/systemd/user
cp borgmatic.yaml ~/.config/borg-admin/borgmatic.yaml
cp borgmatic-{prune,check}.{service,timer} ~/.config/systemd/user/

# Env file the units read (mode 0600; NTFY_TOPIC matches NAS):
cat > ~/.config/borg-admin/env <<'EOF'
NTFY_TOPIC=aisetup-backup-<same-suffix-as-nas>
# BORG_PASSPHRASE intentionally not in env — pulled from `pass borg/aisetup-passphrase`.
EOF
chmod 600 ~/.config/borg-admin/env ~/.config/borg-admin/borgmatic.yaml

systemctl --user daemon-reload
systemctl --user enable --now borgmatic-prune.timer borgmatic-check.timer
systemctl --user list-timers borgmatic-*
```

## Keys (NOT committed)

- `~/.ssh/borg-admin-arch` (mode 0600) — the admin key.
- `~/.ssh/borg-admin-arch.pub` — its public key, already installed on the Storage Box.

Escrow per spec §8 Phase 3 step 3: password manager + printed paper + encrypted USB offsite.

## One-shot commands

```bash
# List archives:
borgmatic --config ~/.config/borg-admin/borgmatic.yaml list

# Browse contents of an archive (borgmatic's first-class mount action — honors config):
borgmatic --config ~/.config/borg-admin/borgmatic.yaml mount --archive ARCHIVE --mount-point /mnt/borg-restore

# Dry-run prune:
borgmatic --config ~/.config/borg-admin/borgmatic.yaml --dry-run prune

# Force a check now:
systemctl --user start borgmatic-check.service && journalctl --user -u borgmatic-check.service -f
```
