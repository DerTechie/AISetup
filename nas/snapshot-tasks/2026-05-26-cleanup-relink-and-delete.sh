#!/usr/bin/env bash
# Follow-up to 2026-05-26-recursive-parent-migration.sh.
#
# Two phases:
#   1. Re-link the 6 previously-broken Replication Tasks from the old
#      per-leaf snapshot task IDs to the new recursive parent task IDs.
#   2. (After confirmation) delete the 25 redundant per-leaf snapshot tasks
#      that are now erroring every hour with "dataset already exists" because
#      the recursive parent tasks beat them to the snapshot.
#
# Hard-coded IDs reflect the actual catalog as of 2026-05-26 (verified via
# `midclt call pool.snapshottask.query`). If you have re-created tasks since
# then, re-derive the maps below before running.
#
# Run on the NAS as the dertechie user.

set -euo pipefail

# --- Phase 0: verify langfuse exclude is set on the new tasks ----------------
echo "=== Phase 0: verify langfuse snapshot tasks exclude clickhouse-logs ==="
sudo midclt call pool.snapshottask.query | \
  jq '.[] | select(.id == 49 or .id == 50) | {id, dataset, exclude, naming_schema, lifetime: "\(.lifetime_value)\(.lifetime_unit)"}'

EXCLUDE_OK=$(sudo midclt call pool.snapshottask.query | \
  jq '[.[] | select(.id == 49 or .id == 50) | (.exclude | contains(["nvme/apps/langfuse/clickhouse-logs"]))] | all')

if [[ "$EXCLUDE_OK" != "true" ]]; then
  echo "WARNING: langfuse new tasks (49, 50) do NOT have clickhouse-logs in their exclude list."
  echo "         Fix before continuing or clickhouse-logs will start receiving snapshots."
  read -rp "Continue anyway? [y/N] " confirm
  [[ "$confirm" == "y" || "$confirm" == "Y" ]] || exit 1
fi

# --- Phase 1: re-link replications -------------------------------------------
echo
echo "=== Phase 1: re-link replications to new recursive parent task IDs ==="

# replication name → JSON array of new task IDs
declare -A RELINK=(
  ["Immich Replica"]="[45, 46]"
  ["LiteLLM Replica"]="[47, 48]"
  ["Langfuse Replica"]="[49, 50]"
  ["n8n Replica"]="[56, 57, 58, 59, 60]"
  ["Paperless Replica"]="[61, 62, 63, 64, 65]"
  ["Metrics Replica"]="[51, 52, 53, 54, 55]"
)

for name in "Immich Replica" "LiteLLM Replica" "Langfuse Replica" \
            "n8n Replica" "Paperless Replica" "Metrics Replica"; do
  ids="${RELINK[$name]}"
  repl_id=$(sudo midclt call replication.query "[[\"name\", \"=\", \"$name\"]]" | jq -r '.[0].id')
  if [[ -z "$repl_id" || "$repl_id" == "null" ]]; then
    echo "  $name: NOT FOUND — skipping"
    continue
  fi
  printf '  %-20s (id=%s) → periodic_snapshot_tasks=%s  ' "$name" "$repl_id" "$ids"
  if sudo midclt call replication.update "$repl_id" "{\"periodic_snapshot_tasks\": $ids}" >/dev/null; then
    echo "OK"
  else
    echo "FAIL"
    exit 1
  fi
done

echo
echo "=== Verify new linkage ==="
sudo midclt call replication.query | \
  jq -r '.[] | "\(.name): \(.periodic_snapshot_tasks | map(.id) | sort | tostring)"' | sort

# --- Phase 2: delete old per-leaf snapshot tasks -----------------------------
echo
echo "=== Phase 2: delete 25 old per-leaf snapshot tasks ==="

# IDs 1–16 (Class A per-leaf), 20–22 (n8n/data B per-leaf),
# 29–31 (paperless-ngx/data B per-leaf), 32–34 (prometheus-data B per-leaf).
OLD_TASK_IDS=(1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 20 21 22 29 30 31 32 33 34)

echo "Tasks to delete:"
sudo midclt call pool.snapshottask.query | \
  jq --argjson ids "$(printf '%s\n' "${OLD_TASK_IDS[@]}" | jq -R . | jq -s 'map(tonumber)')" \
    '.[] | select(.id as $id | $ids | contains([$id])) |
       "\(.id)\t\(.dataset)\t\(.naming_schema)\t\(.lifetime_value)\(.lifetime_unit)"' -r | sort -n

echo
read -rp "Proceed with deletion? Once gone, the hourly 'already exists' errors stop and the catalog is clean. [y/N] " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
  echo "Aborting deletion. Re-linking is complete and persisted; old tasks remain."
  exit 0
fi

for id in "${OLD_TASK_IDS[@]}"; do
  printf '  Deleting task id=%2d ... ' "$id"
  if sudo midclt call pool.snapshottask.delete "$id" >/dev/null 2>&1; then
    echo "OK"
  else
    echo "FAIL (may have already been removed)"
  fi
done

echo
echo "=== Final snapshot-task catalog ==="
sudo midclt call pool.snapshottask.query | \
  jq -r '.[] | "\(.id)\t\(.dataset)\trecursive=\(.recursive)\t\(.naming_schema)\t\(.lifetime_value)\(.lifetime_unit)"' | \
  sort -n

echo
echo "Done. Next: confirm the next hourly snapshot fires cleanly with no 'already exists' errors,"
echo "      check tank/replica/nvme-apps/ keeps receiving new snapshots, and we proceed to"
echo "      spec + journal updates."
