#!/usr/bin/env sh
# Rolling feed coverage — walks a bounded chunk of whatever is not covered yet.
#
# This replaces the old full sweep, which walked ~936 pages from page 0 in one
# uninterrupted run and set `reached_end` at the finish. Measured over 39 days
# that run completed 11 times out of 28: bama.ir answers 503, the container
# restarts, and the whole pass is lost. Because removal detection required two
# *completed* sweeps, delisted ads stayed ACTIVE for days and listing episodes
# ended in lumps on 17 of 39 days — every survival curve computed from them was
# reading this schedule rather than the market.
#
# Coverage now accumulates instead. `crawl_gaps` asks which rank ranges nobody
# read inside the coverage window and fetches a bounded page budget of them, so
# the deep tail is walked a chunk at a time. No single run has to survive start
# to finish; three interrupted partial runs prove exactly what one clean sweep
# proved. A run that dies simply leaves the rest as a gap for the next tick.
#
# Extra args are forwarded to `manage.py crawl_gaps`.
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

# Its own lock: a coverage chunk must not block the delta tick, and two chunks
# must never overlap and double the request rate against bama.ir.
LOCK="${BAMA_COVERAGE_LOCK:-/tmp/bama-saas-coverage.lock}"

# `set -eu` is repeated INSIDE this string deliberately: the script's own
# `set -eu` is not inherited by `sh -c`, which runs a fresh shell. Without it
# the exit status would be that of the last command and a chunk that died on a
# network timeout would still report success — that happened on 2026-08-08.
# Routed through `manage.py run_coverage` (not crawl_gaps directly) so this
# chunk gets a JobRun row -- previously coverage sweeps were invisible to
# /api/admin/jobs/overview, only visible in raw container logs.
RUN='set -eu
     PY="$1"; shift
     "$PY" manage.py run_coverage --since-hours 24 "$@"'

if command -v flock >/dev/null 2>&1; then
    exec flock -n "$LOCK" sh -c "$RUN" _ "$PYTHON_BIN" "$@"
else
    echo "bama.coverage: flock not found on this host; running unlocked (non-Linux dev host)." >&2
    exec sh -c "$RUN" _ "$PYTHON_BIN" "$@"
fi
