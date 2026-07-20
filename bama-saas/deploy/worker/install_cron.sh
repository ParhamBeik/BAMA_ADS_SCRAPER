#!/usr/bin/env sh
# Install (or refresh) the bama-saas worker crontab — all three scheduled jobs:
#
#   pipeline   every 5 min   fetch + maintain + snapshot + deal scores + analytics
#   alerts     every 30 min  evaluate user alerts → notifications (email/Telegram)
#   digest     daily ~08:17  per-user daily digest (weekly digest is a manual run)
#
# Idempotent: each job owns a distinct marker line; re-running refreshes paths
# without duplicating. Usage:
#
#   ./install_cron.sh                       # defaults below
#   ./install_cron.sh "*/15 * * * *"        # override the PIPELINE cadence only
#
# Remove everything:  crontab -l | grep -v 'bama-saas-worker' | crontab -
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)/logs"
mkdir -p "$LOG_DIR"

PIPELINE="$SCRIPT_DIR/run_pipeline.sh"
ALERTS="$SCRIPT_DIR/run_alerts.sh"
DIGEST="$SCRIPT_DIR/run_digest.sh"
chmod +x "$PIPELINE" "$ALERTS" "$DIGEST"

PIPELINE_CADENCE="${1:-*/5 * * * *}"
ALERTS_CADENCE="${2:-*/30 * * * *}"
DIGEST_CADENCE="${3:-17 8 * * *}"   # daily, off the :00 mark

# Each entry: a unique marker comment + the cron line. The marker lets us drop
# only our own lines on refresh (safe alongside the user's other crontab jobs).
mk_entry() {  # <marker> <cadence> <runner>
    printf '%s\n%s %s >> %s/cron.log 2>&1\n' "$1" "$2" "$3" "$LOG_DIR"
}

ENTRIES="$(mk_entry "# bama-saas-worker-pipeline (auto-managed)" "$PIPELINE_CADENCE" "$PIPELINE")"
ENTRIES="$ENTRIES
$(mk_entry "# bama-saas-worker-alerts (auto-managed)" "$ALERTS_CADENCE" "$ALERTS")"
ENTRIES="$ENTRIES
$(mk_entry "# bama-saas-worker-digest (auto-managed)" "$DIGEST_CADENCE" "$DIGEST")"

TMP="$(mktemp)"
# Drop every prior bama-saas-worker entry, then append the fresh set.
crontab -l 2>/dev/null | grep -v "bama-saas-worker" > "$TMP" || true
printf '%s\n' "$ENTRIES" >> "$TMP"
crontab "$TMP"
rm -f "$TMP"

cat <<EOF
Installed worker cron (log → $LOG_DIR/cron.log):
  $PIPELINE_CADENCE  →  $PIPELINE
  $ALERTS_CADENCE  →  $ALERTS
  $DIGEST_CADENCE  →  $DIGEST
Remove:  crontab -l | grep -v 'bama-saas-worker' | crontab -
EOF
