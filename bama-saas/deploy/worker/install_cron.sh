#!/usr/bin/env sh
# Install (or refresh) the bama-saas worker crontab — scheduled jobs:
#
#   pipeline   every 5 min   HOT: delta fetch + mark_inactive + incremental deals
#   analytics  every 30 min  WARM: episodes + snapshots + market index
#   sweep      every 6 h     full-inventory sweep + gap repair + full deals + prune
#   alerts     every 30 min  evaluate user alerts → notifications
#   digest     daily ~08:17  per-user daily digest
#
# The pipeline tick stops early once the top of the feed goes quiet, which is
# cheap but cannot detect ads deleted deep in the feed. The sweep is what makes
# coverage provable; see run_sweep.sh for the full reasoning.
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
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

# Host cron is for a bare-metal deploy. The containerised stack already schedules
# itself via the compose `worker` service, and the two cannot see each other's
# flock files (host /tmp vs container /tmp), so installing both means two
# fetchers hitting the same database concurrently. Refuse instead.
if docker ps --filter name=bama-worker --filter status=running -q 2>/dev/null | grep -q .; then
    echo "install_cron: the compose 'worker' container is running — refusing to install." >&2
    echo "  It already runs these four jobs; host cron would double every fetch." >&2
    echo "  Pick one: 'docker compose stop worker' first, or skip cron entirely." >&2
    exit 1
fi

# Not fatal: settings/base.py defaults DATABASE_URL to the compose Postgres on
# 5433, so cron without a .env still reaches the one real database. Worth saying
# out loud anyway, because the BAMA_* tuning knobs do come from .env.
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "install_cron: no .env at $PROJECT_DIR/.env — using built-in defaults" >&2
    echo "  (DATABASE_URL -> localhost:5433). Run 'cp .env.example .env' to tune." >&2
fi

PIPELINE="$SCRIPT_DIR/run_pipeline.sh"
ANALYTICS="$SCRIPT_DIR/run_analytics.sh"
SWEEP="$SCRIPT_DIR/run_sweep.sh"
ALERTS="$SCRIPT_DIR/run_alerts.sh"
DIGEST="$SCRIPT_DIR/run_digest.sh"
chmod +x "$PIPELINE" "$ANALYTICS" "$SWEEP" "$ALERTS" "$DIGEST"

PIPELINE_CADENCE="${1:-*/5 * * * *}"
ALERTS_CADENCE="${2:-*/30 * * * *}"
DIGEST_CADENCE="${3:-17 8 * * *}"   # daily, off the :00 mark
SWEEP_CADENCE="${4:-23 */6 * * *}"  # 6-hourly, off the :00 mark so a sweep never
                                    # starts in the same second as a tick
ANALYTICS_CADENCE="${5:-7,37 * * * *}"

# Each entry: a unique marker comment + the cron line. The marker lets us drop
# only our own lines on refresh (safe alongside the user's other crontab jobs).
mk_entry() {  # <marker> <cadence> <runner>
    printf '%s\n%s %s >> %s/cron.log 2>&1\n' "$1" "$2" "$3" "$LOG_DIR"
}

ENTRIES="$(mk_entry "# bama-saas-worker-pipeline (auto-managed)" "$PIPELINE_CADENCE" "$PIPELINE --cadence hot")"
ENTRIES="$ENTRIES
$(mk_entry "# bama-saas-worker-analytics (auto-managed)" "$ANALYTICS_CADENCE" "$ANALYTICS")"
ENTRIES="$ENTRIES
$(mk_entry "# bama-saas-worker-sweep (auto-managed)" "$SWEEP_CADENCE" "$SWEEP")"
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
  $PIPELINE_CADENCE  →  $PIPELINE --cadence hot
  $ANALYTICS_CADENCE  →  $ANALYTICS
  $SWEEP_CADENCE  →  $SWEEP
  $ALERTS_CADENCE  →  $ALERTS
  $DIGEST_CADENCE  →  $DIGEST
Remove:  crontab -l | grep -v 'bama-saas-worker' | crontab -
EOF
