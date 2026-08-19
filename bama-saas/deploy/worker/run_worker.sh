#!/usr/bin/env sh
# In-container scheduler — the compose `worker` service's PID 1.
#
# Replaces host cron for the containerised stack: `docker compose up` starts this
# loop and the pipeline keeps fetching on its own, with no host-side setup and no
# .env required (compose sets DATABASE_URL on the service).
#
# Jobs and cadence:
#   pipeline    15 min  HOT: delta fetch + mark_inactive + incremental deals
#   coverage    10 min  rolling chunk of whatever the feed has not shown lately
#   analytics   30 min  WARM: episodes + snapshots + market index
#   maintenance  6 h    full deal rebuild + prune + health report
#
# There is no full sweep any more. Coverage accumulates from bounded chunks
# (see run_coverage.sh), so no job has to survive a ~20-minute uninterrupted
# walk of the feed to make removal detection work.
#
# Logs go to stdout, so `docker compose logs -f worker` is the whole story.
#
# NOTE: deliberately no `set -e`. A failing job must be logged and retried on the
# next tick, never take the container down — a crash-looping worker fetches
# nothing and hides the original error behind restart noise.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"   # -> bama-saas/
cd "$PROJECT_DIR"

PIPELINE_EVERY="${BAMA_PIPELINE_EVERY:-900}"
COVERAGE_EVERY="${BAMA_COVERAGE_EVERY:-600}"
ANALYTICS_EVERY="${BAMA_ANALYTICS_EVERY:-1800}"
MAINTENANCE_EVERY="${BAMA_MAINTENANCE_EVERY:-21600}"
HEARTBEAT="${BAMA_HEARTBEAT:-30}"
HEARTBEAT_FILE="${BAMA_WORKER_HEARTBEAT:-/tmp/bama-worker.ok}"

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] worker: $*"; }

# Previous process is dead; anything still RUNNING is an orphan.
if python manage.py reap_orphan_runs; then
    log "reaped orphan RUNNING rows"
else
    log "reap_orphan_runs failed (continuing)"
fi

run() {  # <label> <script>
    label="$1"
    shift
    date +%s > "$HEARTBEAT_FILE" || true
    log "$label start"
    n=0
    while [ "$n" -lt 3 ]; do
        if sh "$@"; then
            log "$label ok"
            return 0
        fi
        rc=$?
        n=$((n + 1))
        if [ "$n" -lt 3 ]; then
            sleep $((n * 8))
            log "$label retry $n after rc=$rc"
        fi
    done
    log "$label FAILED rc=$rc" >&2
    return "$rc"
}

now=$(date +%s)
next_pipeline=$now                       # first tick immediately: start fetching at once
next_coverage=$((now + COVERAGE_EVERY))
next_analytics=$((now + ANALYTICS_EVERY))
next_maintenance=$((now + MAINTENANCE_EVERY))

# No boot-time sweep scheduling any more. The old loop queried the age of the
# last `reached_end` run to decide whether to sweep at boot; with coverage
# accumulating from bounded chunks there is no single run to be current or
# stale, and a chunk is cheap enough to just run on its normal cadence.
log "started (pipeline=${PIPELINE_EVERY}s coverage=${COVERAGE_EVERY}s analytics=${ANALYTICS_EVERY}s maintenance=${MAINTENANCE_EVERY}s)"

while true; do
    now=$(date +%s)

    # Sequential, not backgrounded: every job here talks to bama.ir or the same
    # tables, and overlapping them would double the request rate.
    if [ "$now" -ge "$next_pipeline" ]; then
        run pipeline "$SCRIPT_DIR/run_pipeline.sh" --cadence hot
        next_pipeline=$(($(date +%s) + PIPELINE_EVERY))
    fi

    if [ "$now" -ge "$next_coverage" ]; then
        run coverage "$SCRIPT_DIR/run_coverage.sh"
        next_coverage=$(($(date +%s) + COVERAGE_EVERY))
    fi

    if [ "$now" -ge "$next_analytics" ]; then
        run analytics "$SCRIPT_DIR/run_analytics.sh"
        next_analytics=$(($(date +%s) + ANALYTICS_EVERY))
    fi

    if [ "$now" -ge "$next_maintenance" ]; then
        run maintenance "$SCRIPT_DIR/run_maintenance.sh"
        next_maintenance=$(($(date +%s) + MAINTENANCE_EVERY))
    fi

    date +%s > "$HEARTBEAT_FILE" || true
    sleep "$HEARTBEAT"
done
