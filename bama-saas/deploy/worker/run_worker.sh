#!/usr/bin/env sh
# In-container scheduler — the compose `worker` service's PID 1.
#
# Replaces host cron for the containerised stack: `docker compose up` starts this
# loop and the pipeline keeps fetching on its own, with no host-side setup and no
# .env required (compose sets DATABASE_URL on the service). install_cron.sh
# remains the path for a bare-metal host deploy; run exactly one of the two, or
# both fetchers race — their flock files live in different mount namespaces
# (host /tmp vs container /tmp) and therefore do not see each other.
#
# Jobs and cadence match install_cron.sh:
#   pipeline  5 min    delta fetch + maintain + snapshot + deal scores + analytics
#   alerts    30 min   evaluate user alerts -> notifications
#   sweep     6 h      full-inventory sweep (page 0 -> end) + gap repair
#   digest    daily    per-user digest, first tick at/after DIGEST_HOUR local
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
ALERTS_EVERY="${BAMA_ALERTS_EVERY:-1800}"
SWEEP_EVERY="${BAMA_SWEEP_EVERY:-21600}"
DIGEST_HOUR="${BAMA_DIGEST_HOUR:-8}"
HEARTBEAT="${BAMA_HEARTBEAT:-30}"

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] worker: $*"; }

run() {  # <label> <script>
    label="$1"
    shift
    log "$label start"
    if sh "$@"; then
        log "$label ok"
    else
        rc=$?
        log "$label FAILED rc=$rc" >&2
    fi
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
next_alerts=$((now + ALERTS_EVERY))
last_digest_day=""

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

log "started (pipeline=${PIPELINE_EVERY}s alerts=${ALERTS_EVERY}s sweep=${SWEEP_EVERY}s digest=${DIGEST_HOUR}:00)"

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
        run pipeline "$SCRIPT_DIR/run_pipeline.sh"
        next_pipeline=$(($(date +%s) + PIPELINE_EVERY))
    fi

    if [ "$now" -ge "$next_alerts" ]; then
        run alerts "$SCRIPT_DIR/run_alerts.sh"
        next_alerts=$(($(date +%s) + ALERTS_EVERY))
    fi

    # Digest is wall-clock daily, not an interval: a 24h timer started from
    # whenever the container last restarted would drift the mail to 3am.
    today=$(date +%Y-%m-%d)
    hour=$(date +%H)
    if [ "$today" != "$last_digest_day" ] && [ "${hour#0}" -ge "$DIGEST_HOUR" ]; then
        run digest "$SCRIPT_DIR/run_digest.sh"
        last_digest_day="$today"
    fi

    sleep "$HEARTBEAT"
done
