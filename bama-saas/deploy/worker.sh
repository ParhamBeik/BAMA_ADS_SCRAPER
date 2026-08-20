#!/usr/bin/env sh
# The scheduler — PID 1 of the compose `worker` service, and the only entry
# point for scheduled work. Also usable from host cron: `worker.sh hot`.
#
#   worker.sh            run the loop forever
#   worker.sh <cadence>  run one tick and exit (hot / warm / coverage / maintenance)
#
# Cadences and why:
#   hot          15 min  delta fetch + removal marking + incremental deals + notify
#   coverage     10 min  one bounded chunk of whatever the feed has not shown lately
#   warm         30 min  episodes + daily snapshot + market index
#   maintenance   6 h    full deal rebuild + prune + health report
#
# There is no full sweep. Coverage accumulates from bounded chunks, so no job has
# to survive a ~20-minute uninterrupted walk of the feed for removal detection to
# work — the old sweep completed 11 times in 28 attempts.
#
# Each tick takes an flock so two never overlap and double the request rate
# against bama.ir. Deliberately no `set -e` in the loop: a failing job must be
# logged and retried next tick, never take the container down — a crash-looping
# worker fetches nothing and hides the original error behind restart noise.
set -u

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

# Explicit override > local venv > system python.
if [ -z "${PYTHON_BIN:-}" ]; then
    if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
        PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
    else
        PYTHON_BIN="python3"
    fi
fi

# Host cron has no compose env; load .env if there is one.
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$PROJECT_DIR/.env"
    set +a
fi

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] worker: $*"; }

# flock is a Linux util; on a macOS dev host we run unlocked.
tick() {  # <cadence>
    lock="/tmp/bama-$1.lock"
    if command -v flock >/dev/null 2>&1; then
        flock -n "$lock" "$PYTHON_BIN" manage.py bama "$@"
    else
        "$PYTHON_BIN" manage.py bama "$@"
    fi
}

# One-shot mode.
if [ "$#" -gt 0 ]; then
    tick "$@"
    exit $?
fi

HOT_EVERY="${BAMA_HOT_EVERY:-900}"
COVERAGE_EVERY="${BAMA_COVERAGE_EVERY:-600}"
WARM_EVERY="${BAMA_WARM_EVERY:-1800}"
MAINTENANCE_EVERY="${BAMA_MAINTENANCE_EVERY:-21600}"
HEARTBEAT="${BAMA_HEARTBEAT:-30}"
HEARTBEAT_FILE="${BAMA_WORKER_HEARTBEAT:-/tmp/bama-worker.ok}"

# The previous process is dead; anything still RUNNING is an orphan.
"$PYTHON_BIN" manage.py bama reap_orphans || log "reap_orphans failed (continuing)"

run() {  # <cadence> — up to 3 attempts, then give up until the next tick
    log "$1 start"
    n=0
    while [ "$n" -lt 3 ]; do
        if tick "$1"; then
            log "$1 ok"
            return 0
        fi
        rc=$?
        n=$((n + 1))
        [ "$n" -lt 3 ] && sleep $((n * 8)) && log "$1 retry $n after rc=$rc"
    done
    log "$1 FAILED rc=$rc" >&2
    return "$rc"
}

now=$(date +%s)
next_hot=$now                                  # fetch immediately on boot
next_coverage=$((now + COVERAGE_EVERY))
next_warm=$((now + WARM_EVERY))
next_maintenance=$((now + MAINTENANCE_EVERY))

log "started (hot=${HOT_EVERY}s coverage=${COVERAGE_EVERY}s warm=${WARM_EVERY}s maintenance=${MAINTENANCE_EVERY}s)"

while true; do
    now=$(date +%s)
    date +%s > "$HEARTBEAT_FILE" 2>/dev/null || true

    # Sequential, not backgrounded: every job talks to bama.ir or the same
    # tables, and overlapping them would double the request rate.
    [ "$now" -ge "$next_hot" ]         && { run hot;         next_hot=$(($(date +%s) + HOT_EVERY)); }
    [ "$now" -ge "$next_coverage" ]    && { run coverage;    next_coverage=$(($(date +%s) + COVERAGE_EVERY)); }
    [ "$now" -ge "$next_warm" ]        && { run warm;        next_warm=$(($(date +%s) + WARM_EVERY)); }
    [ "$now" -ge "$next_maintenance" ] && { run maintenance; next_maintenance=$(($(date +%s) + MAINTENANCE_EVERY)); }

    sleep "$HEARTBEAT"
done
