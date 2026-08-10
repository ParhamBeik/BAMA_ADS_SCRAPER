#!/usr/bin/env sh
# Full-inventory sweep — walks the entire bama.ir feed from page 0 to the end.
#
# The 5-minute tick (run_pipeline.sh) only reads the newest pages and stops early
# once pages stop yielding anything new. That keeps the tick cheap, but early
# stopping can only ever prove "the top of the feed is unchanged" — it cannot
# notice an ad deleted deep in the feed, which pulls everything below it UP past
# a page boundary an earlier run already read. This sweep closes that hole: it
# reads every page, records a PageCoverage row per page, and sets reached_end, so
# full coverage becomes a recorded fact instead of an assumption.
#
# ~936 pages at the default pause is roughly 15-20 minutes, so run this on a slow
# cadence (default 6-hourly in install_cron.sh) — never at tick frequency.
#
# Follows the sweep with crawl_gaps, which refetches any rank range no run has
# covered recently (including ads that shifted during the sweep itself).
# Extra args are forwarded to `manage.py fetch_live`.
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"   # → bama-saas/
cd "$PROJECT_DIR"

# Pick a Python: explicit override > local venv > system python.
PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
    if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
        PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
    else
        PYTHON_BIN="python3"
    fi
fi

# Load env vars (DATABASE_URL, BAMA_*) if a local .env exists.
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$PROJECT_DIR/.env"
    set +a
fi

mkdir -p "$PROJECT_DIR/logs"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.dev}"

# A sweep runs far longer than a tick, so it takes its OWN lock: a sweep in
# progress must not block the 5-minute ticks, and two sweeps must never overlap.
LOCK="${BAMA_SWEEP_LOCK:-/tmp/bama-saas-sweep.lock}"

# The sweep and the gap repair are one unit of work, so they share the lock.
#
# `set -eu` is repeated INSIDE this string, and that is the whole point: the
# script's own `set -eu` at the top is not inherited by `sh -c`, which runs a
# fresh shell. Without it the analytics step ran on top of a failed fetch, and
# the script's exit status was that of the last command — the `crawl_health ||
# echo` below, which cannot fail — so a sweep that died on a network timeout at
# page 619 still exited 0 and reported success. That happened on 2026-08-08.
#
# crawl_health stays last and its exit status stays deliberately swallowed: it is
# a report, not a step, and a red crawler must not make the sweep look like it
# failed to run. The findings go to the log where the operator (and `docker
# compose logs worker`) will see them; the sweep exit code means "did the sweep
# complete", and now it actually does.
RUN='set -eu
     PY="$1"; shift
     "$PY" manage.py fetch_live --mode full "$@"
     "$PY" manage.py crawl_gaps --since-hours 24
     "$PY" manage.py flag_cohort_outliers
     "$PY" manage.py data_quality || echo "bama.sweep: DATA QUALITY DRIFT (see above)" >&2
     echo "--- crawl health ---"
     "$PY" manage.py crawl_health || echo "bama.sweep: CRAWL HEALTH DEGRADED (see above)" >&2'

if command -v flock >/dev/null 2>&1; then
    exec flock -n "$LOCK" sh -c "$RUN" _ "$PYTHON_BIN" "$@"
else
    echo "bama.sweep: flock not found on this host; running unlocked (non-Linux dev host)." >&2
    exec sh -c "$RUN" _ "$PYTHON_BIN" "$@"
fi
