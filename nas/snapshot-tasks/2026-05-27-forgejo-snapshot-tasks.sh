#!/usr/bin/env bash
# Add snapshot tasks for the new Forgejo datasets.
#
# Two parents:
#   - nvme/apps/forgejo  → Class M (hot+standard, 5 tasks recursive)
#                          covers data + postgres_data, matches n8n/paperless/metrics
#   - tank/apps/forgejo  → Class B (standard only, 3 tasks recursive)
#                          covers LFS objects; sparser cadence per design spec §7
#                          ("lfs/ is large and cold — sparser snapshots, longer
#                          retention"). 6h cadence is overkill for LFS but matches
#                          existing Class B shape so we stay in the codified scheme.
#
# Run on the NAS as the dertechie user (sudo prompts as usual). Idempotent? No —
# re-running will create duplicate tasks. Run once.
#
# Schema (from 2026-05-26-recursive-parent-migration.sh):
#   A hourly:  hour=*    dom=*  retention 24 HOUR   -A naming
#   A daily:   hour=3    dom=*  retention 14 DAY    -A naming
#   B 6h:      hour=*/6  dom=*  retention  3 DAY    -B naming
#   B daily:   hour=3    dom=*  retention 30 DAY    -B naming
#   B monthly: hour=3    dom=1  retention  6 MONTH  -B naming

set -euo pipefail

create_task() {
  local dataset="$1" hour="$2" dom="$3" lifetime_value="$4" lifetime_unit="$5" naming_schema="$6"

  local payload
  payload=$(jq -nc \
    --arg dataset "$dataset" \
    --arg hour "$hour" \
    --arg dom "$dom" \
    --argjson lifetime_value "$lifetime_value" \
    --arg lifetime_unit "$lifetime_unit" \
    --arg naming_schema "$naming_schema" \
    '{
      dataset: $dataset,
      recursive: true,
      exclude: [],
      lifetime_value: $lifetime_value,
      lifetime_unit: $lifetime_unit,
      naming_schema: $naming_schema,
      schedule: {
        minute: "0",
        hour: $hour,
        dom: $dom,
        month: "*",
        dow: "*",
        begin: "00:00",
        end: "23:59"
      },
      enabled: true,
      allow_empty: true
    }')

  printf '  %-25s recursive  %-30s  %2d %-5s  hour=%-3s dom=%-1s' \
    "$dataset" "$naming_schema" "$lifetime_value" "$lifetime_unit" "$hour" "$dom"
  if sudo midclt call pool.snapshottask.create "$payload" >/dev/null; then
    echo "  OK"
  else
    echo "  FAIL"
    return 1
  fi
}

echo "=== Class M on nvme/apps/forgejo (5 tasks) ==="
create_task "nvme/apps/forgejo" "*"   "*" 24 HOUR  "auto-%Y-%m-%d_%H-%M-A"
create_task "nvme/apps/forgejo" "3"   "*" 14 DAY   "auto-%Y-%m-%d_%H-%M-A"
create_task "nvme/apps/forgejo" "*/6" "*"  3 DAY   "auto-%Y-%m-%d_%H-%M-B"
create_task "nvme/apps/forgejo" "3"   "*" 30 DAY   "auto-%Y-%m-%d_%H-%M-B"
create_task "nvme/apps/forgejo" "3"   "1"  6 MONTH "auto-%Y-%m-%d_%H-%M-B"

echo "=== Class B on tank/apps/forgejo (3 tasks) ==="
create_task "tank/apps/forgejo" "*/6" "*"  3 DAY   "auto-%Y-%m-%d_%H-%M-B"
create_task "tank/apps/forgejo" "3"   "*" 30 DAY   "auto-%Y-%m-%d_%H-%M-B"
create_task "tank/apps/forgejo" "3"   "1"  6 MONTH "auto-%Y-%m-%d_%H-%M-B"

echo
echo "=== Verify: 8 new recursive tasks should appear ==="
sudo midclt call pool.snapshottask.query | \
  jq -r '.[] | select(.recursive and (.dataset | test("^(nvme|tank)/apps/forgejo$"))) |
         "\(.dataset)\t\(.naming_schema)\t\(.lifetime_value)\(.lifetime_unit)\thour=\(.schedule.hour) dom=\(.schedule.dom)"' | \
  sort

echo
echo "=== Next steps ==="
echo "1. Take one manual recursive snapshot per parent so the replication task in"
echo "   the next step has something to anchor on (don't wait for the next 03:00):"
echo "     TS=\$(date +%Y-%m-%d_%H-%M)"
echo "     sudo zfs snapshot -r nvme/apps/forgejo@auto-\${TS}-A"
echo "     sudo zfs snapshot -r tank/apps/forgejo@auto-\${TS}-B"
echo "2. Create the Replication Task in TrueNAS UI:"
echo "     source nvme/apps/forgejo  →  destination tank/replica/nvme-apps/forgejo"
echo "     recursive, properties-on, 04:00 daily, anchored to the Class-M parent task."
