#!/usr/bin/env bash
# Migrate Phase-1 snapshot tasks from per-leaf to recursive-on-parent for the 6
# NVMe parents whose ZFS replication tasks fail with:
#   [EFAULT] Dataset 'nvme/apps/<x>' does not have any matching snapshots to replicate.
#
# Root cause: TrueNAS recursive Replication needs a matching snapshot on the
# source dataset itself. Class-A per-leaf tasks snapshot only the children, so
# the parent has nothing to anchor on. Canonical TrueNAS pattern (see journal):
# recursive Periodic Snapshot Task on the parent.
#
# This script ADDS the 21 new recursive parent tasks. It does NOT touch the
# existing per-leaf tasks or any replication config. Cleanup of the redundant
# per-leaf tasks comes after replicas are verified.
#
# Run on the NAS as the dertechie user (sudo prompts as usual).

set -euo pipefail

create_task() {
  local dataset="$1" hour="$2" dom="$3" lifetime_value="$4" lifetime_unit="$5" naming_schema="$6"
  local exclude_json="${7:-[]}"

  local payload
  payload=$(jq -nc \
    --arg dataset "$dataset" \
    --arg hour "$hour" \
    --arg dom "$dom" \
    --argjson lifetime_value "$lifetime_value" \
    --arg lifetime_unit "$lifetime_unit" \
    --arg naming_schema "$naming_schema" \
    --argjson exclude "$exclude_json" \
    '{
      dataset: $dataset,
      recursive: true,
      exclude: $exclude,
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

# Pure Class A parents — hourly + daily anchor only
echo "=== Pure Class A parents ==="
for parent in immich litellm; do
  create_task "nvme/apps/$parent" "*" "*" 24 HOUR "auto-%Y-%m-%d_%H-%M-A"
  create_task "nvme/apps/$parent" "3" "*" 14 DAY  "auto-%Y-%m-%d_%H-%M-A"
done

# langfuse: same shape as Class A parents, but exclude clickhouse-logs
echo "=== langfuse (excluding clickhouse-logs) ==="
LANGFUSE_EXCLUDE='["nvme/apps/langfuse/clickhouse-logs"]'
create_task "nvme/apps/langfuse" "*" "*" 24 HOUR "auto-%Y-%m-%d_%H-%M-A" "$LANGFUSE_EXCLUDE"
create_task "nvme/apps/langfuse" "3" "*" 14 DAY  "auto-%Y-%m-%d_%H-%M-A" "$LANGFUSE_EXCLUDE"

# Mixed Class A + B parents — A hourly+daily, B 6h+daily+monthly (5 tasks each)
echo "=== Mixed Class A + B parents ==="
for parent in metrics n8n paperless-ngx; do
  create_task "nvme/apps/$parent" "*"   "*" 24 HOUR  "auto-%Y-%m-%d_%H-%M-A"
  create_task "nvme/apps/$parent" "3"   "*" 14 DAY   "auto-%Y-%m-%d_%H-%M-A"
  create_task "nvme/apps/$parent" "*/6" "*"  3 DAY   "auto-%Y-%m-%d_%H-%M-B"
  create_task "nvme/apps/$parent" "3"   "*" 30 DAY   "auto-%Y-%m-%d_%H-%M-B"
  create_task "nvme/apps/$parent" "3"   "1"  6 MONTH "auto-%Y-%m-%d_%H-%M-B"
done

echo
echo "=== Verify: new recursive parent tasks should appear ==="
sudo midclt call pool.snapshottask.query | \
  jq -r '.[] | select(.recursive and (.dataset | test("^nvme/apps/(immich|langfuse|litellm|metrics|n8n|paperless-ngx)$"))) |
         "\(.dataset)\t\(.naming_schema)\t\(.lifetime_value)\(.lifetime_unit)\thour=\(.schedule.hour) dom=\(.schedule.dom)"' | \
  sort

echo
echo "=== Next steps ==="
echo "1. Wait for the next top-of-hour, or take one manual recursive snapshot per parent"
echo "   to unblock replication immediately, e.g.:"
echo "     TS=\$(date +%Y-%m-%d_%H-%M)"
echo "     for p in immich litellm metrics n8n paperless-ngx; do"
echo "       sudo zfs snapshot -r nvme/apps/\$p@auto-\${TS}-A"
echo "     done"
echo "     # langfuse: snapshot children individually to honor the clickhouse-logs exclusion"
echo "     sudo zfs snapshot nvme/apps/langfuse@auto-\${TS}-A"
echo "     sudo zfs snapshot nvme/apps/langfuse/pgdata@auto-\${TS}-A"
echo "     sudo zfs snapshot nvme/apps/langfuse/clickhouse-data@auto-\${TS}-A"
echo "     sudo zfs snapshot nvme/apps/langfuse/minio-data@auto-\${TS}-A"
echo "2. zfs list -t snapshot -d 1 nvme/apps/{immich,langfuse,litellm,metrics,n8n,paperless-ngx}"
echo "3. Re-run the 6 failed Replication Tasks from the UI."
echo "4. Verify tank/replica/nvme-apps/ now has all 9 parents populated."
echo "5. Confirm working, THEN delete the 25 redundant per-leaf snapshot tasks."
