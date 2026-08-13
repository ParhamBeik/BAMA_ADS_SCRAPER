#!/usr/bin/env sh
# In-container scheduler — the compose `worker` service's PID 1.
#
# Replaces host cron for the containerised stack: `docker compose up` starts this
# loop and the pipeline keeps fetching on its own, with no host-side setup and no
# .env required (compose sets DATABASE_URL on the service).
#
# Jobs and cadence:
#   pipeline  5 min    HOT: delta fetch + mark_inactive + incremental deals
#   analytics 30 min   WARM: episodes + snapshots + market index
#   sweep     6 h      full-inventory sweep + gap repair + full deals + prune
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

PIPELINE_EVERY="${BAMA_PIPELINE_EVERY:-300}"
ANALYTICS_EVERY="${BAMA_ANALYTICS_EVERY:-1800}"
SWEEP_EVERY="${BAMA_SWEEP_EVERY:-21600}"
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
}

# A sweep walks ~939 pages (~15-20 min), so running one on every container
# restart would hammer bama.ir for no gain. Print how long to wait based on the
# AGE of the last run that actually reached the end of the feed — 0 means due
# now. Deriving the delay from the last sweep rather than from boot keeps the
# 6-hourly guarantee real: a restart at 5h59m would otherwise push the next
# sweep to boot+6h, i.e. ~12h since the last one.
sweep_delay() {
    python manage.py shell -c "
from django.utils import timezone
from apps.core.models import FetchRun
last = FetchRun.objects.filter(reached_end=True).order_by('-started_at').first()
# Never swept (fresh deploy): due immediately — a delta tick only reads the top
# of the feed, so without a sweep the DB would stay shallow for six hours.
if last is None:
    print(0)
else:
    age = (timezone.now() - last.started_at).total_seconds()
    print(max(0, int($SWEEP_EVERY - age)))
" 2>/dev/null | tail -1
}

now=$(date +%s)
next_pipeline=$now                       # first tick immediately: start fetching at once
next_analytics=$((now + ANALYTICS_EVERY))

# An unreachable DB or a query error yields no number; defer a full sweep to the
# normal cadence instead of stampeding the feed on every restart loop.
delay="$(sweep_delay)"
case "$delay" in
    ''|*[!0-9]*) log "could not read sweep history — deferring sweep by ${SWEEP_EVERY}s"
                 delay="$SWEEP_EVERY" ;;
esac
next_sweep=$((now + delay))
if [ "$delay" -eq 0 ]; then
    log "no recent completed sweep — sweeping at boot"
else
    log "last sweep still current — next sweep in ${delay}s"
fi

log "started (pipeline=${PIPELINE_EVERY}s analytics=${ANALYTICS_EVERY}s sweep=${SWEEP_EVERY}s)"

while true; do
    now=$(date +%s)

    # Sequential, not backgrounded: a sweep starts at page 0, so it already
    # covers everything a delta tick would have read. Overlapping them would
    # double the request rate against bama.ir to re-read the same pages.
    if [ "$now" -ge "$next_sweep" ]; then
        run sweep "$SCRIPT_DIR/run_sweep.sh"
        next_sweep=$(($(date +%s) + SWEEP_EVERY))
    fi

    if [ "$now" -ge "$next_pipeline" ]; then
        run pipeline "$SCRIPT_DIR/run_pipeline.sh" --cadence hot
        next_pipeline=$(($(date +%s) + PIPELINE_EVERY))
    fi

    if [ "$now" -ge "$next_analytics" ]; then
        run analytics "$SCRIPT_DIR/run_analytics.sh"
        next_analytics=$(($(date +%s) + ANALYTICS_EVERY))
    fi

    date +%s > "$HEARTBEAT_FILE" || true
    sleep "$HEARTBEAT"
done
